"""Monitor rendered player chat alongside the directed-chat test hook."""

import asyncio
from collections import Counter, deque
import html
import re
import time

from loguru import logger
from src.window_text import read_control_text


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
