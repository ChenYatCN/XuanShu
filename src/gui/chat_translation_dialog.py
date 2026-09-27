"""Independent window for first-stage chat receive / Hello testing."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from src.gui.commands import GUICommand, GUICommandType


class ChatTranslationDialog(QDialog):
    def __init__(self, send_queue):
        super().__init__(None)
        self.send_queue = send_queue
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowTitle("玄枢 · 聊天翻译（收发测试）")
        self.resize(600, 420)

        layout = QVBoxLayout(self)
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
