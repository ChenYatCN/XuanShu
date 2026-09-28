"""Independent window for first-stage chat receive / Hello testing."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
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
        self.auto_reply = QCheckBox("自动回复 Hello（测试）")
        self.auto_reply.setChecked(False)
        self.auto_reply.toggled.connect(self._configure)
        row.addWidget(self.auto_reply)
        layout.addLayout(row)

        self.status = QLabel("Chat Hook：未启动")
        layout.addWidget(self.status)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMaximumBlockCount(500)
        layout.addWidget(self.messages, 1)
        clear = QPushButton("清空消息")
        clear.clicked.connect(self.messages.clear)
        layout.addWidget(clear, alignment=Qt.AlignmentFlag.AlignRight)
        self._statuses = {}
        self._title = title
        self._clear_button = clear
        self.retheme()

    def retheme(self):
        ctx = self.ctx
        self.setStyleSheet(
            f'QDialog#ChatTranslationDialog {{ background: {ctx.bg_color}; '
            f'border: 1px solid {ctx.stroke_color}; border-radius: 8px; }}'
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
        self._update_status()
        if self.isVisible():
            self._configure()

    def handle_event(self, event):
        title = event.get("title", "?")
        kind = event.get("kind")
        if kind == "message":
            sender = event.get("sender_gid", 0)
            message = event.get("message", "")
            self.messages.appendPlainText(f"[{title}] GID {sender}: {message}")
        elif kind == "reply":
            self.messages.appendPlainText(
                f"[{title}] → GID {event.get('sender_gid', 0)}: Hello"
            )
        elif kind == "status":
            self._statuses[title] = event.get("status", "")
            self._update_status()

    def _update_status(self):
        if not self.isVisible():
            self.status.setText("Chat Hook：未启动")
            return
        titles = [self.clients.currentData()] if self.clients.currentData() else [
            self.clients.itemData(i) for i in range(1, self.clients.count())
        ]
        parts = [f"{title} {self._statuses.get(title, '等待启动')}" for title in titles]
        self.status.setText("Chat Hook：" + ("；".join(parts) if parts else "无已注入客户端"))

    def _configure(self, *_):
        if not self.isVisible():
            return
        self.send_queue.put(GUICommand(GUICommandType.ConfigureChatTranslation, {
            "enabled": True,
            "selected_title": self.clients.currentData(),
            "auto_reply": self.auto_reply.isChecked(),
        }))
        self._update_status()

    def closeEvent(self, event):
        self.send_queue.put(GUICommand(GUICommandType.ConfigureChatTranslation, {
            "enabled": False,
        }))
        self.auto_reply.blockSignals(True)
        self.auto_reply.setChecked(False)
        self.auto_reply.blockSignals(False)
        self._statuses.clear()
        self.status.setText("Chat Hook：未启动")
        super().closeEvent(event)
