"""Paths and user settings."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "EnZhDict"


def app_data_dir() -> Path:
    """Per-user writable folder (e.g. %APPDATA%\\EnZhDict)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundle_dir() -> Path:
    """Folder holding bundled read-only resources (source tree or PyInstaller bundle)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def dict_db_path() -> Path | None:
    """Locate the prebuilt offline dictionary (see scripts/build_dict.py)."""
    candidates = [
        bundle_dir() / "resources" / "dict.db",
        app_data_dir() / "dict.db",
    ]
    if getattr(sys, "frozen", False):
        candidates.insert(1, Path(sys.executable).parent / "resources" / "dict.db")
    for c in candidates:
        if c.exists():
            return c
    return None


def user_db_path() -> Path:
    return app_data_dir() / "user.db"


def audio_cache_dir() -> Path:
    path = app_data_dir() / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    hotkey_interval_ms: int = 400
    hotkey_enabled: bool = True
    popup_width: int = 380
    popup_max_height: int = 420
    font_size: int = 11
    default_accent: str = "us"  # "uk" or "us"
    auto_play: bool = False
    online_lookups: bool = True
    start_with_windows: bool = False
    show_example_translations: bool = False
    show_tagalog: bool = True   # Tagalog equivalents for English words in the popup and reviews

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or app_data_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in raw.items() if k in known})
        s.hotkey_interval_ms = max(150, min(1500, int(s.hotkey_interval_ms)))
        if s.default_accent not in ("uk", "us"):
            s.default_accent = "us"
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or app_data_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
