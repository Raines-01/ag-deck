"""Command line entry point: ``agdeck`` / ``python -m agdeck``.

    agdeck                      start the web dashboard (same as `agdeck serve`)
    agdeck list --active        sessions currently running
    agdeck show 3e1854          last steps of a session (id prefix is enough)
    agdeck watch 3e1854         follow a session until it goes idle
    agdeck send 3e1854 "..."    send an instruction
    agdeck new ~/proj "..."     start a new session
    agdeck models               list models Antigravity offers
    agdeck doctor               diagnose the connection
    agdeck net                  print URLs a phone can use
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from . import APP_NAME, __version__
from . import client as ag
from .config import load as load_config, save as save_config
from .paths import config_dir, describe as describe_paths
from .platform_util import detect_urls, tailscale_state
from .rpc import diagnose, rpc

STATUS_ICON = {"working": "●", "waiting": "◐", "idle": "○"}


def _one_line(text: Any, width: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:width] + ("…" if len(flat) > width else "")


def resolve(prefix: str) -> str:
    rows = ag.list_sessions()
    hits = [s for s in rows if s["id"].startswith(prefix)]
    if not hits:
        sys.exit(f"没有找到匹配 {prefix} 的会话 / no session matches {prefix}")
    if len(hits) > 1:
        print("匹配到多个会话，请给更长的前缀 / ambiguous prefix:")
        for s in hits:
            print(f"  {s['id'][:8]}  {s['title']}")
        sys.exit(1)
    return hits[0]["id"]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args) -> None:
    rows = ag.list_sessions(activity=True)
    if args.active:
        rows = [s for s in rows if s["running"]]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("没有会话 / no sessions")
        return
    for s in rows:
        icon = "●" if s["running"] else "○"
        flag = " [archived]" if s["archived"] else ""
        print(f"{icon} {s['id'][:8]}  {s['title'][:44]:<46} {s['stepCount']:>6}  "
              f"{s['workspaceName']}{flag}")
        if s["running"] and s.get("activity"):
            print(f"           └─ {s['activity']}")


def cmd_show(args) -> None:
    cid = resolve(args.id)
    detail = ag.get_session_detail(cid, args.limit)
    progress = detail["progress"]
    session = detail["session"]
    print(f"\n{session['title']}")
    print(f"{session['workspace']}   {session['status']}   {progress['totalSteps']} steps\n")
    for ev in detail["events"][-args.lines:]:
        stamp = (ev.get("at") or "")[11:19]
        if ev["kind"] == "user":
            print(f"\n[{stamp}] you: {_one_line(ev.get('text'), 100)}")
        elif ev["kind"] == "assistant":
            text = _one_line(ev.get("text"), 100)
            if text:
                print(f"[{stamp}] A: {text}")
            elif ev.get("thinking"):
                print(f"[{stamp}]   · thinking ({len(ev['thinking'])} chars)")
        elif ev["kind"] == "tool":
            detail_text = ev.get("command") or ev.get("path") or _one_line(ev.get("text"), 70)
            print(f"[{stamp}]   ├ {ev['label']}: {_one_line(detail_text, 90)}")
        elif ev["kind"] == "error":
            print(f"[{stamp}]   ! {_one_line(ev.get('text'), 90)}")


def cmd_watch(args) -> None:
    cid = resolve(args.id)
    last = -1
    try:
        while True:
            detail = ag.get_session_detail(cid, 40)
            progress = detail["progress"]
            if detail["totalSteps"] != last:
                last = detail["totalSteps"]
                print(f"[{time.strftime('%H:%M:%S')}] {STATUS_ICON.get(progress['state'], '·')} "
                      f"{progress['state']:<8} {progress['totalSteps']} steps  "
                      f"{progress['currentAction']}")
            if args.once and not progress["running"] and progress["state"] == "idle":
                return
            time.sleep(2.5)
    except KeyboardInterrupt:
        print("\nstopped")


def cmd_send(args) -> None:
    cid = resolve(args.id)
    result = ag.send_message(cid, args.text, model=args.model)
    print(f"sent (model {result['model']})")


def cmd_new(args) -> None:
    path = os.path.abspath(os.path.expanduser(args.path))
    result = ag.start_cascade(path, args.text, model=args.model)
    print(f"created {result['cascadeId'][:8]} (model {result['model']})")


def cmd_models(args) -> None:
    default = ag.default_model()
    for model in ag.models():
        mark = "*" if model["id"] == default else " "
        dead = "  [disabled]" if "disabled" in (model.get("modelId") or "") else ""
        print(f"{mark} {model['id']:<30} {model['label']}{dead}")


def cmd_doctor(args) -> None:
    info = diagnose()
    print(json.dumps({**info, "paths": describe_paths(),
                      "tailscale": tailscale_state()}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if info.get("ok") else 2)


def cmd_net(args) -> None:
    cfg = load_config()
    port = args.port or cfg["port"]
    for item in detect_urls(port):
        print(f"{item['label']:<16} {item['url']}")
    state = tailscale_state()
    if state["installed"] and not state["connected"]:
        print("\nTailscale 已安装但未登录，运行 `tailscale up` 后这里会自动出现 100.x 地址。")
    elif not state["installed"]:
        print("\n未检测到 Tailscale；手机与电脑在同一 Wi-Fi 下也能访问上面的局域网地址。")


def cmd_config(args) -> None:
    cfg = load_config()
    if args.set:
        for pair in args.set:
            key, _, value = pair.partition("=")
            if key not in cfg:
                sys.exit(f"unknown key: {key}")
            current = cfg[key]
            if isinstance(current, bool):
                cfg[key] = value.lower() in ("1", "true", "yes", "on")
            elif isinstance(current, int):
                cfg[key] = int(value)
            elif isinstance(current, float):
                cfg[key] = float(value)
            else:
                cfg[key] = value
        path = save_config(cfg)
        print(f"saved {path}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print(f"\nconfig dir: {config_dir()}")


def cmd_serve(args) -> None:
    from .server import main as serve

    forwarded: List[str] = []
    if args.port:
        forwarded += ["--port", str(args.port)]
    if args.host:
        forwarded += ["--host", args.host]
    if args.local_only:
        forwarded.append("--local-only")
    if args.open:
        forwarded.append("--open")
    if args.demo:
        forwarded.append("--demo")
    if args.verbose:
        forwarded.append("--verbose")
    serve(forwarded)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agdeck",
        description=f"{APP_NAME} — use Antigravity on your computer from your phone or another machine.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="启动看板（默认）")
    serve.add_argument("--port", type=int)
    serve.add_argument("--host")
    serve.add_argument("--local-only", action="store_true")
    serve.add_argument("--open", action="store_true")
    serve.add_argument("--demo", action="store_true")
    serve.add_argument("--verbose", "-v", action="store_true")
    serve.set_defaults(func=cmd_serve)

    listing = sub.add_parser("list", help="列出会话")
    listing.add_argument("--active", action="store_true")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="查看会话最近的动作")
    show.add_argument("id")
    show.add_argument("--limit", type=int, default=200)
    show.add_argument("--lines", type=int, default=25)
    show.set_defaults(func=cmd_show)

    watch = sub.add_parser("watch", help="实时跟踪会话")
    watch.add_argument("id")
    watch.add_argument("--once", action="store_true")
    watch.set_defaults(func=cmd_watch)

    send = sub.add_parser("send", help="发送指令")
    send.add_argument("id")
    send.add_argument("text")
    send.add_argument("--model")
    send.set_defaults(func=cmd_send)

    new = sub.add_parser("new", help="新建会话并发送第一条指令")
    new.add_argument("path")
    new.add_argument("text")
    new.add_argument("--model")
    new.set_defaults(func=cmd_new)

    sub.add_parser("models", help="列出可用模型").set_defaults(func=cmd_models)
    sub.add_parser("doctor", help="诊断连接问题").set_defaults(func=cmd_doctor)
    sub.add_parser("net", help="打印手机可访问的地址").set_defaults(func=cmd_net)

    config = sub.add_parser("config", help="查看/修改配置")
    config.add_argument("--set", action="append", metavar="KEY=VALUE")
    config.set_defaults(func=cmd_config)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # Bare `agdeck` starts the server; everything else is a subcommand.
        args = parser.parse_args(["serve"] + list(argv if argv is not None else sys.argv[1:]))
    try:
        args.func(args)
    except ag.LSUnavailable as exc:
        sys.exit(f"连接失败 / connection failed: {exc}")
    except ag.RPCError as exc:
        sys.exit(f"调用失败 / rpc failed: {exc.message}")


if __name__ == "__main__":
    main()
