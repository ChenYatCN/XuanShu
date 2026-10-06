"""Monitor rendered player chat alongside the directed-chat test hook."""

import asyncio
import ctypes
from contextlib import asynccontextmanager
from collections import Counter, deque
import html
import re
import time

from loguru import logger
from src.window_text import read_control_text
from src.chat_translation_api import ChatTranslationAPI, TranslationError
from src.utils import clients_share_live_area
from src.automation_ownership import automation_owner, get_client_automation_ownership


async def _chat_texts(client):
    nodes = await client.root_window.get_windows_with_name('chatLog')
    if not nodes:
        raise RuntimeError('未找到 chatLog，不能建立接收基线')
    return [await read_control_text(node) or '' for node in nodes]


async def _chat_edit(client):
    node = client.root_window
    for name in ('WorldView', 'WizardChatBox', 'chatContainer', 'chatEditContainer', 'chatEdit'):
        node = await node.get_child_by_name(name)
        if not await node.is_visible():
            if name in ('chatEditContainer', 'chatEdit'):
                raise RuntimeError(f'{name} 不可见，请先在游戏里展开聊天输入栏（只显示聊天记录不够）')
            raise RuntimeError(f'{name} 不可见，请先展开聊天框')
    if await node.maybe_read_type_name() != 'ControlFreeChat':
        raise RuntimeError('聊天输入控件类型不符')
    return node


async def _chat_readback(client, edit, text):
    current = await _chat_edit(client)
    address = await current.read_base_address()
    expected_address = await edit.read_base_address()
    actual = await read_control_text(current)
    # Fill only. Never change the user's selected channel or whisper target.
    matched = address == expected_address and actual == text
    logger.debug('聊天填入回读：{}，地址={}，原地址={}，期望={!r}，实际={!r}，匹配={}',
                 client.title, address, expected_address, text, actual, matched)
    return matched


async def _chat_ready(client, allow_busy=False):
    if not client.is_running():
        raise RuntimeError('客户端已离线')
    if not allow_busy and any(getattr(client, attr, False) for attr in (
            'questing_status', 'combat_status', 'sigil_status', 'is_fishing',
            'refilling_potions', 'auto_pet_status', 'feeding_pet_status', 'dance_hook_status')):
        raise RuntimeError('客户端有自动任务，请先暂停')
    if await client.is_loading() or await client.is_in_dialog():
        raise RuntimeError('客户端正在加载或 NPC 对话')
    if not allow_busy and await client.in_battle():
        raise RuntimeError('客户端正在战斗；可勾选允许战斗中聊天')
    zone = await client.zone_name()
    if not zone:
        raise RuntimeError('当前区域读取为空，请等待场景加载完成')
    try:
        gid = await client.game_client.player_gid()
    except Exception:
        gid = None
    if not isinstance(gid, int) or gid <= 0:
        try:
            gid = await client.client_object.global_id_full()
        except Exception:
            gid = None
    if not isinstance(gid, int) or gid <= 0:
        # GID identifies receipts, not the target window or editable chat UI.
        gid = None
    return zone, gid


@asynccontextmanager
async def _manual_chat_owner(client, allow_busy):
    ownership = get_client_automation_ownership(client)
    if ownership.locked and not allow_busy:
        raise RuntimeError(f'客户端正在被 {ownership.owner_label} 使用')
    claim = automation_owner(client, 'manual-nearby-chat')
    try:
        async with asyncio.timeout(3):
            await claim.__aenter__()
    except TimeoutError:
        raise RuntimeError('当前点击操作超过 3 秒未结束；本次未发送，请稍后再点') from None
    try:
        yield
    finally:
        await claim.__aexit__(None, None, None)


async def _type_chat(client, text):
    # WM_CHAR goes to this client's HWND only, never the foreground keyboard.
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                     ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_uint,
                     ctypes.POINTER(ctypes.c_size_t)]
    send.restype = ctypes.c_ssize_t
    for char in text:
        if not client.is_running():
            raise RuntimeError('输入期间客户端离线')
        response = ctypes.c_size_t()
        if not send(client.window_handle, 0x102, ord(char), 0, 2, 100,
                    ctypes.byref(response)):
            raise RuntimeError('目标窗口未及时处理字符；未发送')
        await asyncio.sleep(.01)


def parse_chat_log(text):
    """Read player lines from chatLog markup, excluding drops/system notices."""
    messages = []
    for raw in (text or '').splitlines():
        icon = re.search(r'<image;Art/((?:Art_Chat|chat_balloon|Art_Word_Balloon)[^.;>]*)', raw, re.I)
        if not icon or icon[1].casefold().startswith('art_chat_system'):
            continue
        # Preserve image-only emotes instead of dropping their entire message.
        raw_text = re.sub(r'<image;Emoticons/([^.;>]+)\.dds[^>]*>',
                          lambda match: ':' + match[1] + ':', raw, flags=re.I)
        clean = ' '.join(html.unescape(re.sub(r'<[^>]*>', '', raw_text)).replace('\x00', ' ').split())
        sender = re.match(r'^\[([^\]]+)\]\s+(.+)$', clean)
        if not sender:
            continue
        gid = re.search(r'<link;GID:(\d+)[,;]', raw, re.I)
        channel = {'art_chat_say': '附近', 'art_chat_group': '队伍'}.get(
            icon[1].casefold(), icon[1])
        messages.append((channel, int(gid[1]) if gid else 0, sender[1], sender[2]))
    return messages


class ChatLogDelta:
    """Bounded snapshot differencing; no replay on startup or transient empties."""
    def __init__(self):
        self.previous = None
        self.seen = deque(maxlen=2048)

    def update(self, texts):
        parts = [parse_chat_log(text) for text in sorted(texts)]
        main = max(parts, key=len, default=[])
        # A floating group window can mirror the same rows in the main log.
        current = list(main)
        for part in parts:
            if part is main:
                continue
            remaining = Counter(current)
            for message in part:
                if remaining[message]:
                    remaining[message] -= 1
                else:
                    current.append(message)
        if self.previous is None:
            new = []
        elif not current:
            return []
        else:
            new = None
            for start in range(len(self.previous)):
                tail = self.previous[start:]
                if current[:len(tail)] == tail:
                    new = current[len(tail):]
                    break
            if new is None:
                known = set(self.seen)
                new = [message for message in current if message not in known]
        self.previous = current
        known = set(self.seen)
        for message in current:
            if message not in known:
                self.seen.append(message)
                known.add(message)
        # A large view replacement is not a plausible single polling burst.
        return new if len(new) <= 100 else []


class ChatTranslationMonitor:
    def __init__(self, publish):
        self.publish = publish
        self.enabled = False
        self.auto_reply = False
        self.selected_title = None  # None means all injected clients.
        self.tasks = {}  # window handle -> (client, task)
        self.log_tasks = {}
        self.retry_after = {}
        self.recent_hello = {}  # (our GID, recipient GID) -> send timestamp
        self.manual_task = None
        self.observed = deque(maxlen=512)
        self.observation_counter = 0
        self.translation_options = {}
        self.translation_api = None
        self.translation_queue = asyncio.Queue(maxsize=32)
        self.translation_task = None
        self.translation_generation = 0
        self.message_counter = 0
        self.translation_cache = {}
        self.translation_clients = {}
        self.translation_recent = deque(maxlen=128)
        self.translation_enqueued = deque(maxlen=128)
        # Keep provenance, not a text-only blacklist: other players can send
        # identical English and must still receive a translation.
        self.outgoing_translations = deque(maxlen=128)

    def _translation_status(self, text):
        self.publish({'kind': 'translation_status', 'status': text})

    def _reset_translation(self):
        self.translation_generation += 1
        self.translation_cache.clear()
        self.translation_recent.clear()
        self.translation_enqueued.clear()
        while not self.translation_queue.empty():
            self.translation_queue.get_nowait()
        # An in-flight HTTP request is bounded by timeouts. Keep its worker
        # until it finishes, discarding its stale result; never start overlapping
        # workers just because settings changed or the window closed.

    def _publish_message(self, event):
        self.message_counter += 1
        event = {**event, 'message_id': self.message_counter}
        self.publish(event)
        if (event.get('source') == 'chat_log' and event.get('sender_name') == '你'
                and not event.get('sender_gid')):
            return  # Verified local echo stays original-only, however it was sent.
        if not self.enabled or self.translation_api is None or self._is_translated_outgoing_echo(event):
            return
        received_at = time.monotonic()
        try:
            self.translation_queue.put_nowait((self.translation_generation, self.translation_api, event, received_at))
        except asyncio.QueueFull:
            self._translation_status('翻译队列已满，部分消息仅保留原文。')
            return
        self.translation_enqueued.append((self.translation_generation, received_at, event))
        if self.translation_task is None or self.translation_task.done():
            self.translation_task = asyncio.create_task(self._translate_pending())

    def _is_translated_outgoing_echo(self, event):
        gid = event.get('sender_gid')
        for client, own_gid, message in self.outgoing_translations:
            if event.get('message') != message:
                continue
            if isinstance(gid, int) and gid > 0:
                if own_gid is not None and gid == own_gid:
                    return True  # Includes copies seen by other client windows.
            elif (event.get('source') == 'chat_log' and event.get('sender_name') == '你'
                  and event.get('handle') == client.window_handle
                  and self.translation_clients.get(client.window_handle) is client):
                return True  # Verified local self label when the log omits GID.
        return False

    async def _translation_own_gid(self, client):
        try:
            async with asyncio.timeout(.3):
                gid = await client.game_client.player_gid()
            return gid if isinstance(gid, int) and gid > 0 else None
        except Exception:
            return None

    async def _translation_sender(self, event, client):
        gid = event.get('sender_gid')
        if isinstance(gid, int) and gid > 0:
            return ('gid', gid)
        # The observed Chinese game log uses [你] for the local echo, without
        # a link GID. Resolve only this verified label; don't guess unknown IDs.
        if event.get('source') == 'chat_log' and event.get('sender_name') == '你' and client:
            own_gid = await self._translation_own_gid(client)
            if own_gid:
                return ('gid', own_gid)
        return None  # A display name alone is not a unique Wizard101 player ID.

    async def _translation_same_area(self, first, second):
        try:
            async with asyncio.timeout(.5):
                return await clients_share_live_area(first, second)
        except Exception:
            return False

    async def _translation_is_duplicate(self, event, received_at, generation):
        client = self.translation_clients.get(event.get('handle'))
        if self.selected_title is not None or client is None:
            return False
        sender = await self._translation_sender(event, client)
        key = (event.get('source'), event.get('channel'), sender, event['message'])
        if sender is None:
            return False
        owners = [peer for peer in tuple(self.translation_clients.values())
                  if await self._translation_own_gid(peer) == sender[1]]
        if len(owners) == 1:
            source_client = owners[0]
            if source_client is client:
                return False  # Always translate our own source window's echo.
            if await self._translation_same_area(source_client, client):
                # Another listener may poll the shared message first. Allow a
                # short wait for the source echo to enter the queue, without
                # delaying publication or game listening. If no source echo is
                # readable/queued, translate this copy so it isn't lost.
                deadline = time.monotonic() + .6
                while generation == self.translation_generation and self.enabled:
                    for queued_generation, queued_at, queued in tuple(self.translation_enqueued):
                        if (queued_generation == generation
                                and queued.get('handle') == source_client.window_handle
                                and queued.get('source') == event.get('source')
                                and queued.get('channel') == event.get('channel')
                                and queued['message'] == event['message']
                                and abs(queued_at - received_at) <= 3
                                and await self._translation_sender(queued, source_client) == sender):
                            return True
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(.05)
            return False  # Different/unknown areas each retain a translation.
        for previous in tuple(self.translation_recent):
            if (previous['generation'] != generation or previous['key'] != key
                    or abs(received_at - previous['time']) > 3
                    or client.window_handle in previous['handles']):
                continue
            # Zone-name/Zone-ID equality alone cannot establish a shared instance.
            shared = await self._translation_same_area(previous['client'], client)
            if shared:
                previous['handles'].add(client.window_handle)
                return True
        if generation == self.translation_generation:
            self.translation_recent.append({'generation': generation, 'key': key,
                'time': received_at, 'client': client, 'handles': {client.window_handle}})
        return False

    async def _translate_pending(self):
        while not self.translation_queue.empty():
            generation, api, event, received_at = self.translation_queue.get_nowait()
            if generation != self.translation_generation or not self.enabled:
                continue
            message = event['message']
            try:
                if self._is_translated_outgoing_echo(event):
                    continue
                if await self._translation_is_duplicate(event, received_at, generation):
                    continue  # Keep this client's original, not another translation.
                if generation != self.translation_generation or not self.enabled:
                    continue
                cache_key = (event.get('handle', event.get('title')), message)
                result = self.translation_cache.get(cache_key)
                if result is None:
                    result = await asyncio.to_thread(api.translate, message)
                if generation != self.translation_generation or not self.enabled:
                    continue
                self.translation_cache[cache_key] = result
                if len(self.translation_cache) > 256:
                    self.translation_cache.pop(next(iter(self.translation_cache)))
                self.publish({**event, 'kind': 'translation', 'translation': result})
                self._translation_status('翻译：已启用')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if generation == self.translation_generation and self.enabled:
                    # Never log raw transport errors or response bodies/keys.
                    safe = str(exc) if isinstance(exc, TranslationError) else '翻译失败；未自动重试。'
                    logger.warning('聊天翻译接口：{}', safe)
                    self._translation_status('翻译暂不可用，详情见日志；已保留原文。')

    def request_manual_send(self, clients, title, text, blocked=False, allow_busy=False):
        self.auto_reply = False
        if self.manual_task and not self.manual_task.done():
            return  # One transaction at a time; no delayed queued sends.
        self.manual_task = asyncio.create_task(self._manual_send(
            list(clients), title, text, blocked, allow_busy=allow_busy))

    async def _manual_send(self, clients, title, text, blocked=False, verify_seconds=10, allow_busy=False):
        result = {'invoked': False, 'local_echo': False, 'peer_receipts': [],
                  'listener_capture': [], 'message': text, 'filled': False}
        def event(status, done=False):
            payload = {'kind': 'manual_send', 'title': title, 'status': status,
                       'done': done, **result}
            self.publish(payload)
            logger.info('聊天手动填入：{}', payload)
        event('开始检查；尚未发送')
        step = '发送条件检查'
        try:
            if not self.enabled:
                raise RuntimeError('聊天监听未开启')
            if blocked:
                raise RuntimeError(blocked if isinstance(blocked, str) else '脚本/自由镜头正在使用客户端')
            if not isinstance(text, str) or not text.strip() or len(text) > 80 or any(
                    not char.isprintable() or ord(char) > 0xFFFF
                    or 0xD800 <= ord(char) <= 0xDFFF for char in text):
                raise RuntimeError('只接受 1–80 个可打印中英文字符；暂不支持换行或 emoji')
            selected = [c for c in clients if c.title == title]
            if len(selected) != 1:
                raise RuntimeError('发送客户端不存在或名称不唯一')
            client = selected[0]
            if any('\u3400' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff' for char in text):
                step = '中文翻译为英文'
                api = self.translation_api
                generation = self.translation_generation
                if api is None:
                    raise TranslationError('请先配置翻译接口并启用双语翻译；未输入中文原文。')
                await _chat_ready(client, allow_busy=allow_busy)
                event('正在将中文翻译为英文；尚未向游戏输入')
                try:
                    translated = await asyncio.to_thread(api.translate, text, target_language='en')
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    safe = str(exc) if isinstance(exc, TranslationError) else '翻译失败；未输入中文原文。'
                    raise TranslationError(safe) from None
                if not self.enabled or generation != self.translation_generation or self.translation_api is not api:
                    raise TranslationError('翻译期间配置已变化，本次未输入、未发送。')
                if not isinstance(translated, str) or not translated.strip() or len(translated) > 80 or any(
                        not char.isprintable() or ord(char) > 0xFFFF or 0xD800 <= ord(char) <= 0xDFFF
                        or '\u3400' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff' for char in translated):
                    raise TranslationError('英文译文须为 1–80 个可打印字符且不含中文；未输入，请缩短原文后重试。')
                result['source_message'] = text
                result['message'] = text = translated
                result['translation'] = translated
                event('英文翻译完成：' + text)
            if allow_busy and get_client_automation_ownership(client).locked:
                event('允许忙碌中聊天；等待当前点击完成（最多 3 秒）')
            async with _manual_chat_owner(client, allow_busy):
                async with asyncio.timeout(8):
                    step = '角色状态检查'
                    stage = await _chat_ready(client, allow_busy=allow_busy)
                    step = '定位聊天输入控件'
                    edit = await _chat_edit(client)
                    step = '读取聊天输入控件的草稿'
                    if await read_control_text(edit):
                        raise RuntimeError('聊天框已有草稿，未覆盖、未发送')
                    event(f'角色/UI/空草稿检查通过；HWND={client.window_handle}，GID={stage[1]}，区域={stage[0]}')
                    if stage[1] is None:
                        event('角色 GID 暂不可读；仅填入正文，不确认消息归属或接收回执')
                    step = '点击所选窗口的聊天输入控件'
                    async with client.mouse_handler:
                        await client.mouse_handler.click_window(edit)
                    step = '点击后复查聊天草稿'
                    if await read_control_text(edit):
                        raise RuntimeError('点击后草稿发生变化，未发送')
                    if await _chat_ready(client, allow_busy=allow_busy) != stage or not self.enabled:
                        raise RuntimeError('点击后客户端状态发生变化，未输入')
                    current = await _chat_edit(client)
                    if await current.read_base_address() != await edit.read_base_address():
                        raise RuntimeError('点击后聊天输入控件发生变化，未输入')
                    step = '向所选窗口输入文本'
                    await _type_chat(client, text)
                    await asyncio.sleep(.1)
                    step = '回读聊天输入控件和全文'
                    # Read-only wait for UI processing; never retype/retry input.
                    readback_deadline = time.monotonic() + 1
                    while not await _chat_readback(client, edit, text):
                        if time.monotonic() >= readback_deadline:
                            raise RuntimeError('输入回读不一致，未按发送；请检查游戏聊天草稿')
                        await asyncio.sleep(.05)
                    step = '发送前复查角色状态'
                    if await _chat_ready(client, allow_busy=allow_busy) != stage or not self.enabled:
                        raise RuntimeError('客户端状态发生变化，未发送')
                    if not await _chat_readback(client, edit, text):
                        raise RuntimeError('填入后输入文本发生变化；未自动发送，请检查游戏草稿')
                    result['filled'] = True
                    if 'source_message' in result:
                        # The user may confirm this English draft in the game.
                        self.outgoing_translations.append((client, stage[1], text))
                    event('英文已填入游戏；未发送，请在游戏里确认频道和收件人后发送。', True)
        except asyncio.CancelledError:
            event('填入已取消；未自动发送，请检查游戏草稿', True)
            raise
        except Exception as exc:
            result['error'] = str(exc)
            logger.opt(exception=True).warning('聊天手动填入失败：{}，步骤={}', title, step)
            event(f'测试停止（{step}）：{exc}；不自动重试', True)

    def configure(self, enabled, selected_title=None, auto_reply=False, translation_options=None):
        options = {key: value for key, value in (translation_options or {}).items() if key in (
            'chat_translation_enabled', 'chat_translation_api_url',
            'chat_translation_model', 'chat_translation_api_key_protected')}
        changed = (self.enabled != bool(enabled) or self.selected_title != (selected_title or None)
                   or self.translation_options != options)
        self.enabled = bool(enabled)
        self.selected_title = selected_title or None
        self.auto_reply = bool(auto_reply)
        if changed:
            self._reset_translation()
            self.translation_options = options
            self.translation_api = None
            if self.enabled and options.get('chat_translation_enabled') is True:
                try:
                    self.translation_api = ChatTranslationAPI(options)
                except TranslationError as exc:
                    logger.warning('聊天翻译接口配置：{}', exc)
                    self._translation_status('翻译配置不可用，请打开接口设置。')
            if self.translation_api is not None:
                self._translation_status('翻译：已启用')
            elif not options.get('chat_translation_enabled'):
                self._translation_status('翻译：未启用')

    async def sync(self, clients):
        clients = list(clients)
        self.translation_clients = {client.window_handle: client for client in clients
                                    if self.enabled and client.is_running()}
        wanted = {
            client.window_handle: client
            for client in clients
            if self.enabled
            and client.is_running()
            and (self.selected_title is None or client.title == self.selected_title)
        }
        stopped = []
        for tasks in (self.tasks, self.log_tasks):
            for handle, (client, task) in list(tasks.items()):
                if wanted.get(handle) is not client or task.done():
                    tasks.pop(handle)
                    if not task.done():
                        task.cancel()
                    stopped.append(task)
        if stopped:
            await asyncio.gather(*stopped, return_exceptions=True)

        now = time.monotonic()
        for handle, client in wanted.items():
            if handle not in self.log_tasks:
                self.log_tasks[handle] = (client, asyncio.create_task(self._listen_log(client)))
            if handle not in self.tasks and now >= self.retry_after.get(handle, 0):
                task = asyncio.create_task(self._listen(client))
                self.tasks[handle] = (client, task)

    async def stop(self):
        self.enabled = False
        self._reset_translation()
        self.translation_api = None
        self.translation_clients.clear()
        if self.manual_task and not self.manual_task.done():
            self.manual_task.cancel()
            await asyncio.gather(self.manual_task, return_exceptions=True)
        tasks = [task for group in (self.tasks, self.log_tasks) for _, task in group.values()]
        self.tasks.clear()
        self.log_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.recent_hello.clear()

    async def _listen_log(self, client):
        delta = ChatLogDelta()
        last_status = None

        def status(value):
            nonlocal last_status
            if value != last_status:
                last_status = value
                logger.debug('聊天记录监听状态：{}，{}', client.title, value)
                self.publish({'kind': 'status', 'source': 'chat_log',
                              'handle': client.window_handle, 'title': client.title,
                              'status': value})

        try:
            while client.is_running():
                try:
                    root = getattr(client, 'root_window', None)
                    nodes = await root.get_windows_with_name('chatLog') if root else []
                    if not nodes:
                        status('聊天记录：等待 chatLog 控件')
                    else:
                        # Read all nodes, including the floating group chat log.
                        # No tab clicks or writes to the game's chat UI.
                        texts = [await read_control_text(node) or '' for node in nodes]
                        for channel, gid, sender, message in delta.update(texts):
                            self.observation_counter += 1
                            self.observed.append((self.observation_counter, client.title,
                                                  channel, gid, message))
                            self._publish_message({'kind': 'message', 'source': 'chat_log',
                                          'handle': client.window_handle, 'title': client.title,
                                          'channel': channel, 'sender_gid': gid,
                                          'sender_name': sender, 'message': message})
                        status('聊天记录：监听中（含附近 / 队伍）')
                    await asyncio.sleep(.5)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    status(f'聊天记录读取失败：{exc}')
                    await asyncio.sleep(2)
        finally:
            status('聊天记录：已停止')

    async def _listen(self, client):
        handle = client.window_handle
        handler = client.hook_handler
        owned_receive = False
        owned_send = False
        last_counter = None
        send_retry_after = 0
        failed = False

        def status(value):
            logger.debug('聊天收发 Hook 状态：{}，{}', client.title, value)
            self.publish({"kind": "status", "handle": handle,
                          "title": client.title, "status": value})

        try:
            if "recv_counter" not in handler._base_addrs:
                await handler.activate_chat_hook(wait_for_ready=False)
                owned_receive = True
            status("接收 Hook 已启动")
            logger.info("聊天翻译：{} Chat Hook 已启动。", client.title)

            while client.is_running():
                if (self.auto_reply and "send_trigger" not in handler._base_addrs
                        and time.monotonic() >= send_retry_after):
                    try:
                        await handler.activate_chat_send_hook()
                        owned_send = True
                        status("接收 / 发送 Hook 已启动")
                    except Exception as exc:
                        status(f"接收正常；发送 Hook 失败：{exc}")
                        logger.warning("聊天翻译：{} Chat Send Hook 启动失败：{}", client.title, exc)
                        send_retry_after = time.monotonic() + 10

                try:
                    sender_gid, message, counter = await client.chat_owner.wait_for_message(1.0)
                except asyncio.TimeoutError:
                    continue
                if counter == last_counter:
                    continue
                last_counter = counter
                self._publish_message({"kind": "message", "handle": handle,
                              "title": client.title, "sender_gid": sender_gid,
                              "message": message})
                logger.info("聊天翻译：{} 收到来自 GID {} 的消息。", client.title, sender_gid)

                if (not self.auto_reply or not sender_gid
                        or "send_trigger" not in handler._base_addrs):
                    continue
                try:
                    own_gid = await client.game_client.player_gid()
                except Exception as exc:
                    logger.warning("聊天翻译：{} 无法读取自身 GID，跳过自动回复：{}", client.title, exc)
                    status("无法读取自身 GID；自动回复已跳过")
                    continue
                if not own_gid or sender_gid == own_gid:
                    continue
                now = time.monotonic()
                self.recent_hello = {
                    pair: sent_at for pair, sent_at in self.recent_hello.items()
                    if now - sent_at < 15
                }
                if message.strip().casefold() == "hello" and (
                    (own_gid, sender_gid) in self.recent_hello
                    or (sender_gid, own_gid) in self.recent_hello
                ):
                    continue
                try:
                    self.recent_hello[(own_gid, sender_gid)] = now
                    send_task = asyncio.create_task(
                        client.chat_owner.send_msg("Hello", target_gid=sender_gid)
                    )
                    try:
                        await asyncio.shield(send_task)
                    except asyncio.CancelledError:
                        # Let the game's trigger finish before removing ChatSendHook.
                        await send_task
                        raise
                    logger.info("聊天翻译：{} 已向 GID {} 发送测试回复 Hello。", client.title, sender_gid)
                    self.publish({"kind": "reply", "handle": handle,
                                  "title": client.title, "sender_gid": sender_gid})
                except Exception as exc:
                    self.recent_hello.pop((own_gid, sender_gid), None)
                    status(f"Hello 回复失败：{exc}")
                    logger.warning("聊天翻译：{} Hello 回复失败：{}", client.title, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed = True
            self.retry_after[handle] = time.monotonic() + 5
            status(f"Chat Hook 失败：{exc}")
            logger.warning("聊天翻译：{} Chat Hook 失败：{}", client.title, exc)
        finally:
            if client.is_running():
                if owned_send:
                    try:
                        await handler.deactivate_chat_send_hook()
                    except Exception as exc:
                        logger.warning("聊天翻译：{} 清理发送 Hook 失败：{}", client.title, exc)
                if owned_receive:
                    try:
                        await handler.deactivate_chat_hook()
                    except Exception as exc:
                        logger.warning("聊天翻译：{} 清理接收 Hook 失败：{}", client.title, exc)
            if not failed:
                status("已停止")
