"""Read-only status presentation, keyed by client identity rather than position."""

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHeaderView, QStyle, QStyleOptionHeader,
    QTableView, QTableWidget, QTableWidgetItem,
)

from src.gui.theme import blend_color


class _ClientHeader(QHeaderView):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setSectionsClickable(False)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.secondary_color = QColor()

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(size.width(), self.fontMetrics().height() * 2 + 12)

    def paintSection(self, painter, rect, section):
        if not rect.isValid():
            return
        painter.save()
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect, option.section, option.text = rect, section, ''
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)
        text = self.model().headerData(section, Qt.Orientation.Horizontal) or ''
        lines = str(text).split('\n', 1)
        area = rect.adjusted(8, 0, -8, 0)
        metrics = self.fontMetrics()
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        if section == 0 or len(lines) == 1:
            alignment = Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignLeft if section == 0 else Qt.AlignmentFlag.AlignHCenter)
            painter.drawText(area, alignment, metrics.elidedText(lines[0], Qt.TextElideMode.ElideRight, area.width()))
        else:
            line_height = metrics.height()
            top = rect.top() + (rect.height() - line_height * 2) // 2
            first = QRect(area.left(), top, area.width(), line_height)
            second = first.translated(0, line_height)
            painter.drawText(first, Qt.AlignmentFlag.AlignCenter,
                             metrics.elidedText(lines[0], Qt.TextElideMode.ElideRight, area.width()))
            painter.setPen(self.secondary_color)
            painter.drawText(second, Qt.AlignmentFlag.AlignCenter, lines[1])
        painter.restore()


class StatusOverviewTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('statusOverviewTable')
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalHeader(_ClientHeader(self))
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self._theme = None
        self._session = False
        self._known = {}
        self._sizing = False
        self._feature_width = 125

        # Qt's frozen-column pattern: a second view of the SAME model. It cannot
        # toggle or edit states, and it never maintains another copy of the data.
        self.frozen_column = QTableView(self)
        self.frozen_column.setObjectName('statusOverviewFeatures')
        self.frozen_column.setModel(self.model())
        self.frozen_column.setHorizontalHeader(_ClientHeader(self.frozen_column))
        self.frozen_column.verticalHeader().hide()
        self.frozen_column.setFrameShape(QFrame.Shape.NoFrame)
        self.frozen_column.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frozen_column.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.frozen_column.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.frozen_column.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen_column.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen_column.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.frozen_column.setAlternatingRowColors(True)
        self.frozen_column.setShowGrid(False)
        self.frozen_column.setWordWrap(False)
        self.verticalScrollBar().valueChanged.connect(self.frozen_column.verticalScrollBar().setValue)
        self.frozen_column.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self.verticalHeader().sectionResized.connect(
            lambda row, old, height: self.frozen_column.setRowHeight(row, height))

    def set_theme(self, theme):
        self._theme = dict(theme)
        bg, alt, text, stroke = (theme[k] for k in ('bg_color', 'alt_bg', 'text_color', 'stroke_color'))
        alternate = blend_color(bg, text, .045)
        self._muted = blend_color(alt, text, .70)
        line = blend_color(bg, text, .12)
        css = (
            f'QTableView {{ background: {bg}; color: {text}; alternate-background-color: {alternate};'
            ' border: 1px solid transparent; }'
            'QTableView:focus { border-color: transparent; }'
            f'QTableView::item {{ padding: 3px 8px; border-bottom: 1px solid {line}; }}'
            f'QHeaderView::section {{ background: {alt}; color: {text}; border: none;'
            f' border-bottom: 1px solid {line}; }}'
        )
        self.setStyleSheet(css)
        self.frozen_column.setStyleSheet(css + 'QTableView#statusOverviewFeatures { border: none; }')
        for view in (self, self.frozen_column):
            view.horizontalHeader().secondary_color = QColor(self._muted)
            view.horizontalHeader().setFixedHeight(view.horizontalHeader().sizeHint().height())
        self._size_columns()

    def begin_session(self):
        self._session = True
        self._known.clear()

    def end_session(self):
        self._session = False
        self._known.clear()

    @staticmethod
    def _identity(info):
        # A persistent account ID alone is insufficient: the same account may
        # restart into another process. The existing window handle disambiguates it.
        return (str(info.get('stable_id') or ''), info.get('handle'))

    def update_snapshot(self, actions, metadata, fallback_titles, states, identities):
        legacy = metadata is None
        infos = ([dict(title=title, stable_id=f'title:{title}', handle=None)
                  for title in fallback_titles] if legacy else metadata)
        active = {}
        for info in infos:
            title = str(info.get('title') or '').strip()
            if not title:
                continue
            key = self._identity(info)
            # With neither stable ID nor handle, a title is only a display fallback.
            if key == ('', None):
                key = (f'title:{title}', None)
            active[key] = dict(info, title=title)
        columns = list(active.items())
        if self._session:
            columns.extend((key, info) for key, info in self._known.items() if key not in active)
            self._known.update(active)
        else:
            self._known.clear()
        horizontal = self.horizontalScrollBar().value()
        vertical = self.verticalScrollBar().value()
        self.setUpdatesEnabled(False)
        try:
            self.setRowCount(len(actions))
            self.setColumnCount(len(columns) + 1)
            self.setHorizontalHeaderItem(0, QTableWidgetItem('功能'))
            self._feature_width = max(125, min(220, max(
                (self.fontMetrics().horizontalAdvance(name) for name in actions.values()), default=0) + 24))
            height = max(32, self.fontMetrics().height() + 12)
            for row, name in enumerate(actions.values()):
                feature = QTableWidgetItem(name)
                feature.setFlags(Qt.ItemFlag.ItemIsEnabled)
                feature.setToolTip(name)
                feature.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(row, 0, feature)
                self.setRowHeight(row, height)
                self.frozen_column.setRowHeight(row, height)
            for col, (key, info) in enumerate(columns, 1):
                title, nickname = info['title'], info.get('account_nick')
                header = QTableWidgetItem(f'{nickname}\n{title}' if nickname else title)
                header.setToolTip(header.text())
                header.setData(Qt.ItemDataRole.UserRole, key)
                header.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.setHorizontalHeaderItem(col, header)
                connected = key in active
                supplied = identities.get(title) if isinstance(identities, dict) else None
                matches = legacy or (isinstance(supplied, (tuple, list)) and tuple(supplied) == key)
                for row, action in enumerate(actions):
                    action_states = states.get(action, {})
                    state = action_states.get(title) if isinstance(action_states, dict) else None
                    value = ('已断开' if not connected else '未知' if not matches or type(state) is not bool
                             else '●' if state else '○')
                    item = QTableWidgetItem(value)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(QColor(self._theme['stroke_color'] if value == '●' else self._muted))
                    description = '已开启' if value == '●' else '未开启' if value == '○' else value
                    item.setToolTip(f'{nickname or title} · {actions[action]}：{description}')
                    self.setItem(row, col, item)
            self.frozen_column.setColumnHidden(0, False)
            for col in range(1, self.columnCount()):
                self.frozen_column.setColumnHidden(col, True)
            self._size_columns()
            self.horizontalScrollBar().setValue(horizontal)
            self.verticalScrollBar().setValue(vertical)
        finally:
            self.setUpdatesEnabled(True)

    def _size_columns(self):
        if self._sizing or not hasattr(self, 'frozen_column'):
            return
        self._sizing = True
        try:
            self.setColumnWidth(0, self._feature_width)
            self.frozen_column.setColumnWidth(0, self._feature_width)
            count = self.columnCount() - 1
            if count > 0:
                minimum = max(110, self.fontMetrics().horizontalAdvance('— 未开启') + 24)
                available = max(0, self.viewport().width() - self._feature_width)
                width = max(minimum, available // count)
                for col in range(1, count + 1):
                    extra = 1 if width > minimum and col <= available % count else 0
                    self.setColumnWidth(col, width + extra)
            self.frozen_column.setGeometry(self.frameWidth(), self.frameWidth(), self._feature_width,
                                            self.viewport().height() + self.horizontalHeader().height())
            self.frozen_column.raise_()
            self.frozen_column.setVisible(self.columnCount() > 0)
        finally:
            self._sizing = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size_columns()

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        horizontal = self.horizontalScrollBar().value()
        super().scrollTo(index, hint)
        if index.column() == 0:
            self.horizontalScrollBar().setValue(horizontal)
        elif index.column() > 0 and self.visualRect(index).left() < self._feature_width:
            # QTableView considers its entire viewport visible. The overlay
            # occupies the left part, so keep keyboard navigation beyond it.
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() + self.visualRect(index).left() - self._feature_width)
