import os
import ctypes
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import re

import keyboard
import mouse
from PIL import Image, ImageDraw
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QColorDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from config_manager import ConfigManager
from crosshair_window import CrosshairWindow
from version import APP_VERSION


class _MainThreadInvoker(QObject):
    invoke = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.invoke.connect(self._run)

    def _run(self, callback):
        callback()


INSTANCE_MUTEX_NAME = "ConjureCrosshair.SingleInstance"
INSTANCE_EVENT_NAME = "ConjureCrosshair.ShowCrosshair"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
GITHUB_RELEASES_API = "https://api.github.com/repos/embergrave/conjure-crosshair/releases/latest"
INSTALLER_ASSET_NAME = "Conjure Crosshair.exe"


def acquire_instance_handles():
    if os.name != "nt":
        return True, None, None

    kernel32 = ctypes.windll.kernel32
    mutex_handle = kernel32.CreateMutexW(None, True, INSTANCE_MUTEX_NAME)
    if not mutex_handle:
        return False, None, None

    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        event_handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, INSTANCE_EVENT_NAME)
        if event_handle:
            kernel32.SetEvent(event_handle)
            kernel32.CloseHandle(event_handle)
        kernel32.CloseHandle(mutex_handle)
        return False, None, None

    event_handle = kernel32.CreateEventW(None, False, False, INSTANCE_EVENT_NAME)
    if not event_handle:
        kernel32.CloseHandle(mutex_handle)
        return False, None, None
    return True, mutex_handle, event_handle


class ConjureCrosshairApp:
    DEFAULT_CROSSHAIRS = frozenset({"cross.png", "carrot.png", "dot.png"})

    def __init__(self, app, mutex_handle=None, event_handle=None):
        self.app = app
        self._mutex_handle = mutex_handle
        self._event_handle = event_handle
        self.config_manager = ConfigManager()
        self.logger = self._configure_logging()
        self.config = self.config_manager.load()
        self._color_history = [QColor("#FF0000")]
        self.bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        self.asset_dir = os.path.join(self.config_manager.data_dir, "assets")
        self.bundle_asset_dir = os.path.join(self.bundle_dir, "assets")
        os.makedirs(self.asset_dir, exist_ok=True)

        self.window = CrosshairWindow()
        self._main_thread_invoker = _MainThreadInvoker()

        self._registered_hotkey = ""
        self._registered_mouse_button = ""
        self._mouse_hotkey_callback = None
        self._tray_icon = None
        self._position_dialog_instance = None
        self._color_dialog_instance = None
        self._hotkey_dialog_instance = None

        self.ensure_default_assets()
        if not self.config.get("selected_image"):
            self.config["selected_image"] = "cross.png"
        if not self.config.get("image_path"):
            self.config["image_path"] = self.get_asset_path(self.config["selected_image"])

        self.set_crosshair_color(self.config.get("color", "#FF0000"), save=False)
        self.set_selected_image(self.config["selected_image"])
        self.log_monitor_layout()
        self.apply_saved_position()
        self._show_crosshair_impl()
        self.bind_hotkey(self.config.get("hotkey", "F8"))
        self.create_tray_icon()
        self._start_instance_event_listener()

    def _start_instance_event_listener(self):
        if self._event_handle is None:
            return

        def wait_for_show_request():
            ctypes.windll.kernel32.WaitForSingleObject(self._event_handle, -1)
            self._invoke_on_main_thread(self._show_crosshair_impl)
            self._start_instance_event_listener()

        threading.Thread(target=wait_for_show_request, daemon=True).start()

    def _show_crosshair_impl(self):
        self.window.set_visible(True)
        self.config["visible"] = True
        self.config_manager.save(self.config)
        self.refresh_tray_menu()

    def _configure_logging(self):
        logger = logging.getLogger("conjure_crosshair")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            os.makedirs(self.config_manager.data_dir, exist_ok=True)
            log_path = os.path.join(self.config_manager.data_dir, "conjure_crosshair.log")
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        return logger

    def log_monitor_layout(self):
        screens = QApplication.screens()
        if not screens:
            self.logger.warning("No monitors detected")
            return

        for index, screen in enumerate(screens):
            geometry = screen.availableGeometry()
            center_x = geometry.x() + geometry.width() / 2
            center_y = geometry.y() + geometry.height() / 2
            position_x, position_y = self.window.center_position(screen)
            self.logger.info(
                "Monitor %d (%s): resolution=%dx%d origin=(%d,%d) center=(%.1f,%.1f) "
                "crosshair_position=(%d,%d)",
                index + 1,
                screen.name() or "Unnamed",
                geometry.width(),
                geometry.height(),
                geometry.x(),
                geometry.y(),
                center_x,
                center_y,
                position_x,
                position_y,
            )

    def ensure_default_assets(self):
        if not os.path.exists(self.get_asset_path("cross.png")):
            self.create_default_crosshair_asset()

    def create_default_crosshair_asset(self):
        image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.line((64, 0, 64, 128), fill=(255, 255, 255, 255), width=4)
        draw.line((0, 64, 128, 64), fill=(255, 255, 255, 255), width=4)
        draw.ellipse((48, 48, 80, 80), outline=(255, 255, 255, 255), width=3)
        image.save(os.path.join(self.asset_dir, "cross.png"))

    def list_available_image_names(self):
        names = set()
        for directory in (self.bundle_asset_dir, self.asset_dir):
            if not os.path.isdir(directory):
                continue
            for file_name in os.listdir(directory):
                if file_name.lower().endswith(".png"):
                    names.add(file_name)
        return sorted(names) or ["cross.png"]

    def _asset_path_in(self, directory, file_name):
        path = os.path.join(directory, os.path.basename(file_name))
        return path if os.path.exists(path) else ""

    def get_asset_path(self, file_name):
        if not file_name:
            file_name = "cross.png"
        return (
            self._asset_path_in(self.asset_dir, file_name)
            or self._asset_path_in(self.bundle_asset_dir, file_name)
            or os.path.join(self.asset_dir, os.path.basename(file_name))
        )

    def get_user_asset_path(self, file_name):
        return os.path.join(self.asset_dir, os.path.basename(file_name))

    def add_custom_crosshair(self, image_path):
        if not image_path or not os.path.exists(image_path):
            return

        file_name = os.path.basename(image_path)
        if not file_name.lower().endswith(".png"):
            return

        target_name = self._unique_asset_name(file_name)
        target_path = self.get_user_asset_path(target_name)
        shutil.copy2(image_path, target_path)
        self.config["selected_image"] = target_name
        self.config["image_path"] = target_path
        self.config_manager.save(self.config)
        self.set_selected_image(target_name)

    def _unique_asset_name(self, file_name):
        base_name, ext = os.path.splitext(file_name)
        target_name = f"{base_name}{ext}".strip()
        candidate = target_name
        index = 1
        while os.path.exists(self.get_user_asset_path(candidate)):
            candidate = f"{base_name}_{index}{ext}"
            index += 1
        return candidate

    def set_selected_image(self, image_name):
        if not image_name:
            image_name = "cross.png"
        selected_name = os.path.basename(image_name)
        asset_path = self.get_asset_path(selected_name)
        if not os.path.exists(asset_path):
            asset_path = self.get_asset_path("cross.png")
            selected_name = "cross.png"
        was_visible = self.window.visible
        self.set_crosshair_image(asset_path)
        screens = QApplication.screens()
        monitor_index = self._valid_monitor_index(self.config.get("monitor", 0), screens)
        if screens:
            self.window.center_on_screen(screens[monitor_index])
        self.save_current_position()
        self.window.set_visible(was_visible)
        self.config["selected_image"] = selected_name
        self.config["image_path"] = asset_path
        self.config_manager.save(self.config)

    def _tray_select_crosshair(self, image_name):
        self._invoke_on_main_thread(lambda: self.set_selected_image(image_name))

    def apply_saved_position(self):
        screens = QApplication.screens()
        monitor_index = self._valid_monitor_index(self.config.get("monitor", 0), screens)
        self.config["monitor"] = monitor_index

        x = self.config.get("x")
        y = self.config.get("y")
        if isinstance(x, int) and isinstance(y, int):
            self.window.set_position(x, y)
        else:
            self.window.center_on_screen(screens[monitor_index] if screens else None)
            self.save_current_position()

    def save_current_position(self):
        self.config["x"] = self.window.x()
        self.config["y"] = self.window.y()
        self.config_manager.save(self.config)

    @staticmethod
    def _valid_monitor_index(monitor_index, screens):
        try:
            monitor_index = int(monitor_index)
        except (TypeError, ValueError):
            monitor_index = 0
        if not screens:
            return 0
        return max(0, min(monitor_index, len(screens) - 1))

    def set_crosshair_image(self, image_path):
        if not self.window.set_crosshair_image(image_path):
            return
        self.config["image_path"] = image_path
        self.config_manager.save(self.config)

    def create_tray_icon(self):
        icon_path = os.path.join(self.bundle_dir, "icon.ico")
        self._tray_icon = QSystemTrayIcon(QIcon(icon_path), self.app)
        self._tray_icon.setToolTip("Conjure Crosshair")
        self._tray_icon.setContextMenu(self.build_tray_menu())
        self._tray_icon.show()

    def build_tray_menu(self):
        menu = self.build_settings_menu()
        menu.addSeparator()
        self._add_menu_action(menu, "Update", self._tray_update)
        self._add_menu_action(menu, "Exit", self._safe_exit_application)
        menu.aboutToShow.connect(self._keep_tray_visible)
        menu.setStyleSheet(self._tray_menu_stylesheet())
        return menu

    def build_settings_menu(self):
        menu = QMenu()
        crosshair_menu = menu.addMenu("Select Crosshair")
        for image_name in self.list_available_image_names():
            self._add_menu_action(
                crosshair_menu,
                self._display_name(image_name),
                lambda checked=False, name=image_name: self._tray_select_crosshair(name),
            )
        crosshair_menu.addSeparator()
        self._add_menu_action(crosshair_menu, "Add Crosshair", self._tray_add_crosshair)
        remove_menu = crosshair_menu.addMenu("Remove Crosshair")
        custom_images = [
            image_name
            for image_name in self.list_available_image_names()
            if image_name.lower() not in self.DEFAULT_CROSSHAIRS
            and os.path.isfile(self.get_user_asset_path(image_name))
        ]
        remove_menu.setEnabled(bool(custom_images))
        for image_name in custom_images:
            self._add_menu_action(
                remove_menu,
                self._display_name(image_name),
                lambda checked=False, name=image_name: self._tray_remove_crosshair(name),
            )

        self._add_menu_action(menu, "Set Position", self._tray_edit_position)
        self._add_menu_action(menu, "Select Color", self._tray_edit_color)
        monitor_menu = menu.addMenu("Select Monitor")
        for index, screen in enumerate(QApplication.screens()):
            self._add_menu_action(
                monitor_menu,
                f"{index + 1}: {screen.name() or 'Monitor'}",
                lambda checked=False, monitor=index: self._tray_select_monitor(monitor),
            )
        menu.addSeparator()
        self._add_menu_action(
            menu,
            f"Toggle: {'On' if self.window.visible else 'Off'}",
            self._toggle_crosshair_from_tray,
        )
        self._add_menu_action(
            menu,
            f"Set Hotkey: {self.config.get('hotkey', 'F8')}",
            self._tray_set_hotkey,
        )
        return menu

    def _keep_tray_visible(self):
        if self._tray_icon is not None:
            self._tray_icon.show()

    @staticmethod
    def _add_menu_action(menu, label, callback):
        action = menu.addAction(label)
        action.triggered.connect(callback)
        return action

    @staticmethod
    def _tray_menu_stylesheet():
        return """
            QMenu {
                background-color: #171a21;
                border: 1px solid #343a46;
                border-radius: 6px;
                color: #e8edf5;
                padding: 6px;
                font-size: 10pt;
            }
            QMenu::item {
                padding: 7px 35px 7px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2b3442;
                color: #ffffff;
            }
            QMenu::item:disabled {
                color: #687181;
            }
            QMenu::separator {
                height: 1px;
                background-color: #343a46;
                margin: 5px 8px;
            }
            QMenu::right-arrow {
                width: 8px;
                height: 8px;
            }
        """

    def _display_name(self, image_name):
        clean_name = os.path.splitext(os.path.basename(image_name))[0]
        return clean_name[:1].upper() + clean_name[1:] if clean_name else "Crosshair"

    def _crosshair_action(self, image_name):
        def action(icon, item):
            self._tray_select_crosshair(image_name)

        return action

    def _remove_crosshair_action(self, image_name):
        def action(icon, item):
            self._tray_remove_crosshair(image_name)

        return action

    def _tray_remove_crosshair(self, image_name):
        self._invoke_on_main_thread(lambda: self.remove_custom_crosshair(image_name))

    def remove_custom_crosshair(self, image_name):
        selected_name = os.path.basename(image_name)
        if selected_name.lower() in self.DEFAULT_CROSSHAIRS:
            return

        target_path = self.get_user_asset_path(selected_name)
        if not os.path.isfile(target_path) or not selected_name.lower().endswith(".png"):
            return

        if self.config.get("selected_image") == selected_name:
            self.set_selected_image("cross.png")

        try:
            os.remove(target_path)
        except OSError:
            return

        self.refresh_tray_menu()

    def _tray_edit_position(self, icon=None, item=None):
        self._invoke_on_main_thread(self._edit_position)

    def _tray_edit_color(self, icon=None, item=None):
        self._invoke_on_main_thread(self._edit_color)

    def _edit_color(self):
        if self._resurface_dialog(self._color_dialog_instance):
            return

        current_color = QColor(self.config.get("color", "#FF0000"))
        dialog = QDialog()
        self._color_dialog_instance = dialog
        dialog.setWindowFlag(Qt.WindowType.Window, True)
        dialog.setWindowTitle("Conjure Crosshair: Color")
        dialog.setWindowIcon(QIcon(os.path.join(self.bundle_dir, "icon.ico")))
        dialog.setStyleSheet(self._dialog_stylesheet())

        color_source = QColorDialog(current_color, dialog)
        color_source.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        color_source.setWindowFlag(Qt.WindowType.Widget, True)
        color_source.show()
        QApplication.processEvents()

        picker, brightness_bar = self._extract_color_picker(color_source)
        picker.setParent(dialog)
        brightness_bar.setParent(dialog)
        color_source.hide()

        dialog.setFixedSize(420, 360)
        picker.setGeometry(116, 24, 222, 202)
        brightness_bar.setGeometry(350, 24, 20, 208)
        picker.show()
        brightness_bar.show()

        history_buttons = []
        for index in range(10):
            swatch = QPushButton(dialog)
            swatch.setFixedSize(22, 18)
            swatch.move(34, 24 + round(index * (202 - 18) / 9))
            swatch.clicked.connect(
                lambda checked=False, slot=index: select_history_color(slot)
            )
            history_buttons.append(swatch)

        set_button = QPushButton("Set", dialog)
        set_button.setGeometry(150, 270, 120, 36)
        close_button = QPushButton("Close", dialog)
        close_button.setGeometry(150, 314, 120, 36)

        def refresh_history():
            for index, swatch in enumerate(history_buttons):
                color = self._color_history[index] if index < len(self._color_history) else None
                swatch.setEnabled(color is not None)
                if color is None:
                    swatch.setStyleSheet(
                        "min-width: 22px; max-width: 22px; min-height: 18px; max-height: 18px; "
                        "padding: 0; background-color: #20252e; border: 1px solid #343a46; border-radius: 3px;"
                    )
                else:
                    swatch.setToolTip(color.name().upper())
                    swatch.setStyleSheet(
                        f"min-width: 22px; max-width: 22px; min-height: 18px; max-height: 18px; "
                        f"padding: 0; background-color: {color.name()}; border: 1px solid #687181; border-radius: 3px;"
                    )

        def select_history_color(index):
            if index < len(self._color_history):
                color_source.setCurrentColor(self._color_history[index])

        def set_color():
            selected_color = color_source.currentColor()
            if selected_color.isValid():
                self._remember_color(selected_color)
                self.set_crosshair_color(selected_color.name().upper())
                refresh_history()

        set_button.clicked.connect(set_color)
        close_button.clicked.connect(dialog.reject)
        refresh_history()
        dialog.show()
        picker.raise_()
        brightness_bar.raise_()
        for swatch in history_buttons:
            swatch.show()
            swatch.raise_()
        set_button.show()
        close_button.show()
        self._center_dialog(dialog)
        try:
            dialog.exec()
        finally:
            self._color_dialog_instance = None

    def set_crosshair_color(self, color_hex, save=True):
        if not self.window.set_crosshair_color(color_hex):
            return False
        self.config["color"] = self.window.color_hex
        if save:
            self.config_manager.save(self.config)
        return True

    def _edit_position(self):
        if self._resurface_dialog(self._position_dialog_instance):
            return

        original_x = self.window.x()
        original_y = self.window.y()
        dialog = QDialog()
        self._position_dialog_instance = dialog
        dialog.setWindowFlag(Qt.WindowType.Window, True)
        dialog.setWindowTitle("Conjure Crosshair: Position")
        icon_path = os.path.join(self.bundle_dir, "icon.ico")
        if os.path.exists(icon_path):
            dialog.setWindowIcon(QIcon(icon_path))
        dialog.setStyleSheet(self._dialog_stylesheet())
        layout = QGridLayout(dialog)

        x_input = QSpinBox()
        x_input.setRange(-100000, 100000)
        x_input.setValue(original_x)
        x_input.setKeyboardTracking(True)
        x_input.setFixedWidth(80)
        x_input.setStyleSheet("QSpinBox::up-button, QSpinBox::down-button { width: 0px; height: 0px; }")

        y_input = QSpinBox()
        y_input.setRange(-100000, 100000)
        y_input.setValue(original_y)
        y_input.setKeyboardTracking(True)
        y_input.setFixedWidth(80)
        y_input.setStyleSheet("QSpinBox::up-button, QSpinBox::down-button { width: 0px; height: 0px; }")

        coordinate_form = QFormLayout()
        coordinate_form.addRow("X:", x_input)
        coordinate_form.addRow("Y:", y_input)

        up_button = QPushButton("↑")
        left_button = QPushButton("←")
        right_button = QPushButton("→")
        down_button = QPushButton("↓")
        for button, size, tooltip in (
            (up_button, (42, 32), "Move up"),
            (left_button, (32, 42), "Move left"),
            (right_button, (32, 42), "Move right"),
            (down_button, (42, 32), "Move down"),
        ):
            button.setFixedSize(*size)
            button.setToolTip(tooltip)

        layout.addWidget(up_button, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(left_button, 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(coordinate_form, 1, 1)
        layout.addWidget(right_button, 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(down_button, 2, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        save_button = QPushButton("Save")
        save_button.clicked.connect(dialog.accept)
        layout.addWidget(save_button, 3, 0, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        def update_position():
            self.window.set_position(x_input.value(), y_input.value())
            self.save_current_position()

        x_input.valueChanged.connect(update_position)
        y_input.valueChanged.connect(update_position)

        up_button.clicked.connect(lambda: y_input.setValue(y_input.value() - 1))
        left_button.clicked.connect(lambda: x_input.setValue(x_input.value() - 1))
        right_button.clicked.connect(lambda: x_input.setValue(x_input.value() + 1))
        down_button.clicked.connect(lambda: y_input.setValue(y_input.value() + 1))

        dialog.adjustSize()
        self._position_dialog(dialog)

        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.window.set_position(original_x, original_y)
                self.save_current_position()
        finally:
            self._position_dialog_instance = None

    @staticmethod
    def _resurface_dialog(dialog):
        if dialog is None or not dialog.isVisible():
            return False
        dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()
        return True

    @staticmethod
    def _position_dialog(dialog):
        primary_screen = QApplication.primaryScreen()
        if primary_screen is None:
            return

        geometry = primary_screen.availableGeometry()
        x = geometry.x() + geometry.width() // 2 + 250
        y = geometry.y() + (geometry.height() - dialog.height()) // 2
        dialog.move(x, y)

    @staticmethod
    def _center_dialog(dialog):
        primary_screen = QApplication.primaryScreen()
        if primary_screen is None:
            return

        geometry = primary_screen.availableGeometry()
        x = geometry.x() + (geometry.width() - dialog.width()) // 2
        y = geometry.y() + (geometry.height() - dialog.height()) // 2
        dialog.move(x, y)

    def _extract_color_picker(self, dialog):
        direct_widgets = dialog.findChildren(
            QWidget,
            "",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )
        picker = next(
            widget
            for widget in direct_widgets
            if isinstance(widget, QFrame)
            and not isinstance(widget, QLabel)
            and widget.geometry().width() > 100
            and widget.geometry().height() > 100
        )
        brightness_bar = next(
            widget
            for widget in direct_widgets
            if widget.geometry().x() > picker.geometry().right()
            and widget.geometry().height() > 100
            and not isinstance(widget, QFrame)
            and not isinstance(widget, QLabel)
        )
        for widget in direct_widgets:
            if widget not in (picker, brightness_bar):
                widget.hide()
        return picker, brightness_bar

    def _remember_color(self, color):
        color = QColor(color)
        self._color_history = [
            QColor("#FF0000"),
            *[
                previous
                for previous in self._color_history
                if previous.name().upper() not in {"#FF0000", color.name().upper()}
            ],
        ]
        if color.name().upper() != "#FF0000":
            self._color_history.insert(1, color)
        self._color_history = self._color_history[:10]

    @staticmethod
    def _dialog_stylesheet():
        return """
            QDialog {
                background-color: #171a21;
                color: #e8edf5;
            }
            QLabel {
                color: #e8edf5;
            }
            QGroupBox {
                color: #e8edf5;
                border: 1px solid #343a46;
                border-radius: 6px;
                margin-top: 10px;
                padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #9ba7b8;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #20252e;
                border: 1px solid #3b4554;
                border-radius: 4px;
                color: #f5f7fa;
                padding: 6px 8px;
                selection-background-color: #3b82b8;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #5aa9d6;
            }
            QPushButton {
                background-color: #252c37;
                border: 1px solid #3b4554;
                border-radius: 4px;
                color: #e8edf5;
                padding: 7px 16px;
                min-width: 64px;
            }
            QPushButton:hover {
                background-color: #2f3a49;
                border-color: #5aa9d6;
            }
            QPushButton:pressed {
                background-color: #1e6d9b;
            }
            QPushButton:disabled {
                background-color: #20252e;
                border-color: #2c333e;
                color: #687181;
            }
            QAbstractItemView {
                background-color: #20252e;
                border: 1px solid #3b4554;
                color: #e8edf5;
                selection-background-color: #2b3442;
            }
            QCheckBox {
                color: #e8edf5;
                spacing: 6px;
            }
        """

    def _tray_select_monitor(self, monitor_index):
        self._invoke_on_main_thread(lambda: self.select_monitor(monitor_index))

    def select_monitor(self, monitor_index):
        screens = QApplication.screens()
        monitor_index = self._valid_monitor_index(monitor_index, screens)
        self.config["monitor"] = monitor_index
        if screens:
            self.window.center_on_screen(screens[monitor_index])
        self.save_current_position()
        self.refresh_tray_menu()

    def refresh_tray_menu(self):
        if self._tray_icon is not None:
            self._tray_icon.setContextMenu(self.build_tray_menu())

    def _tray_add_crosshair(self, icon=None, item=None):
        self._invoke_on_main_thread(self._add_crosshair_from_tray)

    def _add_crosshair_from_tray(self):
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select crosshair image",
            "",
            "PNG Image Files (*.png);;All Files (*)",
        )
        if path:
            self.add_custom_crosshair(path)
            self.refresh_tray_menu()

    def _tray_set_hotkey(self, icon=None, item=None):
        self._invoke_on_main_thread(self._open_hotkey_dialog)

    def _tray_update(self, icon=None, item=None):
        self._invoke_on_main_thread(self._check_for_updates)

    def _check_for_updates(self):
        threading.Thread(target=self._fetch_latest_release, daemon=True).start()

    def _fetch_latest_release(self):
        try:
            request = urllib.request.Request(
                GITHUB_RELEASES_API,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "Conjure-Crosshair"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                release = json.loads(response.read().decode("utf-8"))
            tag_name = release.get("tag_name", "")
            assets = release.get("assets", [])
            installer_asset = next(
                (
                    asset
                    for asset in assets
                    if "installer" in self._normalize_asset_name(asset.get("name", ""))
                ),
                None,
            )
            if installer_asset is None:
                installer_asset = next(
                    (
                        asset
                        for asset in assets
                        if self._normalize_asset_name(asset.get("name", ""))
                        == self._normalize_asset_name(INSTALLER_ASSET_NAME)
                    ),
                    None,
                )
            if not tag_name or installer_asset is None:
                raise ValueError("The latest release does not contain the installer asset.")
            self._invoke_on_main_thread(
                lambda: self._handle_update_result(tag_name, installer_asset["browser_download_url"])
            )
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            self._invoke_on_main_thread(lambda: self._show_update_error(str(error)))

    def _handle_update_result(self, tag_name, download_url):
        latest_version = self._parse_version(tag_name)
        current_version = self._parse_version(APP_VERSION)
        if latest_version <= current_version:
            self._show_update_message(
                f"You are using the latest version ({APP_VERSION}).",
            )
            return

        result = QMessageBox.question(
            None,
            "Conjure Crosshair: Update Available",
            f"Version {tag_name.lstrip('v')} is available.\n\nDownload and install it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if result == QMessageBox.StandardButton.Yes:
            threading.Thread(
                target=self._download_and_launch_update,
                args=(download_url,),
                daemon=True,
            ).start()

    def _download_and_launch_update(self, download_url):
        installer_path = ""
        try:
            request = urllib.request.Request(
                download_url,
                headers={"User-Agent": "Conjure-Crosshair"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".exe", prefix="ConjureCrosshair-Update-", delete=False
                ) as installer_file:
                    installer_path = installer_file.name
                    shutil.copyfileobj(response, installer_file)
            subprocess.Popen([installer_path], close_fds=True)
            self._invoke_on_main_thread(self._exit_application_impl)
        except (OSError, urllib.error.URLError) as error:
            if installer_path and os.path.exists(installer_path):
                os.remove(installer_path)
            self._invoke_on_main_thread(lambda: self._show_update_error(str(error)))

    def _show_update_message(self, message):
        QMessageBox.information(None, "Conjure Crosshair: Update", message)

    def _show_update_error(self, error):
        QMessageBox.critical(
            None,
            "Conjure Crosshair: Update Failed",
            f"The update could not be completed.\n\n{error}",
        )

    @staticmethod
    def _parse_version(version):
        values = version.lstrip("vV").split(".")
        try:
            return tuple(int(value) for value in values[:3])
        except ValueError:
            return (0, 0, 0)

    @staticmethod
    def _normalize_asset_name(asset_name):
        return re.sub(r"[^a-z0-9]", "", asset_name.lower())

    def _open_hotkey_dialog(self):
        if self._resurface_dialog(self._hotkey_dialog_instance):
            return

        dialog = QDialog()
        self._hotkey_dialog_instance = dialog
        dialog.setWindowTitle("Conjure Crosshair: Hotkey")
        dialog.setWindowIcon(QIcon(os.path.join(self.bundle_dir, "icon.ico")))
        dialog.setWindowFlag(Qt.WindowType.Window, True)
        dialog.setStyleSheet(self._dialog_stylesheet())

        prompt = QLabel("Press a key or extra mouse button to assign it as the Crosshair toggle.")
        prompt.setWordWrap(True)

        selected_hotkey = QLabel("No key selected")
        selected_hotkey.setAlignment(Qt.AlignmentFlag.AlignCenter)
        selected_hotkey.setMinimumWidth(280)
        selected_hotkey.setStyleSheet("font-size: 24px; font-weight: bold; padding: 12px;")

        reassign_button = QPushButton("Reassign")
        save_button = QPushButton("Save")
        save_button.setEnabled(False)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(reassign_button)
        button_layout.addWidget(save_button)
        button_layout.addStretch()

        layout = QVBoxLayout(dialog)
        layout.addWidget(prompt)
        layout.addWidget(selected_hotkey)
        layout.addLayout(button_layout)

        captured_hotkey = {"value": ""}
        capture_hooks = {"keyboard": None, "mouse": None}

        def begin_capture():
            stop_capture()
            prompt.setText("Press a key or extra mouse button to assign it as the Crosshair toggle.")
            selected_hotkey.setText("Listening...")
            save_button.setEnabled(False)
            reassign_button.setEnabled(False)

            def keyboard_callback(event):
                if event.event_type == "down":
                    self._invoke_on_main_thread(lambda: finish_capture(event.name))

            def mouse_callback(event):
                if getattr(event, "event_type", None) == "down":
                    self._invoke_on_main_thread(lambda: finish_capture(f"mouse:{event.button}"))

            capture_hooks["keyboard"] = keyboard.hook(keyboard_callback)
            capture_hooks["mouse"] = mouse.hook(mouse_callback)

        def stop_capture():
            if capture_hooks["keyboard"] is not None:
                keyboard.unhook(capture_hooks["keyboard"])
                capture_hooks["keyboard"] = None
            if capture_hooks["mouse"] is not None:
                mouse.unhook(capture_hooks["mouse"])
                capture_hooks["mouse"] = None

        def finish_capture(hotkey):
            stop_capture()
            reassign_button.setEnabled(True)
            if not hotkey:
                selected_hotkey.setText("No key selected")
                return
            captured_hotkey["value"] = hotkey
            selected_hotkey.setText(self._format_hotkey_label(hotkey))
            save_button.setEnabled(True)

        def save_hotkey():
            hotkey = captured_hotkey["value"]
            if hotkey:
                self.bind_hotkey(hotkey)
                dialog.accept()

        reassign_button.clicked.connect(begin_capture)
        save_button.clicked.connect(save_hotkey)

        dialog.adjustSize()
        self._center_dialog(dialog)
        begin_capture()
        try:
            dialog.exec()
        finally:
            stop_capture()
            self._hotkey_dialog_instance = None

    def _toggle_crosshair_from_tray(self, icon=None, item=None):
        self._invoke_on_main_thread(self._toggle_crosshair_impl)

    def _safe_exit_application(self, *args):
        self._invoke_on_main_thread(self._exit_application_impl)

    def _exit_application_impl(self):
        if self._tray_icon is not None:
            self._tray_icon.hide()
        if self._event_handle is not None:
            ctypes.windll.kernel32.CloseHandle(self._event_handle)
            self._event_handle = None
        if self._mutex_handle is not None:
            ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None
        self.app.quit()

    def _invoke_on_main_thread(self, func, *args):
        if threading.current_thread() is threading.main_thread():
            func(*args)
        else:
            self._main_thread_invoker.invoke.emit(lambda: func(*args))

    def exit_application(self, icon=None, item=None):
        self._safe_exit_application()

    def bind_hotkey(self, hotkey_label):
        normalized = self._normalize_hotkey(hotkey_label)
        if self._registered_hotkey:
            try:
                keyboard.remove_hotkey(self._registered_hotkey)
            except Exception:
                pass
        if self._registered_mouse_button:
            if self._mouse_hotkey_callback is not None:
                mouse.unhook(self._mouse_hotkey_callback)
            self._registered_mouse_button = ""
            self._mouse_hotkey_callback = None

        if not normalized:
            self._registered_hotkey = ""
            return

        self._registered_hotkey = ""
        if normalized.startswith("mouse:"):
            button_name = normalized.removeprefix("mouse:")

            def mouse_callback(event):
                if (
                    getattr(event, "event_type", None) == "down"
                    and event.button == button_name
                ):
                    self.toggle_crosshair()

            self._registered_mouse_button = button_name
            self._mouse_hotkey_callback = mouse.hook(mouse_callback)
        else:
            self._registered_hotkey = normalized
            keyboard.add_hotkey(normalized, self.toggle_crosshair)
        self.config["hotkey"] = hotkey_label
        self.config_manager.save(self.config)
        if self._tray_icon is not None:
            self._tray_icon.setContextMenu(self.build_tray_menu())

    def update_hotkey(self, hotkey_label, normalized_hotkey):
        self.bind_hotkey(hotkey_label)
        self.config["hotkey"] = hotkey_label
        self.config_manager.save(self.config)

    def toggle_crosshair(self):
        self._invoke_on_main_thread(self._toggle_crosshair_impl)

    def _toggle_crosshair_impl(self):
        self.window.toggle_visibility()
        self.config["visible"] = self.window.visible
        self.config_manager.save(self.config)
        self.refresh_tray_menu()

    @staticmethod
    def _normalize_hotkey(hotkey_label):
        if not hotkey_label:
            return ""
        parts = [part.strip().lower() for part in hotkey_label.split("+") if part.strip()]
        return "+".join(parts)

    @staticmethod
    def _format_hotkey_label(hotkey_label):
        if hotkey_label.lower().startswith("mouse:"):
            button_name = hotkey_label.split(":", 1)[1].strip().lower()
            names = {"x": "Mouse X1", "x2": "Mouse X2"}
            return names.get(button_name, f"Mouse {button_name.title()}")
        return hotkey_label.upper()


def main():
    is_first_instance, mutex_handle, event_handle = acquire_instance_handles()
    if not is_first_instance:
        return

    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                ctypes.c_wchar_p("ConjureCrosshair")
            )
        except (AttributeError, OSError):
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Conjure Crosshair")
    bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(bundle_dir, "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    app.setQuitOnLastWindowClosed(False)
    ConjureCrosshairApp(app, mutex_handle, event_handle)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
