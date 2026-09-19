"""Hidden ibao-ST group control panel."""
from time import monotonic
import ctypes
import ctypes.wintypes
import json
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPainterPath, QRegion
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
                            QDoubleSpinBox, QPlainTextEdit, QPushButton, QWidget,
                            QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
                            QFileDialog)
from src.gui.widgets import FlowLayout, ThemedCheckBox
from src.gui.commands import GUICommand, GUICommandType
from src.ibao_locations import DEFAULT_LOCATIONS, expand_location_aliases
from src.ibao_runtime import parse_locations


class LocationTable(QTableWidget):
    def __init__(self):
        super().__init__(0, 3)
        self.setHorizontalHeaderLabels(['地点名称 / 别名', '类型', '坐标（多组用 : 分隔）'])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 120)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(36)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setPlainText(DEFAULT_LOCATIONS)

    def add_row(self, aliases='', kind='Location', points=''):
        row = self.rowCount()
        self.insertRow(row)
        self.setItem(row, 0, QTableWidgetItem(aliases))
        choices = QComboBox()
        choices.addItems(['Location', 'Dungeon'])
        choices.setCurrentText(kind)
        self.setCellWidget(row, 1, choices)
        self.setItem(row, 2, QTableWidgetItem(points))

    def setPlainText(self, text):
        self.setRowCount(0)
        for line in text.splitlines():
            if line.strip():
                self.add_row(*line.split('|'))

    def toPlainText(self):
        return '\n'.join('|'.join([self.item(row, 0).text(),
            self.cellWidget(row, 1).currentText(), self.item(row, 2).text()])
            for row in range(self.rowCount()))


class TripleClickGate:
    def __init__(self):
        self.clicks = []

    def click(self, matches, now=None):
        now = monotonic() if now is None else now
        if not matches:
            self.clicks.clear()
            return False
        self.clicks = [t for t in self.clicks if now - t <= 1.2]
        self.clicks.append(now)
        if len(self.clicks) == 3:
            self.clicks.clear()
            return True
        return False


class RoundedIbaoDialog(QDialog):
    def showEvent(self, event):
        super().showEvent(event)
        try:
            preference = ctypes.c_int(2)
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.wintypes.HWND(int(self.winId())), 33,
                ctypes.byref(preference), ctypes.sizeof(preference))
            self._native_rounding = result == 0
        except (AttributeError, OSError):
            self._native_rounding = False
        self._update_corners()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_corners()

    def _update_corners(self):
        if getattr(self, '_native_rounding', False):
            self.clearMask()
        else:
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width(), self.height(), 8, 8)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))


def build_ibao_dialog(ctx):
    dialog = RoundedIbaoDialog()
    dialog.setObjectName('IbaoDialog')
    dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowMinimizeButtonHint)
    dialog.setSizeGripEnabled(True)
    dialog.setWindowTitle('玄枢 · 挖矿转换生产线')
    dialog.resize(660, 470)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(12, 8, 12, 10)
    layout.setSpacing(8)
    titlebar = QWidget()
    title_row = QHBoxLayout(titlebar)
    title_row.setContentsMargins(0, 0, 0, 0)
    title = QLabel('玄枢 · 挖矿转换生产线')
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    close = QPushButton()
    close.setFixedSize(28, 26)
    close.setToolTip('关闭窗口（采集继续运行）')
    close.clicked.connect(dialog.close)
    title_row.addSpacing(28)
    title_row.addWidget(title, 1)
    minimize = QPushButton('−')
    minimize.setFixedSize(28, 26)
    minimize.setToolTip('最小化生产线（采集继续运行）')
    minimize.clicked.connect(dialog.showMinimized)
    title_row.addWidget(minimize)
    title_row.addWidget(close)
    def drag(event):
        if event.button() == Qt.MouseButton.LeftButton and dialog.windowHandle():
            dialog.windowHandle().startSystemMove()
    titlebar.mousePressEvent = drag
    title.mousePressEvent = drag
    layout.addWidget(titlebar)
    note = QLabel('使用已启动的客户端。启动后会返回角色选择界面，按地点配置轮换采集。')
    note.setWordWrap(True)
    layout.addWidget(note)
    host = QWidget()
    flow = FlowLayout(host)
    client_label = QLabel('客户端')
    client_label.setStyleSheet('color: #a6a6b0; font-weight: normal;')
    flow.addWidget(client_label)
    layout.addWidget(host)
    checks = {}
    running = set()
    accounts = {}
    saved_configs = {}
    last_rows = []
    settings_row = QHBoxLayout()
    pages = ThemedCheckBox('启用第七角色翻页', ctx.stroke_color, ctx.text_color, ctx.alt_bg)
    delay = QDoubleSpinBox()
    delay.setRange(.1, 10)
    delay.setSingleStep(.1)
    delay.setValue(.3)
    settings_row.addWidget(pages)
    settings_row.addWidget(QLabel('角色切换间隔（秒）'))
    settings_row.addWidget(delay)
    layout.addLayout(settings_row)
    location_title = QLabel('采集地点')
    location_title.setToolTip('地点别名|Location 或 Dungeon|坐标')
    location_row = QHBoxLayout()
    location_row.addWidget(location_title)
    info = QPushButton()
    info.setFixedSize(26, 26)
    info.setToolTip('查看和编辑采集地点')
    info.setAccessibleName('编辑采集地点')
    location_row.addWidget(info)
    location_row.addStretch()
    layout.addLayout(location_row)
    location_dialog = RoundedIbaoDialog(dialog)
    location_dialog.setObjectName('IbaoLocations')
    location_dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    location_dialog.resize(780, 440)
    location_layout = QVBoxLayout(location_dialog)
    location_header = QHBoxLayout()
    location_header.addWidget(QLabel('采集地点配置'), 1)
    location_close = QPushButton('关闭')
    location_close.clicked.connect(location_dialog.close)
    location_header.addWidget(location_close)
    location_layout.addLayout(location_header)
    location_hint = QLabel('名称可用 : 分隔别名；坐标为 X,Y,Z，多组用 : 分隔。\n修改仅在下次启动时生效。')
    location_hint.setWordWrap(True)
    location_layout.addWidget(location_hint)
    locations = LocationTable()
    location_layout.addWidget(locations, 1)
    table_actions = QHBoxLayout()
    for label, callback in (
        ('添加', lambda: locations.add_row()),
        ('删除所选行', lambda: [locations.removeRow(r) for r in sorted({i.row() for i in locations.selectedIndexes()}, reverse=True)]),
        ('恢复默认', lambda: locations.setPlainText(DEFAULT_LOCATIONS))):
        button = QPushButton(label)
        button.clicked.connect(callback)
        table_actions.addWidget(button)
    location_layout.addLayout(table_actions)
    def validate_locations():
        try:
            parse_locations(locations.toPlainText())
        except (ValueError, TypeError) as exc:
            location_hint.setText('配置错误：' + str(exc))
            return False
        location_hint.setText('配置有效；启动时按关联账号保存，下次可载入。')
        return True
    validate_button = QPushButton('检查配置')
    validate_button.clicked.connect(validate_locations)
    table_actions.addWidget(validate_button)
    save_config = QPushButton('保存到所选账号')
    def save_to_account():
        if not selected():
            location_hint.setText('请先在生产线主窗口勾选客户端')
            return
        if validate_locations():
            ctx.send_queue.put(GUICommand(GUICommandType.SaveIbaoConfig,
                {'clients': selected(), 'settings': {'page_turning': pages.isChecked(),
                 'switch_delay': delay.value(), 'locations': locations.toPlainText()}}))
    save_config.clicked.connect(save_to_account)
    table_actions.addWidget(save_config)
    def show_locations():
        location_dialog.show()
        location_dialog.raise_()
        location_dialog.activateWindow()
    info.clicked.connect(show_locations)
    collection_title = QLabel('本轮 0 · 历史 0')
    summary_row = QHBoxLayout()
    summary_row.addWidget(collection_title)
    summary_row.addStretch()
    status = QLabel('未运行')
    status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    status.setWordWrap(True)
    summary_row.addWidget(status, 1)
    layout.addLayout(summary_row)
    collection = QPlainTextEdit()
    collection.setReadOnly(True)
    collection.setPlaceholderText('启动后，这里显示各客户端的采集数量与运行状态。')
    layout.addWidget(collection, 1)
    tools_row = QHBoxLayout()
    load_saved = QPushButton('载入所选账号配置')
    def load_config():
        names = selected()
        if len(names) != 1:
            status.setText('请只勾选一个客户端以载入配置')
            return
        config = saved_configs.get(accounts.get(names[0]))
        if not config:
            status.setText('该账号暂无保存配置')
            return
        pages.setChecked(config.get('page_turning', False))
        delay.setValue(config.get('switch_delay', .3))
        locations.setPlainText(expand_location_aliases(config.get('locations', DEFAULT_LOCATIONS)))
        status.setText('已载入账号配置，下次启动生效')
    load_saved.clicked.connect(load_config)
    clear = QPushButton('清空本轮统计')
    clear.clicked.connect(lambda: ctx.send_queue.put(GUICommand(GUICommandType.ClearIbaoRound)))
    export = QPushButton('导出记录')
    def export_records():
        path, _ = QFileDialog.getSaveFileName(dialog, '导出采集记录', '生产线记录.json', 'JSON (*.json)')
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as stream:
                    json.dump(last_rows, stream, ensure_ascii=False, indent=2)
                status.setText('记录已导出')
            except OSError as exc:
                status.setText('导出失败：' + str(exc))
    export.clicked.connect(export_records)
    for button in (load_saved, clear, export):
        tools_row.addWidget(button)
    layout.addLayout(tools_row)
    actions = QHBoxLayout()
    start = QPushButton()
    stop = QPushButton()
    for button, label in ((start, '启动所选'), (stop, '停止所选')):
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFixedSize(40, 36)
        button.setIconSize(QSize(24, 24))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
    actions.addStretch()
    actions.addWidget(start)
    actions.addWidget(stop)
    actions.addStretch()
    layout.addLayout(actions)
    def selected():
        return [name for name, check in checks.items() if check.isChecked()]
    def launch():
        if not selected():
            status.setText('请先选择客户端')
            return
        if not validate_locations():
            status.setText(location_hint.text())
            return
        ctx.send_queue.put(GUICommand(GUICommandType.StartIbaoGroup,
            {'clients': selected(), 'settings': {'page_turning': pages.isChecked(),
             'switch_delay': delay.value(), 'locations': locations.toPlainText()}}))
        ctx.send_queue.put(GUICommand(GUICommandType.GetIbaoData))
    start.clicked.connect(launch)
    stop.clicked.connect(lambda: ctx.send_queue.put(GUICommand(
        GUICommandType.StopIbaoGroup, {'clients': selected()})))
    def set_clients(titles):
        nonlocal accounts
        accounts = {v['title']: v.get('account_nick') for v in titles if isinstance(v, dict)}
        titles = [v['title'] if isinstance(v, dict) else v for v in titles]
        previous = set(selected())
        for check in checks.values():
            flow.removeWidget(check)
            check.setParent(None)
            check.deleteLater()
        checks.clear()
        def key(t):
            return (0, int(t[1:])) if t.lower().startswith('p') and t[1:].isdigit() else (1, t)
        for title in sorted(set(titles), key=key):
            if not title:
                continue
            check = ThemedCheckBox(title, ctx.stroke_color, ctx.text_color, ctx.alt_bg)
            check.setChecked(title in previous or title in running)
            check.toggled.connect(lambda checked, name=title: (
                ctx.send_queue.put(GUICommand(GUICommandType.StopIbaoGroup, {'clients': [name]}))
                if not checked else None))
            checks[title] = check
            flow.addWidget(check)
    def set_groups(groups):
        nonlocal last_rows, running
        last_rows = groups
        active = [g for g in groups if g.get('state') in ('采集中', '正在重启')]
        running = {name for g in active for name in g['clients']}
        for name in running:
            if name in checks:
                checks[name].blockSignals(True)
                checks[name].setChecked(True)
                checks[name].blockSignals(False)
        status.setText('运行中：' + '、'.join('+'.join(g['clients']) for g in active) if active else '未运行')
        collection_title.setText(f"本轮 {sum(g.get('collected', 0) for g in groups if not g.get('archived'))} · 历史 {sum(g.get('collected', 0) for g in groups if g.get('archived'))}")
        collection.setPlainText('\n\n'.join(
            f"{'、'.join(g['clients'])}  {g.get('account', '')}    {g.get('state', '采集中')}\n"
            f"万灵秘药：{g.get('collected', 0)}   运行 {g.get('elapsed', 0)/60:.1f} 分钟   "
            f"每小时 {g.get('collected', 0)*3600/max(1, g.get('elapsed', 0)):.1f}   重启 {g.get('restarts', 0)} 次\n"
            f"开始：{g.get('started_at', '')}"
            for g in groups if not g.get('archived')))
    def retheme():
        dialog.setStyleSheet(
            f"QDialog#IbaoDialog {{ background: {ctx.bg_color}; border: 1px solid {ctx.stroke_color}; border-radius: 8px; }}")
        title.setStyleSheet(f'color: {ctx.text_color}; font-weight: bold;')
        note.setStyleSheet(f'color: {ctx.text_color}; font-weight: normal;')
        location_title.setStyleSheet(f'color: {ctx.text_color}; font-weight: bold;')
        status.setStyleSheet(f'color: {ctx.stroke_color};')
        locations.setStyleSheet(
            f'QTableWidget {{ background: {ctx.alt_bg}; color: {ctx.text_color}; border: none; padding: 0; }}'
            f'QTableWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {ctx.bg_color}; }}'
            f'QTableWidget::item:selected {{ background: {ctx.bg_color}; color: {ctx.stroke_color}; }}')
        locations.horizontalHeader().setStyleSheet(f'QHeaderView::section {{ background: {ctx.bg_color}; color: {ctx.text_color}; padding: 6px; border: none; }}')
        collection.setStyleSheet(f'background: {ctx.alt_bg}; color: {ctx.text_color}; border: none; padding: 8px;')
        collection_title.setStyleSheet(f'color: {ctx.stroke_color}; font-weight: bold;')
        location_dialog.setStyleSheet(f'QDialog#IbaoLocations {{ background: {ctx.bg_color}; color: {ctx.text_color}; border: 1px solid {ctx.stroke_color}; border-radius: 8px; }} QLabel {{ color: {ctx.text_color}; }}')
        location_close.setStyleSheet(ctx.icon_btn_style)
        for button in location_dialog.findChildren(QPushButton) + [load_saved, clear, export]:
            button.setStyleSheet(ctx.icon_btn_style)
        info.setStyleSheet(ctx.icon_btn_style)
        info.setIcon(ctx.titlebar_svg_icon(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{ctx.stroke_color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m16 3 5 5-12 12-6 1 1-6Z M13 6l5 5 M4 15l5 5"/></svg>', 22))
        for check in (pages, *checks.values()):
            check.set_theme_colors(ctx.stroke_color, ctx.text_color, ctx.alt_bg)
        for button, icon in ((start, 'play'), (stop, 'kill')):
            button.setStyleSheet(ctx.icon_btn_style)
            button.setIcon(ctx.titlebar_svg_icon(ctx.svgs[icon], 24))
        close.setStyleSheet(ctx.icon_btn_style)
        minimize.setStyleSheet(ctx.icon_btn_style)
        close.setIcon(ctx.titlebar_svg_icon(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{ctx.stroke_color}" stroke-width="2"><path d="M6 6L18 18M6 18L18 6"/></svg>', 18))
    ctx.exports['ibao'] = {'set_available_clients': set_clients, 'set_running_groups': set_groups,
                           'set_saved_configs': lambda value: saved_configs.update(value),
                           'show_error': status.setText, 'retheme': retheme}
    ctx.send_queue.put(GUICommand(GUICommandType.GetIbaoData))
    retheme()
    return dialog
