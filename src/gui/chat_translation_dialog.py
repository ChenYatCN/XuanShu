"""Independent window for first-stage chat receive / Hello testing."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget, QLineEdit,
)

from src.gui.commands import GUICommand, GUICommandType
from src.gui.helpers import configure_titlebar_button
from src.gui.ibao_dialog import RoundedIbaoDialog


class ChatTranslationDialog(RoundedIbaoDialog):
    def __init__(self, send_queue, ctx):
        super().__init__(None)
        self.send_queue = send_queue
        self.ctx = ctx
        self.setObjectName('ChatTranslationDialog')
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setSizeGripEnabled(True)
        self.setWindowTitle("玄枢 · 聊天翻译（收发测试）")
        self.resize(600, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(8)
        titlebar = QWidget()
        title_row = QHBoxLayout(titlebar)
        titlebar.setFixedHeight(32)
        title_row.setContentsMargins(4, 0, 4, 0)
        title_row.setSpacing(0)
        title_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title = QLabel('玄枢 · 聊天翻译（收发测试）')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.minimize_button = QPushButton()
        self.minimize_button.setToolTip('最小化聊天翻译')
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button = QPushButton()
        self.close_button.setToolTip('关闭聊天翻译')
        self.close_button.clicked.connect(self.close)
        title_row.addSpacing(64)
        title_row.addWidget(title, 1)
        title_row.addWidget(self.minimize_button)
        title_row.addWidget(self.close_button)
        def drag(event):
            if event.button() == Qt.MouseButton.LeftButton and self.windowHandle():
                self.windowHandle().startSystemMove()
        titlebar.mousePressEvent = drag
        title.mousePressEvent = drag
        layout.addWidget(titlebar)

        row = QHBoxLayout()
        row.addWidget(QLabel("监听客户端"))
        self.clients = QComboBox()
        self.clients.addItem("全部已注入客户端", None)
        self.clients.currentIndexChanged.connect(self._configure)
        row.addWidget(self.clients, 1)
        self.auto_reply = QCheckBox("自动回复已关闭（手动测试阶段）")
        self.auto_reply.setChecked(False)
        self.auto_reply.setEnabled(False)
        row.addWidget(self.auto_reply)
        layout.addLayout(row)

        send_row = QHBoxLayout()
        send_row.addWidget(QLabel('附近发送窗口'))
        self.sender = QComboBox()
        self.sender.addItem('p1（未就绪）', 'p1')
        self.sender.currentIndexChanged.connect(self._update_send_button)
        send_row.addWidget(self.sender)
        self.send_text = QLineEdit('hello')
        self.send_text.setMaxLength(80)
        send_row.addWidget(self.send_text, 1)
        self.send_button = QPushButton('发送一次（附近）')
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._send_once)
        send_row.addWidget(self.send_button)
        layout.addLayout(send_row)
        self.allow_busy_chat = QCheckBox('允许自动任务 / 战斗中聊天')
        self.allow_busy_chat.setToolTip('放行自动任务、脚本和战斗；仍检查 Loading、NPC 对话、空草稿及输入回读。')
        layout.addWidget(self.allow_busy_chat)
        self.send_status = QLabel('尚未发送；仅支持英文测试，不翻译、不自动回复。')
        self.send_status.setWordWrap(True)
        layout.addWidget(self.send_status)
        self._sending = False
        self._available_titles = []

        self.status = QLabel("聊天监听：未启动")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setPlaceholderText('监听开启后的新消息；首次读取不补入历史。\n'
                                        '附近 / 队伍读取游戏 chatLog，私聊保留 Hook 收发测试。')
        self.messages.setMaximumBlockCount(500)
        layout.addWidget(self.messages, 1)
        clear = QPushButton("清空消息")
        clear.clicked.connect(self.messages.clear)
        layout.addWidget(clear, alignment=Qt.AlignmentFlag.AlignRight)
        self._statuses = {}
        self._log_statuses = {}
        self._title = title
        self._clear_button = clear
        self.retheme()

    def retheme(self):
        ctx = self.ctx
        self.setStyleSheet(
            f'QDialog#ChatTranslationDialog {{ background: {ctx.bg_color}; '
            f'border: 1px solid {ctx.stroke_color}; border-radius: 8px; }}'
            f'QLabel, QCheckBox {{ color: {ctx.text_color}; }}'
            f'QLineEdit, QComboBox {{ background: {ctx.alt_bg}; color: {ctx.text_color}; }}'
        )
        self._title.setStyleSheet(f'color: {ctx.text_color}; font-weight: bold;')
        self.status.setStyleSheet(f'color: {ctx.text_color};')
        self.messages.setStyleSheet(
            f'background: {ctx.alt_bg}; color: {ctx.text_color}; border: none; padding: 8px;'
        )
        self._clear_button.setStyleSheet(ctx.icon_btn_style)
        configure_titlebar_button(self.minimize_button, ctx.titlebar_svg_icon, ctx.stroke_color)
        configure_titlebar_button(self.close_button, ctx.titlebar_svg_icon, ctx.stroke_color, close=True)

    def open_or_raise(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self._configure()

    def set_available_clients(self, titles):
        self._available_titles = list(titles)
        sender = self.sender.currentData() or 'p1'
        self.sender.blockSignals(True)
        self.sender.clear()
        for title in dict.fromkeys([sender, 'p1', *titles]):
            self.sender.addItem(title if title in titles else f'{title}（未就绪）', title)
        self.sender.setCurrentIndex(self.sender.findData(sender))
        self.sender.blockSignals(False)
        self._update_send_button()
        selected = self.clients.currentData()
        self.clients.blockSignals(True)
        self.clients.clear()
        self.clients.addItem("全部已注入客户端", None)
        for title in titles:
            self.clients.addItem(title, title)
        index = self.clients.findData(selected)
        self.clients.setCurrentIndex(index if index >= 0 else 0)
        self.clients.blockSignals(False)
        if selected is not None and index < 0:
            # A vanished selection must not silently broaden Hello replies.
            self.auto_reply.blockSignals(True)
            self.auto_reply.setChecked(False)
            self.auto_reply.blockSignals(False)
        self._statuses = {title: value for title, value in self._statuses.items()
                          if title in titles}
        self._log_statuses = {title: value for title, value in self._log_statuses.items()
                              if title in titles}
        self._update_status()
        if self.isVisible():
            self._configure()

    def handle_event(self, event):
        title = event.get("title", "?")
        kind = event.get("kind")
        if kind == 'manual_send':
            summary = (f"[{title}] {event.get('status', '')}\n"
                       f"发送调用={event.get('invoked', False)}；"
                       f"本地附近回显={event.get('local_echo', False)}；"
                       f"其他窗口接收={event.get('peer_receipts', [])}；"
                       f"监听器捕获={event.get('listener_capture', [])}")
            self.send_status.setText(summary)
            self.messages.appendPlainText(summary)
            if event.get('done'):
                self._sending = False
                self.sender.setEnabled(True)
                self.send_text.setEnabled(True)
                self._update_send_button()
        elif kind == "message":
            sender = event.get("sender_gid", 0)
            message = event.get("message", "")
            if event.get('source') == 'chat_log':
                self.messages.appendPlainText(
                    f"[{title}][{event.get('channel', '聊天')}] "
                    f"[{event.get('sender_name', '?')}]: {message}"
                )
            else:
                self.messages.appendPlainText(f"[{title}] GID {sender}: {message}")
        elif kind == "reply":
            self.messages.appendPlainText(
                f"[{title}] → GID {event.get('sender_gid', 0)}: Hello"
            )
        elif kind == "status":
            if event.get('source') == 'chat_log':
                self._log_statuses[title] = event.get('status', '')
            else:
                self._statuses[title] = event.get("status", "")
            self._update_status()

    def _update_status(self):
        if not self.isVisible():
            self.status.setText("聊天监听：未启动")
            return
        titles = [self.clients.currentData()] if self.clients.currentData() else [
            self.clients.itemData(i) for i in range(1, self.clients.count())
        ]
        parts = [f"{title} {self._log_statuses.get(title, '聊天记录：等待启动')} / "
                 f"{self._statuses.get(title, '私聊 Hook：等待启动')}" for title in titles]
        self.status.setText("聊天监听：" + ("；".join(parts) if parts else "无已注入客户端"))

    def _configure(self, *_):
        if not self.isVisible():
            return
        self.send_queue.put(GUICommand(GUICommandType.ConfigureChatTranslation, {
            "enabled": True,
            "selected_title": self.clients.currentData(),
            "auto_reply": False,
        }))
        self._update_status()

    def _send_once(self):
        if self._sending:
            return
        title = self.sender.currentData()
        if title not in self._available_titles:
            self.send_status.setText(f'{title} 未就绪；未发送，不切换其他窗口。')
            return
        self._sending = True
        self.send_button.setEnabled(False)
        self.sender.setEnabled(False)
        self.send_text.setEnabled(False)
        self.send_status.setText('请求已提交；尚未证明游戏已发送。')
        self.send_queue.put(GUICommand(GUICommandType.SendNearbyChatTest, {
            'title': title, 'text': self.send_text.text(),
            'allow_busy': self.allow_busy_chat.isChecked(),
        }))

    def _update_send_button(self, *_):
        if hasattr(self, 'send_button'):
            self.send_button.setEnabled(not self._sending
                                       and self.sender.currentData() in self._available_titles)

    def closeEvent(self, event):
        self.send_queue.put(GUICommand(GUICommandType.ConfigureChatTranslation, {
            "enabled": False,
        }))
        self.auto_reply.blockSignals(True)
        self.auto_reply.setChecked(False)
        self.auto_reply.blockSignals(False)
        self._statuses.clear()
        self._log_statuses = {}
        self.status.setText("聊天监听：未启动")
        super().closeEvent(event)
