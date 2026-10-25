"""Demo backend: a fake Antigravity instance for screenshots and `--demo`.

Running ``agdeck serve --demo`` lets anyone try the UI (and lets the project
ship screenshots that contain no real project names) without installing
Antigravity. It implements the same module surface as :mod:`agdeck.client`.
"""

from __future__ import annotations

import datetime
import itertools
from typing import Any, Dict, List, Optional

from .client import build_progress, compact_event
from .rpc import LSUnavailable, RPCError

_MODEL = "MODEL_PLACEHOLDER_M318"


def _ts(minutes_ago: float) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return moment.isoformat().replace("+00:00", "Z")


def _ev(index: int, stype: str, kind: str, label: str, **extra: Any) -> Dict[str, Any]:
    base = {
        "index": index,
        "type": stype,
        "kind": kind,
        "label": label,
        "status": "DONE",
        "at": _ts(30),
        "startedAt": _ts(30),
        "finishedAt": _ts(29),
        "model": _MODEL,
        "tokensIn": 0,
        "tokensOut": 0,
        "ms": 900,
    }
    base.update(extra)
    return base


def _view(index: int, path: str, lines: int, preview: str) -> Dict[str, Any]:
    return _ev(index, "CORTEX_STEP_TYPE_VIEW_FILE", "tool", "读取文件",
               path=f"~/demo/aurora-shop/{path}", range=f"1-{lines}", numLines=lines,
               fileLines=lines * 3, preview=preview, ms=120)


def _cmd(index: int, command: str, output: str, exit_code: int = 0, ms: int = 2100) -> Dict[str, Any]:
    return _ev(index, "CORTEX_STEP_TYPE_RUN_COMMAND", "tool", "执行命令",
               command=command, cwd="~/demo/aurora-shop", exitCode=exit_code,
               output=output, ms=ms)


def _edit(index: int, path: str, description: str, diff: List[Dict[str, str]],
          additions: int, deletions: int) -> Dict[str, Any]:
    return _ev(index, "CORTEX_STEP_TYPE_CODE_ACTION", "tool", "修改代码",
               path=f"~/demo/aurora-shop/{path}", description=description,
               additions=additions, deletions=deletions, diff=diff, ms=1400)


def _build_session_one() -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    add = events.append

    add(_ev(0, "CORTEX_STEP_TYPE_USER_INPUT", "user", "", at=_ts(46),
            text="给商品列表加一个购物车抽屉：点 + 号加到右侧抽屉，支持改数量和删除。"))
    add(_ev(1, "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "assistant", "",
            at=_ts(45), tokensIn=8200, tokensOut=310,
            thinking="先看现有商品卡片和状态管理，确认购物车状态是不是已经在 store 里。",
            text="我先看一下现有的商品卡片和状态管理，再把抽屉接到已有 store 上，避免重复造状态。",
            toolCalls=[{"name": "view_file", "args": "{\"path\":\"src/components/ProductCard.tsx\"}"}]))

    add(_view(2, "src/components/ProductCard.tsx", 64, "export function ProductCard({ product }) {\n  ...\n}"))
    add(_view(3, "src/store/cart.ts", 88, "export const useCart = create(() => ({\n  items: [],\n  add: (id) => ...,\n}))"))
    add(_view(4, "src/components/AppShell.tsx", 42, "export function AppShell({ children }) {\n  return <main>{children}</main>;\n}"))
    add(_view(5, "src/styles/tokens.css", 31, ":root {\n  --surface: #ffffff;\n  --shadow-lg: 0 18px 40px rgba(15,23,42,.16);\n}"))

    add(_cmd(6, "npm run test -- --run src/store", "✓ src/store/cart.test.ts (6 tests) 412ms\n\nTest Files  1 passed (1)\n     Tests  6 passed (6)", ms=3400))

    add(_edit(7, "src/store/cart.ts", "新增 quantity / remove / subtotal", [
        {"mark": " ", "text": "export const useCart = create((set, get) => ({"},
        {"mark": "+", "text": "  quantityOf: (id) => get().items.find((i) => i.id === id)?.qty ?? 0,"},
        {"mark": "+", "text": "  setQty: (id, qty) => set((s) => ({ items: s.items.map((i) =>"},
        {"mark": "+", "text": "    i.id === id ? { ...i, qty: Math.max(1, qty) } : i) })),"},
        {"mark": "+", "text": "  remove: (id) => set((s) => ({ items: s.items.filter((i) => i.id !== id) })),"},
    ], 5, 0))

    add(_edit(8, "src/components/CartDrawer.tsx", "新建购物车抽屉组件", [
        {"mark": "+", "text": "export function CartDrawer({ open, onClose }) {"},
        {"mark": "+", "text": "  const { items, setQty, remove, subtotal } = useCart();"},
        {"mark": "+", "text": "  return ("},
        {"mark": "+", "text": "    <aside className={open ? 'drawer open' : 'drawer'} aria-hidden={!open}>"},
        {"mark": "+", "text": "      {items.map((item) => <CartRow key={item.id} item={item} />)}"},
        {"mark": "+", "text": "      <footer>{formatMoney(subtotal())}</footer>"},
        {"mark": "+", "text": "    </aside>"},
    ], 42, 0))

    add(_cmd(9, "npm run build", "vite v6.3.5 building for production...\n✓ 1421 modules transformed.\ndist/assets/index-Ck2f.js  184.22 kB │ gzip: 58.11 kB\n✓ built in 3.42s", ms=5100))
    add(_ev(10, "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "assistant", "",
            at=_ts(38), tokensIn=21400, tokensOut=520,
            text="抽屉已经接上了，购物车状态沿用原来的 `useCart`，没有新增全局 store。\n\n"
                 "- `+` 按钮走 `add(id)`，重复点击累加数量\n"
                 "- 抽屉里可以改数量、删除单项，底部实时算小计\n"
                 "- 构建和 store 的 6 个单测都通过\n\n"
                 "要不要顺手把「清空购物车」也加上？"))

    # 最后一轮故意停在生成中，方便演示/截图展示实时状态
    add(_ev(11, "CORTEX_STEP_TYPE_USER_INPUT", "user", "", at=_ts(0.6),
            text="好，加上清空购物车，并在移动端改成底部弹出。"))
    add(_ev(12, "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "assistant", "",
            at=_ts(0.4), status="GENERATING", ms=0, tokensIn=24800, tokensOut=90,
            thinking="先确认抽屉的媒体查询断点，再加 clear() 和二次确认。",
            text="", toolCalls=[{"name": "view_file", "args": "{\"path\":\"src/styles/drawer.css\"}"}]))
    return events


def _build_session_two() -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    add = events.append
    add(_ev(0, "CORTEX_STEP_TYPE_USER_INPUT", "user", "", at=_ts(190),
            text="把 auth 中间件拆成可测试的纯函数，并补上 refresh token 的过期用例。"))
    add(_ev(1, "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "assistant", "", at=_ts(188),
            text="拆成 `parseAuthHeader` / `verifySession` / `attachUser` 三个纯函数，"
                 "中间件只负责串起来，这样 refresh 的边界条件就能单独测。",
            tokensIn=6100, tokensOut=280))
    add(_cmd(2, "npm run test -- --run src/server/auth", "✓ src/server/auth/middleware.test.ts (11 tests)\n✓ src/server/auth/refresh.test.ts (7 tests)\n\nTests  18 passed (18)", ms=4300))
    add(_ev(3, "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "assistant", "", at=_ts(180),
            text="拆完了，`middleware.ts` 从 210 行降到 46 行，新增 18 个用例覆盖过期、时钟漂移和并发刷新。"))
    return events


DEMO_SESSIONS: List[Dict[str, Any]] = [
    {
        "id": "demo-aurora-cart",
        "title": "购物车抽屉 + 移动端底部弹出",
        "status": "CASCADE_RUN_STATUS_RUNNING",
        "running": True,
        "stepCount": 13,
        "createdAt": _ts(52),
        "updatedAt": _ts(0.1),
        "workspace": "~/demo/aurora-shop",
        "workspaceName": "aurora-shop",
        "repo": "aurora-shop",
        "branch": "feat/cart-drawer",
        "archived": False,
        "activity": "正在生成回复…",
    },
    {
        "id": "demo-aurora-auth",
        "title": "拆分 auth 中间件并补测试",
        "status": "CASCADE_RUN_STATUS_IDLE",
        "running": False,
        "stepCount": 4,
        "createdAt": _ts(210),
        "updatedAt": _ts(176),
        "workspace": "~/demo/aurora-shop",
        "workspaceName": "aurora-shop",
        "repo": "aurora-shop",
        "branch": "refactor/auth-pure",
        "archived": False,
        "activity": "",
    },
    {
        "id": "demo-docs",
        "title": "写一份部署说明",
        "status": "CASCADE_RUN_STATUS_IDLE",
        "running": False,
        "stepCount": 2,
        "createdAt": _ts(1200),
        "updatedAt": _ts(1150),
        "workspace": "~/demo/aurora-docs",
        "workspaceName": "aurora-docs",
        "repo": "aurora-docs",
        "branch": "main",
        "archived": True,
        "activity": "",
    },
]

_EXTRA_EVENTS: Dict[str, List[Dict[str, Any]]] = {
    "demo-aurora-cart": _build_session_one(),
    "demo-aurora-auth": _build_session_two(),
    "demo-docs": [
        _ev(0, "CORTEX_STEP_TYPE_USER_INPUT", "user", "", text="写一份部署说明。"),
        _ev(1, "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "assistant", "",
            text="已经生成 `docs/deploy.md`，包含环境变量、构建和回滚三节。"),
    ],
}

_counter = itertools.count(100)


def discover(force: bool = False) -> Dict[str, Any]:
    return {"pid": 0, "csrf": "", "port": 0, "scheme": "demo", "base": "",
            "ideVersion": "demo", "appDataDir": ""}


def list_sessions(include_archived: bool = True, activity: bool = False) -> List[Dict[str, Any]]:
    rows = [dict(s) for s in DEMO_SESSIONS if include_archived or not s["archived"]]
    rows.sort(key=lambda s: s["updatedAt"], reverse=True)
    return rows


def get_steps(cascade_id: str, offset: int = 0, timeout: float = 5.0) -> List[Dict[str, Any]]:
    return _EXTRA_EVENTS.get(cascade_id, [])


def get_session_detail(cascade_id: str, limit: int = 200,
                       session: Optional[Dict[str, Any]] = None,
                       compact: bool = True) -> Dict[str, Any]:
    full = _EXTRA_EVENTS.get(cascade_id)
    if full is None:
        raise RPCError("not_found", f"没有找到会话 {cascade_id}")
    if session is None:
        session = next((s for s in DEMO_SESSIONS if s["id"] == cascade_id), None)
    if session is None:
        raise RPCError("not_found", f"没有找到会话 {cascade_id}")

    window = full[-limit:] if limit else full
    offset = max(0, len(full) - len(window))
    events = [compact_event(e) for e in window] if compact else window
    return {
        "session": session,
        "events": events,
        "offset": offset,
        "totalSteps": len(full),
        "hasMore": offset > 0,
        "compact": compact,
        "progress": build_progress(window, session["status"], len(full)),
    }


def get_step_body(cascade_id: str, index: int) -> Optional[Dict[str, Any]]:
    for event in _EXTRA_EVENTS.get(cascade_id, []):
        if event["index"] == index:
            return event
    return None


def models(cascade_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return [
        {"id": _MODEL, "label": "Gemini 3 Pro", "modelId": "gemini-3-pro", "recommended": True, "quota": 0.82},
        {"id": "MODEL_PLACEHOLDER_M35", "label": "Claude Sonnet 4.5", "modelId": "claude-sonnet-4-5",
         "recommended": False, "quota": 0.46},
        {"id": "MODEL_PLACEHOLDER_M12", "label": "GPT-5", "modelId": "gpt-5", "recommended": False, "quota": 0.61},
    ]


def default_model() -> str:
    return _MODEL


def last_used_model(cascade_id: str) -> Optional[str]:
    return _MODEL


def workspace_candidates() -> List[Dict[str, Any]]:
    return [
        {"path": "~/demo/aurora-shop", "name": "aurora-shop", "repo": "aurora-shop",
         "sessions": 2, "lastUsed": _ts(1)},
        {"path": "~/demo/aurora-docs", "name": "aurora-docs", "repo": "aurora-docs",
         "sessions": 1, "lastUsed": _ts(1100)},
    ]


def send_message(cascade_id: str, text: str, model: Optional[str] = None,
                 blocking: bool = False) -> Dict[str, Any]:
    events = _EXTRA_EVENTS.setdefault(cascade_id, [])
    idx = len(events)
    events.append(_ev(idx, "CORTEX_STEP_TYPE_USER_INPUT", "user", "", text=text,
                      at=_ts(0)))
    return {"ok": True, "cascadeId": cascade_id, "model": model or _MODEL, "demo": True}


def start_cascade(workspace_path: str, text: Optional[str] = None,
                  model: Optional[str] = None) -> Dict[str, Any]:
    cid = f"demo-new-{next(_counter)}"
    DEMO_SESSIONS.insert(0, {
        "id": cid,
        "title": (text or "新的会话")[:28],
        "status": "CASCADE_RUN_STATUS_RUNNING",
        "running": True,
        "stepCount": 1 if text else 0,
        "createdAt": _ts(0),
        "updatedAt": _ts(0),
        "workspace": workspace_path,
        "workspaceName": workspace_path.rstrip("/").split("/")[-1],
        "repo": "",
        "branch": "",
        "archived": False,
        "activity": "已收到指令，正在启动…",
    })
    if text:
        _EXTRA_EVENTS[cid] = [_ev(0, "CORTEX_STEP_TYPE_USER_INPUT", "user", "", text=text, at=_ts(0))]
    return {"ok": True, "cascadeId": cid, "model": model or _MODEL, "demo": True}


def cancel(cascade_id: str) -> Dict[str, Any]:
    return {"ok": True, "demo": True}
