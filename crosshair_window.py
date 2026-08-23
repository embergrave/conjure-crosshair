import ctypes
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QWidget


class CrosshairWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setWindowOpacity(1.0)
        self.setStyleSheet("QWidget { background: transparent; } QLabel { background: transparent; border: none; }")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(self)
        self._label.setStyleSheet("background: transparent; border: none;")
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setAutoFillBackground(False)
        self._label.setContentsMargins(0, 0, 0, 0)

        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.resize(1, 1)
        self._visible = True
        self._image_path = ""
        self._color = QColor("#FF0000")

    def set_crosshair_image(self, image_path, recenter=True):
        if not image_path or not os.path.exists(image_path):
            return False

        image = QImage(image_path).convertToFormat(QImage.Format.Format_RGBA8888)
        if image.isNull():
            return False

        for y in range(image.height()):
            for x in range(image.width()):
                alpha = image.pixelColor(x, y).alpha()
                image.setPixelColor(x, y, QColor(
                    self._color.red(),
                    self._color.green(),
                    self._color.blue(),
                    alpha,
                ))

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return False

        self._image_path = image_path
        self._label.setPixmap(pixmap)
        self._label.adjustSize()
        self.resize(pixmap.size())
        if recenter:
            self.center_on_primary_screen()
        self._apply_click_through()
        return True

    def set_crosshair_color(self, color_hex):
        color = QColor(color_hex)
        if not color.isValid():
            return False

        self._color = color
        if self._image_path:
            return self.set_crosshair_image(self._image_path, recenter=False)
        return True

    @property
    def color_hex(self):
        return self._color.name().upper()

    def center_on_primary_screen(self):
        self.center_on_screen(QApplication.primaryScreen())

    def center_on_screen(self, screen):
        if screen is None:
            return

        self.move(*self.center_position(screen))

    def center_position(self, screen):
        if screen is None:
            return self.x(), self.y()

        # Use full screen geometry (not availableGeometry) so the taskbar
        # doesn't skew the center vertically/horizontally.
        geometry = screen.geometry()
        x = (geometry.width() - self.width()) // 2 + geometry.x()
        y = (geometry.height() - self.height()) // 2 + geometry.y()
        return x, y

    def set_position(self, x, y):
        self.move(int(x), int(y))

    def _apply_click_through(self):
        if os.name != "nt":
            return

        if not self.isVisible():
            return

        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            0x0002 | 0x0001 | 0x0020,
        )

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._apply_click_through()
        self._visible = True

    def hide_window(self):
        self.hide()
        self._visible = False

    def toggle_visibility(self):
        if self.isVisible():
            self.hide_window()
        else:
            self.show_window()

    @property
    def visible(self):
        return self._visible

    def set_visible(self, visible):
        if visible:
            self.show_window()
        else:
            self.hide_window()
