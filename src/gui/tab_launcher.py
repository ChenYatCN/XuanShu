import html
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QLineEdit, QListWidget, QListWidgetItem, QDialog,
    QSizePolicy, QFileDialog, QComboBox, QMenu,
)
from PyQt6.QtCore import Qt, QSize, QSignalBlocker

from src.account_groups import get_account_groups, get_account_group, set_account_group
from src.gui.account_group_choice import AccountGroupChoice
from src.gui.account_group_dialogs import (
    account_group_defaults, show_account_group_name_dialog, show_manage_account_groups,
)
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
    _more_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{ctx.stroke_color}">'
        '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/>'
        '<circle cx="19" cy="12" r="1.6"/></svg>'
    )

    _hover_rgba = "rgba(255,255,255,15)" if ctx.theme in ('black', 'dark') else "rgba(0,0,0,15)"
    _launcher_list_style = (
        "QListWidget, QListWidget:focus { border: none; }"
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
    acct_header.setSpacing(4)
    acct_header.addWidget(centered_label(tl('saved_accounts')), 1)
    account_group_choice = AccountGroupChoice(lambda: {
        'alt_bg': getattr(ctx, 'alt_bg', '#2d2d2d'),
        'text_color': getattr(ctx, 'text_color', '#ffffff'),
        'stroke_color': ctx.stroke_color,
        'button_color': getattr(ctx, 'btn_color_hex', '#4a019e'),
    })
    account_group_choice.setObjectName('accountGroupChoice')
    account_group_choice.setAccessibleName(tl('account_group'))
    account_group_choice.setFixedHeight(22)
    account_group_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    account_group_choice.setMinimumContentsLength(4)
    account_group_choice.setMaximumWidth(110)
    saved_group = ctx.settings.get_setting('launcher_account_group') if ctx.settings else None
    current_group = [saved_group or 'favorites']
    default_group_names = account_group_defaults(ctx)
    acct_header.addWidget(account_group_choice)

    def _toggle_search():
        opening = search_input.isHidden()
        search_input.setVisible(opening)
        if opening:
            search_input.setFocus()
        else:
            search_input.clear()

    search_btn = launcher_small_icon_btn(ctx, svgs['search'], tl('search_accounts'), _toggle_search)
    search_btn.setObjectName('accountSearchToggle')
    acct_header.addWidget(search_btn)
    left_col.addLayout(acct_header)
    search_input = QLineEdit()
    search_input.setObjectName('accountSearch')
    search_input.setPlaceholderText(tl('search_accounts'))
    search_input.setClearButtonEnabled(True)
    search_input.setFixedHeight(22)
    search_input.hide()
    left_col.addWidget(search_input)

    account_list = QListWidget()
    account_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
    account_list.setDefaultDropAction(Qt.DropAction.MoveAction)
    account_list.setStyleSheet(_launcher_list_style)
    ctx.widget_tags['AccountList'] = account_list
    account_selection_order: list[str] = []
    account_checkboxes: dict[str, QCheckBox] = {}
    launch_confirmation = [None]

    def _selected_accounts():
        return [nickname for nickname in account_selection_order
                if nickname in account_checkboxes
                and account_checkboxes[nickname].isEnabled()
                and account_checkboxes[nickname].isChecked()]

    def _update_launch_confirmation():
        if launch_confirmation[0] is not None:
            launch_confirmation[0]()

    def _account_toggled(nickname, checked):
        update_account_selection_order(account_selection_order, nickname, checked)
        _update_launch_confirmation()

    def _filter_accounts():
        query = search_input.text().strip().casefold()
        group = current_group[0]
        groups = get_account_groups(ctx.settings, default_group_names)
        for i in range(account_list.count()):
            item = account_list.item(i)
            nick = item.data(Qt.ItemDataRole.UserRole)
            account_group = get_account_group(ctx.settings, nick)
            item.setHidden(query not in nick.casefold() if query
                           else group != 'all' and group != account_group)
            badge = account_list.itemWidget(item).findChild(QLabel, 'accountGroupLabel')
            badge.setText(groups[account_group])
            badge.setToolTip(groups[account_group])
            badge.setVisible(bool(query) or group == 'all')

    def _change_group():
        group = account_group_choice.currentData()
        if group != 'all' and group not in get_account_groups(ctx.settings, default_group_names):
            return
        current_group[0] = group
        account_group_choice.setToolTip(account_group_choice.currentText())
        if ctx.settings:
            ctx.settings.set_setting('launcher_account_group', group)
        _filter_accounts()

    def _refresh_group_choices(selected=None):
        groups = get_account_groups(ctx.settings, default_group_names)
        group = selected or current_group[0]
        if not isinstance(group, str) or (group != 'all' and group not in groups):
            group = 'favorites'
        with QSignalBlocker(account_group_choice):
            account_group_choice.clear()
            account_group_choice.addItem(tl('account_group_all'), 'all')
            for group_id, name in groups.items():
                account_group_choice.addItem(name, group_id)
                account_group_choice.setItemData(account_group_choice.count() - 1, name,
                                                 Qt.ItemDataRole.ToolTipRole)
            account_group_choice.insertSeparator(account_group_choice.count())
            account_group_choice.addItem('+' + tl('new_account_group').lstrip('+＋').strip(), '@create')
            account_group_choice.addItem(tl('manage_account_groups'), '@manage')
            account_group_choice.setCurrentIndex(account_group_choice.findData(group))
        current_group[0] = group
        account_group_choice.setToolTip(account_group_choice.currentText())
        _filter_accounts()

    def _activate_group_choice(index):
        action = account_group_choice.itemData(index)
        if action not in ('@create', '@manage'):
            return
        _refresh_group_choices()
        if action == '@create':
            group = show_account_group_name_dialog(ctx)
            _refresh_group_choices(group)
            if group:
                _change_group()
        else:
            show_manage_account_groups(ctx, _refresh_group_choices)

    account_group_choice.currentIndexChanged.connect(_change_group)
    account_group_choice.activated.connect(_activate_group_choice)
    search_input.textChanged.connect(_filter_accounts)
    _refresh_group_choices()

    def _on_account_rows_moved(*_args):
        nicknames = []
        for i in range(account_list.count()):
            item = account_list.item(i)
            nicknames.append(item.data(Qt.ItemDataRole.UserRole))
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

        dlg_layout.addWidget(QLabel(tl('account_group')))
        group_choice = QComboBox()
        group_choice.setObjectName('accountEditGroup')
        for group_id, name in get_account_groups(ctx.settings, default_group_names).items():
            group_choice.addItem(name, group_id)
        initial_group = (get_account_group(ctx.settings, nickname) if editing
                         else current_group[0] if current_group[0] != 'all' else 'favorites')
        group_choice.setCurrentIndex(group_choice.findData(initial_group))
        dlg_layout.addWidget(group_choice)

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
                        (nickname, nick, steam_value, private_value, group_choice.currentData()),
                    )
                )
                dlg.accept()
                return

            send_queue.put(
                GUICommand(GUICommandType.SaveAccount, (nick, steam_value, private_value, group_choice.currentData()))
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
            lambda checked, nick=nickname: _account_toggled(nick, checked)
        )
        row_layout.addWidget(cb)

        lbl = QLabel(nickname)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setToolTip(nickname)
        lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row_layout.addWidget(lbl, 1)
        group_label = QLabel()
        group_label.setObjectName('accountGroupLabel')
        group_label.setTextFormat(Qt.TextFormat.PlainText)
        group_label.setStyleSheet('font-weight: normal;')
        row_layout.addWidget(group_label)

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

        def _delete_this():
            send_queue.put(GUICommand(GUICommandType.DeleteAccount, nickname))

        def _move_group():
            dlg = QDialog(ctx.window)
            dlg.setWindowTitle(tl('move_account_group'))
            layout = QVBoxLayout(dlg)
            name = QLabel(nickname)
            name.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(name)
            choice = QComboBox()
            for group_id, name in get_account_groups(ctx.settings, default_group_names).items():
                choice.addItem(name, group_id)
            choice.setCurrentIndex(choice.findData(get_account_group(ctx.settings, nickname)))
            layout.addWidget(choice)

            def save():
                set_account_group(ctx.settings, nickname, choice.currentData())
                _filter_accounts()
                dlg.accept()

            buttons = QHBoxLayout()
            for key, callback in (('save_account', save), ('cancel', dlg.reject)):
                button = QPushButton(tl(key))
                button.setStyleSheet(ctx.btn_style)
                button.clicked.connect(callback)
                buttons.addWidget(button)
            layout.addLayout(buttons)
            dlg.exec()
            dlg.deleteLater()

        more_btn = launcher_small_icon_btn(ctx, _more_svg, tl('account_more'), lambda: _show_more())
        more_btn.setObjectName('accountMore')
        more_btn.setAccessibleName(tl('account_more'))

        def _show_more():
            # Rows are destroyed by asynchronous account-list updates.
            menu = QMenu(ctx.window)
            menu.addAction(tl('move_account_group'), _move_group)
            menu.addAction(tl('window_config_tooltip'), _window_config)
            menu.addSeparator()
            menu.addAction(tl('remove_account'), _delete_this)
            menu.exec(more_btn.mapToGlobal(more_btn.rect().bottomLeft()))
            menu.deleteLater()

        row_layout.addWidget(more_btn)

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
            item.setData(Qt.ItemDataRole.UserRole, nick)
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
        _refresh_group_choices()
        _update_launch_confirmation()

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
                lbl.setToolTip(nick)
        _update_launch_confirmation()

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
        if launch_confirmation[0] is not None:
            return
        selected = _selected_accounts()
        visible = {account_list.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(account_list.count()) if not account_list.item(i).isHidden()}
        if any(nick not in visible for nick in selected):
            selected = _confirm_hidden_accounts(selected)
        if selected:
            game_path = game_path_input.text().strip()
            send_queue.put(GUICommand(GUICommandType.LaunchInstance, (selected, game_path)))

    def _confirm_hidden_accounts(selected):
        dlg = QDialog(ctx.window)
        dlg.setObjectName('confirmAccountLaunch')
        dlg.setWindowTitle(tl('confirm_account_launch'))
        layout = QVBoxLayout(dlg)
        note = QLabel(tl('hidden_account_launch_note'))
        note.setWordWrap(True)
        note.setStyleSheet('font-weight: normal;')
        layout.addWidget(note)
        review = QListWidget()
        review.setStyleSheet(_launcher_list_style)
        layout.addWidget(review)
        buttons = QHBoxLayout()
        confirm = QPushButton(tl('launch_login'))
        confirm.setObjectName('confirmLaunchAccounts')
        for button, callback in ((confirm, dlg.accept), (QPushButton(tl('cancel')), dlg.reject)):
            button.setStyleSheet(ctx.btn_style)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        def remaining():
            eligible = set(_selected_accounts())
            return [nick for nick in selected if nick in eligible]

        def refresh():
            review.clear()
            groups = get_account_groups(ctx.settings, default_group_names)
            for nick in remaining():
                review.addItem(f'{nick} [{groups[get_account_group(ctx.settings, nick)]}]')
            confirm.setEnabled(review.count() > 0)

        launch_confirmation[0] = refresh
        refresh()
        dlg.resize(360, 320)
        try:
            return remaining() if dlg.exec() == QDialog.DialogCode.Accepted else []
        finally:
            launch_confirmation[0] = None
            dlg.deleteLater()

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
    launch_btn = ctx.registry.action_icon_btn(svgs['play'], tl('launch_login'), _launch_and_login)
    launch_btn.setObjectName('launchAccounts')
    launcher_action_row.addWidget(launch_btn)
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
