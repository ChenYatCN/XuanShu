"""Flythrough, Bot, and Combat tabs — all share the editor+import/export/execute/kill pattern."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QFileDialog,
    QPushButton, QLabel, QSizePolicy,
)
from PyQt6.QtCore import Qt

from src.gui.commands import GUICommand, GUICommandType
from src.gui.helpers import centered_label, repo_icon_btn, add_recent, show_recent_menu
from src.gui.widgets import FlowLayout, ThemedCheckBox


def _build_client_toolbar(ctx, wiki_tooltip_key, wiki_path, with_status=False):
    """Create the compact, wrapping client selector shared by Bot and Combat."""
    toolbar = QWidget()
    toolbar.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Minimum,
    )
    toolbar_row = QHBoxLayout(toolbar)
    toolbar_row.setContentsMargins(0, 0, 0, 0)
    toolbar_row.setSpacing(4)

    flow_host = QWidget(toolbar)
    flow_host.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Minimum,
    )
    flow = FlowLayout(
        flow_host,
        margin=0,
        horizontal_spacing=10,
        vertical_spacing=2,
    )
    flow.addWidget(QLabel(ctx.tl('bot_target_clients')))

    all_clients = ThemedCheckBox(
        ctx.tl('bot_target_all'),
        ctx.stroke_color,
        ctx.text_color,
        ctx.alt_bg,
    )
    flow.addWidget(all_clients)

    status_label = None
    if with_status:
        status_label = QLabel()
        status_font = status_label.font()
        if status_font.pointSizeF() > 0:
            status_font.setPointSizeF(max(8.0, status_font.pointSizeF() - 2.0))
        status_label.setFont(status_font)
        status_label.setStyleSheet(f"color: {ctx.stroke_color};")
        status_label.hide()
        flow.addWidget(status_label)

    toolbar_row.addWidget(flow_host, 1)
    if wiki_path is not None:
        info_tooltip = (
            f"{ctx.tl('advanced_warning')}\n"
            f"{ctx.tl(wiki_tooltip_key)}"
        )
        toolbar_row.addWidget(
            repo_icon_btn(
                ctx,
                ctx.svgs['readme'],
                info_tooltip,
                f"{ctx.wiki_base}/{wiki_path}",
            ),
            0,
            Qt.AlignmentFlag.AlignTop,
        )
    return toolbar, flow, all_clients, status_label


def _make_toggle_btn(ctx, play_tooltip, kill_tooltip, execute_cb, kill_cb, action_id):
    """Create a single play/kill toggle button."""
    _running = [False]

    btn = QPushButton()
    btn.setIcon(ctx.titlebar_svg_icon(ctx.svgs['play'], 32))
    btn.setFixedSize(40, 40)
    btn.setStyleSheet(ctx.icon_btn_style)
    btn.setToolTip(play_tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _toggle():
        if _running[0]:
            kill_cb()
        else:
            execute_cb()

    btn.clicked.connect(_toggle)
    ctx.registry.register(action_id, play_tooltip, getattr(ctx, 'current_tab_name', ''), _toggle)
    ctx.registry.make_bindable(btn, action_id)

    def set_running(running):
        _running[0] = running
        svg = ctx.svgs['kill'] if running else ctx.svgs['play']
        btn.setIcon(ctx.titlebar_svg_icon(svg, 32))
        btn.setToolTip(kill_tooltip if running else play_tooltip)

    ctx.tracked_toggle_btns.append((btn, _running, 32))

    return btn, set_running


def build_flythrough_tab(ctx):
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(4, 4, 4, 4)
    header = QHBoxLayout()
    header.addWidget(centered_label(ctx.tl('advanced_warning')), 1)
    header.addWidget(repo_icon_btn(ctx, ctx.svgs['readme'], ctx.tl('tooltip_wiki_flythroughs'), f"{ctx.wiki_base}/Flythroughs"))
    layout.addLayout(header)

    editor = QPlainTextEdit()
    ctx.widget_tags['flythrough_creator'] = editor
    layout.addWidget(editor, 1)

    btn_row = QHBoxLayout()

    def flythrough_import():
        filepath, _ = QFileDialog.getOpenFileName(ctx.window, ctx.tl('import_flythrough'), "", "Text Files (*.txt)")
        if filepath:
            try:
                with open(filepath) as f:
                    editor.setPlainText(f.read())
                add_recent('flythrough', filepath)
            except Exception:
                pass

    def flythrough_export():
        filepath, _ = QFileDialog.getSaveFileName(ctx.window, ctx.tl('export_flythrough'), "flythrough.txt", "Text Files (*.txt)")
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    f.write(editor.toPlainText())
            except Exception:
                pass

    def execute_flythrough_callback():
        ctx.send_queue.put(GUICommand(GUICommandType.ExecuteFlythrough, editor.toPlainText()))

    def kill_flythrough_callback():
        ctx.send_queue.put(GUICommand(GUICommandType.KillFlythrough))

    toggle_btn, set_flythrough_running = _make_toggle_btn(
        ctx, ctx.tl('execute_flythrough'), ctx.tl('kill_flythrough'),
        execute_flythrough_callback, kill_flythrough_callback, 'toggle_flythrough')

    recent_btn = ctx.registry.action_icon_btn(ctx.svgs['recent'], ctx.tl('recent_imports'), lambda: None)
    recent_btn.clicked.disconnect()
    recent_btn.clicked.connect(lambda: show_recent_menu(ctx, 'flythrough', editor, recent_btn))
    btn_row.addStretch()
    btn_row.addWidget(recent_btn)
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['import'], ctx.tl('import_flythrough'), flythrough_import))
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['export'], ctx.tl('export_flythrough'), flythrough_export))
    btn_row.addWidget(toggle_btn)
    btn_row.addStretch()
    layout.addLayout(btn_row)

    ctx.exports['flythrough'] = {'set_running': set_flythrough_running}
    return tab


def build_bot_tab(ctx):
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(2)
    (
        client_toolbar,
        target_flow,
        all_clients,
        running_groups_label,
    ) = _build_client_toolbar(
        ctx,
        'tooltip_wiki_bots',
        'Bots',
        with_status=True,
    )
    client_checks = {}
    layout.addWidget(client_toolbar)

    active_groups = []
    initialized_clients = [False]
    updating_checks = [False]

    def selected_clients():
        return [
            title for title, check in client_checks.items()
            if check.isEnabled() and check.isChecked()
        ]

    def sync_all_check():
        if updating_checks[0]:
            return
        enabled = [check for check in client_checks.values() if check.isEnabled()]
        updating_checks[0] = True
        all_clients.setChecked(bool(enabled) and all(check.isChecked() for check in enabled))
        updating_checks[0] = False

    def toggle_all_clients(checked):
        if updating_checks[0]:
            return
        updating_checks[0] = True
        for check in client_checks.values():
            if check.isEnabled():
                check.setChecked(checked)
        updating_checks[0] = False

    all_clients.toggled.connect(toggle_all_clients)

    def set_available_clients(titles):
        new_titles = []
        seen = set()
        for title in titles or []:
            value = str(title).strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                new_titles.append(value)

        def title_sort_key(value):
            folded = value.casefold()
            if folded.startswith('p') and folded[1:].isdigit():
                return (0, int(folded[1:]))
            return (1, folded)

        new_titles.sort(key=title_sort_key)
        previous_checks = {
            title.casefold(): check.isChecked()
            for title, check in client_checks.items()
        }
        select_all = (
            (not initialized_clients[0] and bool(new_titles))
            or all_clients.isChecked()
        )

        updating_checks[0] = True
        for check in client_checks.values():
            target_flow.removeWidget(check)
            check.deleteLater()
        client_checks.clear()

        for title in new_titles:
            check = ThemedCheckBox(
                title,
                ctx.stroke_color,
                ctx.text_color,
                ctx.alt_bg,
            )
            check.setChecked(
                select_all or previous_checks.get(title.casefold(), False)
            )
            check.toggled.connect(sync_all_check)
            client_checks[title] = check
            target_flow.insertWidget(target_flow.count() - 1, check)

        all_clients.setEnabled(bool(new_titles))
        if not new_titles:
            all_clients.setChecked(False)
            initialized_clients[0] = False
        else:
            initialized_clients[0] = True
        updating_checks[0] = False
        sync_all_check()

    def set_running_groups(groups):
        active_groups.clear()
        active_groups.extend(tuple(group) for group in (groups or []))
        if active_groups:
            display = '、'.join('+'.join(group) for group in active_groups)
            running_groups_label.setText(
                ctx.tl('bot_running_groups').replace('{groups}', display)
            )
            running_groups_label.show()
        else:
            running_groups_label.clear()
            running_groups_label.hide()

    def retheme():
        for check in (all_clients, *client_checks.values()):
            check.set_theme_colors(
                ctx.stroke_color,
                ctx.text_color,
                ctx.alt_bg,
            )
        running_groups_label.setStyleSheet(f"color: {ctx.stroke_color};")

    set_available_clients([])

    editor = QPlainTextEdit()
    ctx.widget_tags['bot_creator'] = editor
    layout.addWidget(editor, 1)

    btn_row = QHBoxLayout()

    def bot_import():
        filepath, _ = QFileDialog.getOpenFileName(ctx.window, ctx.tl('import_bot'), "", "Text Files (*.txt)")
        if filepath:
            try:
                with open(filepath) as f:
                    editor.setPlainText(f.read())
                add_recent('bot', filepath)
            except Exception:
                pass

    def bot_export():
        filepath, _ = QFileDialog.getSaveFileName(ctx.window, ctx.tl('export_bot'), "bot.txt", "Text Files (*.txt)")
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    f.write(editor.toPlainText())
            except Exception:
                pass

    def run_bot_callback():
        targets = selected_clients()
        if not targets:
            running_groups_label.setText(ctx.tl('bot_select_required'))
            running_groups_label.show()
            return
        ctx.send_queue.put(GUICommand(
            GUICommandType.ExecuteBot,
            {'text': editor.toPlainText(), 'clients': targets},
        ))

    def kill_bot_callback():
        targets = selected_clients()
        if not targets:
            running_groups_label.setText(ctx.tl('bot_select_required'))
            running_groups_label.show()
            return
        ctx.send_queue.put(GUICommand(GUICommandType.KillBot, {'clients': targets}))

    def bot_search():
        from src.gui.popups import show_bot_search_popup
        existing = getattr(ctx, 'bot_search_dialog', None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass
        ctx.bot_search_dialog = show_bot_search_popup(ctx, tab)
        ctx.send_queue.put(GUICommand(GUICommandType.SearchBots))

    def bot_publish():
        from src.gui.popups import show_bot_publish_popup
        if not editor.toPlainText().strip():
            return
        existing = getattr(ctx, 'bot_publish_dialog', None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass
        ctx.bot_publish_dialog = show_bot_publish_popup(ctx, editor.toPlainText())
        ctx.send_queue.put(GUICommand(GUICommandType.PrepareBotPublish))

    run_btn = ctx.registry.action_icon_btn(
        ctx.svgs['play'], ctx.tl('run_bot_selected'), run_bot_callback,
        action_id='toggle_bot',
    )
    kill_btn = ctx.registry.action_icon_btn(
        ctx.svgs['kill'], ctx.tl('kill_bot_selected'), kill_bot_callback,
        action_id='kill_selected_bot',
    )

    recent_btn = ctx.registry.action_icon_btn(ctx.svgs['recent'], ctx.tl('recent_imports'), lambda: None)
    recent_btn.clicked.disconnect()
    recent_btn.clicked.connect(lambda: show_recent_menu(ctx, 'bot', editor, recent_btn))
    btn_row.addStretch()
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['search'], ctx.tl('search_bots'), bot_search))
    btn_row.addWidget(recent_btn)
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['import'], ctx.tl('import_bot'), bot_import))
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['export'], ctx.tl('export_bot'), bot_export))
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['publish'], ctx.tl('publish_bot'), bot_publish))
    btn_row.addWidget(run_btn)
    btn_row.addWidget(kill_btn)
    btn_row.addStretch()
    layout.addLayout(btn_row)

    ctx.exports['bot'] = {
        'set_available_clients': set_available_clients,
        'set_running_groups': set_running_groups,
        'selected_clients': selected_clients,
        'retheme': retheme,
    }
    return tab


def build_combat_tab(ctx):
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(2)
    (
        client_toolbar,
        target_flow,
        all_clients,
        _,
    ) = _build_client_toolbar(
        ctx,
        'tooltip_wiki_playstyles',
        'Playstyles',
    )
    client_checks = {}
    layout.addWidget(client_toolbar)

    initialized_clients = [False]
    updating_checks = [False]

    def selected_clients():
        return [
            title for title, check in client_checks.items()
            if check.isEnabled() and check.isChecked()
        ]

    def sync_all_check():
        if updating_checks[0]:
            return
        enabled = [check for check in client_checks.values() if check.isEnabled()]
        updating_checks[0] = True
        all_clients.setChecked(
            bool(enabled) and all(check.isChecked() for check in enabled)
        )
        updating_checks[0] = False

    def toggle_all_clients(checked):
        if updating_checks[0]:
            return
        updating_checks[0] = True
        for check in client_checks.values():
            if check.isEnabled():
                check.setChecked(checked)
        updating_checks[0] = False

    all_clients.toggled.connect(toggle_all_clients)

    def set_available_clients(titles):
        new_titles = []
        seen = set()
        for title in titles or []:
            value = str(title).strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                new_titles.append(value)

        def title_sort_key(value):
            folded = value.casefold()
            if folded.startswith('p') and folded[1:].isdigit():
                return (0, int(folded[1:]))
            return (1, folded)

        new_titles.sort(key=title_sort_key)
        previous_checks = {
            title.casefold(): check.isChecked()
            for title, check in client_checks.items()
        }
        select_all = (
            (not initialized_clients[0] and bool(new_titles))
            or all_clients.isChecked()
        )

        updating_checks[0] = True
        for check in client_checks.values():
            target_flow.removeWidget(check)
            check.deleteLater()
        client_checks.clear()

        for title in new_titles:
            check = ThemedCheckBox(
                title,
                ctx.stroke_color,
                ctx.text_color,
                ctx.alt_bg,
            )
            check.setChecked(
                select_all or previous_checks.get(title.casefold(), False)
            )
            check.toggled.connect(sync_all_check)
            client_checks[title] = check
            target_flow.addWidget(check)

        all_clients.setEnabled(bool(new_titles))
        if not new_titles:
            all_clients.setChecked(False)
            initialized_clients[0] = False
        else:
            initialized_clients[0] = True
        updating_checks[0] = False
        sync_all_check()

    def retheme():
        for check in (all_clients, *client_checks.values()):
            check.set_theme_colors(
                ctx.stroke_color,
                ctx.text_color,
                ctx.alt_bg,
            )

    set_available_clients([])

    editor = QPlainTextEdit()
    ctx.widget_tags['combat_config'] = editor
    layout.addWidget(editor, 1)

    btn_row = QHBoxLayout()

    def combat_import():
        filepath, _ = QFileDialog.getOpenFileName(ctx.window, ctx.tl('import_playstyle'), "", "Text Files (*.txt)")
        if filepath:
            try:
                with open(filepath) as f:
                    editor.setPlainText(f.read())
                add_recent('combat', filepath)
            except Exception:
                pass

    def combat_export():
        filepath, _ = QFileDialog.getSaveFileName(ctx.window, ctx.tl('export_playstyle'), "playstyle.txt", "Text Files (*.txt)")
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    f.write(editor.toPlainText())
            except Exception:
                pass

    def set_playstyles_callback():
        targets = selected_clients()
        if not targets:
            return
        ctx.send_queue.put(GUICommand(
            GUICommandType.SetPlaystyles,
            {'text': editor.toPlainText(), 'clients': targets},
        ))

    def reset_playstyles_callback():
        targets = selected_clients()
        if not targets:
            return
        ctx.send_queue.put(GUICommand(
            GUICommandType.ResetPlaystyles,
            {'clients': targets},
        ))

    recent_btn = ctx.registry.action_icon_btn(ctx.svgs['recent'], ctx.tl('recent_imports'), lambda: None)
    recent_btn.clicked.disconnect()
    recent_btn.clicked.connect(lambda: show_recent_menu(ctx, 'combat', editor, recent_btn))
    btn_row.addStretch()
    btn_row.addWidget(recent_btn)
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['import'], ctx.tl('import_playstyle'), combat_import))
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['export'], ctx.tl('export_playstyle'), combat_export))
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['apply'], ctx.tl('set_playstyles'), set_playstyles_callback))
    btn_row.addWidget(ctx.registry.action_icon_btn(ctx.svgs['reset'], ctx.tl('reset_playstyles'), reset_playstyles_callback))
    btn_row.addStretch()
    layout.addLayout(btn_row)
    ctx.exports['combat'] = {
        'set_available_clients': set_available_clients,
        'selected_clients': selected_clients,
        'retheme': retheme,
    }
    return tab
