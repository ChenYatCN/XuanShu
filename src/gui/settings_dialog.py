# Modified 2026-09-09: XuanShu branding and path compatibility; see NOTICE.md.
import glob
import os
import shutil

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.gui.commands import GUICommand, GUICommandType
from src.gui.icon_manager import (
    choose_custom_icon,
    reset_custom_icon,
    update_desktop_shortcut,
)
from src.settings_manager import DEFAULT_SETTINGS, DEFAULT_THEME, RESTART_REQUIRED_KEYS
from src.quest_party import FRIEND_ICON_PRESETS
from src.gui.widgets import FlowLayout, ThemedCheckBox


class _NoScrollComboBox(QComboBox):
    """QComboBox that ignores scroll wheel events to prevent accidental changes."""

    def wheelEvent(self, event):
        event.ignore()


class _NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class _NoScrollDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


def _scan_locales():
    """Scan locale/ directory for available .lang files."""
    codes = []
    locale_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "locale"
    )
    for path in glob.glob(os.path.join(locale_dir, "*.lang")):
        codes.append(os.path.splitext(os.path.basename(path))[0])
    return sorted(codes) if codes else ["en"]


_CLIENT_OPTIONS = ["None", "p1", "p2", "p3", "p4"]

_THEME_KEYS = [
    ("bg_color", "setting_bg_color"),
    ("alt_bg", "setting_alt_bg"),
    ("text_color", "setting_text_color"),
    ("button_color", "setting_button_color"),
    ("stroke_color", "setting_stroke_color"),
    ("titlebar_bg", "setting_titlebar_bg"),
]


def _color_swatch(color_hex):
    """Create a small colored QWidget swatch."""
    swatch = QWidget()
    swatch.setFixedSize(24, 24)
    swatch.setStyleSheet(
        f"background-color: {color_hex}; border: 1px solid rgba(255,255,255,60); border-radius: 3px;"
    )
    return swatch


def show_settings_dialog(ctx):
    tl = ctx.tl
    current = ctx.settings.get_settings()
    current_theme = ctx.settings.get_theme()
    original_theme = dict(current_theme)
    theme_edits = dict(current_theme)

    dialog = QDialog(ctx.window)
    dialog.setWindowTitle(tl("settings_title"))
    dialog.setModal(True)
    dialog.setMinimumWidth(380)
    dialog.setStyleSheet(
        f"QWidget {{ background-color: {ctx.bg_color}; color: {ctx.text_color}; }}"
        f"QGroupBox {{ border: 1px solid rgba(255,255,255,40); border-radius: 4px; margin-top: 14px; padding-top: 8px; }}"
        f"QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; font-weight: bold; }}"
    )

    outer_layout = QVBoxLayout(dialog)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet(
        "QScrollArea { border: none; }"
        "QScrollBar:vertical { width: 6px; background: transparent; }"
        "QScrollBar::handle:vertical { background: rgba(255,255,255,40); border-radius: 3px; min-height: 20px; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
    )
    outer_layout.addWidget(scroll)

    scroll_widget = QWidget()
    layout = QVBoxLayout(scroll_widget)
    layout.setSpacing(8)
    scroll.setWidget(scroll_widget)

    widgets = {}  # key -> widget
    multi_select_widgets = {}  # key -> {client title: checkbox}

    def _add_checkbox(form, key, label_key):
        cb = QCheckBox(tl(label_key))
        cb.setChecked(bool(current.get(key, DEFAULT_SETTINGS.get(key))))
        form.addRow(cb)
        widgets[key] = cb

    def _add_client_combo(form, key, label_key):
        combo = _NoScrollComboBox()
        combo.addItems(_CLIENT_OPTIONS)
        val = current.get(key)
        combo.setCurrentText(str(val) if val else "None")
        form.addRow(tl(label_key), combo)
        widgets[key] = combo

    def _add_client_multi_select(form, key, label_key, client_options):
        container = QWidget()
        flow = FlowLayout(
            container,
            margin=0,
            horizontal_spacing=10,
            vertical_spacing=2,
        )
        selected = {
            str(title).strip().casefold()
            for title in current.get(key, DEFAULT_SETTINGS.get(key, [])) or []
        }
        checkboxes = {}
        for option in client_options:
            value = option["value"]
            checkbox = ThemedCheckBox(
                option["label"],
                accent_color=ctx.btn_color_hex,
                text_color=ctx.text_color,
                background_color=ctx.alt_bg,
            )
            option_aliases = {
                str(alias).strip().casefold()
                for alias in option.get("aliases", [value])
            }
            checkbox.setChecked(bool(option_aliases.intersection(selected)))
            flow.addWidget(checkbox)
            checkboxes[value] = checkbox
        if not client_options:
            empty_label = QLabel(tl("setting_quest_party_no_clients"))
            empty_label.setStyleSheet("color: rgba(255,255,255,100);")
            flow.addWidget(empty_label)
        form.addRow(tl(label_key), container)
        multi_select_widgets[key] = checkboxes
        return container, checkboxes

    # ---- General ----
    general_group = QGroupBox(tl("settings_general"))
    general_form = QFormLayout(general_group)
    general_form.setSpacing(4)

    speed_spin = _NoScrollDoubleSpinBox()
    speed_spin.setRange(0.1, 20.0)
    speed_spin.setSingleStep(0.5)
    speed_spin.setDecimals(1)
    speed_spin.setValue(float(current.get("speed_multiplier", 5.0)))
    general_form.addRow(tl("setting_speed_multiplier"), speed_spin)
    widgets["speed_multiplier"] = speed_spin

    _add_checkbox(general_form, "use_potions", "setting_use_potions")
    _add_checkbox(general_form, "buy_potions", "setting_buy_potions")
    _add_checkbox(general_form, "rich_presence", "setting_rich_presence")
    _add_checkbox(general_form, "drop_logging", "setting_drop_logging")
    _add_checkbox(general_form, "use_anti_afk", "setting_use_anti_afk")

    layout.addWidget(general_group)

    # ---- Theme (color swatches) ----
    theme_group = QGroupBox(tl("settings_theme"))
    theme_form = QFormLayout(theme_group)
    theme_form.setSpacing(4)

    swatches = {}

    def _update_swatch(key):
        swatches[key].setStyleSheet(
            f"background-color: {theme_edits[key]}; border: 1px solid rgba(255,255,255,60); border-radius: 3px;"
        )

    def _make_color_row(key, label_key):
        hex_val = theme_edits[key]
        swatch = _color_swatch(hex_val)
        swatches[key] = swatch
        pick_btn = QPushButton("...")
        pick_btn.setFixedSize(28, 24)
        pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        def _pick(k=key, lk=label_key):
            color = QColorDialog.getColor(QColor(theme_edits[k]), dialog, tl(lk))
            if color.isValid():
                theme_edits[k] = color.name()
                _update_swatch(k)

        pick_btn.clicked.connect(lambda checked, k=key, lk=label_key: _pick(k, lk))

        reset_btn = QPushButton()
        reset_btn.setIcon(ctx.titlebar_svg_icon(ctx.svgs["reset"], 14))
        reset_btn.setFixedSize(22, 22)
        reset_btn.setStyleSheet(ctx.icon_btn_style)
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setToolTip(tl("reset_to_default").format(DEFAULT_THEME[key]))

        def _reset(k=key):
            theme_edits[k] = DEFAULT_THEME[k]
            _update_swatch(k)

        reset_btn.clicked.connect(lambda checked, k=key: _reset(k))

        row = QHBoxLayout()
        row.addWidget(swatch)
        row.addWidget(pick_btn)
        row.addWidget(reset_btn)
        row.addStretch()
        theme_form.addRow(tl(label_key), row)

    for key, label_key in _THEME_KEYS:
        _make_color_row(key, label_key)

    # Import/Export row
    ie_row = QHBoxLayout()

    import_theme_btn = QPushButton(tl("settings_import_theme"))
    import_theme_btn.setStyleSheet(ctx.btn_style)
    import_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _import_theme():
        path, _ = QFileDialog.getOpenFileName(
            dialog, tl("settings_import_theme"), "", "JSON files (*.json)"
        )
        if path:
            new_theme = ctx.settings.import_theme(path)
            theme_edits.update(new_theme)
            for k in swatches:
                _update_swatch(k)
            from src.gui.theme import apply_theme

            apply_theme(ctx, new_theme)

    import_theme_btn.clicked.connect(_import_theme)
    ie_row.addWidget(import_theme_btn)

    export_theme_btn = QPushButton(tl("settings_export_theme"))
    export_theme_btn.setStyleSheet(ctx.btn_style)
    export_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _export_theme():
        path, _ = QFileDialog.getSaveFileName(
            dialog, tl("settings_export_theme"), "", "JSON files (*.json)"
        )
        if path:
            ctx.settings.export_theme(path)

    export_theme_btn.clicked.connect(_export_theme)
    ie_row.addWidget(export_theme_btn)
    ie_row.addStretch()

    theme_form.addRow(ie_row)

    layout.addWidget(theme_group)

    # ---- Appearance (font + locale) ----
    appearance_group = QGroupBox(tl("settings_appearance"))
    appearance_form = QFormLayout(appearance_group)
    appearance_form.setSpacing(4)

    locale_combo = _NoScrollComboBox()
    locale_combo.addItems(_scan_locales())
    locale_combo.setCurrentText(str(current.get("locale", "en")))
    widgets["locale"] = locale_combo

    locale_row = QHBoxLayout()
    locale_row.addWidget(locale_combo, 1)
    import_lang_btn = QPushButton()
    import_lang_btn.setIcon(ctx.titlebar_svg_icon(ctx.svgs["import"], 16))
    import_lang_btn.setFixedSize(24, 24)
    import_lang_btn.setStyleSheet(ctx.icon_btn_style)
    import_lang_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    import_lang_btn.setToolTip(tl("import_lang_file"))

    def _import_lang():
        path, _ = QFileDialog.getOpenFileName(
            dialog, tl("import_lang_title"), "", "Language files (*.lang)"
        )
        if path:
            locale_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "locale"
            )
            dest = os.path.join(locale_dir, os.path.basename(path))
            if not os.path.abspath(dest).startswith(os.path.abspath(locale_dir)):
                return
            shutil.copy2(path, dest)
            code = os.path.splitext(os.path.basename(path))[0]
            codes = _scan_locales()
            locale_combo.clear()
            locale_combo.addItems(codes)
            locale_combo.setCurrentText(code)

    import_lang_btn.clicked.connect(_import_lang)
    locale_row.addWidget(import_lang_btn)
    appearance_form.addRow(tl("setting_locale") + " *", locale_row)

    font_edit = QLineEdit(str(current.get("font", "Segoe UI")))
    widgets["font"] = font_edit

    font_reset_btn = QPushButton()
    font_reset_btn.setIcon(ctx.titlebar_svg_icon(ctx.svgs["reset"], 14))
    font_reset_btn.setFixedSize(22, 22)
    font_reset_btn.setStyleSheet(ctx.icon_btn_style)
    font_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    font_reset_btn.setToolTip(tl("reset_to_default").format(DEFAULT_SETTINGS["font"]))
    font_reset_btn.clicked.connect(
        lambda checked: font_edit.setText(DEFAULT_SETTINGS["font"])
    )

    font_row = QHBoxLayout()
    font_row.addWidget(font_edit, 1)
    font_row.addWidget(font_reset_btn)
    appearance_form.addRow(tl("setting_font"), font_row)

    font_size_spin = _NoScrollSpinBox()
    font_size_spin.setRange(6, 24)
    font_size_spin.setValue(int(current.get("font_size", 9)))
    widgets["font_size"] = font_size_spin

    font_size_reset_btn = QPushButton()
    font_size_reset_btn.setIcon(ctx.titlebar_svg_icon(ctx.svgs["reset"], 14))
    font_size_reset_btn.setFixedSize(22, 22)
    font_size_reset_btn.setStyleSheet(ctx.icon_btn_style)
    font_size_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    font_size_reset_btn.setToolTip(
        tl("reset_to_default").format(DEFAULT_SETTINGS["font_size"])
    )
    font_size_reset_btn.clicked.connect(
        lambda checked: font_size_spin.setValue(DEFAULT_SETTINGS["font_size"])
    )

    font_size_row = QHBoxLayout()
    font_size_row.addWidget(font_size_spin, 1)
    font_size_row.addWidget(font_size_reset_btn)
    appearance_form.addRow(tl("setting_font_size"), font_size_row)

    # ---- Custom App Icon ----
    icon_row = QHBoxLayout()

    change_icon_btn = QPushButton("更换软件图标")
    change_icon_btn.setStyleSheet(ctx.btn_style)
    change_icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    change_icon_btn.clicked.connect(lambda: choose_custom_icon(ctx, dialog))

    reset_icon_btn = QPushButton("恢复默认图标")
    reset_icon_btn.setStyleSheet(ctx.btn_style)
    reset_icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_icon_btn.clicked.connect(lambda: reset_custom_icon(ctx, dialog))

    icon_row.addWidget(change_icon_btn)
    icon_row.addWidget(reset_icon_btn)
    icon_row.addStretch()

    appearance_form.addRow("软件图标", icon_row)

    shortcut_icon_btn = QPushButton("更新桌面快捷方式图标")
    shortcut_icon_btn.setStyleSheet(ctx.btn_style)
    shortcut_icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    shortcut_icon_btn.clicked.connect(
        lambda: update_desktop_shortcut(ctx, "XuanShu.lnk", dialog)
    )

    appearance_form.addRow("", shortcut_icon_btn)

    layout.addWidget(appearance_group)

    # ---- Sigil ----
    sigil_group = QGroupBox(tl("settings_sigil"))
    sigil_form = QFormLayout(sigil_group)
    sigil_form.setSpacing(4)

    _add_checkbox(sigil_form, "use_team_up", "setting_use_team_up")
    _add_client_combo(sigil_form, "client_to_follow", "setting_client_to_follow")

    layout.addWidget(sigil_group)

    # ---- Questing ----
    questing_group = QGroupBox(tl("settings_questing"))
    questing_form = QFormLayout(questing_group)
    questing_form.setSpacing(4)

    launcher_state = ctx.exports.get("launcher", {})
    hooked_data = launcher_state.get("last_hooked_data", {})
    live_client_options = []
    for info in hooked_data.get("hooked", []):
        title = str(info.get("title", "")).strip()
        if not title:
            continue
        nickname = str(info.get("account_nick", "")).strip()
        stable_id = str(info.get("stable_id", "")).strip() or f"title:{title.casefold()}"
        live_client_options.append(
            {
                "title": title,
                "label": f"{title}（{nickname}）" if nickname else title,
                "value": stable_id,
                "aliases": [stable_id, title, f"title:{title.casefold()}"],
            }
        )

    _add_checkbox(
        questing_form,
        "quest_party_enabled",
        "setting_quest_party_enabled",
    )
    quester_container, quester_checks = _add_client_multi_select(
        questing_form,
        "questing_clients",
        "setting_questing_clients",
        live_client_options,
    )
    hitter_container, hitter_checks = _add_client_multi_select(
        questing_form,
        "questing_hitter_clients",
        "setting_questing_hitter_clients",
        live_client_options,
    )
    party_note = QLabel(tl("setting_quest_party_note"))
    party_note.setWordWrap(True)
    party_note.setStyleSheet("color: rgba(255,255,255,120); font-size: 10px;")
    questing_form.addRow("", party_note)

    for option in live_client_options:
        identity = option["value"]
        quester_box = quester_checks[identity]
        hitter_box = hitter_checks[identity]
        quester_box.toggled.connect(
            lambda checked, other=hitter_box: other.setChecked(False)
            if checked
            else None
        )
        hitter_box.toggled.connect(
            lambda checked, other=quester_box: other.setChecked(False)
            if checked
            else None
        )

    friend_icon_widget = QWidget()
    friend_icon_layout = QVBoxLayout(friend_icon_widget)
    friend_icon_layout.setContentsMargins(0, 0, 0, 0)
    friend_icon_layout.setSpacing(2)
    friend_icon_rows = {}
    friend_icon_combos = {}
    saved_friend_icons = current.get("quest_friend_icons", {}) or {}

    for option in live_client_options:
        identity = option["value"]
        aliases = {
            str(alias).strip().casefold()
            for alias in option.get("aliases", [identity])
        }
        saved_icon = next(
            (
                value
                for saved_identity, value in saved_friend_icons.items()
                if str(saved_identity).strip().casefold() in aliases
                and isinstance(value, dict)
            ),
            None,
        )

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(QLabel(option["label"]))
        icon_combo = _NoScrollComboBox()
        icon_combo.setMinimumWidth(120)
        icon_combo.addItem(tl("setting_quest_friend_icon_none"), None)
        for preset_name, preset_value in FRIEND_ICON_PRESETS.items():
            icon_combo.addItem(
                tl(f"setting_quest_friend_icon_{preset_name}"), preset_value
            )
        if saved_icon is not None:
            try:
                saved_value = (
                    int(saved_icon.get("icon_list")),
                    int(saved_icon.get("icon_index")),
                )
            except (TypeError, ValueError):
                saved_value = None
            saved_index = icon_combo.findData(saved_value)
            if saved_index >= 0:
                icon_combo.setCurrentIndex(saved_index)
        row_layout.addWidget(icon_combo, 1)
        friend_icon_layout.addWidget(row)
        friend_icon_rows[identity] = row
        friend_icon_combos[identity] = icon_combo

    friend_icon_note = QLabel(tl("setting_quest_friend_icon_note"))
    friend_icon_note.setWordWrap(True)
    friend_icon_note.setStyleSheet(
        "color: rgba(255,255,255,120); font-size: 10px;"
    )
    friend_icon_layout.addWidget(friend_icon_note)
    questing_form.addRow(tl("setting_quest_friend_icons"), friend_icon_widget)

    def _refresh_friend_icon_rows():
        any_visible = False
        for identity, row in friend_icon_rows.items():
            visible = quester_checks[identity].isChecked()
            row.setVisible(visible)
            any_visible = any_visible or visible
        friend_icon_note.setVisible(any_visible)
        friend_icon_widget.setVisible(any_visible)

    for box in quester_checks.values():
        box.toggled.connect(_refresh_friend_icon_rows)
    _refresh_friend_icon_rows()

    assignment_mode = _NoScrollComboBox()
    assignment_mode.addItem(tl("setting_quest_assignment_auto"), "auto")
    assignment_mode.addItem(tl("setting_quest_assignment_manual"), "manual")
    saved_mode = str(current.get("quest_hitter_assignment_mode", "auto"))
    assignment_mode.setCurrentIndex(max(0, assignment_mode.findData(saved_mode)))
    questing_form.addRow(tl("setting_quest_assignment_mode"), assignment_mode)
    widgets["quest_hitter_assignment_mode"] = assignment_mode

    manual_mapping_widget = QWidget()
    manual_mapping_layout = QVBoxLayout(manual_mapping_widget)
    manual_mapping_layout.setContentsMargins(0, 0, 0, 0)
    manual_mapping_layout.setSpacing(2)
    manual_assignment_rows = {}
    manual_assignment_combos = {}
    saved_assignments = current.get("quest_hitter_assignments", {}) or {}
    for option in live_client_options:
        identity = option["value"]
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(QLabel(f"{option['label']} →"))
        combo = _NoScrollComboBox()
        combo.setMinimumWidth(120)
        row_layout.addWidget(combo, 1)
        manual_mapping_layout.addWidget(row)
        manual_assignment_rows[identity] = row
        manual_assignment_combos[identity] = combo
    questing_form.addRow(tl("setting_quest_manual_assignments"), manual_mapping_widget)

    def _refresh_manual_assignments():
        manual = assignment_mode.currentData() == "manual"
        selected_questers = [
            option
            for option in live_client_options
            if quester_checks[option["value"]].isChecked()
        ]
        for hitter_id, combo in manual_assignment_combos.items():
            hitter_option = next(
                option
                for option in live_client_options
                if option["value"] == hitter_id
            )
            hitter_aliases = {
                str(alias).strip().casefold()
                for alias in hitter_option.get("aliases", [])
            }
            saved_target = next(
                (
                    target
                    for saved_hitter, target in saved_assignments.items()
                    if str(saved_hitter).strip().casefold() in hitter_aliases
                ),
                None,
            )
            previous = combo.currentData() or saved_target
            if previous:
                previous_key = str(previous).strip().casefold()
                previous = next(
                    (
                        option["value"]
                        for option in selected_questers
                        if previous_key
                        in {
                            str(alias).strip().casefold()
                            for alias in option.get("aliases", [])
                        }
                    ),
                    previous,
                )
            combo.blockSignals(True)
            combo.clear()
            for option in selected_questers:
                combo.addItem(option["label"], option["value"])
            if previous:
                index = combo.findData(previous)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)
            manual_assignment_rows[hitter_id].setVisible(
                manual and hitter_checks[hitter_id].isChecked()
            )
        manual_mapping_widget.setVisible(
            manual and any(box.isChecked() for box in hitter_checks.values())
        )

    assignment_mode.currentIndexChanged.connect(_refresh_manual_assignments)
    for box in quester_checks.values():
        box.toggled.connect(_refresh_manual_assignments)
    for box in hitter_checks.values():
        box.toggled.connect(_refresh_manual_assignments)
    _refresh_manual_assignments()

    _add_client_combo(questing_form, "client_to_boost", "setting_client_to_boost")
    _add_checkbox(questing_form, "friend_teleport", "setting_friend_teleport")
    _add_checkbox(
        questing_form, "gear_switching_in_solo_zones", "setting_gear_switching"
    )
    _add_client_combo(questing_form, "hitter_client", "setting_hitter_client")

    def _update_party_controls(enabled):
        quester_container.setEnabled(enabled)
        hitter_container.setEnabled(enabled)
        assignment_mode.setEnabled(enabled)
        manual_mapping_widget.setEnabled(enabled)
        friend_icon_widget.setEnabled(enabled)
        party_note.setEnabled(enabled)
        widgets["client_to_boost"].setEnabled(not enabled)
        widgets["hitter_client"].setEnabled(not enabled)

    widgets["quest_party_enabled"].toggled.connect(_update_party_controls)
    _update_party_controls(widgets["quest_party_enabled"].isChecked())

    layout.addWidget(questing_group)

    # ---- Auto Pet ----
    pet_group = QGroupBox(tl("settings_auto_pet"))
    pet_form = QFormLayout(pet_group)
    pet_form.setSpacing(4)

    _add_checkbox(pet_form, "ignore_pet_level_up", "setting_ignore_pet_level_up")
    _add_checkbox(pet_form, "only_play_dance_game", "setting_only_dance_game")

    layout.addWidget(pet_group)

    # ---- Combat ----
    combat_group = QGroupBox(tl("settings_combat"))
    combat_form = QFormLayout(combat_group)
    combat_form.setSpacing(4)

    _add_checkbox(combat_form, "kill_minions_first", "setting_kill_minions_first")
    _add_checkbox(
        combat_form, "automatic_team_based_combat", "setting_auto_team_combat"
    )
    _add_checkbox(combat_form, "discard_duplicate_cards", "setting_discard_duplicates")

    layout.addWidget(combat_group)

    # ---- Client ----
    client_group = QGroupBox(tl("settings_client"))
    client_form = QFormLayout(client_group)
    client_form.setSpacing(4)

    _add_checkbox(client_form, "client_resizing", "setting_client_resizing")

    layout.addWidget(client_group)

    # ---- Launcher ----
    launcher_group = QGroupBox(tl("settings_launcher"))
    launcher_form = QFormLayout(launcher_group)
    launcher_form.setSpacing(4)

    _add_checkbox(
        launcher_form, "remember_chosen_clients", "setting_remember_chosen_clients"
    )
    _add_checkbox(
        launcher_form, "verify_patch_files", "setting_verify_patch_files"
    )

    layout.addWidget(launcher_group)

    layout.addStretch()

    # ---- Bottom buttons ----
    restart_label = QLabel("* " + tl("settings_restart_note"))
    restart_label.setStyleSheet("color: orange; font-style: italic;")
    restart_label.setVisible(False)
    outer_layout.addWidget(restart_label)

    btn_row = QHBoxLayout()
    btn_row.addStretch()

    save_btn = QPushButton(tl("settings_save"))
    save_btn.setStyleSheet(ctx.btn_style)
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_row.addWidget(save_btn)

    cancel_btn = QPushButton(tl("settings_cancel"))
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_row.addWidget(cancel_btn)

    outer_layout.addLayout(btn_row)

    def _collect_values():
        values = {}
        for key, w in widgets.items():
            if isinstance(w, QCheckBox):
                values[key] = w.isChecked()
            elif isinstance(w, QDoubleSpinBox):
                values[key] = w.value()
            elif isinstance(w, QSpinBox):
                values[key] = w.value()
            elif isinstance(w, QComboBox):
                text = w.currentText()
                if key == "quest_hitter_assignment_mode":
                    values[key] = w.currentData()
                elif key in ("client_to_follow", "client_to_boost", "hitter_client"):
                    values[key] = None if text == "None" else text
                else:
                    values[key] = text
            elif isinstance(w, QLineEdit):
                values[key] = w.text()
        for key, checkboxes in multi_select_widgets.items():
            if checkboxes:
                visible_aliases = {
                    str(alias).strip().casefold()
                    for option in live_client_options
                    for alias in option.get("aliases", [])
                }
                unseen_saved = [
                    saved_value
                    for saved_value in current.get(key, []) or []
                    if str(saved_value).strip().casefold() not in visible_aliases
                ]
                values[key] = unseen_saved + [
                    title for title, checkbox in checkboxes.items() if checkbox.isChecked()
                ]
            else:
                # Opening settings before clients are injected must not erase a
                # previously saved party configuration.
                values[key] = list(current.get(key, DEFAULT_SETTINGS.get(key, [])) or [])
        visible_aliases = {
            str(alias).strip().casefold()
            for option in live_client_options
            for alias in option.get("aliases", [])
        }
        values["quest_hitter_assignments"] = {
            hitter_id: quester_id
            for hitter_id, quester_id in (
                current.get("quest_hitter_assignments", {}) or {}
            ).items()
            if str(hitter_id).strip().casefold() not in visible_aliases
        }
        values["quest_hitter_assignments"].update({
            hitter_id: combo.currentData()
            for hitter_id, combo in manual_assignment_combos.items()
            if hitter_checks[hitter_id].isChecked() and combo.currentData()
        })
        values["quest_friend_icons"] = {
            quester_id: icon
            for quester_id, icon in saved_friend_icons.items()
            if str(quester_id).strip().casefold() not in visible_aliases
        }
        values["quest_friend_icons"].update({
            quester_id: {
                "icon_list": preset[0],
                "icon_index": preset[1],
            }
            for quester_id, combo in friend_icon_combos.items()
            if quester_checks[quester_id].isChecked()
            and (preset := combo.currentData()) is not None
        })
        return values

    saved = [False]

    def _on_save():
        from src.gui.theme import apply_theme

        # Handle theme changes
        if theme_edits != original_theme:
            ctx.settings.set_theme(theme_edits)
            apply_theme(ctx, theme_edits)

        # Handle font changes
        new_font = font_edit.text()
        new_font_size = font_size_spin.value()
        old_font = current.get("font", "Segoe UI")
        old_font_size = current.get("font_size", 9)
        if new_font != old_font or new_font_size != old_font_size:
            ctx.gui_font = new_font
            ctx.gui_font_size = new_font_size
            from src.gui.theme import compute_styles

            styles = compute_styles(theme_edits, new_font, new_font_size)
            app = QApplication.instance()
            if app:
                app.setStyleSheet(styles["app_style"])

        # Handle non-theme settings
        new_values = _collect_values()
        changed = {}
        for key, new_val in new_values.items():
            old_val = current.get(key, DEFAULT_SETTINGS.get(key))
            if new_val != old_val:
                changed[key] = new_val

        if changed:
            ctx.settings.set_settings(changed)
            ctx.send_queue.put(GUICommand(GUICommandType.UpdateSettings, changed))

        saved[0] = True

        if changed.keys() & RESTART_REQUIRED_KEYS:
            restart_label.setVisible(True)
            save_btn.setEnabled(False)
            return

        dialog.accept()

    def _on_cancel():
        if not saved[0] and theme_edits != original_theme:
            from src.gui.theme import apply_theme

            apply_theme(ctx, original_theme)
            ctx.settings.set_theme(original_theme)
        dialog.reject()

    save_btn.clicked.connect(_on_save)
    cancel_btn.clicked.connect(_on_cancel)

    dialog.exec()
