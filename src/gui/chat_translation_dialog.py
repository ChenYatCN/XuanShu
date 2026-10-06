"""Independent chat history and nearby-send window."""

from loguru import logger
from collections import deque

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget, QLineEdit, QDialog, QFormLayout,
)

from src.gui.commands import GUICommand, GUICommandType
from src.gui.helpers import configure_titlebar_button, settings_control_svg, add_dialog_titlebar
from src.gui.ibao_dialog import RoundedIbaoDialog
from src.settings_manager import DEFAULT_SETTINGS
from src.chat_translation_api import (
    completion_url, validate_model, protect_api_key, unprotect_api_key, TranslationError, ChatTranslationAPI,
)


class ChatAPISettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.chat_dialog = parent
        ctx = parent.ctx
        self.settings = ctx.settings
        current = self.settings.get_settings()
        self.setWindowTitle('聊天翻译 · API 接口设置')
        self.setMinimumWidth(460)
        self.setStyleSheet(f'QDialog {{ background: {ctx.bg_color}; color: {ctx.text_color}; }}'
                           f'QLabel, QCheckBox {{ color: {ctx.text_color}; }}'
                           f'QLineEdit {{ background: {ctx.alt_bg}; color: {ctx.text_color}; }}')
        layout = QVBoxLayout(self)
        add_dialog_titlebar(self, layout, {
            'titlebar_bg': ctx.bg_color, 'text_color': ctx.text_color,
            'stroke_color': ctx.stroke_color,
        }, ctx.titlebar_svg_icon)
        form = QFormLayout()
        self.api_url = QLineEdit(current.get('chat_translation_api_url', DEFAULT_SETTINGS['chat_translation_api_url']))
        self.api_url.setPlaceholderText('https://api.deepseek.com 或完整 /chat/completions 地址')
        self.model = QLineEdit(current.get('chat_translation_model', DEFAULT_SETTINGS['chat_translation_model']))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setMaxLength(2048)
        self.api_key.setPlaceholderText('已保存；留空保留原 Key' if current.get('chat_translation_api_key_protected')
                                        else '填写 API Key（本机加密保存）')
        self.clear_key = QCheckBox('删除已保存的 API Key')
        form.addRow('API 地址', self.api_url)
        form.addRow('模型', self.model)
        form.addRow('API Key', self.api_key)
        layout.addLayout(form)
        layout.addWidget(self.clear_key)
        notice = QLabel('支持 DeepSeek / OpenAI 兼容接口。启用后，新收到的聊天正文及手动发送的中文会发送到所填服务，'
                        '可能产生费用；不会自动回复。点击“翻译并填入”只填入英文正文，不切换频道或发送；请在游戏里确认后发送。'
                        'Key 由当前 Windows 用户加密保存。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.save_button = QPushButton('保存')
        self.save_button.clicked.connect(self._save)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.save_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.finished.connect(lambda _result: self.api_key.clear())

    def _save(self):
        try:
            url = completion_url(self.api_url.text())
            model = validate_model(self.model.text())
            protected = self.settings.get_setting('chat_translation_api_key_protected') or ''
            if self.clear_key.isChecked():
                protected = ''
            elif self.api_key.text():
                protected = protect_api_key(self.api_key.text())
            enabled = self.settings.get_setting('chat_translation_enabled') is True
            if enabled:
                if not protected:
                    raise TranslationError('启用翻译前请填写 API Key。')
                unprotect_api_key(protected)
            self.settings.set_settings({
                'chat_translation_api_url': url,
                'chat_translation_model': model,
                'chat_translation_api_key_protected': protected,
            })
        except TranslationError as exc:
            self.error.setText(str(exc))
            return
        except Exception:
            self.error.setText('接口设置保存失败，请检查设置文件是否可写。')
            return
        self.chat_dialog._configure()
        self.accept()


class ChatTranslationDialog(RoundedIbaoDialog):
    def __init__(self, send_queue, ctx):
        super().__init__(None)
        self.send_queue = send_queue
        self.ctx = ctx
        self.setObjectName('ChatTranslationDialog')
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setSizeGripEnabled(True)
        self.setWindowTitle("玄枢 · 聊天翻译")
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
        title = QLabel('玄枢 · 聊天翻译')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.minimize_button = QPushButton()
        self.minimize_button.setToolTip('最小化聊天翻译')
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button = QPushButton()
        self.close_button.setToolTip('关闭聊天翻译')
        self.close_button.clicked.connect(self.close)
        self.api_settings_button = QPushButton()
        self.api_settings_button.setToolTip('翻译 API 接口设置')
        self.api_settings_button.setAccessibleName('翻译 API 接口设置')
        self.api_settings_button.clicked.connect(self._open_api_settings)
        title_row.addWidget(self.api_settings_button)
        title_row.addSpacing(32)
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
        row.addWidget(QLabel("监听"))
        self.clients = QComboBox()
        self.clients.addItem("全部已注入客户端", None)
        self.clients.currentIndexChanged.connect(self._configure)
        self.clients.setMaximumWidth(200)
        row.addWidget(self.clients)
        self.allow_busy_chat = QCheckBox('允许忙碌中聊天')
        self.allow_busy_chat.setToolTip('允许自动任务、脚本和战斗中手动聊天；仍检查 Loading、NPC 对话、空草稿及输入回读。')
        row.addWidget(self.allow_busy_chat)
        self.translation_enabled = QCheckBox('启用双语翻译')
        self.translation_enabled.setToolTip('收到的聊天中英文双向显示；手动发送的中文先翻译成英文。正文将发送至配置的 API，可能产生费用。')
        self.translation_enabled.setChecked(ctx.settings.get_setting('chat_translation_enabled') is True)
        self.translation_enabled.toggled.connect(self._toggle_translation)
        row.addWidget(self.translation_enabled)
        row.addStretch()
        layout.addLayout(row)

        send_row = QHBoxLayout()
        send_row.addWidget(QLabel('填入'))
        self.sender = QComboBox()
        self.sender.addItem('p1（未就绪）', 'p1')
        self.sender.setMaximumWidth(110)
        self.sender.currentIndexChanged.connect(self._update_send_button)
        send_row.addWidget(self.sender)
        self.send_text = QLineEdit()
        self.send_text.setPlaceholderText('输入中文，回车翻译并填入英文（不自动发送）')
        self.send_text.setToolTip('中文译成英文，英文直接填入；最多 80 字。不覆盖草稿、不切换频道或私聊对象；由你在游戏里发送。')
        self.send_text.setMaxLength(80)
        self.send_text.returnPressed.connect(self._send_once)
        self.send_text.textChanged.connect(self._update_send_button)
        send_row.addWidget(self.send_text, 1)
        self.send_button = QPushButton('翻译并填入')
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._send_once)
        send_row.addWidget(self.send_button)
        layout.addLayout(send_row)
        self.send_status = QLabel()
        self.send_status.setWordWrap(True)
        self.send_status.hide()
        self._sending = False
        self._available_titles = []

        self.status = QLabel("聊天监听：未启动")
        self.status.setWordWrap(True)
        self.translation_status = QLabel('翻译：已启用' if self.translation_enabled.isChecked() else '翻译：未启用')
        self.translation_status.setToolTip('点击标题栏接口图标配置 API；仅在软件内显示译文。')
        self.translation_status.setWordWrap(True)
        self._history = deque(maxlen=200)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setPlaceholderText('这里显示监听开启后的聊天消息。')
        self.messages.setMaximumBlockCount(500)
        layout.addWidget(self.messages, 1)
        clear = QPushButton("清空消息")
        clear.clicked.connect(self._clear_history)
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.translation_status)
        footer.addWidget(clear)
        layout.addLayout(footer)
        layout.addWidget(self.send_status)
        self._log_statuses = {}
        self._title = title
        self._clear_button = clear
        # QDialog otherwise promotes the first titlebar button to the default
        # Return action, minimizing the window while editing the message.
        for button in (self.api_settings_button, self.minimize_button, self.close_button, self.send_button, clear):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.retheme()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # The input's returnPressed signal is the only Return send action.
            event.accept()
            return
        super().keyPressEvent(event)

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
        configure_titlebar_button(self.api_settings_button, ctx.titlebar_svg_icon, ctx.stroke_color)
        self.api_settings_button.setIcon(ctx.titlebar_svg_icon(
            settings_control_svg(ctx.stroke_color), 24))

    def _toggle_translation(self, enabled):
        previous = self.ctx.settings.get_setting('chat_translation_enabled') is True
        try:
            if enabled:
                ChatTranslationAPI(self.ctx.settings.get_settings())  # Validate locally; no HTTP request.
            self.ctx.settings.set_setting('chat_translation_enabled', enabled)
        except Exception as exc:
            self.translation_enabled.blockSignals(True)
            self.translation_enabled.setChecked(previous)
            self.translation_enabled.blockSignals(False)
            self.translation_status.setText('翻译：启停失败，请检查接口设置')
            self.translation_status.setToolTip(str(exc) if isinstance(exc, TranslationError)
                                               else '无法保存翻译开关设置。')
            return
        self.translation_status.setText('翻译：已启用' if enabled else '翻译：未启用')
        self._configure()

    def _open_api_settings(self):
        dialog = ChatAPISettingsDialog(self)
        dialog.exec()

    def _clear_history(self):
        self._history.clear()
        self.messages.clear()

    def _render_history(self):
        scroll = self.messages.verticalScrollBar()
        position = scroll.value()
        at_bottom = position >= scroll.maximum()
        self.messages.setPlainText('\n'.join(
            row['original'] + ('\n  译文：' + row['translation'] if row.get('translation') else '')
            for row in self._history))
        scroll.setValue(scroll.maximum() if at_bottom else position)

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
        self._log_statuses = {title: value for title, value in self._log_statuses.items()
                              if title in titles}
        self._update_status()
        if self.isVisible():
            self._configure()

    def handle_event(self, event):
        title = event.get("title", "?")
        kind = event.get("kind")
        if kind == 'manual_send':
            if event.get('translation'):
                self.send_status.show()
                self.send_status.setText('英文：' + event['translation'])
            elif str(event.get('status', '')).startswith('正在将中文翻译'):
                self.send_status.show()
                self.send_status.setText('正在将中文翻译成英文…')
            if event.get('done'):
                self.send_status.show()
                self.send_status.setText('英文已填入游戏，未发送；请在游戏里确认频道和收件人后发送。' if event.get('filled') else
                                         event.get('error') or '填入未完成，请查看日志；游戏草稿不会被自动清除。')
                self._sending = False
                self.sender.setEnabled(True)
                self.send_text.setEnabled(True)
                self._update_send_button()
        elif kind == "message":
            sender = event.get("sender_gid", 0)
            message = event.get("message", "")
            if event.get('source') == 'chat_log':
                original = (
                    f"[{title}][{event.get('channel', '聊天')}] "
                    f"[{event.get('sender_name', '?')}]: {message}"
                )
            else:
                original = f"[{title}] GID {sender}: {message}"
            self._history.append({'message_id': event.get('message_id'), 'original': original})
            self._render_history()
        elif kind == 'translation':
            if event.get('message_id') is not None:
                for row in self._history:
                    if row['message_id'] == event['message_id']:
                        row['translation'] = ' '.join(str(event.get('translation', '')).split())
                        self._render_history()
                        break
        elif kind == 'translation_status':
            self.translation_status.setText(event.get('status', '翻译：未启用'))
        elif kind == "status":
            if event.get('source') == 'chat_log':
                self._log_statuses[title] = event.get('status', '')
            self._update_status()

    def _update_status(self):
        if not self.isVisible():
            self.status.setText("聊天监听：未启动")
            return
        titles = [self.clients.currentData()] if self.clients.currentData() else [
            self.clients.itemData(i) for i in range(1, self.clients.count())
        ]
        parts = [f"{title} " + ('监听中' if '监听中' in self._log_statuses.get(title, '') else
                                '记录暂不可用，详情见日志' if '失败' in self._log_statuses.get(title, '') else
                                '等待聊天记录') for title in titles]
        self.status.setToolTip("；".join(parts))
        if len(parts) > 1:
            active = sum('监听中' in self._log_statuses.get(title, '') for title in titles)
            self.status.setText(f'监听：{active}/{len(parts)} 个客户端监听中')
        else:
            self.status.setText("监听：" + (parts[0] if parts else "无已注入客户端"))

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
        self.send_status.show()
        title = self.sender.currentData()
        if title not in self._available_titles:
            self.send_status.setText(f'{title} 未就绪；未发送，不切换其他窗口。')
            logger.info('聊天发送未提交：{} 未就绪', title)
            return
        if not self.send_text.text().strip():
            self.send_status.setText('请输入消息。')
            return
        self._sending = True
        self.send_button.setEnabled(False)
        self.sender.setEnabled(False)
        self.send_text.setEnabled(False)
        self.send_status.setText('正在翻译并填入…')
        self.send_queue.put(GUICommand(GUICommandType.SendNearbyChatTest, {
            'title': title, 'text': self.send_text.text(),
            'allow_busy': self.allow_busy_chat.isChecked(),
        }))

    def _update_send_button(self, *_):
        if hasattr(self, 'send_button'):
            self.send_button.setEnabled(not self._sending
                                       and self.sender.currentData() in self._available_titles
                                       and bool(self.send_text.text().strip()))

    def closeEvent(self, event):
        self.send_queue.put(GUICommand(GUICommandType.ConfigureChatTranslation, {
            "enabled": False,
        }))
        self._log_statuses = {}
        self.status.setText("聊天监听：未启动")
        super().closeEvent(event)
