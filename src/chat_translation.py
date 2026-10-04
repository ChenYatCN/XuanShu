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
from src.automation_ownership import automation_owner, get_client_automation_ownership
from wizwalker import Keycode


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
            raise RuntimeError(f'{name} 不可见，请先展开聊天框')
    if await node.maybe_read_type_name() != 'ControlFreeChat':
        raise RuntimeError('聊天输入控件类型不符')
    return node


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

    def request_manual_send(self, clients, title, text, blocked=False, allow_busy=False):
        self.auto_reply = False
        if self.manual_task and not self.manual_task.done():
            return  # One transaction at a time; no delayed queued sends.
        self.manual_task = asyncio.create_task(self._manual_send(
            list(clients), title, text, blocked, allow_busy=allow_busy))

    async def _manual_send(self, clients, title, text, blocked=False, verify_seconds=10, allow_busy=False):
        result = {'invoked': False, 'local_echo': False, 'peer_receipts': [],
                  'listener_capture': [], 'message': text}
        def event(status, done=False):
            payload = {'kind': 'manual_send', 'title': title, 'status': status,
                       'done': done, **result}
            self.publish(payload)
            logger.info('附近手动发送测试：{}', payload)
        event('开始检查；尚未发送')
        try:
            if not self.enabled:
                raise RuntimeError('聊天监听未开启')
            if blocked:
                raise RuntimeError(blocked if isinstance(blocked, str) else '脚本/自由镜头正在使用客户端')
            if not isinstance(text, str) or not text.strip() or len(text) > 80 or any(
                    ord(char) < 32 or ord(char) > 126 for char in text):
                raise RuntimeError('临时测试只接受 1–80 个可打印英文字符')
            selected = [c for c in clients if c.title == title]
            if len(selected) != 1:
                raise RuntimeError('发送客户端不存在或名称不唯一')
            client = selected[0]
            if allow_busy and get_client_automation_ownership(client).locked:
                event('允许忙碌中聊天；等待当前点击完成（最多 3 秒）')
            async with _manual_chat_owner(client, allow_busy):
                async with asyncio.timeout(8):
                    stage = await _chat_ready(client, allow_busy=allow_busy)
                    edit = await _chat_edit(client)
                    if await read_control_text(edit):
                        raise RuntimeError('聊天框已有草稿，未覆盖、未发送')
                    event(f'角色/UI/空草稿检查通过；HWND={client.window_handle}，GID={stage[1]}，区域={stage[0]}')
                    if stage[1] is None:
                        event('角色 GID 暂不可读；允许本次手动发送，但不确认消息归属或接收回执')
                    baselines = {}
                    for peer in clients:
                        if peer.is_running():
                            try:
                                delta = ChatLogDelta()
                                delta.update(await _chat_texts(peer))
                                baselines[peer.window_handle] = (peer, delta)
                            except Exception as exc:
                                event(f'{peer.title} 接收基线不可用：{exc}')
                    if client.window_handle not in baselines:
                        raise RuntimeError('发送窗口聊天记录不可读取')
                    start_counter = self.observation_counter
                    event(f'新消息基线已建立：{[peer.title for peer, _delta in baselines.values()]}')
                    async with client.mouse_handler:
                        await client.mouse_handler.click_window(edit)
                    if await read_control_text(edit):
                        raise RuntimeError('点击后草稿发生变化，未发送')
                    # /s is Chat_CommandSay from the game's language catalog.
                    wire_text = '/s ' + text
                    await _type_chat(client, wire_text)
                    await asyncio.sleep(.1)
                    current_edit = await _chat_edit(client)
                    if (await current_edit.read_base_address() != await edit.read_base_address()
                            or await read_control_text(current_edit) != wire_text):
                        raise RuntimeError('输入回读不一致，未按发送；请检查游戏聊天草稿')
                    if await _chat_ready(client, allow_busy=allow_busy) != stage or not self.enabled:
                        raise RuntimeError('客户端状态发生变化，未发送')
                    event('所选窗口已输入 /s 文本；输入控件地址与全文回读一致，状态复查通过')
                    await client.send_hotkey([], Keycode.ENTER)
                    result['invoked'] = True
                    event('仅已调用一次 Enter；等待附近频道回显/其他窗口接收')
            deadline = time.monotonic() + verify_seconds
            while time.monotonic() < deadline:
                for handle, (peer, delta) in baselines.items():
                    try:
                        async with asyncio.timeout(.8):
                            rows = delta.update(await _chat_texts(peer))
                    except Exception:
                        continue
                    for channel, gid, name, message in rows:
                        if channel != '附近' or message != text:
                            continue
                        # GID-less self labels are deliberately not assumed proof.
                        if stage[1] is None or gid != stage[1]:
                            continue
                        if handle == client.window_handle:
                            result['local_echo'] = True
                        elif peer.title not in result['peer_receipts']:
                            result['peer_receipts'].append(peer.title)
                result['listener_capture'] = sorted({observed_title
                    for counter, observed_title, channel, gid, message in self.observed
                    if counter > start_counter and channel == '附近'
                    and stage[1] is not None and gid == stage[1] and message == text})
                await asyncio.sleep(.25)
            event('核验结束（未观察到不等于发送失败；不自动重发）', True)
        except asyncio.CancelledError:
            event('测试已取消；不再发送，已调用的 Enter 无法撤回', True)
            raise
        except Exception as exc:
            event(f'测试停止：{exc}；不自动重试', True)

    def configure(self, enabled, selected_title=None, auto_reply=False):
        self.enabled = bool(enabled)
        self.selected_title = selected_title or None
        self.auto_reply = bool(auto_reply)

    async def sync(self, clients):
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
                            self.publish({'kind': 'message', 'source': 'chat_log',
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
                self.publish({"kind": "message", "handle": handle,
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
