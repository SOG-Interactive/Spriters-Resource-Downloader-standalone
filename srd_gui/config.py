"""Paths, persisted settings and environment overrides."""
from __future__ import annotations

import json
import os

APP_NAME = "SpritersResourceDownloader"
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(PACKAGE_DIR, "ui")

# Set SRD_BASE_URL to point the app at a local test server (see tests/fixture_server.py).
BASE_URL = os.environ.get("SRD_BASE_URL", "https://www.spriters-resource.com").rstrip("/")


def _appdata(kind: str) -> str:
    root = os.environ.get(kind) or os.path.expanduser("~")
    path = os.path.join(root, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    override = os.environ.get("SRD_SETTINGS_PATH")
    if override:
        return override
    return os.path.join(_appdata("APPDATA"), "settings.json")


def profile_dir() -> str:
    """WebView2 user-data folder. Persisting it keeps the site's cookies
    (including the bot-check clearance) between runs."""
    override = os.environ.get("SRD_PROFILE_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    path = os.path.join(_appdata("LOCALAPPDATA"), "WebView2Profile")
    os.makedirs(path, exist_ok=True)
    return path


DEFAULT_SETTINGS = {
    "last_url": "",
    "dest": os.path.join(os.path.expanduser("~"), "Downloads", "Spriters Resource"),
    "extract_zips": True,
    "skip_existing": True,
    "nsfw": False,
    "thumbs": True,
}


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update({k: v for k, v in loaded.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return data


def save_settings(data: dict) -> dict:
    current = load_settings()
    current.update({k: v for k, v in (data or {}).items() if k in DEFAULT_SETTINGS})
    tmp = settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    os.replace(tmp, settings_path())
    return current
