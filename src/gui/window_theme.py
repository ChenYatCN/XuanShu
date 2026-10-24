"""Apply the existing theme to native application dialog captions.

Keep the native window frame: resizing, system-menu actions, modality and keyboard
handling remain owned by Qt/Windows. OS-owned pickers are intentionally excluded.
"""

import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QObject, QEvent, Qt, QPointF
from PyQt6.QtGui import QColor, QPalette, QPen, QPolygonF, QPainter
from PyQt6.QtWidgets import QDialog, QFileDialog, QColorDialog, QFontDialog, QMenu, QAbstractSpinBox, QProxyStyle, QStyle


class ClassicSpinStyle(QProxyStyle):
    """Classic spin geometry with visible theme-colored chevrons."""
    def __init__(self, color):
        super().__init__('Fusion')
        self.arrow_color = color

    def drawPrimitive(self, element, option, painter, widget=None):
        up = (QStyle.PrimitiveElement.PE_IndicatorSpinUp,
              QStyle.PrimitiveElement.PE_IndicatorArrowUp)
        down = (QStyle.PrimitiveElement.PE_IndicatorSpinDown,
                QStyle.PrimitiveElement.PE_IndicatorArrowDown)
        if element not in up + down:
            return super().drawPrimitive(element, option, painter, widget)
        center = option.rect.center()
        half = max(2., min(4., (option.rect.width() - 4) / 2))
        direction = -1 if element in up else 1
        color = QColor(self.arrow_color)
        if not option.state & QStyle.StateFlag.State_Enabled:
            color.setAlpha(90)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([
            QPointF(center.x() - half, center.y() - direction * 1.5),
            QPointF(center.x(), center.y() + direction * 1.5),
            QPointF(center.x() + half, center.y() - direction * 1.5)]))
        painter.restore()


class WindowThemeController(QObject):
    def __init__(self, app, theme):
        super().__init__(app)
        self.app = app
        self.theme = dict(theme)
        self._spin_style = ClassicSpinStyle(theme["stroke_color"])
        self._spin_style.setParent(self)
        app.installEventFilter(self)
        self.set_theme(theme)

    @staticmethod
    def owns_window(widget):
        return isinstance(widget, (QDialog, QMenu)) and not isinstance(
            widget, (QFileDialog, QColorDialog, QFontDialog))

    def _apply_spin_style(self, widget):
        if isinstance(widget, QAbstractSpinBox) and not widget.property("xuanClassicSpinStyle"):
            widget.setProperty("xuanClassicSpinStyle", True)
            widget.setStyle(self._spin_style)

    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Polish:
            self._apply_spin_style(widget)
        if event.type() == QEvent.Type.Show and self.owns_window(widget):
            self._apply_caption(widget)
        return False

    def set_theme(self, theme):
        self.theme = dict(theme)
        self._spin_style.arrow_color = theme["stroke_color"]
        palette = self.app.palette()
        for role, key in ((QPalette.ColorRole.Window, 'bg_color'),
                          (QPalette.ColorRole.Base, 'alt_bg'),
                          (QPalette.ColorRole.AlternateBase, 'bg_color'),
                          (QPalette.ColorRole.Text, 'text_color'),
                          (QPalette.ColorRole.WindowText, 'text_color'),
                          (QPalette.ColorRole.ButtonText, 'text_color'),
                          (QPalette.ColorRole.Button, 'button_color'),
                          (QPalette.ColorRole.Highlight, 'button_color'),
                          (QPalette.ColorRole.HighlightedText, 'text_color'),
                          (QPalette.ColorRole.ToolTipBase, 'alt_bg'),
                          (QPalette.ColorRole.ToolTipText, 'text_color')):
            palette.setColor(role, QColor(theme[key]))
        self.app.setPalette(palette)
        # Include controls created before theme installation; Polish covers new ones.
        for widget in self.app.allWidgets():
            self._apply_spin_style(widget)
            if isinstance(widget, QAbstractSpinBox):
                widget.update()
        for widget in self.app.topLevelWidgets():
            if self.owns_window(widget) and widget.isVisible():
                self._apply_caption(widget)

    def _apply_caption(self, widget):
        if (self.app.platformName() != 'windows'
                or widget.windowFlags() & Qt.WindowType.FramelessWindowHint
                or isinstance(widget, QMenu)):
            return
        try:
            setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
            hwnd = wintypes.HWND(int(widget.winId()))
            colors = []
            for attribute, key in ((35, 'titlebar_bg'), (36, 'text_color')):
                color = QColor(self.theme[key])
                value = wintypes.DWORD(color.red() | color.green() << 8 | color.blue() << 16)
                colors.append((attribute, value))
            dark = wintypes.BOOL(QColor(self.theme['titlebar_bg']).lightness() < 128)
            results = {'dark': setter(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))}
            for attribute, value in colors:
                results[str(attribute)] = setter(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
            # The result is diagnostic only; unsupported Windows versions retain
            # their native frame and use immersive dark mode where available.
            widget.setProperty('xuanCaptionThemeResult', results)
        except (AttributeError, OSError):
            widget.setProperty('xuanCaptionThemeResult', {'unavailable': True})


def install_window_theme(app, theme):
    controller = getattr(app, '_xuan_window_theme', None)
    if controller is None:
        controller = WindowThemeController(app, theme)
        app._xuan_window_theme = controller
    else:
        controller.set_theme(theme)
    return controller
