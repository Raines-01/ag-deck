"""Cross-platform helpers: processes, listening ports, local addresses.

Three platforms are supported with the tools that ship with the OS itself, so
AG Deck keeps zero runtime dependencies:

    Linux    /proc, ss (fallback), ip
    macOS    ps, lsof, ifconfig
    Windows  PowerShell CIM, netstat, Get-NetIPAddress

Everything in this module is defensive: discovery failing must never crash the
server, it should only produce a useful diagnostic.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Tuple

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Hide the console window that would otherwise flash on Windows.
_NO_WINDOW = 0x08000000 if IS_WIN else 0

#: Interfaces that never make sense to advertise on a phone.
_SKIP_IFACE = ("docker", "br-", "veth", "virbr", "vmnet", "lo", "Loopback")
#: Addresses that are link-local, container-internal or proxy fake-ip ranges.
_SKIP_IP = ("127.", "169.254.", "172.17.", "172.18.", "198.18.", "0.")


def run(cmd: List[str], timeout: float = 8.0) -> str:
    """Run a command and return stdout, or "" on any failure."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.decode("utf-8", "replace") if proc.stdout else ""


# ---------------------------------------------------------------------------
# Process listing
# ---------------------------------------------------------------------------

def iter_processes() -> Iterable[Tuple[int, str]]:
    """Yield ``(pid, command line)`` for every visible process."""
    if IS_LINUX:
        yield from _proc_processes()
    elif IS_WIN:
        yield from _windows_processes()
    else:
        yield from _ps_processes()


def _proc_processes() -> Iterable[Tuple[int, str]]:
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for name in entries:
        if not name.isdigit():
            continue
        try:
            raw = open(f"/proc/{name}/cmdline", "rb").read()
        except OSError:
            continue
        if not raw:
            continue
        cmd = " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)
        yield int(name), cmd


def _ps_processes() -> Iterable[Tuple[int, str]]:
    out = run(["ps", "-axo", "pid=,command="], timeout=10)
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        if pid.isdigit() and cmd.strip():
            yield int(pid), cmd.strip()


def _windows_processes() -> Iterable[Tuple[int, str]]:
    script = (
        "Get-CimInstance Win32_Process | ForEach-Object "
        "{ \"$($_.ProcessId)`t$($_.CommandLine)\" }"
    )
    out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=20)
    for line in out.splitlines():
        pid, _, cmd = line.partition("\t")
        pid = pid.strip()
        if pid.isdigit() and cmd.strip():
            yield int(pid), cmd.strip()


# ---------------------------------------------------------------------------
# Listening ports of one process
# ---------------------------------------------------------------------------

def listen_ports(pid: int) -> List[int]:
    if IS_LINUX:
        ports = _ports_linux_proc(pid) or _ports_ss(pid)
    elif IS_WIN:
        ports = _ports_netstat(pid)
    else:
        ports = _ports_lsof(pid)
    return _dedupe(ports)


def _ports_linux_proc(pid: int) -> List[int]:
    """Match socket inodes from /proc/<pid>/fd against /proc/net/tcp."""
    inodes = set()
    fd_dir = f"/proc/{pid}/fd"
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        return []
    for fd in fds:
        try:
            link = os.readlink(os.path.join(fd_dir, fd))
        except OSError:
            continue
        if link.startswith("socket:["):
            inodes.add(link[8:-1])
    if not inodes:
        return []

    ports: List[int] = []
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(table).read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A" or parts[9] not in inodes:
                continue
            try:
                ports.append(int(parts[1].split(":")[1], 16))
            except (ValueError, IndexError):
                continue
    return ports


def _ports_ss(pid: int) -> List[int]:
    out = run(["ss", "-lntpH"], timeout=4)
    ports = []
    for line in out.splitlines():
        if f"pid={pid}," in line:
            match = re.search(r":(\d+)\s", line)
            if match:
                ports.append(int(match.group(1)))
    return ports


def _ports_lsof(pid: int) -> List[int]:
    out = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)], timeout=8)
    ports = []
    for line in out.splitlines():
        match = re.search(r"TCP\s+\S*:(\d+)\s+\(LISTEN\)", line)
        if match:
            ports.append(int(match.group(1)))
    return ports


def _ports_netstat(pid: int) -> List[int]:
    out = run(["netstat", "-ano"], timeout=15)
    ports = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() not in ("TCP", "TCPV6"):
            continue
        if "LISTEN" not in parts[3].upper() or parts[-1] != str(pid):
            continue
        match = re.search(r":(\d+)$", parts[1])
        if match:
            ports.append(int(match.group(1)))
    return ports


def _dedupe(items: List[int]) -> List[int]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Local addresses
# ---------------------------------------------------------------------------

def _friendly(name: str) -> str:
    low = name.lower()
    if low.startswith("tailscale"):
        return "Tailscale"
    if low.startswith(("enp", "eth", "eno", "ens")):
        return "Ethernet"
    if low.startswith(("wlp", "wlan", "wlo", "wlx")):
        return "Wi-Fi"
    if low.startswith("zt"):
        return "ZeroTier"
    if low.startswith(("tun", "utun", "wg", "ppp")):
        return "VPN"
    return name


def local_ipv4() -> List[Tuple[str, str]]:
    """Return ``[(interface, address)]`` for usable IPv4 addresses."""
    if IS_LINUX:
        found = _ipv4_linux()
    elif IS_WIN:
        found = _ipv4_windows()
    elif IS_MAC:
        found = _ipv4_macos()
    else:
        found = []

    if not found:
        probed = _ipv4_socket()
        found = [("network", probed)] if probed else []

    out, seen = [], set()
    for iface, ip in found:
        if not ip or ip in seen:
            continue
        if ip.startswith(_SKIP_IP) or iface.startswith(_SKIP_IFACE):
            continue
        seen.add(ip)
        out.append((iface, ip))
    return out


def _ipv4_linux() -> List[Tuple[str, str]]:
    found = []
    out = run(["ip", "-4", "-o", "addr", "show"], timeout=4)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if match:
            found.append((parts[1], match.group(1)))
    return found


def _ipv4_macos() -> List[Tuple[str, str]]:
    found, iface = [], ""
    for line in run(["ifconfig"], timeout=6).splitlines():
        head = re.match(r"^([A-Za-z0-9_.-]+):\s", line)
        if head:
            iface = head.group(1)
            continue
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if match:
            found.append((iface, match.group(1)))
    return found


def _ipv4_windows() -> List[Tuple[str, str]]:
    script = (
        "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object "
        "{ \"$($_.InterfaceAlias)`t$($_.IPAddress)\" }"
    )
    out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=15)
    found = []
    for line in out.splitlines():
        alias, _, ip = line.partition("\t")
        if ip.strip():
            found.append((alias.strip(), ip.strip()))
    return found


def _ipv4_socket() -> str:
    """Last-resort: ask the OS which source address reaches the internet."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 53))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        if sock is not None:
            sock.close()


# ---------------------------------------------------------------------------
# Tailscale
# ---------------------------------------------------------------------------

_TAILSCALE_PATHS = [
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
]


def tailscale_bin() -> Optional[str]:
    found = shutil.which("tailscale")
    if found:
        return found
    for path in _TAILSCALE_PATHS:
        if os.path.isfile(path):
            return path
    return None


def tailscale_state() -> Dict[str, object]:
    binary = tailscale_bin()
    if not binary:
        return {"installed": False, "connected": False, "ips": [], "binary": None}
    out = run([binary, "ip", "-4"], timeout=5)
    ips = [ip for ip in out.split() if ip.count(".") == 3]
    return {"installed": True, "connected": bool(ips), "ips": ips, "binary": binary}


# ---------------------------------------------------------------------------
# Public endpoint list
# ---------------------------------------------------------------------------

def detect_urls(port: int) -> List[Dict[str, str]]:
    """Every URL a phone could use to reach this server."""
    out: List[Dict[str, str]] = [
        {"label": "本机 / local", "url": f"http://127.0.0.1:{port}/", "iface": "lo"}
    ]
    seen = {"127.0.0.1"}

    for ip in tailscale_state()["ips"]:  # type: ignore[index]
        if ip not in seen:
            seen.add(ip)
            out.append({"label": "Tailscale", "url": f"http://{ip}:{port}/", "iface": "tailscale0"})

    for iface, ip in local_ipv4():
        if ip in seen or ip.startswith(_SKIP_IP) or iface.startswith(_SKIP_IFACE):
            continue
        seen.add(ip)
        out.append({"label": _friendly(iface), "url": f"http://{ip}:{port}/", "iface": iface})

    priority = {"Tailscale": 0, "Wi-Fi": 1, "Ethernet": 2, "ZeroTier": 3}
    out.sort(key=lambda item: 99 if item["iface"] == "lo" else priority.get(item["label"], 9))
    return out
