"""Filesystem locations used by AG Deck.

Everything that must survive an upgrade (access token, config, logs) lives in a
per-user config directory instead of the source tree:

    Linux    $XDG_CONFIG_HOME/ag-deck   (default ~/.config/ag-deck)
    macOS    ~/Library/Application Support/AG Deck
    Windows  %APPDATA%\\AG Deck

`AGDECK_HOME` overrides the location, which is handy for tests and for running
a throw-away instance.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_SLUG = "ag-deck"
DISPLAY_NAME = "AG Deck"


def config_dir() -> Path:
    override = os.environ.get("AGDECK_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / DISPLAY_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / DISPLAY_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_SLUG


def ensure_config_dir() -> Path:
    path = config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return config_dir() / "token"


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return config_dir() / "agdeck.log"


def static_dir() -> Path:
    """Bundled web assets (works from a wheel, a checkout, or a zipapp)."""
    return Path(__file__).resolve().parent / "static"


def describe() -> dict:
    """Human readable summary, used by `agdeck doctor`."""
    return {
        "configDir": str(config_dir()),
        "tokenFile": str(token_path()),
        "configFile": str(config_path()),
        "staticDir": str(static_dir()),
        "platform": sys.platform,
        "python": sys.version.split()[0],
    }
