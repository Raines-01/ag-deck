"""Low-level transport for Antigravity's local RPC endpoint.

The agent panel in Antigravity is driven by a separate process,
``resources/bin/language_server``. It starts with a random ``--csrf_token`` and
listens on a random loopback port, speaking Connect(JSON) under the service
name ``exa.language_server_pb.LanguageServerService``.

Because both the token and the port change on every restart, AG Deck rediscovers
them on demand:

    scan processes -> read --csrf_token -> find listening ports -> probe RPC

Nothing here depends on a specific OS; process/port enumeration is delegated to
:mod:`agdeck.platform_util`.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from . import USER_AGENT
from .platform_util import iter_processes, listen_ports

SERVICE = "exa.language_server_pb.LanguageServerService"
CSRF_HEADER = "x-codeium-csrf-token"

CONNECT_TTL = 45.0
PROBE_TIMEOUT = 1.5

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX))

_LOCK = threading.RLock()
_CONN: Optional[Dict[str, Any]] = None
_CONN_AT: float = 0.0
_LAST_ERROR: str = ""


class LSUnavailable(RuntimeError):
    """Raised when the Antigravity language server cannot be found or reached."""


class RPCError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Command line parsing (pure functions, unit tested)
# ---------------------------------------------------------------------------

_OPTION_TEMPLATE = r"--{name}(?:=|\s+)(?:\"([^\"]*)\"|'([^']*)'|(\S+))"


def option(cmdline: str, name: str) -> str:
    pattern = _OPTION_TEMPLATE.format(name=re.escape(name))
    match = re.search(pattern, cmdline)
    if not match:
        return ""
    return next((g for g in match.groups() if g is not None), "")


def executable_of(cmdline: str) -> str:
    cmdline = cmdline.strip()
    if cmdline.startswith('"'):
        end = cmdline.find('"', 1)
        return cmdline[1:end] if end > 0 else cmdline[1:]
    return cmdline.split(" ", 1)[0]


def parse_language_server(pid: int, cmdline: str) -> Optional[Dict[str, Any]]:
    """Return the discovery record for a process, or None if it is not one.

    Deliberately strict about the *executable* name so that a shell command
    which merely mentions ``language_server`` cannot be mistaken for the real
    thing.
    """
    if not cmdline or "language_server" not in cmdline:
        return None
    exe = executable_of(cmdline).replace("\\", "/")
    base = os.path.basename(exe).lower()
    if base not in ("language_server", "language_server.exe"):
        return None
    csrf = option(cmdline, "csrf_token")
    if not csrf:
        return None
    return {
        "pid": pid,
        "csrf": csrf,
        "exe": exe,
        "ideVersion": option(cmdline, "override_ide_version"),
        "appDataDir": option(cmdline, "app_data_dir"),
    }


def find_servers() -> List[Dict[str, Any]]:
    found = []
    for pid, cmdline in iter_processes():
        record = parse_language_server(pid, cmdline)
        if record:
            found.append(record)
    # Newer instances first: the most recently started one is usually the live UI
    found.sort(key=lambda r: r["pid"], reverse=True)
    return found


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------

def post_json(url: str, body: Dict[str, Any], csrf: str, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            CSRF_HEADER: csrf,
            "User-Agent": USER_AGENT,
        },
    )
    with _OPENER.open(request, timeout=timeout) as response:
        raw = response.read()
    text = raw.decode("utf-8", "replace").strip()
    return json.loads(text) if text else {}


def probe(scheme: str, port: int, csrf: str, timeout: float = PROBE_TIMEOUT) -> bool:
    url = f"{scheme}://127.0.0.1:{port}/{SERVICE}/GetAllCascadeTrajectories"
    try:
        body = post_json(url, {"excludeSubtrajectories": True}, csrf, timeout)
    except Exception:
        return False
    return isinstance(body, dict) and "trajectorySummaries" in body


def discover(force: bool = False) -> Dict[str, Any]:
    """Locate a reachable RPC endpoint; raises :class:`LSUnavailable`."""
    global _CONN, _CONN_AT, _LAST_ERROR

    with _LOCK:
        if not force and _CONN and (time.time() - _CONN_AT) < CONNECT_TTL:
            return _CONN

        servers = find_servers()
        if not servers:
            _CONN = None
            _LAST_ERROR = (
                "没有找到 Antigravity 的 language_server 进程 / "
                "no running Antigravity language_server process"
            )
            raise LSUnavailable(_LAST_ERROR)

        attempted: List[str] = []
        for server in servers:
            pid = server["pid"]
            ports = listen_ports(pid)
            if not ports:
                attempted.append(f"PID {pid}: no listening port")
                continue
            for port in ports:
                for scheme in ("http", "https"):
                    if probe(scheme, port, server["csrf"]):
                        _CONN = {
                            **server,
                            "port": port,
                            "scheme": scheme,
                            "base": f"{scheme}://127.0.0.1:{port}/{SERVICE}",
                        }
                        _CONN_AT = time.time()
                        _LAST_ERROR = ""
                        return _CONN
            attempted.append(f"PID {pid}: ports {ports} did not answer")

        _CONN = None
        _LAST_ERROR = "找到了 Antigravity 进程但 RPC 无响应：" + "; ".join(attempted)
        raise LSUnavailable(_LAST_ERROR)


def reset() -> None:
    global _CONN_AT
    with _LOCK:
        _CONN_AT = 0.0


def last_error() -> str:
    return _LAST_ERROR


def rpc(method: str, body: Optional[Dict[str, Any]] = None, timeout: float = 30.0,
        retry: bool = True) -> Dict[str, Any]:
    conn = discover()
    url = f"{conn['base']}/{method}"
    try:
        data = post_json(url, body or {}, conn["csrf"], timeout)
    except (urllib.error.URLError, OSError) as exc:
        if retry:
            reset()
            discover(force=True)
            return rpc(method, body, timeout, retry=False)
        raise LSUnavailable(f"调用 {method} 失败 / call failed: {exc}") from exc

    if isinstance(data, dict) and set(data.keys()) <= {"code", "message"} and data.get("code"):
        raise RPCError(str(data["code"]), str(data.get("message", "")))
    return data


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def diagnose() -> Dict[str, Any]:
    """Structured answer for the onboarding screen and `agdeck doctor`."""
    info: Dict[str, Any] = {
        "ok": False,
        "servers": [],
        "error": "",
        "platform": sys.platform,
    }
    servers = find_servers()
    info["servers"] = [
        {"pid": s["pid"], "exe": s["exe"], "ports": listen_ports(s["pid"]),
         "ideVersion": s["ideVersion"]}
        for s in servers
    ]
    try:
        conn = discover(force=True)
    except LSUnavailable as exc:
        info["error"] = str(exc)
        info["hint"] = (
            "启动 Antigravity，并确认它的 agent 面板至少打开过一次。"
            if not servers else
            "进程在运行，但本地 RPC 端口没有响应；试试重启 Antigravity。"
        )
        return info
    info.update({
        "ok": True,
        "pid": conn["pid"],
        "port": conn["port"],
        "scheme": conn["scheme"],
        "ideVersion": conn.get("ideVersion", ""),
    })
    return info
