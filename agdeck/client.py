"""Cascade session layer: read, normalise and write Antigravity sessions.

The functions here are pure transformations on top of :mod:`agdeck.rpc`, except
for the small write helpers at the bottom (send / start / cancel).

Event dictionaries are the contract with the web UI; ``normalize_step`` and
``compact_event`` are covered by unit tests because every screen depends on
their shape.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .rpc import LSUnavailable, RPCError, discover, rpc

RUNNING_STATES = {
    "CASCADE_RUN_STATUS_RUNNING",
    "CASCADE_RUN_STATUS_BUSY",
    "CASCADE_RUN_STATUS_CANCELING",
}


def _basename(path: str) -> str:
    """Works with both POSIX and Windows separators, regardless of host OS."""
    return os.path.basename(path.replace("\\", "/").rstrip("/"))


# --------------------------------------------------------------------------
# 会话读取
# --------------------------------------------------------------------------

def _uri_to_path(uri: str) -> str:
    """file:// URI to a readable path, independent of the host OS.

    Antigravity emits POSIX URIs (``file:///home/me/app``) on Linux/macOS and
    drive URIs (``file:///C:/Users/me/app``) on Windows. ``url2pathname`` is
    OS-specific — on Windows it would turn the POSIX form into ``\\home\\...``
    — so we normalise both shapes ourselves. The result is only used for
    display, never for filesystem access.
    """
    if not uri:
        return ""
    if not uri.startswith("file://"):
        return uri
    raw = urllib.parse.unquote(uri[len("file://"):])
    if re.match(r"^/[A-Za-z]:", raw):        # Windows drive: /C:/Users/me
        return raw[1:].replace("/", "\\")
    if not raw.startswith("/"):              # UNC share: file://server/share
        return "//" + raw
    return raw


def list_sessions(include_archived: bool = True, activity: bool = False) -> List[Dict[str, Any]]:
    data = rpc("GetAllCascadeTrajectories", {"excludeSubtrajectories": True})
    summaries = data.get("trajectorySummaries") or {}
    out: List[Dict[str, Any]] = []

    for cid, s in summaries.items():
        ws = (s.get("workspaces") or [{}])[0]
        ann = s.get("annotations") or {}
        status = s.get("status") or "CASCADE_RUN_STATUS_UNSPECIFIED"
        archived = bool(ann.get("archived"))
        if archived and not include_archived:
            continue
        folder = _uri_to_path(ws.get("workspaceFolderAbsoluteUri", ""))
        out.append({
            "id": cid,
            "title": ann.get("title") or s.get("summary") or "未命名会话",
            "status": status,
            "running": status in RUNNING_STATES,
            "stepCount": int(s.get("stepCount") or 0),
            "createdAt": s.get("createdTime"),
            "updatedAt": s.get("lastModifiedTime"),
            "workspace": folder,
            "workspaceName": _basename(folder) or folder,
            "repo": (ws.get("repository") or {}).get("computedName", ""),
            "branch": ws.get("branchName", ""),
            "archived": archived,
        })

    out.sort(key=lambda x: x.get("updatedAt") or "", reverse=True)

    if activity:
        # 给正在运行的会话补一句"现在在干什么"，让侧边栏直接可读
        for s in out:
            if not s["running"]:
                continue
            try:
                offset = max(0, s["stepCount"] - 6)
                steps = get_steps(s["id"], offset, timeout=8)
                if steps:
                    events = normalize_steps(steps, offset)
                    s["activity"] = build_progress(events, s["status"], s["stepCount"])["currentAction"]
                else:
                    s["activity"] = "正在工作…"
            except Exception:
                s["activity"] = "正在工作…"

    return out


def get_steps(cascade_id: str, offset: int = 0, timeout: float = 45.0) -> List[Dict[str, Any]]:
    data = rpc(
        "GetCascadeTrajectorySteps",
        {"cascadeId": cascade_id, "stepOffset": max(0, int(offset))},
        timeout=timeout,
    )
    return data.get("steps") or []


def tail_steps(cascade_id: str, count: int = 150,
               total: Optional[int] = None) -> Tuple[List[Dict[str, Any]], int, int]:
    """取尾部若干步。返回 (steps, 起始下标, 总数)。"""
    if total is None:
        total = 0
        for s in list_sessions():
            if s["id"] == cascade_id:
                total = s["stepCount"]
                break
    offset = max(0, total - count) if total else 0
    steps = get_steps(cascade_id, offset)
    if not steps and offset:
        steps = get_steps(cascade_id, 0)
        offset = 0
    return steps, offset, max(total, offset + len(steps))


# --------------------------------------------------------------------------
# 步骤归一化
# --------------------------------------------------------------------------

_STEP_KIND = {
    "CORTEX_STEP_TYPE_USER_INPUT": "user",
    "CORTEX_STEP_TYPE_PLANNER_RESPONSE": "assistant",
    "CORTEX_STEP_TYPE_SYSTEM_MESSAGE": "system",
    "CORTEX_STEP_TYPE_ERROR_MESSAGE": "error",
    "CORTEX_STEP_TYPE_ASK_QUESTION": "question",
    "CORTEX_STEP_TYPE_CHECKPOINT": "checkpoint",
}

_STEP_LABEL = {
    "CORTEX_STEP_TYPE_RUN_COMMAND": "执行命令",
    "CORTEX_STEP_TYPE_VIEW_FILE": "读取文件",
    "CORTEX_STEP_TYPE_CODE_ACTION": "修改代码",
    "CORTEX_STEP_TYPE_GREP_SEARCH": "搜索代码",
    "CORTEX_STEP_TYPE_CODE_SEARCH": "检索代码",
    "CORTEX_STEP_TYPE_LIST_DIRECTORY": "浏览目录",
    "CORTEX_STEP_TYPE_FIND": "查找",
    "CORTEX_STEP_TYPE_FIND_ALL_REFERENCES": "查找引用",
    "CORTEX_STEP_TYPE_MCP_TOOL": "调用 MCP 工具",
    "CORTEX_STEP_TYPE_SEARCH_WEB": "联网搜索",
    "CORTEX_STEP_TYPE_READ_URL_CONTENT": "读取网页",
    "CORTEX_STEP_TYPE_INVOKE_SUBAGENT": "派生子代理",
    "CORTEX_STEP_TYPE_TASK_BOUNDARY": "任务边界",
    "CORTEX_STEP_TYPE_VIEW_FILE_OUTLINE": "查看文件结构",
    "CORTEX_STEP_TYPE_VIEW_CODE_ITEM": "查看代码片段",
    "CORTEX_STEP_TYPE_RETRIEVE_MEMORY": "检索记忆",
    "CORTEX_STEP_TYPE_WRITE_BLOB": "写入附件",
    "CORTEX_STEP_TYPE_GENERATE_IMAGE": "生成图片",
    "CORTEX_STEP_TYPE_BROWSER_SUBAGENT": "浏览器操作",
    "CORTEX_STEP_TYPE_TOOL_CALL_PROPOSAL": "请求工具授权",
    "CORTEX_STEP_TYPE_TRAJECTORY_CHOICE": "等待选择",
    "CORTEX_STEP_TYPE_SUGGESTED_RESPONSES": "建议回复",
    "CORTEX_STEP_TYPE_NOTIFY_USER": "通知",
    "CORTEX_STEP_TYPE_FINISH": "收尾",
    "CORTEX_STEP_TYPE_PLAN_INPUT": "计划输入",
    "CORTEX_STEP_TYPE_BRAIN_UPDATE": "更新记忆",
}

_PAYLOAD_KEYS = {
    "CORTEX_STEP_TYPE_USER_INPUT": "userInput",
    "CORTEX_STEP_TYPE_PLANNER_RESPONSE": "plannerResponse",
    "CORTEX_STEP_TYPE_RUN_COMMAND": "runCommand",
    "CORTEX_STEP_TYPE_VIEW_FILE": "viewFile",
    "CORTEX_STEP_TYPE_CODE_ACTION": "codeAction",
    "CORTEX_STEP_TYPE_SYSTEM_MESSAGE": "systemMessage",
    "CORTEX_STEP_TYPE_ERROR_MESSAGE": "errorMessage",
    "CORTEX_STEP_TYPE_ASK_QUESTION": "askQuestion",
    "CORTEX_STEP_TYPE_GREP_SEARCH": "grepSearch",
    "CORTEX_STEP_TYPE_CODE_SEARCH": "codeSearch",
    "CORTEX_STEP_TYPE_LIST_DIRECTORY": "listDirectory",
    "CORTEX_STEP_TYPE_MCP_TOOL": "mcpTool",
    "CORTEX_STEP_TYPE_SEARCH_WEB": "searchWeb",
    "CORTEX_STEP_TYPE_INVOKE_SUBAGENT": "invokeSubagent",
}

_DIFF_LINE = {
    "UNIFIED_DIFF_LINE_TYPE_INSERT": "+",
    "UNIFIED_DIFF_LINE_TYPE_DELETE": "-",
    "UNIFIED_DIFF_LINE_TYPE_UNCHANGED": " ",
}


def _generic_label(step_type: str) -> str:
    tail = step_type.replace("CORTEX_STEP_TYPE_", "")
    return tail.replace("_", " ").title()


def _payload(step: Dict[str, Any], step_type: str) -> Dict[str, Any]:
    key = _PAYLOAD_KEYS.get(step_type)
    if key and isinstance(step.get(key), dict):
        return step[key]
    for k, v in step.items():
        if k in ("type", "status", "metadata"):
            continue
        if isinstance(v, dict):
            return v
    return {}


def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n…（已截断，共 {len(s)} 字符）"


def _diff_lines(payload: Dict[str, Any], limit: int = 300) -> List[Dict[str, str]]:
    edit = ((payload.get("actionResult") or {}).get("edit")) or {}
    diff = (edit.get("diff") or {})
    unified = diff.get("unifiedDiff") or {}
    lines = unified.get("lines") or []
    out = []
    for ln in lines[:limit]:
        mark = _DIFF_LINE.get(ln.get("type", ""), " ")
        out.append({"mark": mark, "text": ln.get("text", "")})
    return out


def _parse_ts(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    text = str(value).rstrip("Z")
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _elapsed_ms(meta: Dict[str, Any]) -> int:
    started = _parse_ts(meta.get("startedAt"))
    done = _parse_ts(meta.get("completedAt"))
    if started and done:
        delta = (done - started).total_seconds() * 1000
        return int(delta) if delta > 0 else 0
    return 0


#: 一轮结束后 Antigravity 的 summary 状态还会滞后几秒才翻成 IDLE。
#: 超过这个宽限期仍是 RUNNING，就说明真有后台任务在跑。
TURN_END_GRACE = 8.0


def _seconds_since(value: Any) -> Optional[float]:
    parsed = _parse_ts(value)
    if parsed is None:
        return None
    now = datetime.datetime.utcnow()
    return max(0.0, (now - parsed).total_seconds())


def normalize_step(step: Dict[str, Any], index: int) -> Dict[str, Any]:
    stype = step.get("type") or ""
    meta = step.get("metadata") or {}
    usage = meta.get("modelUsage") or {}
    payload = _payload(step, stype)

    task = step.get("taskDetails") or {}
    ev: Dict[str, Any] = {
        "index": index,
        "type": stype,
        "kind": _STEP_KIND.get(stype, "tool"),
        "label": _STEP_LABEL.get(stype) or _generic_label(stype),
        "status": (step.get("status") or "").replace("CORTEX_STEP_STATUS_", ""),
        "at": meta.get("createdAt"),
        "startedAt": meta.get("startedAt"),
        "finishedAt": meta.get("completedAt"),
        "model": usage.get("model") or "",
        "tokensIn": int(usage.get("inputTokens") or 0),
        "tokensOut": int(usage.get("outputTokens") or 0),
        "ms": _elapsed_ms(meta),
    }
    if task:
        # 后台任务：step 本身已经 DONE，但任务仍在跑（日志/进程由 IDE 管）
        ev["task"] = {
            "id": task.get("id", ""),
            "title": task.get("title") or (task.get("description") or "")[:80],
        }

    if stype == "CORTEX_STEP_TYPE_USER_INPUT":
        items = payload.get("items") or []
        text = payload.get("userResponse") or "".join(
            i.get("text", "") for i in items if isinstance(i, dict)
        )
        ev["text"] = _clip(text, 20000)
        cfg = ((payload.get("userConfig") or {}).get("plannerConfig") or {})
        ev["model"] = ev["model"] or cfg.get("planModel", "")

    elif stype == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
        ev["text"] = _clip(payload.get("modifiedResponse") or payload.get("response") or "", 20000)
        ev["thinking"] = _clip(payload.get("thinking") or "", 8000)
        calls = payload.get("toolCalls") or []
        ev["toolCalls"] = [
            {
                "name": c.get("name", ""),
                "args": _clip(c.get("argumentsJson", ""), 900),
            }
            for c in calls
        ]
        ev["stopReason"] = (payload.get("stopReason") or "").replace("STOP_REASON_", "")

    elif stype == "CORTEX_STEP_TYPE_RUN_COMMAND":
        ev["command"] = _clip(payload.get("commandLine") or payload.get("proposedCommandLine") or "", 500)
        ev["cwd"] = payload.get("cwd", "")
        ev["exitCode"] = payload.get("exitCode")
        out = payload.get("combinedOutput") or {}
        ev["output"] = _clip(out.get("full", "") if isinstance(out, dict) else out, 6000)

    elif stype == "CORTEX_STEP_TYPE_VIEW_FILE":
        path = _uri_to_path(payload.get("absolutePathUri", ""))
        ev["path"] = path
        start = int(payload.get("startLine") or 0)
        end = int(payload.get("endLine") or 0)
        ev["range"] = f"{start}-{end}"
        ev["numLines"] = max(0, end - start + 1)
        ev["fileLines"] = payload.get("numLines")
        ev["preview"] = _clip(payload.get("content", ""), 1500)

    elif stype == "CORTEX_STEP_TYPE_CODE_ACTION":
        edit = ((payload.get("actionResult") or {}).get("edit")) or {}
        ev["path"] = _uri_to_path(edit.get("absoluteUri", ""))
        ev["description"] = _clip(payload.get("description", ""), 500)
        stats = payload.get("diffStats") or {}
        ev["additions"] = int(stats.get("additions") or 0)
        ev["deletions"] = int(stats.get("deletions") or 0)
        ev["diff"] = _diff_lines(payload)

    elif stype == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
        err = payload.get("error") or payload
        ev["text"] = _clip(
            err.get("userErrorMessage") or err.get("shortError") or err.get("message") or "", 3000
        )
        ev["detailText"] = _clip(err.get("fullError") or "", 4000)

    elif stype == "CORTEX_STEP_TYPE_SYSTEM_MESSAGE":
        info = payload.get("renderInfo") or {}
        ev["text"] = _clip(info.get("title") or payload.get("message", ""), 2000)
        ev["subtype"] = payload.get("eventType", "")

    elif stype == "CORTEX_STEP_TYPE_ASK_QUESTION":
        ev["text"] = _clip(payload.get("question") or json.dumps(payload, ensure_ascii=False)[:800], 2000)

    elif stype == "CORTEX_STEP_TYPE_GENERIC" or meta.get("toolCall"):
        # 一些内置工具（如 manage_task）没有独立步骤类型，藏在 metadata.toolCall 里
        tc = meta.get("toolCall") or {}
        name = tc.get("name", "")
        try:
            args = json.loads(tc.get("argumentsJson") or "{}")
        except (ValueError, TypeError):
            args = {}
        if name == "manage_task":
            ev["label"] = "子任务"
            ev["text"] = args.get("toolSummary") or args.get("toolAction") or ""
            ev["subtype"] = args.get("Action", "")
        else:
            ev["label"] = name.replace("_", " ").title() or "工具调用"
            ev["text"] = args.get("toolSummary") or _clip(tc.get("argumentsJson", ""), 300)

    else:
        # 通用兜底：挑几个标量字段展示
        bits = []
        for k, v in payload.items():
            if isinstance(v, (str, int, float, bool)) and v not in ("", None):
                bits.append(f"{k}={_clip(v, 160)}")
            if len(bits) >= 4:
                break
        ev["text"] = "\n".join(bits)

    return ev


def normalize_steps(steps: Iterable[Dict[str, Any]], start: int = 0) -> List[Dict[str, Any]]:
    return [normalize_step(s, start + i) for i, s in enumerate(steps)]


# 手机端（尤其走 Tailscale 中继）扛不住几百 KB 的详情，把"大块内容"留给展开时按需取：
# 命令行输出、diff、文件预览、超长思考/回复都只发摘要 + hasBody 标记。
COMPACT_TEXT_LIMIT = 4000
COMPACT_USER_LIMIT = 8000
COMPACT_THINKING_LIMIT = 300


def compact_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ev)
    body = False
    for key in ("output", "preview", "detailText"):
        if out.pop(key, None):
            body = True
    if out.pop("diff", None):
        body = True

    thinking = out.get("thinking") or ""
    if len(thinking) > COMPACT_THINKING_LIMIT:
        out["thinking"] = thinking[:COMPACT_THINKING_LIMIT]
        out["thinkingLen"] = len(thinking)
        out["thinkingTruncated"] = True
        body = True

    text = out.get("text") or ""
    cap = COMPACT_TEXT_LIMIT if out.get("kind") == "assistant" else COMPACT_USER_LIMIT
    if len(text) > cap:
        out["text"] = text[:cap]
        out["textLen"] = len(text)
        out["textTruncated"] = True
        body = True

    if body:
        out["hasBody"] = True
    return out


# 最近打开过的会话详情（含完整内容），供"展开某一步"时直接取
_DETAIL_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 3


def _cache_detail(cid: str, offset: int, total: int, full: List[Dict[str, Any]]) -> None:
    with _CACHE_LOCK:
        _DETAIL_CACHE[cid] = {"offset": offset, "total": total, "full": full,
                              "at": time.time()}
        while len(_DETAIL_CACHE) > _CACHE_MAX:
            oldest = min(_DETAIL_CACHE.items(), key=lambda kv: kv[1]["at"])[0]
            _DETAIL_CACHE.pop(oldest, None)


def get_step_body(cascade_id: str, index: int) -> Optional[Dict[str, Any]]:
    """按需取某一步的完整内容（命令输出 / diff / 完整思考等）。"""
    with _CACHE_LOCK:
        entry = _DETAIL_CACHE.get(cascade_id)
        if entry:
            for ev in entry["full"]:
                if ev["index"] == index:
                    return ev

    # 缓存没命中就重新拉一次尾部（顺带把缓存填上）
    get_session_detail(cascade_id, 200, compact=False)
    with _CACHE_LOCK:
        entry = _DETAIL_CACHE.get(cascade_id)
        if entry:
            for ev in entry["full"]:
                if ev["index"] == index:
                    return ev
    return None


def describe(ev: Optional[Dict[str, Any]]) -> str:
    """一句话描述某一步在干什么，用于列表里的"当前动作"。"""
    if not ev:
        return ""
    kind = ev.get("kind")
    label = ev.get("label", "")
    status = ev.get("status", "")
    if kind == "assistant":
        if ev.get("toolCalls"):
            return "思考中…"
        if status == "GENERATING":
            return "正在生成回复…"
        return "本轮回复完成"
    if ev.get("command"):
        return f"{label}：{ev['command'].splitlines()[0][:80]}"
    if ev.get("path"):
        return f"{label}：{_basename(ev['path'])}"
    if kind == "user":
        return "已收到指令，正在启动…"
    if ev.get("text"):
        return f"{label}：{ev['text'].splitlines()[0][:90]}" if label else ev["text"][:90]
    return label


def build_progress(events: List[Dict[str, Any]], status: str, step_count: int) -> Dict[str, Any]:
    running = status in RUNNING_STATES
    last_user_idx = -1
    for i, ev in enumerate(events):
        if ev.get("kind") == "user":
            last_user_idx = i
    turn = events[last_user_idx + 1:] if last_user_idx >= 0 else events
    tokens_in = sum(e.get("tokensIn", 0) for e in turn)
    tokens_out = sum(e.get("tokensOut", 0) for e in turn)
    current = events[-1] if events else None

    waiting = False
    if current:
        if current.get("kind") in ("question",):
            waiting = True
        if current.get("type") == "CORTEX_STEP_TYPE_TOOL_CALL_PROPOSAL":
            waiting = True
        if current.get("status") in ("WAITING", "PENDING") and not running:
            waiting = True

    # 最后一步已经是收尾的回复文本，就视为这一轮结束（Antigravity 的 summary 状态
    # 会滞后几秒才翻成 IDLE，直接用状态会让界面看起来"永远在跑"）
    finished_turn = bool(
        current
        and current.get("kind") == "assistant"
        and current.get("status") == "DONE"
        and not current.get("toolCalls")
        and (current.get("text") or "").strip()
    )

    # 最后一步是收尾回复时，状态可能滞后几秒；超过宽限期还 RUNNING，
    # 说明这一轮虽然结束了、但还有后台任务撑着 executor（IDE 里显示 "N task running"）。
    last_age = _seconds_since((current or {}).get("finishedAt") or (current or {}).get("at"))
    background = bool(running and finished_turn and (last_age is None or last_age >= TURN_END_GRACE))

    if running and not finished_turn:
        state = "working"
    elif background:
        state = "working"
    elif waiting:
        state = "waiting"
    else:
        state = "idle"

    # 最近一次出现过的模型，用于输入框的默认选中值
    model = ""
    for ev in reversed(events):
        if ev.get("model"):
            model = ev["model"]
            break

    action = describe(current)
    if background:
        task = next((e.get("task") for e in reversed(turn) if e.get("task")), None)
        title = (task or {}).get("title") or ""
        action = f"后台任务运行中：{title}" if title else "后台任务运行中…"

    return {
        "state": state,
        "running": running,
        "background": background,
        "totalSteps": step_count,
        "turnSteps": len(turn),
        "turnCapped": last_user_idx < 0 and bool(events),
        "currentAction": action,
        "currentStepType": (current or {}).get("type", ""),
        "currentStatus": (current or {}).get("status", ""),
        "lastActivityAt": (current or {}).get("at") or (current or {}).get("finishedAt"),
        "tokensIn": tokens_in,
        "tokensOut": tokens_out,
        "model": model,
    }


def get_session_detail(cascade_id: str, limit: int = 200,
                      session: Optional[Dict[str, Any]] = None,
                      compact: bool = True) -> Dict[str, Any]:
    if session is None:
        sessions = list_sessions()
        session = next((s for s in sessions if s["id"] == cascade_id), None)
    if session is None:
        raise RPCError("not_found", f"没有找到会话 {cascade_id}")

    steps, offset, total = tail_steps(cascade_id, limit, total=session["stepCount"])
    full = normalize_steps(steps, offset)
    _cache_detail(cascade_id, offset, total, full)

    events = [compact_event(e) for e in full] if compact else full
    return {
        "session": session,
        "events": events,
        "offset": offset,
        "totalSteps": total,
        "hasMore": offset > 0,
        "compact": compact,
        "progress": build_progress(full, session["status"], total),
    }

# --------------------------------------------------------------------------
# 写入：发消息 / 新建 / 停止
# --------------------------------------------------------------------------

def models(cascade_id: Optional[str] = None) -> List[Dict[str, Any]]:
    data = rpc("GetCascadeModelConfigData", {"cascadeId": cascade_id or ""})
    out = []
    for m in data.get("clientModelConfigs") or []:
        out.append({
            "id": (m.get("modelOrAlias") or {}).get("model", ""),
            "label": m.get("label", ""),
            "modelId": m.get("modelId", ""),
            "recommended": bool(m.get("isRecommended")),
            "quota": (m.get("quotaInfo") or {}).get("remainingFraction"),
        })
    return out


def last_used_model(cascade_id: str) -> Optional[str]:
    """从会话历史里找出上次使用的模型，作为默认值。"""
    try:
        steps, _, _ = tail_steps(cascade_id, 60)
    except Exception:
        return None
    for step in reversed(steps):
        if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT":
            cfg = ((step.get("userInput") or {}).get("userConfig") or {}).get("plannerConfig") or {}
            if cfg.get("planModel"):
                return cfg["planModel"]
    return None


def default_model() -> str:
    """挑一个还能用的默认模型：优先"最近用过且仍在可用列表里"的。"""
    try:
        avail = [m["id"] for m in models() if "disabled" not in (m.get("modelId") or "")]
    except Exception:
        avail = []

    try:
        for s in list_sessions():
            m = last_used_model(s["id"])
            if m and (not avail or m in avail):
                return m
    except Exception:
        pass

    # 兜底：推荐模型里排掉已下线的（modelId 带 disabled）
    try:
        for m in models():
            if m.get("recommended") and "disabled" not in (m.get("modelId") or ""):
                return m["id"]
    except Exception:
        pass
    if avail:
        return avail[0]
    return "MODEL_PLACEHOLDER_M318"


def send_message(cascade_id: str, text: str, model: Optional[str] = None,
                 blocking: bool = False) -> Dict[str, Any]:
    model = model or last_used_model(cascade_id) or default_model()
    body = {
        "cascadeId": cascade_id,
        "items": [{"text": text}],
        "cascadeConfig": {"plannerConfig": {"planModel": model}},
        "blocking": blocking,
    }
    rpc("SendUserCascadeMessage", body, timeout=60)
    return {"ok": True, "cascadeId": cascade_id, "model": model}


def start_cascade(workspace_path: str, text: Optional[str] = None,
                  model: Optional[str] = None) -> Dict[str, Any]:
    if not os.path.isdir(workspace_path):
        raise RPCError("invalid_argument", f"目录不存在：{workspace_path}")
    uri = "file://" + urllib.request.pathname2url(os.path.abspath(workspace_path))
    resp = rpc(
        "StartCascade",
        {"workspaceUris": [uri], "source": "CORTEX_TRAJECTORY_SOURCE_CIDER"},
        timeout=30,
    )
    cid = resp.get("cascadeId")
    if not cid:
        raise RPCError("internal", f"新建会话未返回 cascadeId：{resp}")
    model = model or default_model()
    if text:
        send_message(cid, text, model=model)
    return {"ok": True, "cascadeId": cid, "model": model}


def cancel(cascade_id: str) -> Dict[str, Any]:
    # 已结束的会话再取消，语言服务器会直接 500，先自己拦一下
    cur = next((s for s in list_sessions() if s["id"] == cascade_id), None)
    if cur is not None and not cur["running"]:
        return {"ok": True, "skipped": True, "message": "会话当前没有在运行"}
    rpc("CancelCascadeInvocation", {"cascadeId": cascade_id}, timeout=20)
    return {"ok": True}


def workspace_candidates() -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for s in list_sessions():
        ws = s.get("workspace") or ""
        if not ws:
            continue
        if ws not in seen:
            seen[ws] = {
                "path": ws,
                "name": s.get("workspaceName") or _basename(ws),
                "repo": s.get("repo", ""),
                "sessions": 0,
                "lastUsed": s.get("updatedAt"),
            }
        seen[ws]["sessions"] += 1
    return sorted(seen.values(), key=lambda x: x.get("lastUsed") or "", reverse=True)


if __name__ == "__main__":
    conn = discover()
    print("连接:", conn)
    for s in list_sessions()[:5]:
        print(f"  [{s['status']}] {s['title']}  steps={s['stepCount']}  {s['workspace']}")
