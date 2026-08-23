from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
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

    chest_group = QGroupBox(tl("fish_chest_filter"))
    filters_row = QHBoxLayout()
    filters_row.setSpacing(8)

    chest_layout = QVBoxLayout(chest_group)
    chest_layout.setContentsMargins(10, 18, 10, 8)
    chest_only = QPushButton(tl("fish_mode_chest"))
    chest_only.setCheckable(True)
    chest_only.setChecked(bool(current.get("fish_chest_only", False)))
    chest_only.setCursor(Qt.CursorShape.PointingHandCursor)
    chest_only.setMinimumSize(180, 46)
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

    field_style = _filter_field_style(ctx)
    for field in (school, rank, fish_id, size_min, size_max):
        field.setStyleSheet(field_style)

    form.setColumnStretch(1, 1)
    form.setColumnStretch(3, 1)

    filters_row.addWidget(fish_group, 2)
    outer.addLayout(filters_row)

    hint = QLabel(tl("fish_start_hint"))
    hint.setWordWrap(True)
    hint.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
    hint.setStyleSheet(f"color: {ctx.text_color}; font-style: italic;")
    outer.addWidget(hint)

    toggle_row = QHBoxLayout()
    toggle_row.setContentsMargins(0, 12, 0, 0)
    toggle_row.addStretch()
    toggle = QPushButton(tl("auto_fish"))
    toggle.setCheckable(True)
    toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    toggle.setMinimumSize(180, 48)
    _configure_icon_toggle(ctx, toggle, _fish_svg(ctx.stroke_color))
    toggle_row.addWidget(toggle)
    toggle_row.addStretch()
    outer.addLayout(toggle_row)
    outer.addStretch()

    ctx.widget_tags["Auto FishStatus"] = toggle

    fish_filter_widgets = [school, rank, fish_id, size_min, size_max]
    is_running = [False]

    def _sync_filter_state():
        chest_only.setEnabled(not is_running[0])
        fish_filters_enabled = not is_running[0] and not chest_only.isChecked()
        fish_group.setEnabled(fish_filters_enabled)
        for widget in fish_filter_widgets:
            widget.setEnabled(fish_filters_enabled)

    chest_only.toggled.connect(lambda _checked: _sync_filter_state())
    _sync_filter_state()

    def _toggle_fishing(checked: bool):
        values = {
            "fish_chest_only": chest_only.isChecked(),
            "fish_school": str(school.currentData()),
            "fish_rank": rank.value(),
            "fish_id": fish_id.value(),
            "fish_size_min": size_min.value(),
            "fish_size_max": size_max.value(),
        }

        if values["fish_size_max"] < values["fish_size_min"]:
            values["fish_size_max"] = values["fish_size_min"]
            size_max.setValue(values["fish_size_max"])

        ctx.settings.set_settings(values)
        ctx.send_queue.put(GUICommand(GUICommandType.UpdateSettings, values))
        ctx.send_queue.put(GUICommand(GUICommandType.ToggleOption, GUIKeys.toggle_auto_fish))
        is_running[0] = checked
        _sync_filter_state()

    toggle.clicked.connect(_toggle_fishing)

    def _set_running(running_state: bool):
        toggle.setChecked(running_state)
        is_running[0] = bool(running_state)
        _sync_filter_state()

    ctx.exports["fishing"] = {"set_running": _set_running}

    return tab
