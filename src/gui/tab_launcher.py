import html
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QLineEdit, QListWidget, QListWidgetItem, QDialog,
    QSizePolicy, QFileDialog, QComboBox,
)
from PyQt6.QtCore import Qt, QSize

from src.gui.commands import GUICommand, GUICommandType
from src.gui.helpers import (
    add_dialog_titlebar, centered_label, launcher_icon_btn, launcher_small_icon_btn,
    spinning_loader_widget,
)
from src.game_language import PATCHES, installed_language_patches


def show_launcher_settings_dialog(ctx, game_path_input):
    tl = ctx.tl
    dlg = QDialog(ctx.window)
    dlg.setWindowTitle(tl('settings'))
    dlg.setModal(True)
    dlg.setMinimumWidth(470)
    dlg_layout = QVBoxLayout(dlg)
    dlg_layout.setSpacing(10)
    add_dialog_titlebar(dlg, dlg_layout, ctx.settings.get_theme(), ctx.titlebar_svg_icon)
    dlg_layout.addWidget(QLabel(tl('game_path')))
    path_row = QHBoxLayout()
    path_input = QLineEdit(game_path_input.text())
    path_input.setReadOnly(True)
    path_row.addWidget(path_input)

    def pick():
        path = QFileDialog.getExistingDirectory(dlg, tl('game_path'))
        if path:
            path = os.path.normpath(path)
            path_input.setText(path)
            game_path_input.setText(path)
            ctx.settings.set_setting('game_path', path)
            # A saved choice belongs to the previous install, not the new path.
            ctx.settings.set_setting('game_language', None)
            committed[0] = None
            language.setCurrentIndex(0)
            refresh_patches()
            show_status('')

    path_row.addWidget(launcher_icon_btn(ctx, ctx.svgs['folder'], tl('game_path'), pick))
    dlg_layout.addLayout(path_row)
    dlg_layout.addWidget(QLabel(tl('game_language')))
    language = QComboBox()
    language.setObjectName('gameLanguageChoice')
    language.addItem(tl('game_language_keep'), None)
    language.addItem(tl('game_language_english'), 'en')
    language.addItem(tl('game_language_chinese'), 'zh')
    saved = ctx.settings.get_setting('game_language')
    committed = [saved]
    language.setCurrentIndex(max(0, language.findData(saved)))
    dlg_layout.addWidget(language)
    dlg_layout.addWidget(QLabel(tl('game_language_patch')))
    patches = QComboBox()
    patches.setObjectName('gameLanguagePatch')
    dlg_layout.addWidget(patches)

    def refresh_patches():
        patches.clear()
        available = installed_language_patches(path_input.text()) if path_input.text() else []
        saved_patch = ctx.settings.get_setting('game_language_patch')
        for name in available:
            patches.addItem(name, name)
        # English can disable an overlay even when its Chinese source is absent.
        if saved_patch in PATCHES and saved_patch not in available:
            patches.addItem(saved_patch + ' (' + tl('game_language_missing') + ')', saved_patch)
        if patches.count() == 0:
            patches.addItem(PATCHES[0] + ' (' + tl('game_language_missing') + ')', PATCHES[0])
        # With no explicit saved choice, prefer an actually installed patch.
        patches.setCurrentIndex(max(0, patches.findData(saved_patch)) if committed[0] in ('en', 'zh') else 0)

    refresh_patches()
    note = QLabel()
    note.setObjectName('gameLanguageNote')
    note.setTextFormat(Qt.TextFormat.RichText)
    note.setText('<p style="line-height:145%;">' + html.escape(tl('game_language_note')) + '</p>')
    note.setStyleSheet('font-weight: normal; padding: 4px 0px;')
    note.setWordWrap(True)
    note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    dlg_layout.addWidget(note)
    status = QLabel('')
    status.setObjectName('gameLanguageStatus')
    status.setTextFormat(Qt.TextFormat.RichText)
    status.setStyleSheet('font-weight: normal; padding: 4px 0px;')
    status.setWordWrap(True)
    status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    status.hide()
    dlg_layout.addWidget(status)
    apply = QPushButton(tl('game_language_apply'))
    apply.setObjectName('gameLanguageApply')
    apply.setStyleSheet(ctx.btn_style)
    dlg_layout.addWidget(apply, alignment=Qt.AlignmentFlag.AlignCenter)
    pending = [False]

    def show_status(text):
        wrapped = html.escape(text).replace('\n', '<br>').replace('\\', '\\&#8203;')
        status.setText('<p style="line-height:145%;">' + wrapped + '</p>')
        status.setToolTip(text)
        status.setVisible(bool(text))
        # Feedback and backup paths can add several lines after the dialog opens.
        # Let the window grow instead of squeezing the wrapped labels together.
        dlg.adjustSize()

    def update_enabled():
        apply.setEnabled(not pending[0] and
                         (language.currentData() is not None or committed[0] is not None))

    def request():
        pending[0] = True
        language.setEnabled(False)
        patches.setEnabled(False)
        path_row.itemAt(1).widget().setEnabled(False)
        update_enabled()
        show_status(tl('game_language_applying'))
        ctx.send_queue.put(GUICommand(GUICommandType.SetGameLanguage, {
            'game_path': path_input.text(), 'language': language.currentData(),
            'patch_name': patches.currentData(),
        }))

    def result(data):
        pending[0] = False
        if data.get('ok'):
            committed[0] = language.currentData()
        language.setEnabled(True)
        patches.setEnabled(True)
        path_row.itemAt(1).widget().setEnabled(True)
        update_enabled()
        show_status(tl(data['message_key']) + ('\n' + data['detail'] if data.get('detail') else ''))

    ctx.exports['launcher']['game_language_result'] = result
    language.currentIndexChanged.connect(update_enabled)
    apply.clicked.connect(request)
    update_enabled()
    try:
        dlg.exec()
    finally:
        ctx.exports['launcher'].pop('game_language_result', None)
        dlg.deleteLater()


def update_account_selection_order(order: list[str], nickname: str, checked: bool):
    """Track account selection by click order instead of visual row order."""
    if nickname in order:
        order.remove(nickname)
    if checked:
        order.append(nickname)


def build_launcher_tab(ctx):
    tab = QWidget()
    launcher_layout = QVBoxLayout(tab)
    launcher_layout.setContentsMargins(4, 4, 4, 4)
    launcher_layout.setSpacing(4)

    tl = ctx.tl
    send_queue = ctx.send_queue
    svgs = ctx.svgs

    # Amber warning icon for saved accounts whose metadata needs attention.
    _warning_svg = svgs['triangle-alert'].replace(ctx.stroke_color, '#E0A100')

    _hover_rgba = "rgba(255,255,255,15)" if ctx.theme in ('black', 'dark') else "rgba(0,0,0,15)"
    _launcher_list_style = (
        "QListWidget::item {"
        "  background: transparent;"
        "  border-radius: 4px;"
        "  padding: 2px;"
        "  margin: 1px 2px;"
        "}"
        "QListWidget::item:hover {"
        f"  background-color: {_hover_rgba};"
        "}"
        "QListWidget::item:disabled {"
        "  background: transparent;"
        "}"
        "QScrollBar:vertical { width: 6px; background: transparent; }"
        "QScrollBar::handle:vertical { background: rgba(255,255,255,40); border-radius: 3px; min-height: 20px; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
    )

    # --- Two-column layout ---
    columns_layout = QHBoxLayout()

    # ===== Left column: Saved Accounts =====
    left_col = QVBoxLayout()
    left_col.setSpacing(2)

    acct_header = QHBoxLayout()
    acct_header.addWidget(centered_label(tl('saved_accounts')), 1)
    left_col.addLayout(acct_header)

    account_list = QListWidget()
    account_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
    account_list.setDefaultDropAction(Qt.DropAction.MoveAction)
    account_list.setStyleSheet(_launcher_list_style)
    ctx.widget_tags['AccountList'] = account_list
    account_selection_order: list[str] = []
    account_checkboxes: dict[str, QCheckBox] = {}

    def _on_account_rows_moved(*_args):
        nicknames = []
        for i in range(account_list.count()):
            item = account_list.item(i)
            w = account_list.itemWidget(item)
            if w:
                label = w.findChild(QLabel)
                if label:
                    nicknames.append(label.text())
        if nicknames:
            send_queue.put(GUICommand(GUICommandType.ReorderAccounts, nicknames))

    account_list.model().rowsMoved.connect(_on_account_rows_moved)
    left_col.addWidget(account_list, 1)

    columns_layout.addLayout(left_col, 1)

    # ===== Right column: Hooked Clients =====
    right_col = QVBoxLayout()
    right_col.setSpacing(2)

    hooked_header = QHBoxLayout()
    hooked_header.addWidget(centered_label(tl('hooked_clients')), 1)
    right_col.addLayout(hooked_header)

    hooked_clients_list = QListWidget()
    hooked_clients_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
    hooked_clients_list.setDefaultDropAction(Qt.DropAction.MoveAction)
    hooked_clients_list.setStyleSheet(_launcher_list_style)
    ctx.widget_tags['HookedClientsList'] = hooked_clients_list
    right_col.addWidget(hooked_clients_list, 1)

    _hooking_handles = set()
    _last_hooked_data = {}

    columns_layout.addLayout(right_col, 1)
    launcher_layout.addLayout(columns_layout, 1)

    # --- Account dialog and helpers ---
    def _show_account_dialog(nickname: str = None, steam: bool = False, private: bool = False):
        """Add or edit an account without handling a password in Qt."""
        editing = nickname is not None
        dlg = QDialog(ctx.window)
        dlg.setWindowTitle(tl('update_account') if editing else tl('add_account'))
        dlg.setModal(True)
        dlg_layout = QVBoxLayout(dlg)

        dlg_layout.addWidget(QLabel(tl('nickname')))
        nick_input = QLineEdit()
        nick_input.setPlaceholderText(tl('nickname'))
        if editing:
            nick_input.setText(nickname)
        dlg_layout.addWidget(nick_input)
        validation_label = QLabel('')
        validation_label.setStyleSheet('color: #D9534F;')
        dlg_layout.addWidget(validation_label)

        steam_cb = QCheckBox(tl('steam_mode'))
        steam_cb.setChecked(bool(steam))
        dlg_layout.addWidget(steam_cb)

        private_cb = QCheckBox(tl('private_mode'))
        private_cb.setToolTip(tl('private_mode_tooltip'))
        private_cb.setChecked(bool(private))
        dlg_layout.addWidget(private_cb)

        def _keep_modes_exclusive(checked: bool, other: QCheckBox):
            if checked and other.isChecked():
                other.setChecked(False)
                validation_label.setText(tl('steam_private_conflict'))

        steam_cb.toggled.connect(lambda checked: _keep_modes_exclusive(checked, private_cb))
        private_cb.toggled.connect(lambda checked: _keep_modes_exclusive(checked, steam_cb))
        if steam_cb.isChecked() and private_cb.isChecked():
            validation_label.setText(tl('steam_private_conflict'))

        if editing:
            credentials_btn = QPushButton(tl('update_account_credentials'))
            credentials_btn.setStyleSheet(ctx.btn_style)
            credentials_btn.clicked.connect(
                lambda: send_queue.put(
                    GUICommand(GUICommandType.UpdateAccountCredentials, nickname)
                )
            )
            dlg_layout.addWidget(credentials_btn)

        save_btn = QPushButton(tl('save_account'))
        save_btn.setStyleSheet(ctx.btn_style)

        def _on_save():
            steam_value = steam_cb.isChecked()
            private_value = private_cb.isChecked()
            nick = nick_input.text().strip()
            if steam_value and private_value:
                validation_label.setText(tl('steam_private_conflict'))
                return
            if not nick:
                validation_label.setText(tl('nickname_required'))
                return
            if nick != nickname and nick in account_checkboxes:
                validation_label.setText(tl('account_already_exists'))
                return
            if editing:
                send_queue.put(
                    GUICommand(
                        GUICommandType.UpdateAccount,
                        (nickname, nick, steam_value, private_value),
                    )
                )
                dlg.accept()
                return

            send_queue.put(
                GUICommand(GUICommandType.SaveAccount, (nick, steam_value, private_value))
            )
            dlg.accept()

        save_btn.clicked.connect(_on_save)
        buttons = QHBoxLayout()
        buttons.addWidget(save_btn)
        cancel_btn = QPushButton(tl('cancel'))
        cancel_btn.setStyleSheet(ctx.btn_style)
        cancel_btn.clicked.connect(dlg.reject)
        buttons.addWidget(cancel_btn)
        dlg_layout.addLayout(buttons)

        dlg.adjustSize()
        dlg.exec()

    def _build_account_item_widget(
        nickname: str,
        disabled: bool = False,
        error: str = None,
        steam: bool = None,
        private: bool = False,
    ):
        row = QWidget(account_list)
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 0, 2, 0)
        row_layout.setSpacing(4)

        cb = QCheckBox()
        account_checkboxes[nickname] = cb
        cb.toggled.connect(
            lambda checked, nick=nickname: update_account_selection_order(
                account_selection_order, nick, checked
            )
        )
        row_layout.addWidget(cb)

        lbl = QLabel(nickname)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row_layout.addWidget(lbl, 1)

        if disabled:
            cb.setEnabled(False)
            cb.setChecked(False)
            lbl.setStyleSheet("color: rgba(255,255,255,80);" if ctx.theme in ('black', 'dark') else "color: rgba(0,0,0,80);")
            lbl.setToolTip(tl('already_active'))

        def _edit_this():
            _show_account_dialog(nickname=nickname, steam=bool(steam), private=bool(private))

        if error:
            warning_btn = launcher_small_icon_btn(
                ctx, _warning_svg, error, _edit_this
            )
            row_layout.addWidget(warning_btn)

        edit_btn = launcher_small_icon_btn(
            ctx, svgs['pencil'], tl('edit_account'), _edit_this
        )
        row_layout.addWidget(edit_btn)

        def _window_config():
            from src.gui.window_config_dialog import show_window_config_dialog

            show_window_config_dialog(ctx, nickname)

        window_btn = launcher_small_icon_btn(
            ctx,
            svgs['proportions'],
            tl('window_config_tooltip'),
            _window_config,
        )
        row_layout.addWidget(window_btn)

        def _delete_this():
            send_queue.put(GUICommand(GUICommandType.DeleteAccount, nickname))
        trash_btn = launcher_small_icon_btn(ctx, svgs['trash'], tl('remove_account'), _delete_this)
        row_layout.addWidget(trash_btn)

        return row

    def _populate_account_list(accounts: list):
        remember = ctx.settings and ctx.settings.get_setting('remember_chosen_clients')
        managed = ctx.widget_tags.get('managed_accounts', set())
        previous_selection_order = list(account_selection_order)
        account_selection_order.clear()
        account_checkboxes.clear()
        account_list.setUpdatesEnabled(False)
        account_list.clear()
        for entry in accounts:
            if isinstance(entry, dict):
                nick = entry.get('nick')
                error = entry.get('error')
                steam = entry.get('steam')
                private = entry.get('private', False)
            else:
                nick, error, steam = entry, None, None
                private = False
            if not nick:
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 28))
            row_widget = _build_account_item_widget(
                nick,
                disabled=(nick in managed and not remember),
                error=error,
                steam=steam,
                private=private,
            )
            account_list.addItem(item)
            account_list.setItemWidget(item, row_widget)
        # Reapply existing checks in their original click order after rebuilding.
        for nickname in previous_selection_order:
            checkbox = account_checkboxes.get(nickname)
            if checkbox is not None and checkbox.isEnabled():
                checkbox.setChecked(True)
        account_list.setUpdatesEnabled(True)

    def _refresh_account_eligibility(managed_accounts):
        remember = ctx.settings and ctx.settings.get_setting('remember_chosen_clients')
        managed = set(managed_accounts)
        for i in range(account_list.count()):
            item = account_list.item(i)
            w = account_list.itemWidget(item)
            if not w:
                continue
            cb = w.findChild(QCheckBox)
            lbl = w.findChild(QLabel)
            if not cb or not lbl:
                continue
            nick = lbl.text()
            if nick in managed and not remember:
                cb.setEnabled(False)
                cb.setChecked(False)
                lbl.setStyleSheet("color: rgba(255,255,255,80);" if ctx.theme in ('black', 'dark') else "color: rgba(0,0,0,80);")
                lbl.setToolTip(tl('already_active'))
            else:
                cb.setEnabled(True)
                lbl.setStyleSheet("")
                lbl.setToolTip("")

    def _build_hooked_client_widget(info: dict):
        row = QWidget(hooked_clients_list)
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 0, 2, 0)
        row_layout.setSpacing(4)

        title = info['title']
        nick = info.get('account_nick')
        display = f"{title} ({nick})" if nick else title
        lbl = QLabel(display)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row_layout.addWidget(lbl, 1)

        handle = info['handle']
        def _kill_this():
            send_queue.put(GUICommand(GUICommandType.KillClient, handle))
        kill_btn = launcher_small_icon_btn(ctx, svgs['kill_client'], tl('kill_client'), _kill_this)
        row_layout.addWidget(kill_btn)

        if nick:
            def _relaunch_this():
                send_queue.put(GUICommand(GUICommandType.RelaunchClient, (handle, nick)))
            relaunch_btn = launcher_small_icon_btn(ctx, svgs['relaunch'], tl('relaunch_client'), _relaunch_this)
            row_layout.addWidget(relaunch_btn)

        def _eject_this():
            send_queue.put(GUICommand(GUICommandType.UnhookClient, handle))
        eject_btn = launcher_small_icon_btn(ctx, svgs['eject'], tl('unhook_client'), _eject_this)
        row_layout.addWidget(eject_btn)

        return row

    def _build_unmanaged_client_widget(handle: int):
        row = QWidget(hooked_clients_list)
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 0, 2, 0)
        row_layout.setSpacing(4)

        lbl = QLabel(f"Wizard101 ({handle})")
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lbl.setStyleSheet("color: rgba(255,255,255,120);" if ctx.theme in ('black', 'dark') else "color: rgba(0,0,0,120);")
        row_layout.addWidget(lbl, 1)

        def _kill_this():
            send_queue.put(GUICommand(GUICommandType.KillClient, handle))
        kill_btn = launcher_small_icon_btn(ctx, svgs['kill_client'], tl('kill_client'), _kill_this)
        row_layout.addWidget(kill_btn)

        def _hook_this():
            _hooking_handles.add(handle)
            _rebuild_hooked_clients_list()
            send_queue.put(GUICommand(GUICommandType.HookClient, handle))
        hook_btn = launcher_small_icon_btn(ctx, svgs['hook'], tl('hook_client'), _hook_this)
        row_layout.addWidget(hook_btn)

        return row

    def _build_hooking_client_widget(handle: int, nick: str = None):
        row = QWidget(hooked_clients_list)
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 0, 2, 0)
        row_layout.setSpacing(4)

        display = nick if nick else f"Wizard101 ({handle})"
        lbl = QLabel(display)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lbl.setStyleSheet("color: rgba(255,255,255,120);" if ctx.theme in ('black', 'dark') else "color: rgba(0,0,0,120);")
        row_layout.addWidget(lbl, 1)

        row_layout.addWidget(spinning_loader_widget(ctx))

        return row

    def _rebuild_hooked_clients_list():
        hooked_clients_list.setUpdatesEnabled(False)
        hooked_clients_list.clear()
        hooked = _last_hooked_data.get('hooked', [])
        unmanaged = _last_hooked_data.get('unmanaged', [])
        hooking_backend = set(_last_hooked_data.get('hooking', []))
        hooked_handle_set = {info['handle'] for info in hooked}

        for info in hooked:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 28))
            h = info['handle']
            if h in hooking_backend:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                row_widget = _build_hooking_client_widget(h, info.get('account_nick'))
            else:
                item.setData(Qt.ItemDataRole.UserRole, h)
                row_widget = _build_hooked_client_widget(info)
            hooked_clients_list.addItem(item)
            hooked_clients_list.setItemWidget(item, row_widget)

        for h in list(_hooking_handles):
            if h not in hooked_handle_set:
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 28))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                row_widget = _build_hooking_client_widget(h)
                hooked_clients_list.addItem(item)
                hooked_clients_list.setItemWidget(item, row_widget)

        remaining = [h for h in unmanaged if h not in _hooking_handles]

        if remaining:
            sep_item = QListWidgetItem()
            sep_item.setSizeHint(QSize(0, 20))
            sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
            sep_widget = QWidget(hooked_clients_list)
            sep_widget.setStyleSheet("background: transparent;")
            sep_layout = QHBoxLayout(sep_widget)
            sep_layout.setContentsMargins(4, 2, 4, 2)
            sep_lbl = QLabel(tl('unmanaged_clients'))
            sep_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sep_lbl.setStyleSheet("color: rgba(255,255,255,80); font-size: 10px;" if ctx.theme in ('black', 'dark') else "color: rgba(0,0,0,80); font-size: 10px;")
            sep_layout.addWidget(sep_lbl, 1)
            hooked_clients_list.addItem(sep_item)
            hooked_clients_list.setItemWidget(sep_item, sep_widget)
            for handle in remaining:
                u_item = QListWidgetItem()
                u_item.setSizeHint(QSize(0, 28))
                u_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                u_widget = _build_unmanaged_client_widget(handle)
                hooked_clients_list.addItem(u_item)
                hooked_clients_list.setItemWidget(u_item, u_widget)
        hooked_clients_list.setUpdatesEnabled(True)

    def _on_hooked_rows_moved(*_args):
        handles = []
        for i in range(hooked_clients_list.count()):
            item = hooked_clients_list.item(i)
            handle = item.data(Qt.ItemDataRole.UserRole)
            if handle is not None:
                handles.append(handle)
        if handles:
            send_queue.put(GUICommand(GUICommandType.ReorderClients, handles))
            old_hooked = _last_hooked_data.get('hooked', [])
            handle_to_info = {info['handle']: info for info in old_hooked}
            _last_hooked_data['hooked'] = [handle_to_info[h] for h in handles if h in handle_to_info]
            _rebuild_hooked_clients_list()

    hooked_clients_list.model().rowsMoved.connect(_on_hooked_rows_moved)

    # Launch & Login button
    def _launch_and_login():
        selected = [
            nickname
            for nickname in account_selection_order
            if nickname in account_checkboxes
            and account_checkboxes[nickname].isEnabled()
            and account_checkboxes[nickname].isChecked()
        ]
        if selected:
            game_path = game_path_input.text().strip()
            send_queue.put(GUICommand(GUICommandType.LaunchInstance, (selected, game_path)))

    # Resolve game path: saved setting > auto-detect
    _saved_path = ctx.settings.get_setting('game_path') if ctx.settings else None
    if _saved_path and os.path.isdir(_saved_path):
        _resolved_path = _saved_path
    else:
        _steam_path = r"C:\Program Files (x86)\Steam\steamapps\common\Wizard101"
        _default_path = r"C:\ProgramData\KingsIsle Entertainment\Wizard101"
        _resolved_path = ""
        if os.path.isdir(_steam_path):
            _resolved_path = _steam_path
        elif os.path.isdir(_default_path):
            _resolved_path = _default_path

    game_path_input = QLineEdit(_resolved_path)
    game_path_input.setReadOnly(True)
    game_path_input.setVisible(False)
    ctx.widget_tags['GamePath'] = game_path_input

    def _show_settings_dialog():
        show_launcher_settings_dialog(ctx, game_path_input)

    launcher_action_row = QHBoxLayout()
    launcher_action_row.addStretch()
    launcher_action_row.addWidget(ctx.registry.action_icon_btn(svgs['add'], tl('add_account'), lambda: _show_account_dialog()))
    launcher_action_row.addWidget(ctx.registry.action_icon_btn(svgs['play'], tl('launch_login'), _launch_and_login))
    launcher_action_row.addWidget(ctx.registry.action_icon_btn(svgs['gear'], tl('settings'), _show_settings_dialog))
    launcher_action_row.addStretch()
    launcher_layout.addLayout(launcher_action_row)

    # Request account list from backend on startup
    send_queue.put(GUICommand(GUICommandType.LoadAccounts))

    # Export state for the event loop
    ctx.exports['launcher'] = {
        'populate_account_list': _populate_account_list,
        'rebuild_hooked_clients_list': _rebuild_hooked_clients_list,
        'refresh_account_eligibility': _refresh_account_eligibility,
        'hooking_handles': _hooking_handles,
        'last_hooked_data': _last_hooked_data,
        'account_list': account_list,
    }

    return tab
