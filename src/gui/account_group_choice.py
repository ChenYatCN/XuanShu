"""Presentation of the launcher's compact account-group selector."""

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QComboBox, QListView, QStyledItemDelegate, QStyle


class _GroupMenuDelegate(QStyledItemDelegate):
    # Use symmetric margins for centered labels and the separator.
    padding = 10
    row_margin = 4
    horizontal_padding = 2 * (padding + row_margin)

    def sizeHint(self, option, index):
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == 'separator':
            return QSize(0, 7)
        return QSize(0, max(24, option.fontMetrics.height() + 4))

    def paint(self, painter, option, index):
        painter.save()
        painter.setClipRect(option.rect)
        palette = option.palette
        row_rect = option.rect.adjusted(self.row_margin, 0, -self.row_margin, 0)
        text_rect = row_rect.adjusted(self.padding, 0, -self.padding, 0)
        painter.fillRect(option.rect, palette.brush(QPalette.ColorRole.Base))
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == 'separator':
            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            y = option.rect.center().y()
            painter.drawLine(text_rect.left(), y, text_rect.right(), y)
        else:
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            painter.fillRect(row_rect, palette.brush(
                QPalette.ColorRole.Highlight if selected else QPalette.ColorRole.Base))
            painter.setPen(palette.color(
                QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
            painter.setFont(option.font)
            text = index.data()
            if index.data(Qt.ItemDataRole.UserRole) not in ('@create', '@manage'):
                text = option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                                     text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class AccountGroupChoice(QComboBox):
    """Retain QComboBox selection/closing behavior; size only its popup."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        view = QListView(self)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.setItemDelegate(_GroupMenuDelegate(view))
        self.setView(view)
        self.setMaxVisibleItems(12)

    def showPopup(self):
        view = self.view()
        theme = self._theme()
        bg = QColor(theme['alt_bg']).name()
        text = QColor(theme['text_color']).name()
        border = QColor(theme['stroke_color']).name()
        # Explicit opaque colors also cover the popup's frame and blank areas.
        view.setStyleSheet(
            f'QListView {{ background: {bg}; color: {text}; border: none; padding: 0; }}')
        palette = view.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(bg))
        palette.setColor(QPalette.ColorRole.Text, QColor(text))
        highlight = QColor(theme['button_color'])
        highlight.setAlpha(255)
        palette.setColor(QPalette.ColorRole.Highlight, highlight)
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(text))
        view.setPalette(palette)
        bounds = QRect(self.window().mapToGlobal(self.window().rect().topLeft()), self.window().size())
        bounds = bounds.intersected(self.screen().availableGeometry())
        right = min(self.mapToGlobal(self.rect().topRight()).x(), bounds.right())
        metrics = view.fontMetrics()
        actions = [metrics.horizontalAdvance(self.itemText(i)) for i in range(self.count())
                   if self.itemData(i) in ('@create', '@manage')]
        names = [metrics.horizontalAdvance(self.itemText(i)) for i in range(self.count())]
        # Allow long custom names to elide without making the menu excessively wide.
        content = max(max(actions, default=0), min(240, max(names, default=0)))
        width = min(max(self.width(), content + _GroupMenuDelegate.horizontal_padding + 16 + 2),
                    right - bounds.left() + 1)
        view.setMinimumWidth(max(0, width - 2))
        popup = view.window()
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        popup.setWindowOpacity(1.0)
        popup.setStyleSheet(f'QFrame {{ background: {bg}; border: 1px solid {border}; }}')
        super().showPopup()
        below = self.mapToGlobal(self.rect().bottomLeft()).y() + 1
        above = self.mapToGlobal(self.rect().topLeft()).y()
        height = min(popup.height(), bounds.height())
        y = below if below + height <= bounds.bottom() + 1 else above - height
        y = max(bounds.top(), min(y, bounds.bottom() - height + 1))
        popup.setGeometry(right - width + 1, y, width, height)
