from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QSizePolicy,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.gui.commands import GUICommand, GUICommandType, GUIKeys
from src.gui.tab_actions import _build_client_toolbar
from src.gui.widgets import ThemedCheckBox


_SCHOOLS = ["Any", "Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance"]


def _icon_toggle_style(ctx):
    return (
        "QPushButton {"
        "  spacing: 10px;"
        "  padding: 7px 12px;"
        "  border: 2px solid transparent;"
        "  border-radius: 7px;"
        "  background-color: transparent;"
        "}"
        "QPushButton:hover {"
        f"  color: {ctx.stroke_color};"
        "}"
        "QPushButton:checked {"
        f"  border: 2px solid {ctx.stroke_color};"
        "}"
    )


def _chest_svg(stroke):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
        viewBox="0 0 24 24" fill="none" stroke="{stroke}" stroke-width="1.8"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M5 9.5 6.5 5h11L19 9.5"/>
        <rect x="3" y="9.5" width="18" height="10.5" rx="2"/>
        <path d="M3 13h18M12 9.5V20M9.5 14.5h5"/>
    </svg>'''


def _fish_svg(stroke):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
        viewBox="0 0 24 24" fill="none" stroke="{stroke}" stroke-width="1.8"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M6.5 12c1.1-2.7 3.7-4.5 6.7-4.5 3.2 0 6 1.8 7.3 4.5-1.3 2.7-4.1 4.5-7.3 4.5-3 0-5.6-1.8-6.7-4.5Z"/>
        <path d="m6.5 12-3.5-4v8l3.5-4ZM10.5 8.1 9 5.5M10.5 15.9 9 18.5"/>
        <circle cx="16.5" cy="11" r=".7" fill="{stroke}" stroke="none"/>
    </svg>'''


def _configure_icon_toggle(ctx, checkbox, svg):
    checkbox.setIcon(ctx.titlebar_svg_icon(svg, 26))
    checkbox.setIconSize(QSize(26, 26))
    checkbox.setStyleSheet(_icon_toggle_style(ctx))
    ctx.tracked_svg_labels.append([checkbox, svg, 26, "icon"])


def _rgba(color_value, alpha):
    color = QColor(str(color_value))
    if not color.isValid():
        color = QColor("#ffffff")
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha})"


def _filter_field_style(ctx):
    field_bg = _rgba(ctx.text_color, 12)
    return (
        "QComboBox, QSpinBox, QDoubleSpinBox {"
        f"  background-color: {field_bg};"
        "}"
    )


def build_fishing_tab(ctx):
    tab = QWidget()
    outer = QVBoxLayout(tab)
    outer.setContentsMargins(8, 8, 8, 8)
    outer.setSpacing(6)

    current = ctx.settings.get_settings()
    tl = ctx.tl

    toolbar, target_flow, all_clients, running_label = _build_client_toolbar(
        ctx, None, None, with_status=True)
    checks = {}
    active_groups = []
    updating = [False]
    initialized_clients = [False]
    target_flow.removeWidget(running_label)
    target_flow.setAlignment(Qt.AlignmentFlag.AlignLeft)
    toolbar.setObjectName("FishingClientSelector")
    running_label.setObjectName("FishingRunningGroups")
    running_label.setWordWrap(True)
    running_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    running_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    running_label.show()

    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(4)
    outer.addWidget(content)
    outer.addStretch()

    chest_group = QGroupBox(tl("fish_chest_filter"))
    filters_row = QHBoxLayout()
    filters_row.setSpacing(8)

    chest_layout = QVBoxLayout(chest_group)
    chest_layout.setContentsMargins(10, 10, 10, 6)
    chest_only = QPushButton(tl("fish_mode_chest"))
    chest_only.setCheckable(True)
    chest_only.setChecked(bool(current.get("fish_chest_only", False)))
    chest_only.setCursor(Qt.CursorShape.PointingHandCursor)
    chest_only.setMinimumSize(160, 36)
    _configure_icon_toggle(ctx, chest_only, _chest_svg(ctx.stroke_color))
    chest_layout.addWidget(chest_only)
    chest_hint = QLabel(tl("fish_chest_hint"))
    chest_hint.setWordWrap(True)
    chest_hint.setStyleSheet(f"color: {ctx.text_color}; font-style: italic;")
    chest_layout.addWidget(chest_hint)
    chest_layout.addStretch()
    filters_row.addWidget(chest_group, 1)

    fish_group = QGroupBox(tl("fish_specific_filter"))
    fish_group.setStyleSheet(
        "QGroupBox {"
        f"  border: 2px solid {ctx.stroke_color};"
        "  border-radius: 7px;"
        "  margin-top: 10px;"
        "  padding-top: 8px;"
        "}"
        "QGroupBox::title {"
        "  subcontrol-origin: margin;"
        "  subcontrol-position: top left;"
        "  left: 10px;"
        "  padding: 0 6px;"
        f"  color: {ctx.text_color};"
        f"  background-color: {ctx.bg_color};"
        "  font-weight: bold;"
        "}"
    )
    form = QGridLayout(fish_group)
    form.setContentsMargins(10, 18, 10, 8)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(5)

    school = QComboBox()
    for school_name in _SCHOOLS:
        school.addItem(tl("fish_any") if school_name == "Any" else school_name, school_name)
    selected_school = str(current.get("fish_school", "Any"))
    school_index = school.findData(selected_school)
    school.setCurrentIndex(max(0, school_index))
    form.addWidget(QLabel(tl("fish_school")), 0, 0)
    form.addWidget(school, 0, 1)

    rank = QSpinBox()
    rank.setRange(0, 99)
    rank.setSpecialValueText(tl("fish_any"))
    rank.setValue(int(current.get("fish_rank", 0)))
    form.addWidget(QLabel(tl("fish_rank")), 0, 2)
    form.addWidget(rank, 0, 3)

    fish_id = QSpinBox()
    fish_id.setRange(0, 2_147_483_647)
    fish_id.setSpecialValueText(tl("fish_any"))
    fish_id.setValue(int(current.get("fish_id", 0)))
    fish_id.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    fish_id.setMinimumWidth(190)
    form.addWidget(QLabel(tl("fish_template_id")), 1, 0)
    form.addWidget(fish_id, 1, 1)

    size_min = QDoubleSpinBox()
    size_min.setRange(0, 999)
    size_min.setDecimals(2)
    size_min.setValue(float(current.get("fish_size_min", 0)))
    form.addWidget(QLabel(tl("fish_size_min")), 1, 2)
    form.addWidget(size_min, 1, 3)

    size_max = QDoubleSpinBox()
    size_max.setRange(0, 999)
    size_max.setDecimals(2)
    size_max.setValue(float(current.get("fish_size_max", 999)))
    form.addWidget(QLabel(tl("fish_size_max")), 2, 0)
    form.addWidget(size_max, 2, 1)

    reset = QPushButton()
    reset.setObjectName("ResetFishFilters")
    reset.setAccessibleName(tl("fish_reset_filters"))
    reset.setToolTip(tl("fish_reset_filters"))
    reset.setCursor(Qt.CursorShape.PointingHandCursor)
    reset.setFixedSize(32, 32)
    reset_svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{ctx.stroke_color}" stroke-width="1.8"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/>
    </svg>'''
    _configure_icon_toggle(ctx, reset, reset_svg)
    reset.setIconSize(QSize(22, 22))
    reset.setStyleSheet(
        "QPushButton { padding: 3px; border: none; border-radius: 5px; background: transparent; }"
        f"QPushButton:hover {{ background: {_rgba(ctx.stroke_color, 28)}; }}"
    )

    def _reset_filters():
        school.setCurrentIndex(0)
        rank.setValue(0)
        fish_id.setValue(0)
        size_min.setValue(0)
        size_max.setValue(999)

    reset.clicked.connect(_reset_filters)
    form.addWidget(reset, 2, 3, alignment=Qt.AlignmentFlag.AlignRight)

    field_style = _filter_field_style(ctx)
    for field in (school, rank, fish_id, size_min, size_max):
        field.setStyleSheet(field_style)
        field.setMinimumHeight(max(32, field.sizeHint().height()))

    form.setColumnStretch(1, 1)
    form.setColumnStretch(3, 1)

    fish_group.setMinimumHeight(form.sizeHint().height() + 20)
    filters_row.addWidget(fish_group, 2)
    content_layout.addLayout(filters_row)

    hint = QLabel(tl("fish_start_hint"))
    hint.setWordWrap(True)
    hint.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
    hint.setStyleSheet(f"color: {ctx.text_color}; font-style: italic;")
    content_layout.addWidget(hint)

    toggle_row = QHBoxLayout()
    toggle_row.setContentsMargins(0, 2, 0, 0)
    toggle_row.setSpacing(8)
    toggle_row.addWidget(toolbar, 1, Qt.AlignmentFlag.AlignVCenter)
    toggle = QPushButton()
    toggle.setObjectName("ToggleFishingGroup")
    toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    toggle.setFixedSize(40, 40)
    toggle.setIconSize(QSize(32, 32))
    toggle.setStyleSheet(ctx.icon_btn_style)
    toggle_row.addWidget(toggle)
    toggle_row.addWidget(running_label, 1)
    outer.addLayout(toggle_row)

    ctx.widget_tags["Auto FishStatus"] = toggle

    fish_filter_widgets = [school, rank, fish_id, size_min, size_max]

    def selected_clients():
        return [title for title, check in checks.items() if check.isChecked()]

    def _values():
        return {
            "fish_chest_only": chest_only.isChecked(),
            "fish_school": str(school.currentData()),
            "fish_rank": rank.value(), "fish_id": fish_id.value(),
            "fish_size_min": size_min.value(),
            "fish_size_max": max(size_min.value(), size_max.value()),
        }

    def _sync_filter_state():
        if updating[0]:
            return
        selected = set(selected_clients())
        overlaps = [g for g in active_groups if selected.intersection(g["clients"])]
        running = bool(overlaps)
        toggle.setIcon(ctx.titlebar_svg_icon(_fish_svg(ctx.stroke_color), 32))
        description = tl("fish_start_selected")
        toggle.setToolTip(description)
        toggle.setAccessibleName(description)
        toggle.setEnabled(bool(selected) and not running)
        chest_only.setEnabled(not running)
        fish_group.setEnabled(not running and not chest_only.isChecked())
        stop_selected.setEnabled(running)

    def _selection_changed():
        if updating[0]:
            return
        updating[0] = True
        all_clients.setChecked(bool(checks) and all(c.isChecked() for c in checks.values()))
        # Selecting exactly one running group displays its actual configuration.
        selected = set(selected_clients())
        group = next((g for g in active_groups if set(g['clients']) == selected), None)
        if group:
            values = group['settings']
            chest_only.setChecked(bool(values.get('fish_chest_only', False)))
            school.setCurrentIndex(max(0, school.findData(values.get('fish_school', 'Any'))))
            rank.setValue(values.get('fish_rank', 0))
            fish_id.setValue(values.get('fish_id', 0))
            size_min.setValue(values.get('fish_size_min', 0))
            size_max.setValue(values.get('fish_size_max', 999))
        updating[0] = False
        _sync_filter_state()

    def _toggle_all(checked):
        if updating[0]:
            return
        updating[0] = True
        for check in checks.values():
            check.setChecked(checked)
        updating[0] = False
        _selection_changed()

    stop_selected = QPushButton()
    stop_selected.setObjectName("StopFishingGroup")
    stop_selected.setFixedSize(40, 40)
    stop_selected.setIconSize(QSize(32, 32))
    stop_selected.setCursor(Qt.CursorShape.PointingHandCursor)
    stop_selected.setStyleSheet(ctx.icon_btn_style)
    stop_selected.setToolTip(tl("fish_stop_selected"))
    stop_selected.setAccessibleName(tl("fish_stop_selected"))
    stop_selected_svg = ctx.svgs['kill']
    stop_selected.setIcon(ctx.titlebar_svg_icon(stop_selected_svg, 32))
    ctx.tracked_svg_labels.append([stop_selected, stop_selected_svg, 32, "icon"])
    toggle_row.insertWidget(toggle_row.count() - 1, stop_selected)


    def _start_fishing(_checked=False):
        selected = selected_clients()
        if not selected or any(set(selected).intersection(g['clients']) for g in active_groups):
            return
        values = _values()
        size_max.setValue(values['fish_size_max'])
        ctx.settings.set_settings(values)
        ctx.send_queue.put(GUICommand(GUICommandType.StartFishingGroup,
                                     {"clients": selected, "settings": values}))
        _sync_filter_state()

    def _stop_fishing(_checked=False):
        selected = selected_clients()
        if selected and any(set(selected).intersection(g['clients']) for g in active_groups):
            ctx.send_queue.put(GUICommand(GUICommandType.StopFishingGroup, {"clients": selected}))
        _sync_filter_state()

    def _toggle_fishing():
        # Preserve the keyboard shortcut while keeping the two UI actions fixed.
        if any(set(selected_clients()).intersection(g['clients']) for g in active_groups):
            _stop_fishing()
        else:
            _start_fishing()

    def set_available_clients(titles):
        titles = sorted(dict.fromkeys(t for t in titles if t),
                        key=lambda t: (0, int(t[1:])) if t.lower().startswith('p') and t[1:].isdigit() else (1, t))
        previous = {t: c.isChecked() for t, c in checks.items()}
        select_all = (not initialized_clients[0] and bool(titles)) or all_clients.isChecked()
        updating[0] = True
        for check in checks.values():
            target_flow.removeWidget(check)
            check.setParent(None)
            check.deleteLater()
        checks.clear()
        for title in titles:
            check = ThemedCheckBox(title, ctx.stroke_color, ctx.text_color, ctx.alt_bg)
            check.setChecked(select_all or previous.get(title, False))
            check.toggled.connect(_selection_changed)
            checks[title] = check
            target_flow.addWidget(check)
        all_clients.setEnabled(bool(titles))
        initialized_clients[0] = bool(titles)
        updating[0] = False
        _selection_changed()

    def set_running_groups(groups):
        active_groups[:] = groups or []
        display = '、'.join('+'.join(g['clients']) for g in active_groups)
        running_label.setText(tl('bot_running_groups').replace('{groups}', display) if display else '')
        running_label.setToolTip(running_label.text())
        _sync_filter_state()

    def retheme():
        for check in (all_clients, *checks.values()):
            check.set_theme_colors(ctx.stroke_color, ctx.text_color, ctx.alt_bg)
        running_label.setStyleSheet(f"color: {ctx.stroke_color};")

        toggle.setStyleSheet(ctx.icon_btn_style)
        stop_selected.setStyleSheet(ctx.icon_btn_style)
        _sync_filter_state()

    chest_only.toggled.connect(lambda _: _sync_filter_state())
    all_clients.toggled.connect(_toggle_all)
    all_clients.setEnabled(False)
    toggle.clicked.connect(_start_fishing)
    stop_selected.clicked.connect(_stop_fishing)
    _sync_filter_state()
    ctx.exports["fishing"] = {
        "retheme": retheme,
        "set_running": lambda _: _sync_filter_state(),
        "set_available_clients": set_available_clients,
        "set_running_groups": set_running_groups,
        "toggle_selected": lambda: _toggle_fishing(),
    }
    return tab
