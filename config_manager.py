import json
import os
import sys
from copy import deepcopy

DEFAULT_CONFIG = {
    "selected_image": "cross.png",
    "image_path": "",
    "hotkey": "F8",
    "color": "#FFFFFF",
    "visible": True,
    "x": None,
    "y": None,
    "monitor": 0,
}


class ConfigManager:
    def __init__(self, config_path=None):
        self.data_dir = self._default_data_dir()
        if config_path is None:
            config_path = os.path.join(self.data_dir, "config.json")
        self.config_path = config_path

    @staticmethod
    def _default_data_dir():
        if getattr(sys, "frozen", False):
            base_dir = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            return os.path.join(base_dir, "Conjure Crosshair")
        return os.path.dirname(os.path.abspath(__file__))

    def load(self):
        if not os.path.exists(self.config_path):
            return deepcopy(DEFAULT_CONFIG)

        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                loaded = json.load(file)
        except (OSError, ValueError):
            return deepcopy(DEFAULT_CONFIG)

        merged = deepcopy(DEFAULT_CONFIG)
        if isinstance(loaded, dict):
            merged.update(loaded)
        return merged

    def save(self, config):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2)
            file.write("\n")
