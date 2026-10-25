#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AG Deck — local web dashboard for Antigravity Cascade sessions.

Serves one single-page UI plus a small JSON API on top of the language server
that Antigravity runs locally. Security model:

  * loopback clients are trusted, so the desktop experience needs no login
  * every other client needs the access token (cookie / header / ``?t=``),
    obtained once with the 6-digit pairing code printed on the host
  * writes additionally require the ``X-AGConsole: 1`` header plus a
    SameSite=Lax cookie, which a cross-site page cannot forge

Only the Python standard library is used; Linux, macOS and Windows are
supported through :mod:`agdeck.platform_util`.
"""

from __future__ import annotations

import argparse
import gzip
import hmac
import json
import mimetypes
import os
import secrets
import signal
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from . import APP_NAME, __version__
from . import client as backend
from . import demo as demo_backend
from .config import load as load_config
from .config import platform_note
from .paths import config_dir, describe as describe_paths, ensure_config_dir, static_dir, token_path
from .platform_util import detect_urls, tailscale_state
from .rpc import diagnose as rpc_diagnose

#: Swapped to :mod:`agdeck.demo` by ``--demo``; handlers always go through it.
ag = backend
DEMO = False
VERBOSE = False
STATIC = str(static_dir())

#: Live-update cadence, overridable from config.json
POLL_IDLE = 8.0
POLL_BUSY = 2.0

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}

LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
COOKIE_NAME = "ag_token"


# --------------------------------------------------------------------------
# 访问令牌 / 配对码
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 访问令牌 / 配对码
# --------------------------------------------------------------------------

def _load_token() -> str:
    """Persistent access token, stored outside the source tree (0600)."""
    path = token_path()
    try:
        tok = path.read_text(encoding="utf-8").strip()
    except OSError:
        tok = ""
    if len(tok) < 20:
        tok = secrets.token_urlsafe(24)
        try:
            ensure_config_dir()
            path.write_text(tok, encoding="utf-8")
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError:
            pass
    return tok


TOKEN = _load_token()
PAIR_CODE = f"{secrets.randbelow(900000) + 100000}"
_PAIR_FAILS: Dict[str, List[float]] = {}
_FAIL_LOCK = threading.Lock()
PAIR_WINDOW = 600.0
PAIR_MAX_FAILS = 6


def _pair_locked(ip: str) -> bool:
    with _FAIL_LOCK:
        rec = _PAIR_FAILS.get(ip)
        if not rec:
            return False
        count, first = rec
        if time.time() - first > PAIR_WINDOW:
            _PAIR_FAILS.pop(ip, None)
            return False
        return count >= PAIR_MAX_FAILS


def _pair_note_fail(ip: str) -> None:
    with _FAIL_LOCK:
        rec = _PAIR_FAILS.get(ip)
        now = time.time()
        if not rec or now - rec[1] > PAIR_WINDOW:
            _PAIR_FAILS[ip] = [1, now]
        else:
            rec[0] += 1


# --------------------------------------------------------------------------
# 业务 API
# --------------------------------------------------------------------------

class Api:
    @staticmethod
    def health() -> Dict[str, Any]:
        try:
            conn = ag.discover()
            return {"ok": True, "pid": conn["pid"], "port": conn["port"],
                    "ideVersion": conn.get("ideVersion", ""), "demo": DEMO}
        except ag.LSUnavailable as exc:
            return {"ok": False, "error": str(exc), "demo": DEMO}

    @staticmethod
    def doctor() -> Dict[str, Any]:
        """Everything a first-run wizard or a bug report needs."""
        return {
            "app": APP_NAME,
            "version": __version__,
            "demo": DEMO,
            "platform": platform_note(),
            "paths": describe_paths(),
            "tailscale": tailscale_state(),
            "backend": {"demo": True, "ok": True, "servers": [], "error": ""}
            if DEMO else rpc_diagnose(),
        }


    @staticmethod
    def sessions(include_archived: bool = True) -> Dict[str, Any]:
        return {
            "sessions": ag.list_sessions(include_archived, activity=True),
            "connection": Api.health(),
            "serverTime": time.time(),
        }

    @staticmethod
    def detail(cid: str, limit: int = 200) -> Dict[str, Any]:
        return ag.get_session_detail(cid, limit)

    @staticmethod
    def send(cid: str, text: str, model: Optional[str]) -> Dict[str, Any]:
        if not text.strip():
            raise ag.RPCError("invalid_argument", "消息不能为空")
        return ag.send_message(cid, text, model=model)

    @staticmethod
    def create(workspace: str, text: str, model: Optional[str]) -> Dict[str, Any]:
        if not workspace:
            raise ag.RPCError("invalid_argument", "请先选择或填写工作目录")
        return ag.start_cascade(workspace, text, model=model)

    @staticmethod
    def cancel(cid: str) -> Dict[str, Any]:
        return ag.cancel(cid)


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#4457ff">
<title>连接到 AG Deck</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;
       font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
       background:#eef0f4;color:#171a21;-webkit-font-smoothing:antialiased}
  .card{width:100%;max-width:360px;background:#fff;border-radius:16px;padding:26px 22px;
        box-shadow:0 10px 40px rgba(20,25,40,.08)}
  .logo{width:44px;height:44px;border-radius:12px;display:grid;place-items:center;color:#fff;
        font-weight:700;font-size:19px;background:linear-gradient(140deg,#4457ff,#7a5cff 60%,#22d3ee);
        box-shadow:0 4px 14px rgba(68,87,255,.32);margin-bottom:16px}
  h1{margin:0 0 6px;font-size:18px;font-weight:650}
  p{margin:0 0 18px;font-size:13px;color:#5b6472}
  input{width:100%;padding:14px 16px;border:1px solid #e3e6ec;border-radius:12px;
        font-size:22px;letter-spacing:8px;text-align:center;font-family:ui-monospace,Menlo,monospace;
        outline:none;background:#fbfcfe}
  input::placeholder{font-size:14px;letter-spacing:1px;color:#b3bac6;font-family:inherit}
  input:focus{border-color:#b9c2ff;box-shadow:0 0 0 4px rgba(68,87,255,.1)}
  button{width:100%;margin-top:12px;padding:14px;border:0;border-radius:12px;background:#4457ff;
         color:#fff;font-size:15px;font-weight:600;cursor:pointer}
  button:disabled{opacity:.6}
  .err{color:#c33;font-size:13px;margin-top:10px;min-height:18px}
  .hint{margin-top:18px;font-size:12px;color:#8b94a3;text-align:center;line-height:1.7}
</style></head>
<body>
  <div class="card">
    <div class="logo">A</div>
    <h1>连接到 AG Deck</h1>
    <p>输入电脑上显示的 6 位配对码<br><span style="font-size:12px;color:#8b94a3">Enter the 6-digit pairing code shown on your computer</span></p>
    <form id="f">
      <input id="code" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="one-time-code"
             placeholder="6 位数字" autofocus>
      <button id="btn" type="submit">连接</button>
    </form>
    <div class="err" id="err"></div>
    <div class="hint">配对码在电脑上的「手机访问」弹窗里<br>配对一次即可，之后自动登录</div>
  </div>
<script>
const f=document.getElementById('f'),code=document.getElementById('code'),
      btn=document.getElementById('btn'),err=document.getElementById('err');
f.addEventListener('submit',async(e)=>{
  e.preventDefault();
  const v=(code.value||'').trim();
  if(v.length!==6){err.textContent='请输入 6 位数字';return}
  btn.disabled=true;btn.textContent='连接中…';err.textContent='';
  try{
    const r=await fetch('/api/pair',{method:'POST',
      headers:{'Content-Type':'application/json','X-AGConsole':'1'},
      body:JSON.stringify({code:v})});
    if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.error||'配对失败')}
    location.replace('/');
  }catch(ex){
    err.textContent=ex.message;btn.disabled=false;btn.textContent='连接';
    code.select();
  }
});
code.addEventListener('input',()=>{code.value=code.value.replace(/\\D/g,'')});
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "AntigravityConsole/1.1"
    protocol_version = "HTTP/1.1"

    # ---------------- 基础响应 ----------------

    def _accepts_gzip(self) -> bool:
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _send_json(self, obj: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        extra = dict(headers or {})
        # 详情动辄几十 KB，手机上走中继时代价很高，能压就压
        if len(body) > 900 and self._accepts_gzip():
            body = gzip.compress(body, 5)
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_html(self, text: str, status: int = 200, headers: Optional[Dict[str, str]] = None) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _is_local(self) -> bool:
        return self._client_ip() in LOOPBACK

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return urllib.parse.unquote(v)
        return ""

    def _authed(self) -> bool:
        if self._is_local():
            return True
        cand = (self._cookie_token()
                or self.headers.get("X-AG-Token", "")
                or "")
        if not cand:
            return False
        return hmac.compare_digest(cand, TOKEN)

    def _csrf_ok(self) -> bool:
        # 浏览器跨站请求无法携带自定义头（会触发预检且我们不应答），配合 SameSite=Lax 足够
        return self.headers.get("X-AGConsole") == "1"

    def _cookie_header(self) -> Dict[str, str]:
        return {"Set-Cookie": f"{COOKIE_NAME}={urllib.parse.quote(TOKEN)}; Path=/; "
                              f"Max-Age=31536000; HttpOnly; SameSite=Lax"}

    def log_message(self, fmt: str, *args) -> None:
        if VERBOSE:
            sys.stderr.write("%s  %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---------------- GET ----------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 用带令牌的链接打开一次，落 Cookie 后跳到干净地址
        if path in ("/", "") and query.get("t"):
            if hmac.compare_digest(query["t"][0], TOKEN):
                self.send_response(302)
                self.send_header("Location", "/")
                for k, v in self._cookie_header().items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_html(LOGIN_PAGE, 401)
            return

        if path in ("/", "/index.html") and not self._authed():
            self._send_html(LOGIN_PAGE, 200)
            return

        if path.startswith("/api/"):
            if not self._authed():
                self._error(401, "未授权：请先在电脑上获取配对码")
                return
            self._api_get(path, query)
            return

        self._serve_static(path)

    def _api_get(self, path: str, query: Dict[str, List[str]]) -> None:
        if path == "/api/stream":
            try:
                limit = max(50, min(1500, int(query.get("limit", ["150"])[0])))
            except ValueError:
                limit = 150
            self._stream(query.get("session", [""])[0], limit)
            return

        try:
            if path == "/api/health":
                self._send_json(Api.health())
            elif path == "/api/sessions":
                self._send_json(Api.sessions(query.get("archived", ["1"])[0] != "0"))
            elif path.startswith("/api/sessions/") and "/steps/" in path:
                # 按需取某一步的完整内容（命令输出 / diff / 完整思考）
                parts = [p for p in path.split("/") if p]
                if len(parts) < 5:
                    self._error(400, "bad path")
                    return
                cid = parts[2]
                try:
                    idx = int(parts[4])
                except ValueError:
                    self._error(400, "bad step index")
                    return
                ev = ag.get_step_body(cid, idx)
                if ev is None:
                    self._error(404, "步骤不存在或已移出窗口")
                else:
                    self._send_json({"event": ev})
            elif path.startswith("/api/sessions/"):
                cid = path.split("/")[-1]
                limit = int(query.get("limit", ["200"])[0])
                self._send_json(Api.detail(cid, limit))
            elif path == "/api/models":
                self._send_json({"models": ag.models(), "default": ag.default_model()})
            elif path == "/api/workspaces":
                self._send_json({"workspaces": ag.workspace_candidates()})
            elif path == "/api/doctor":
                info = Api.doctor()
                info["local"] = self._is_local()
                info["paired"] = bool(self._cookie_token())
                self._send_json(info)
            elif path == "/api/net":
                port = self.server.server_address[1]
                self._send_json({
                    "urls": detect_urls(port),
                    "code": PAIR_CODE,
                    "token": TOKEN,
                    "local": self._is_local(),
                    "paired": bool(self._cookie_token()),
                    "tailscale": tailscale_state(),
                })
            else:
                self._error(404, "not found")
        except ag.LSUnavailable as exc:
            self._error(503, str(exc))
        except ag.RPCError as exc:
            self._error(400, exc.message)
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, str(exc))

    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            rel = path.lstrip("/")
        target = os.path.normpath(os.path.join(STATIC, rel))
        if not target.startswith(STATIC) or not os.path.isfile(target):
            self._error(404, "not found")
            return
        ext = os.path.splitext(target)[1]
        ctype = MIME.get(ext) or mimetypes.guess_type(target)[0] or "application/octet-stream"
        try:
            stat = os.stat(target)
            data = open(target, "rb").read()
        except OSError:
            self._error(404, "not found")
            return

        # 允许浏览器/WebView 缓存，但每次用 ETag 校验，改了就立刻生效
        etag = 'W/"%x-%x"' % (int(stat.st_mtime), len(data))
        cache = "public, max-age=0, must-revalidate"
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body = data
        extra: Dict[str, str] = {}
        if ctype.startswith(("text/", "application/javascript", "application/json")) \
                and len(body) > 900 and self._accepts_gzip():
            body = gzip.compress(body, 5)
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("ETag", etag)
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------- POST ----------------

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self._csrf_ok():
            self._error(403, "forbidden")
            return

        if path == "/api/pair":
            self._handle_pair()
            return

        if not self._authed():
            self._error(401, "未授权")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._error(400, "请求体不是合法 JSON")
            return

        try:
            if path == "/api/sessions":
                self._send_json(Api.create(
                    (payload.get("workspace") or "").strip(),
                    payload.get("text") or "",
                    payload.get("model"),
                ))
                return
            if path.startswith("/api/sessions/") and path.endswith("/messages"):
                cid = path.split("/")[3]
                self._send_json(Api.send(cid, payload.get("text") or "", payload.get("model")))
                return
            if path.startswith("/api/sessions/") and path.endswith("/cancel"):
                cid = path.split("/")[3]
                self._send_json(Api.cancel(cid))
                return
        except ag.LSUnavailable as exc:
            self._error(503, str(exc))
            return
        except ag.RPCError as exc:
            self._error(400, exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, str(exc))
            return

        self._error(404, "not found")

    def _handle_pair(self) -> None:
        ip = self._client_ip()
        if _pair_locked(ip):
            self._error(429, "尝试次数过多，请 10 分钟后再试")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._error(400, "请求体不是合法 JSON")
            return
        code = str(payload.get("code") or "").strip()
        if not code or not hmac.compare_digest(code, PAIR_CODE):
            _pair_note_fail(ip)
            self._error(403, "配对码不正确")
            return
        self._send_json({"ok": True}, headers=self._cookie_header())

    # ---------------- SSE ----------------

    def _stream(self, session_id: str, limit: int = 150) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")

        # 手机走中继时，未压缩的 JSON 太贵；SSE 也能整体 gzip，按帧 flush 即可
        gz = None
        if self._accepts_gzip():
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            gz = zlib.compressobj(5, zlib.DEFLATED, 31)
        self.end_headers()

        def emit(chunk: bytes) -> None:
            if gz is not None:
                chunk = gz.compress(chunk) + gz.flush(zlib.Z_SYNC_FLUSH)
            self.wfile.write(chunk)

        last_sig = None
        last_count = -1
        last_detail_at = 0.0
        last_detail_sig = None
        last_index_sent = None
        last_offset = None

        while True:
            try:
                payload: Dict[str, Any] = {"connection": Api.health()}
                try:
                    sessions = ag.list_sessions(activity=True)
                    payload["sessions"] = sessions
                except Exception as exc:  # noqa: BLE001
                    payload["error"] = str(exc)
                    sessions = []

                watch = next((s for s in sessions if s["id"] == session_id), None)
                fresh_enough = (time.time() - last_detail_at) > 2.4
                need_detail = watch and (
                    watch["running"] or watch["stepCount"] != last_count or fresh_enough
                )
                if need_detail:
                    try:
                        detail = ag.get_session_detail(session_id, limit, session=watch)
                        evs = detail["events"]
                        prog = detail["progress"]
                        # 只有内容真的变了才重发详情，否则手机端每秒都在下载同样的东西
                        dsig = (
                            len(evs),
                            evs[-1]["index"] if evs else -1,
                            (evs[-1].get("status") if evs else ""),
                            len((evs[-1].get("text") or "") if evs else ""),
                            prog.get("state"),
                            prog.get("turnSteps"),
                        )
                        if dsig != last_detail_sig:
                            # 窗口只是往后滑（老步骤被挤出、新步骤追加）时只发增量；
                            # offset 变大 = 往前滑，变小 = 用户加载了更早的内容，需要整包
                            can_delta = (
                                last_index_sent is not None
                                and evs
                                and last_offset is not None
                                and detail["offset"] >= last_offset
                                and evs[0]["index"] <= last_index_sent + 1
                            )
                            if can_delta:
                                new_evs = [e for e in evs if e["index"] > last_index_sent]
                                if not new_evs and evs:
                                    # 同一步在流式增长（文字变长），重发最后一步即可
                                    new_evs = [evs[-1]]
                                if new_evs:
                                    payload["detailDelta"] = {
                                        "sessionId": session_id,
                                        "from": detail["offset"],
                                        "events": new_evs,
                                        "totalSteps": detail["totalSteps"],
                                        "progress": prog,
                                        "hasMore": detail["hasMore"],
                                    }
                                    last_index_sent = max(last_index_sent, new_evs[-1]["index"])
                                    last_offset = detail["offset"]
                            else:
                                payload["detail"] = detail
                                last_index_sent = evs[-1]["index"] if evs else None
                                last_offset = detail["offset"]
                            last_detail_sig = dsig
                        last_count = watch["stepCount"]
                    except Exception as exc:  # noqa: BLE001
                        payload["detailError"] = str(exc)
                    last_detail_at = time.time()

                sig = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if sig != last_sig:
                    emit(b"data: " + sig.encode("utf-8") + b"\n\n")
                    last_sig = sig
                else:
                    emit(b": ping\n\n")

                # 空闲时没必要 2 秒一问，退到 8 秒，省电也省中继流量
                any_running = any(s.get("running") for s in sessions)
                if watch and watch.get("running"):
                    delay = POLL_BUSY
                elif any_running:
                    delay = max(POLL_BUSY, (POLL_BUSY + POLL_IDLE) / 2)
                else:
                    delay = POLL_IDLE
                time.sleep(delay)

            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two processes bind the same port, which
    # would hide "address already in use" instead of reporting it.
    allow_reuse_address = os.name != "nt"


def _bind(host: str, port: int) -> "tuple[Any, int, str]":
    """Bind the requested port, falling back to the next free ones."""
    last: Optional[OSError] = None
    for candidate in range(port, min(port + 10, 65536)):
        try:
            return _Server((host, candidate), Handler), candidate, ""
        except OSError as exc:
            last = exc
            if getattr(exc, "errno", None) not in (48, 98, 10048):
                break
    reason = str(last) if last else "unknown error"
    hint = ""
    if "in use" in reason.lower() or "使用" in reason or getattr(last, "errno", 0) in (48, 98, 10048):
        hint = f"端口 {port}~{port + 9} 都被占用，换一个：agdeck --port 9000"
    elif getattr(last, "errno", 0) in (1, 13):
        hint = "端口小于 1024 需要管理员权限，换一个：agdeck --port 8848"
    return None, port, f"{reason}\n  {hint}" if hint else reason


def _print_status(port: int, host: str) -> None:
    line = "  " + "─" * 58

    print()
    print(f"  {APP_NAME} {__version__}  ·  Antigravity dashboard")
    print(line)

    if DEMO:
        print("  ◆ DEMO MODE — 展示示例数据，不连接 Antigravity")
    else:
        try:
            conn = ag.discover(force=True)
            print(f"  ✓ 已连接 Antigravity   PID {conn['pid']} · "
                  f"{conn['scheme']}://127.0.0.1:{conn['port']}"
                  + (f" · v{conn['ideVersion']}" if conn.get("ideVersion") else ""))
        except ag.LSUnavailable as exc:
            print(f"  ! 没有连上 Antigravity / not connected")
            print(f"    {exc}")
            print()
            print("    1. 打开 Antigravity，并让 agent 面板至少打开过一次")
            print("    2. 服务会自动重连，不需要重启本程序")
            print("    仍然失败？运行 `agdeck doctor` 查看诊断")

    local = f"http://127.0.0.1:{port}/"
    print()
    print(f"  桌面 / Desktop   {local}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        others = [u for u in detect_urls(port) if not u["url"].startswith("http://127.0.0.1")]
        if others:
            first = others[0]
            print(f"  手机 / Phone     {first['url']}")
            for extra in others[1:3]:
                print(f"                   {extra['url']}  ({extra['label']})")
            if len(others) > 3:
                print(f"                   还有 {len(others) - 3} 个地址，见看板右上角「手机访问」")
        else:
            print("  手机 / Phone     未检测到局域网地址（仅本机可用）")
    else:
        print("  手机 / Phone     已禁用（--local-only）")

    print(f"  配对码 / Pair    {PAIR_CODE}")
    print(line)
    print(f"  配置 / Config    {config_dir()}")
    print(f"  探测 / Tailscale {'已连接' if tailscale_state()['connected'] else '未连接'}"
          f"   ·   {platform_note()}")
    print("  Ctrl+C 退出")
    print()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agdeck",
        description="AG Deck — a local dashboard for Antigravity Cascade sessions.",
        epilog="https://github.com/  ·  MIT License  ·  unofficial project",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--port", type=int, help="HTTP 端口（默认 8848）")
    parser.add_argument("--host", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--local-only", action="store_true", help="只监听 127.0.0.1")
    parser.add_argument("--open", action="store_true", dest="open_browser",
                        help="启动后打开浏览器")
    parser.add_argument("--demo", action="store_true", help="用示例数据演示界面，不连接 Antigravity")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印请求日志")
    parser.add_argument("--config", action="store_true", help="打印解析后的配置并退出")
    args = parser.parse_args(argv)

    global ag, DEMO, VERBOSE
    DEMO = bool(args.demo)
    VERBOSE = bool(args.verbose)
    # keep the banner readable when stdout is a pipe (systemd, nohup, tmux)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    if DEMO:
        ag = demo_backend
        setattr(Handler, "_demo", True)

    cfg = load_config()
    port = int(args.port or cfg["port"])
    host = args.host or ("127.0.0.1" if args.local_only else cfg["host"])

    global POLL_IDLE, POLL_BUSY
    try:
        POLL_IDLE = max(1.0, float(cfg.get("pollIdleSeconds") or 8.0))
        POLL_BUSY = max(0.5, float(cfg.get("pollBusySeconds") or 2.0))
    except (TypeError, ValueError):
        pass

    if args.config:
        print(json.dumps({
            "config": cfg,
            "resolved": {"port": port, "host": host, "demo": DEMO},
            "paths": {
                "configDir": str(config_dir()),
                "token": str(token_path()),
                "static": STATIC,
            },
        }, ensure_ascii=False, indent=2))
        return

    httpd, actual_port, error = _bind(host, port)
    if httpd is None:
        print(f"启动失败 / cannot bind {host}:{port}\n  {error}", file=sys.stderr)
        sys.exit(1)
    if actual_port != port:
        print(f"! 端口 {port} 已被占用，改用 {actual_port}")

    _print_status(actual_port, host)

    if args.open_browser or cfg.get("openBrowser"):
        threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{actual_port}/")).start()

    def _stop(_signum=None, _frame=None):
        print("\n已退出 / bye")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in ("SIGINT", "SIGTERM"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), _stop)
            except (ValueError, OSError):
                pass

    try:
        httpd.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
