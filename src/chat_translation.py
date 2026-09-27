"""First-stage directed-chat monitor for the translation window."""

import asyncio
import time

from loguru import logger


class ChatTranslationMonitor:
    def __init__(self, publish):
        self.publish = publish
        self.enabled = False
        self.auto_reply = False
        self.selected_title = None  # None means all injected clients.
        self.tasks = {}  # window handle -> (client, task)
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
        for handle, (client, task) in list(self.tasks.items()):
            if wanted.get(handle) is not client or task.done():
                self.tasks.pop(handle)
                if not task.done():
                    task.cancel()
                stopped.append(task)
        if stopped:
            await asyncio.gather(*stopped, return_exceptions=True)

        now = time.monotonic()
        for handle, client in wanted.items():
            if handle not in self.tasks and now >= self.retry_after.get(handle, 0):
                task = asyncio.create_task(self._listen(client))
                self.tasks[handle] = (client, task)

    async def stop(self):
        self.enabled = False
        tasks = [task for _, task in self.tasks.values()]
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.recent_hello.clear()

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
