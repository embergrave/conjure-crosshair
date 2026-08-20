import os
import ctypes
import logging
import shutil
import sys
import threading

import keyboard
import mouse
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QColorDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from config_manager import ConfigManager
from crosshair_window import CrosshairWindow


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

        self.ensure_default_assets()
        if not self.config.get("selected_image"):
            self.config["selected_image"] = "cross.png"
        if not self.config.get("image_path"):
            self.config["image_path"] = self.get_asset_path(self.config["selected_image"])

        self.set_crosshair_color(self.config.get("color", "#FFFFFF"), save=False)
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
        with Image.open(icon_path) as source_icon:
            icon = source_icon.convert("RGBA")

        menu = self.build_tray_menu()
        self._tray_icon = Icon("Conjure Crosshair", icon, "Conjure Crosshair", menu)
        thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        thread.start()

    def build_tray_menu(self):
        settings_menu = self.build_settings_menu()
        return Menu(*settings_menu.items, MenuItem("Exit", self._safe_exit_application))

    def build_settings_menu(self):
        crosshair_menu = Menu(
            *[
                MenuItem(
                    self._display_name(image_name),
                    self._crosshair_action(image_name),
                )
                for image_name in self.list_available_image_names()
            ],
            Menu.SEPARATOR,
            MenuItem("Add Crosshair", self._tray_add_crosshair),
            MenuItem("Remove Crosshair", self.build_remove_crosshair_menu()),
        )
        return Menu(
            MenuItem("Select Crosshair", crosshair_menu),
            MenuItem(
                "Set Position",
                self._tray_edit_position,
            ),
            MenuItem("Select Color", self._tray_edit_color),
            MenuItem(
                "Select Monitor",
                self.build_monitor_menu(),
            ),
            MenuItem(
                lambda item: f"Set Hotkey: {self.config.get('hotkey', 'F8')}",
                self._tray_set_hotkey,
            ),
            MenuItem(
                lambda item: f"Toggle: {'On' if self.window.visible else 'Off'}",
                self._toggle_crosshair_from_tray,
            ),
        )

    def build_remove_crosshair_menu(self):
        custom_images = [
            image_name
            for image_name in self.list_available_image_names()
            if image_name.lower() not in self.DEFAULT_CROSSHAIRS
            and os.path.isfile(self.get_user_asset_path(image_name))
        ]
        return Menu(
            *[
                MenuItem(
                    self._display_name(image_name),
                    self._remove_crosshair_action(image_name),
                )
                for image_name in custom_images
            ]
        )

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

    def build_monitor_menu(self):
        screens = QApplication.screens()
        return Menu(
            *[
                MenuItem(
                    f"{index + 1}: {screen.name() or 'Monitor'}",
                    self._monitor_action(index),
                )
                for index, screen in enumerate(screens)
            ]
        )

    def _monitor_action(self, monitor_index):
        def action(icon, item):
            self._tray_select_monitor(monitor_index)

        return action

    def _tray_edit_position(self, icon=None, item=None):
        self._invoke_on_main_thread(self._edit_position)

    def _tray_edit_color(self, icon=None, item=None):
        self._invoke_on_main_thread(self._edit_color)

    def _edit_color(self):
        current_color = QColor(self.config.get("color", "#FFFFFF"))
        dialog = QColorDialog(current_color)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.setWindowFlag(Qt.WindowType.Window, True)
        dialog.setWindowTitle("Conjure Crosshair: Color")
        dialog.setWindowIcon(QIcon(os.path.join(self.bundle_dir, "icon.ico")))
        dialog.adjustSize()
        self._position_dialog(dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_color = dialog.currentColor()
            if selected_color.isValid():
                self.set_crosshair_color(selected_color.name().upper())

    def set_crosshair_color(self, color_hex, save=True):
        if not self.window.set_crosshair_color(color_hex):
            return False
        self.config["color"] = self.window.color_hex
        if save:
            self.config_manager.save(self.config)
        return True

    def _edit_position(self):
        original_x = self.window.x()
        original_y = self.window.y()
        dialog = QDialog()
        dialog.setWindowFlag(Qt.WindowType.Window, True)
        dialog.setWindowTitle("Conjure Crosshair: Position")
        icon_path = os.path.join(self.bundle_dir, "icon.ico")
        if os.path.exists(icon_path):
            dialog.setWindowIcon(QIcon(icon_path))
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

        up_button = QPushButton("▲")
        left_button = QPushButton("◀")
        right_button = QPushButton("▶")
        down_button = QPushButton("▼")
        for button, tooltip in (
            (up_button, "Move up"),
            (left_button, "Move left"),
            (right_button, "Move right"),
            (down_button, "Move down"),
        ):
            button.setFixedSize(42, 32)
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

        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.window.set_position(original_x, original_y)
            self.save_current_position()

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
            self._tray_icon.menu = self.build_tray_menu()

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

    def _open_hotkey_dialog(self):
        dialog = QDialog()
        dialog.setWindowTitle("Conjure Crosshair: Hotkey")
        dialog.setWindowIcon(QIcon(os.path.join(self.bundle_dir, "icon.ico")))
        dialog.setWindowFlag(Qt.WindowType.Window, True)

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
        dialog.exec()
        stop_capture()

    def _toggle_crosshair_from_tray(self, icon=None, item=None):
        self._invoke_on_main_thread(self._toggle_crosshair_impl)

    def _safe_exit_application(self, *args):
        self._invoke_on_main_thread(self._exit_application_impl)

    def _exit_application_impl(self):
        if self._tray_icon is not None:
            self._tray_icon.stop()
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
            self._tray_icon.menu = self.build_tray_menu()

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
