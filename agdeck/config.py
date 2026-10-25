"""Persistent configuration for AG Deck (`config.json` next to the token)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from .paths import config_path, ensure_config_dir

#: Every option can also be given on the command line; this file only changes
#: the defaults so that `agdeck` with no arguments does the right thing.
DEFAULTS: Dict[str, Any] = {
    "port": 8848,
    "host": "0.0.0.0",          # "127.0.0.1" for --local-only
    "openBrowser": False,
    "defaultModel": "",         # empty = ask Antigravity which model to use
    "pollIdleSeconds": 8.0,     # SSE cadence when nothing is running
    "pollBusySeconds": 2.0,     # SSE cadence while a session is running
}

_ENV = {
    "port": ("AGDECK_PORT", int),
    "host": ("AGDECK_HOST", str),
}


def load() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for key in DEFAULTS:
                if key in raw and raw[key] is not None:
                    cfg[key] = raw[key]
    for key, (env, cast) in _ENV.items():
        value = os.environ.get(env)
        if value not in (None, ""):
            try:
                cfg[key] = cast(value)
            except ValueError:
                pass
    return cfg


def save(cfg: Dict[str, Any]) -> Path:
    """Write only known keys, atomically, with 0600 where the OS supports it."""
    data = {k: cfg[k] for k in DEFAULTS if k in cfg}
    directory = ensure_config_dir()
    path = directory / "config.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)
    return path


def platform_note() -> str:
    if os.name == "nt":
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"
