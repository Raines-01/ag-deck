/* AG Deck · front-end logic (zero dependencies) */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const main = $('#main');
  const listEl = $('#list');
  const toastEl = $('#toast');

  const isMobile = () => window.matchMedia('(max-width: 760px)').matches;

  const state = {
    sessions: [],
    detail: null,
    selectedId: null,
    filter: 'all',
    query: '',
    connection: null,
    models: [],
    defaultModel: '',
    openTools: new Set(),
    lastListSig: '',
    lastDetailSig: '',
    es: null,
    tick: null,
    runningMap: new Map(),
    notify: true,
    audio: null,
    screen: 'list',
    detailCache: new Map(),
    fullEvents: new Map(),
    pendingNew: false,
    scrollToBottom: false,
    composerH: 0,
  };

  function showScreen(name) {
    state.screen = name;
    document.body.dataset.screen = name;
  }

  /** 手机端进入详情时压一条历史，让系统返回键/浏览器返回先回到列表 */
  function pushDetailState() {
    if (history.state && history.state.screen === 'detail') return;
    try { history.pushState({ screen: 'detail' }, ''); } catch (_) { /* 忽略 */ }
  }

  function goBackToList() {
    if (history.state && history.state.screen === 'detail') {
      history.back();
    } else {
      showScreen('list');
    }
  }

  /* ---------------- 工具函数 ---------------- */

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const num = (n) => (n === undefined || n === null ? '0' : Number(n).toLocaleString('zh-CN'));

  function relTime(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return '';
    const d = (Date.now() - t) / 1000;
    if (d < 60) return '刚刚';
    if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
    if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
    if (d < 86400 * 30) return `${Math.floor(d / 86400)} 天前`;
    return new Date(iso).toLocaleDateString('zh-CN');
  }

  function dur(sec) {
    sec = Math.max(0, Math.floor(sec));
    if (sec < 60) return `${sec}秒`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return `${m}分${s ? s + '秒' : ''}`;
    const h = Math.floor(m / 60);
    return `${h}小时${m % 60 ? (m % 60) + '分' : ''}`;
  }

  function shortPath(p, keep = 2) {
    if (!p) return '';
    const parts = String(p).split('/').filter(Boolean);
    return parts.slice(-keep).join('/');
  }

  function toast(msg, bad) {
    toastEl.textContent = msg;
    toastEl.className = 'toast on' + (bad ? ' bad' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { toastEl.className = 'toast'; }, bad ? 4200 : 2200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { 'X-AGConsole': '1', 'Content-Type': 'application/json', ...(opts.headers || {}) },
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* ignore */ }
    if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
    return data;
  }

  async function copyText(text, msg) {
    try {
      await navigator.clipboard.writeText(text);
      toast(msg || '已复制');
      return true;
    } catch (_) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        toast(msg || '已复制');
        return true;
      } catch (_) {
        toast('复制失败，请长按手动选择', true);
        return false;
      }
    }
  }

  /* ---------------- 完成提醒 ---------------- */

  function unlockAudio() {
    if (state.audio) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try { state.audio = new AC(); } catch (_) { state.audio = null; }
  }

  function beep() {
    const ctx = state.audio;
    if (!ctx || !state.notify) return;
    try {
      if (ctx.state === 'suspended') ctx.resume();
      const t0 = ctx.currentTime;
      [0, 0.16].forEach((offset, i) => {
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.connect(g);
        g.connect(ctx.destination);
        o.type = 'sine';
        o.frequency.value = i === 0 ? 880 : 1180;
        g.gain.setValueAtTime(0.0001, t0 + offset);
        g.gain.exponentialRampToValueAtTime(0.16, t0 + offset + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + offset + 0.18);
        o.start(t0 + offset);
        o.stop(t0 + offset + 0.2);
      });
    } catch (_) { /* 忽略 */ }
  }

  function notifyDone(s) {
    if (!state.notify) return;
    toast(`「${s.title}」本轮已完成`);
    beep();
    if ('Notification' in window && Notification.permission === 'granted' && window.isSecureContext) {
      try {
        new Notification('会话完成', { body: s.title, tag: 'ag-' + s.id, icon: '/static/icon.svg' });
      } catch (_) { /* 忽略 */ }
    }
  }

  async function toggleNotify() {
    const btn = $('#btn-bell');
    state.notify = !state.notify;
    btn && btn.classList.toggle('on', state.notify);
    if (!state.notify) {
      toast('已关闭完成提醒');
      return;
    }
    unlockAudio();
    if (!('Notification' in window)) {
      toast('已开启声音提醒（浏览器不支持系统通知）');
      return;
    }
    if (!window.isSecureContext) {
      toast('已开启声音提醒；系统通知需要 HTTPS 访问');
      return;
    }
    if (Notification.permission === 'granted') {
      toast('已开启通知');
      return;
    }
    const p = await Notification.requestPermission();
    toast(p === 'granted' ? '已开启通知' : '仅开启声音提醒');
  }

  function updateTitle() {
    const n = state.sessions.filter((s) => s.running).length;
    document.title = n ? `● ${n} 运行中 · AG Deck` : 'AG Deck — Antigravity 控制台';
  }

  /* ---------------- 轻量 Markdown ---------------- */

  /** markdown 里的 | a | b | 表格，直接按行块转换 */
  function tables(text) {
    const lines = text.split('\n');
    const out = [];
    let i = 0;
    const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
    while (i < lines.length) {
      if (isRow(lines[i]) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        const head = cells(lines[i]);
        i += 2;
        const rows = [];
        while (i < lines.length && isRow(lines[i])) {
          rows.push(cells(lines[i]));
          i += 1;
        }
        const thead = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead>`;
        const tbody = `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>`;
        out.push('', `<div class="tbl"><table>${thead}${tbody}</table></div>`, '');
      } else {
        out.push(lines[i]);
        i += 1;
      }
    }
    return out.join('\n');
  }

  function md(text) {
    let src = esc(text || '');
    const blocks = [];

    src = src.replace(/```[^\n]*\n?([\s\S]*?)```/g, (_, code) => {
      blocks.push(`<pre><code>${code.replace(/\n$/, '')}</code></pre>`);
      return `\u0000${blocks.length - 1}\u0000`;
    });

    src = src.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    // Antigravity 的回复里常有 LaTeX，没渲染引擎时至少让它别破坏排版
    src = src.replace(/\$\$([^$]{1,400})\$\$/g, (_, m) => `<pre><code>${m}</code></pre>`);
    src = src.replace(/\$([^$\n]{1,120})\$/g, (_, m) => `<code>${m}</code>`);
    src = src.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    src = src.replace(/\[([^\]]+)\]\((file:\/\/[^)\s]+)\)/g,
      (_, label, href) => `<a href="#" class="flink" data-copy="${esc(decodeURI(href.replace('file://', '')))}">${label}</a>`);
    src = src.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      (_, label, href) => `<a href="${href}" target="_blank" rel="noreferrer">${label}</a>`);
    src = src.replace(/^#{1,4}\s+(.+)$/gm, '<h4>$1</h4>');
    src = src.replace(/^&gt;\s?(.*)$/gm, '<blockquote>$1</blockquote>');
    src = tables(src);

    const blocksHtml = src.split(/\n{2,}/).map((p) => {
      const s = p.trim();
      if (!s) return '';
      if (/^<(h4|pre|ul|ol|blockquote|div)/.test(s)) return s;
      if (/^([-*]|\d+\.)\s/.test(s)) {
        const items = s.split('\n').map((l) => l.replace(/^([-*]|\d+\.)\s+/, ''));
        const ordered = /^\d+\./.test(s);
        const tag = ordered ? 'ol' : 'ul';
        return `<${tag}>${items.map((i) => `<li>${i}</li>`).join('')}</${tag}>`;
      }
      return `<p>${s.replace(/\n/g, '<br>')}</p>`;
    }).join('');

    return blocksHtml.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
  }

  /* ---------------- 状态展示 ---------------- */

  function stateOf(s) {
    if (!s) return { key: 'idle', label: '空闲' };
    if (s.running) return { key: 'running', label: s.status === 'CASCADE_RUN_STATUS_CANCELING' ? '停止中' : '进行中' };
    if (s.archived) return { key: 'idle', label: '已归档' };
    return { key: 'idle', label: '空闲' };
  }

  function modelLabel(id) {
    if (!id) return '';
    const m = state.models.find((x) => x.id === id);
    if (m) return m.label;
    return id.replace('MODEL_PLACEHOLDER_', 'Model ');
  }

  /* ---------------- 侧边栏列表 ---------------- */

  function filteredSessions() {
    let rows = state.sessions.slice();
    if (state.filter === 'active') rows = rows.filter((s) => s.running);
    else if (state.filter === 'archived') rows = rows.filter((s) => s.archived);
    else rows = rows.filter((s) => !s.archived || true);

    const q = state.query.trim().toLowerCase();
    if (q) {
      rows = rows.filter((s) => [s.title, s.workspace, s.repo, s.branch]
        .filter(Boolean).join(' ').toLowerCase().includes(q));
    }
    rows.sort((a, b) => {
      if (a.running !== b.running) return a.running ? -1 : 1;
      return String(b.updatedAt || '').localeCompare(String(a.updatedAt || ''));
    });
    return rows;
  }

  function renderList(force) {
    const rows = filteredSessions();
    // 结构没变时只更新"当前动作"那一行文字，避免每 2 秒重建整个列表
    const sig = JSON.stringify(rows.map((s) => [s.id, s.title, s.stepCount, s.running, s.updatedAt, s.archived]));
    if (!force && sig === state.lastListSig) {
      updateLiveRows(rows);
      return;
    }
    state.lastListSig = sig;

    const scroller = listEl;
    const keepScroll = scroller.scrollTop;

    if (!rows.length) {
      listEl.innerHTML = `<div style="padding:28px 12px;text-align:center;color:var(--text-3);font-size:12px">
        ${state.query ? '没有匹配的会话' : state.filter === 'active' ? '当前没有正在运行的会话' : '还没有会话'}
      </div>`;
      return;
    }

    let html = '';
    if (state.filter === 'all') {
      const running = rows.filter((s) => s.running).length;
      const archived = rows.filter((s) => s.archived).length;
      html += `<div class="group-label">${rows.length} 个会话 · ${running} 个进行中${archived ? ' · ' + archived + ' 个归档' : ''}</div>`;
    }

    for (const s of rows) {
      const st = stateOf(s);
      const cls = ['item'];
      if (s.id === state.selectedId) cls.push('on');
      if (st.key === 'running') cls.push('running');
      if (s.archived) cls.push('archived');
      const live = s.running
        ? `<div class="live"><span>${esc(s.activity || '正在工作…')}</span></div>`
        : '';
      html += `<div class="${cls.join(' ')}" data-id="${s.id}">
        <span class="status-dot"></span>
        <div class="t">${esc(s.title)}</div>
        <div class="m">
          <span class="ws">${esc(s.workspaceName || '未指定工作区')}</span>
          <i>·</i><span>${num(s.stepCount)} 步</span>
          <i>·</i><span>${relTime(s.updatedAt)}</span>
        </div>
        ${live}
      </div>`;
    }
    listEl.innerHTML = html;
    listEl.scrollTop = keepScroll;
  }

  /** 只刷新运行中会话的"当前动作"文本，不重建 DOM */
  function updateLiveRows(rows) {
    const map = new Map(rows.map((s) => [s.id, s]));
    document.querySelectorAll('.item').forEach((el) => {
      const s = map.get(el.dataset.id);
      if (!s) return;
      const span = el.querySelector('.live span');
      if (!span) return;
      const text = s.activity || '正在工作…';
      if (s.running && span.textContent !== text) span.textContent = text;
    });
  }

  /* ---------------- 详情骨架 ---------------- */

  function ensureShell() {
    if ($('#view-root')) return;
    main.innerHTML = `<div id="view-root" style="display:flex;flex-direction:column;min-height:0;flex:1">
      <div id="banner"></div>
      <header class="head" id="head"></header>
      <section class="progress" id="progress"></section>
      <section class="timeline" id="timeline"></section>
      <button id="to-bottom" class="to-bottom" hidden>↓ 回到最新</button>
      <footer class="composer" id="composer"></footer>
    </div>`;
    buildComposer();
  }

  function showEmpty() {
    state.detail = null;
    const down = state.connection && !state.connection.ok;
    if (!down) {
      main.innerHTML = `<div class="empty">
        <div class="big">◈</div>
        <h2>选择左侧的一个会话</h2>
        <p>这里会显示 Antigravity 正在做什么、做到哪一步，你也可以直接在下面继续下指令。</p>
      </div>`;
      return;
    }

    // 首次运行引导：告诉新用户"缺什么、下一步做什么"，而不是只报一个连接断开
    main.innerHTML = `<div class="empty onboard">
      <div class="big">◈</div>
      <h2>还没有连上 Antigravity</h2>
      <p>AG Deck 通过 Antigravity 自带的 <code>language_server</code> 读取会话。它没启动时，这里就是空的。</p>
      <ol class="steps">
        <li><b>打开 Antigravity</b><span>进到主界面，不是启动画面</span></li>
        <li><b>打开一次 agent 面板</b><span>让 <code>language_server</code> 进程起来</span></li>
        <li><b>回到这里</b><span>服务会自动重试，也可以点下面的按钮</span></li>
      </ol>
      <div class="empty-actions">
        <button class="btn primary" id="btn-retry-conn">重新检测</button>
        <button class="btn" id="btn-doctor">查看诊断</button>
      </div>
      <pre class="doctor" id="doctor" hidden></pre>
      ${state.connection && state.connection.error ? `<div class="err-line">${esc(state.connection.error)}</div>` : ''}
    </div>`;
    bindOnboarding();
  }

  function bindOnboarding() {
    const retry = $('#btn-retry-conn');
    if (retry) retry.addEventListener('click', async () => {
      retry.disabled = true;
      retry.textContent = '检测中…';
      await loadSessions(true);
      if (!state.sessions.length) showEmpty();
      else retry.disabled = false;
    });
    const doctor = $('#btn-doctor');
    if (doctor) doctor.addEventListener('click', async () => {
      const pre = $('#doctor');
      if (!pre.hidden) { pre.hidden = true; return; }
      pre.hidden = false;
      pre.textContent = '读取中…';
      try {
        pre.textContent = JSON.stringify(await api('/api/doctor'), null, 2);
      } catch (err) {
        pre.textContent = String(err.message || err);
      }
    });
  }

  function renderBanner() {
    const el = $('#banner');
    if (!el) return;
    const c = state.connection;
    if (c && !c.ok) {
      el.innerHTML = `<div class="banner"><b>连接断开</b><span class="grow">${esc(c.error || '')}</span>
        <button class="btn" onclick="location.reload()">重试</button></div>`;
    } else {
      el.innerHTML = '';
    }
  }

  /* ---------------- 详情：头部 ---------------- */

  function renderHead(d) {
    const s = d.session;
    const ps = (d.progress && d.progress.state) || 'idle';
    const st = ps === 'working'
      ? { key: 'running', label: '进行中' }
      : ps === 'waiting'
        ? { key: 'waiting', label: '等待确认' }
        : { key: 'idle', label: s.archived ? '已归档' : '空闲' };
    const chips = [];
    if (s.repo) chips.push(`<span class="chip">${esc(s.repo)}</span>`);
    if (s.branch) chips.push(`<span class="chip">⑂ ${esc(s.branch)}</span>`);
    if (s.workspace) chips.push(`<span class="chip path" title="${esc(s.workspace)}">${esc(s.workspace)}</span>`);
    if (s.archived) chips.push('<span class="chip">已归档</span>');

    const lastReq = [...(d.events || [])].reverse().find((e) => e.kind === 'user');
    const reqText = lastReq
      ? String(lastReq.text || '').replace(/@\[Quote\]\s*/g, '').replace(/\s+/g, ' ').trim().slice(0, 160)
      : '';

    $('#head').innerHTML = `
      <div class="head-top">
        <button class="icon-btn back mobile-only" id="btn-back" aria-label="返回">‹</button>
        <h2>${esc(s.title)}</h2>
        <span class="pill ${st.key}"><span class="d"></span>${st.label}</span>
      </div>
      <div class="meta">
        ${chips.join('')}
        <span class="last-req" title="${esc(reqText)}">${reqText ? '你：' + esc(reqText) : '创建于 ' + esc(relTime(s.createdAt))}</span>
      </div>`;
  }

  /* ---------------- 详情：进度条 ---------------- */

  function renderProgress(d) {
    const p = d.progress || {};
    const el = $('#progress');
    const running = p.state === 'working';
    const waiting = p.state === 'waiting';

    let action;
    if (running) action = p.currentAction || '正在工作…';
    else if (waiting) action = p.currentAction || '等待你的回应';
    else action = '本轮已结束，等待新的指令';

    const turnStart = turnStartAt(d.events);
    const lastAt = lastEventAt(d.events);
    const elapsed = running
      ? (turnStart ? (Date.now() - new Date(turnStart).getTime()) / 1000 : 0)
      : (turnStart && lastAt ? (new Date(lastAt).getTime() - new Date(turnStart).getTime()) / 1000 : 0);
    const showElapsed = elapsed >= 1;
    const tokens = (p.tokensIn || 0) + (p.tokensOut || 0);

    el.innerHTML = `
      <div class="pg-row">
        <span class="pg-action">${running ? '<b>现在：</b>' : ''}${esc(action)}</span>
        <span class="pg-stats">
          <span>第 <b>${num(p.totalSteps)}</b> 步</span>
          <span>本轮 <b>${p.turnCapped ? num(p.turnSteps) + '+' : num(p.turnSteps)}</b> 步</span>
          ${showElapsed ? `<span>用时 <b data-elapsed>${p.turnCapped ? '≥' : ''}${dur(elapsed)}</b></span>` : ''}
          ${tokens ? `<span class="tok"><b>${num(tokens)}</b> tok</span>` : ''}
        </span>
      </div>
      <div class="bar ${running ? 'live' : ''}"><div class="fill"></div></div>`;

    if (running) startTick();
    else stopTick();
  }

  function turnStartAt(events) {
    if (!events || !events.length) return null;
    let idx = -1;
    events.forEach((e, i) => { if (e.kind === 'user') idx = i; });
    if (idx < 0) idx = 0;
    for (let i = idx; i < events.length; i++) {
      if (events[i].at) return events[i].at;
      if (events[i].finishedAt) return events[i].finishedAt;
    }
    return null;
  }

  function lastEventAt(events) {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].finishedAt) return events[i].finishedAt;
      if (events[i].at) return events[i].at;
    }
    return null;
  }

  function startTick() {
    if (state.tick) return;
    state.tick = setInterval(() => {
      const t = state.detail && turnStartAt(state.detail.events);
      if (!t) return;
      const node = $('[data-elapsed]');
      if (node) node.textContent = dur((Date.now() - new Date(t).getTime()) / 1000);
    }, 1000);
  }

  function stopTick() {
    if (state.tick) { clearInterval(state.tick); state.tick = null; }
  }

  /* ---------------- 详情：时间线 ---------------- */

  function iconKind(t) {
    if (t.includes('RUN_COMMAND') || t.includes('SHELL')) return 'cmd';
    if (t.includes('CODE_ACTION')) return 'edit';
    if (t.includes('VIEW_FILE') || t.includes('READ_')) return 'read';
    if (t.includes('SEARCH') || t.includes('FIND')) return 'search';
    if (t.includes('MCP_TOOL')) return 'mcp';
    if (t.includes('WEB') || t.includes('URL') || t.includes('BROWSER')) return 'net';
    if (t.includes('SUBAGENT') || t.includes('TASK')) return 'task';
    return 'other';
  }

  function iconFor(t) {
    const k = iconKind(t);
    return {
      cmd: '&gt;_', edit: '✎', read: '▤', search: '⌕',
      mcp: '⚙', net: '◎', task: '⑃', other: '·',
    }[k];
  }

  function fmtMs(ms) {
    if (!ms || ms < 200) return '';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    const s = Math.round(ms / 1000);
    return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
  }

  function toolLine(ev) {
    if (ev.command) {
      const lines = String(ev.command).split('\n');
      return lines[0] + (lines.length > 1 ? ' …' : '');
    }
    if (ev.path) {
      const r = ev.range && ev.range !== '-' ? ':' + ev.range.replace('-', '–') : '';
      return shortPath(ev.path) + r;
    }
    if (ev.text) return String(ev.text).split('\n')[0].slice(0, 160);
    return '';
  }

  const BODY_PLACEHOLDER = '<div class="body-loading">展开后加载内容…</div>';

  function toolBody(ev) {
    if (ev.diff && ev.diff.length) {
      const rows = ev.diff.map((l) => {
        const cls = l.mark === '+' ? 'add' : l.mark === '-' ? 'del' : 'ctx';
        return `<div class="${cls}">${esc(l.mark + (l.text || ''))}</div>`;
      }).join('');
      return `${ev.description ? `<div class="cmd">${esc(ev.description)}</div>` : ''}<div class="diff">${rows}</div>`;
    }
    if (ev.command) {
      const cmd = `<div class="cmd">$ <b>${esc(ev.command)}</b>${ev.cwd ? `<span style="opacity:.55"> · ${esc(ev.cwd)}</span>` : ''}</div>`;
      if (ev.hasBody && !ev.output) return cmd + BODY_PLACEHOLDER;
      return cmd + `<pre>${esc(ev.output || '（无输出）')}</pre>`;
    }
    if (ev.preview) return `<pre>${esc(ev.preview)}</pre>`;
    if (ev.hasBody && !ev.text) return BODY_PLACEHOLDER;
    return `<pre>${esc(ev.text || '')}</pre>`;
  }

  const GROUPABLE = new Set([
    'CORTEX_STEP_TYPE_VIEW_FILE',
    'CORTEX_STEP_TYPE_GREP_SEARCH',
    'CORTEX_STEP_TYPE_CODE_SEARCH',
    'CORTEX_STEP_TYPE_LIST_DIRECTORY',
    'CORTEX_STEP_TYPE_FIND',
  ]);

  /** 连续多次"读取文件/搜索"合并成一组，避免刷屏 */
  function toolGroup(list) {
    const first = list[0];
    const key = 'g' + first.index;
    const open = state.openTools.has(key);
    const names = [...new Set(list
      .map((e) => (e.path ? shortPath(e.path) : toolLine(e)))
      .filter(Boolean))];
    const previewText = names.slice(0, 3).join('、')
      + (names.length > 3 ? ` 等 ${names.length} 处` : '');
    return `<details class="tool-group" data-idx="${key}" ${open ? 'open' : ''}>
      <summary>
        <span class="ico ${iconKind(first.type)}">${iconFor(first.type)}</span>
        <span class="lbl">${esc(first.label)} ×${list.length}</span>
        <span class="arg">${esc(previewText)}</span>
        <span class="chev">›</span>
      </summary>
      <div class="group-body">${list.map(toolCard).join('')}</div>
    </details>`;
  }

  function toolCard(ev) {
    const open = state.openTools.has(ev.index);
    let tag = '';
    if (ev.exitCode !== undefined && ev.exitCode !== null) {
      tag = `<span class="tag ${ev.exitCode === 0 ? 'ok' : 'bad'}">exit ${ev.exitCode}</span>`;
    } else if (ev.additions || ev.deletions) {
      tag = `<span class="tag"><span style="color:var(--green)">+${ev.additions || 0}</span> <span style="color:var(--red)">−${ev.deletions || 0}</span></span>`;
    } else if (ev.numLines) {
      const tip = ev.fileLines ? ` title="文件共 ${ev.fileLines} 行"` : '';
      tag = `<span class="tag"${tip}>${num(ev.numLines)} 行</span>`;
    }
    const cost = fmtMs(ev.ms);
    const time = cost ? `<span class="tag time">${cost}</span>` : '';
    const copy = ev.command
      ? `<button class="copy" data-copy-cmd="${esc(ev.command)}" title="复制命令">⧉</button>`
      : '';
    const bad = ev.exitCode !== undefined && ev.exitCode !== null && ev.exitCode !== 0;
    return `<div class="tool ${open ? 'open' : ''} ${bad ? 'err' : ''}" data-idx="${ev.index}" data-body="${ev.hasBody ? '1' : ''}">
      <div class="tool-head">
        <span class="ico ${iconKind(ev.type)}">${iconFor(ev.type)}</span>
        <span class="lbl">${esc(ev.label)}</span>
        <span class="arg">${esc(toolLine(ev))}</span>
        ${copy}
        ${time}
        ${tag}
      </div>
      <div class="tool-body">${toolBody(ev)}</div>
    </div>`;
  }

  function assistantMsg(ev, streaming) {
    // 没有正文的 planner 步骤：折叠成一行"思考"，避免刷屏占掉整屏
    if (!ev.text || !ev.text.trim()) {
      if (streaming) {
        return `<div class="think-row"><span>正在思考<span class="live-cursor"></span></span></div>`;
      }
      if (!ev.thinking) return '';   // 纯工具调用，交给下面的工具卡片表达
      const len = ev.thinkingLen || ev.thinking.length;
      const label = `思考过程 · ${num(len)} 字`;
      const key = 't' + ev.index;
      const open = state.openTools.has(key) ? 'open' : '';
      const body = ev.thinkingTruncated ? '1' : '';
      return `<details class="think-row" data-idx="${key}" data-step="${ev.index}" data-body="${body}" ${open}><summary>▸ ${esc(label)}</summary><div class="inner">${esc(ev.thinking)}</div></details>`;
    }

    const more = ev.textTruncated
      ? `<button class="more" data-more="${ev.index}">展开全文 · ${num(ev.textLen || 0)} 字</button>`
      : '';
    const parts = [`<div class="body-text">${md(ev.text)}${streaming ? '<span class="live-cursor"></span>' : ''}</div>${more}`];
    if (ev.thinking) {
      const len = ev.thinkingLen || ev.thinking.length;
      const key = 'h' + ev.index;
      const body = ev.thinkingTruncated ? '1' : '';
      const open = state.openTools.has(key) ? 'open' : '';
      parts.push(`<details class="thinking" data-idx="${key}" data-step="${ev.index}" data-body="${body}" ${open}><summary>▸ 思考过程 · ${num(len)} 字</summary><div class="inner">${esc(ev.thinking)}</div></details>`);
    }
    return `<div class="msg bot">
      <div class="avatar bot">A</div>
      <div class="bubble">
        ${parts.join('')}
      </div>
    </div>`;
  }

  function userMsg(ev) {
    return `<div class="msg user">
      <div class="avatar user">你</div>
      <div class="bubble">
        <div class="body-text">${esc(ev.text || '')}</div>
      </div>
    </div>`;
  }

  function renderTimeline(d, force) {
    const events = d.events || [];
    const last = events[events.length - 1] || {};
    const sig = [
      d.session.id, events.length,
      last.index, last.status, (last.text || '').length,
      d.progress && d.progress.state,
    ].join('|');
    if (!force && sig === state.lastDetailSig) return;
    state.lastDetailSig = sig;

    const el = $('#timeline');
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    const firstRender = !el.dataset.rendered || el.dataset.sid !== d.session.id;
    const grew = !firstRender && events.length > Number(el.dataset.count || 0);

    let html = '';
    if (d.hasMore) {
      html += `<div class="older"><button class="btn" id="older">加载更早的 ${num(d.offset)} 步</button></div>`;
    }

    let turn = 0;
    const running = d.progress && d.progress.state === 'working';

    for (let i = 0; i < events.length; i += 1) {
      const raw = events[i];
      const ev = state.fullEvents.get(d.session.id + ':' + raw.index) || raw;

      // 连续 4 个以上同类"读取/搜索"合并成一组
      // （中间夹着的空 planner 步骤本来就不显示，扫描时要跳过）
      if (ev.kind === 'tool' && GROUPABLE.has(ev.type)) {
        const group = [ev];
        let j = i + 1;
        while (j < events.length) {
          const nxt = state.fullEvents.get(d.session.id + ':' + events[j].index) || events[j];
          if (nxt.type === ev.type) { group.push(nxt); j += 1; continue; }
          const blank = nxt.kind === 'assistant'
            && !(nxt.text || '').trim() && !nxt.thinking;
          if (blank) { j += 1; continue; }
          break;
        }
        if (group.length >= 4) {
          html += toolGroup(group);
          i = j - 1;
          continue;
        }
      }

      if (ev.kind === 'user') {
        turn += 1;
        html += `<div class="turn-sep">第 ${turn} 轮 · ${relTime(ev.at)}</div>`;
        html += userMsg(ev);
        continue;
      }
      if (ev.kind === 'assistant') {
        html += assistantMsg(ev, running && i === events.length - 1);
        continue;
      }
      if (ev.kind === 'tool') { html += toolCard(ev); continue; }
      if (ev.kind === 'error') {
        html += `<div class="tool err open" data-idx="${ev.index}">
          <div class="tool-head"><span class="ico">!</span><span class="lbl" style="color:var(--red)">执行出错</span>
          <span class="arg">${esc((ev.text || '').split('\n')[0])}</span></div>
          <div class="tool-body"><pre>${esc(ev.detailText || ev.text || '')}</pre></div></div>`;
        continue;
      }
      if (ev.kind === 'question') {
        html += `<div class="msg bot"><div class="avatar bot">?</div><div class="bubble">
          <div class="who">需要你确认</div><div class="body-text">${esc(ev.text || '')}</div></div></div>`;
        continue;
      }
      html += `<div class="sys"><span class="bar-dot"></span>${esc(ev.text || ev.label)}</div>`;
    }

    if (!events.length) {
      html = `<div class="empty" style="padding:40px 0"><div class="big">◌</div>
        <h2>这个会话还没有内容</h2><p>在下面输入第一条指令，它就会开始工作。</p></div>`;
    }

    el.innerHTML = `${html}<div id="tl-end" style="height:1px"></div>`;
    el.dataset.rendered = '1';
    el.dataset.sid = d.session.id;
    el.dataset.count = String(events.length);
    if (state.scrollToBottom) {
      scrollToEnd(el);
      state.scrollToBottom = false;
      state.pendingNew = false;
    } else if (firstRender || nearBottom) {
      scrollToEnd(el);
      state.pendingNew = false;
    } else if (grew) {
      state.pendingNew = true;
    }
    toggleToBottom();
  }

  /** 滚到底：字体/图片加载会改高度，单次赋值不够，补两帧再校一次 */
  function scrollToEnd(el) {
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    setTimeout(() => { el.scrollTop = el.scrollHeight; toggleToBottom(); }, 120);
  }

  /** 用户往上翻的时候，右下角给一个"回到最新"的浮标（直接按滚动位置算，最稳） */
  function toggleToBottom() {
    const el = $('#timeline');
    const btn = $('#to-bottom');
    if (!el || !btn) return;

    // 输入框高度会变，浮标跟着抬起来，避免压住发送栏
    const composer = $('.composer');
    if (composer) {
      const h = Math.round(composer.getBoundingClientRect().height);
      if (h && state.composerH !== h) {
        state.composerH = h;
        document.documentElement.style.setProperty('--composer-h', h + 'px');
      }
    }

    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    const far = dist > 120;
    if (far !== !btn.hidden) btn.hidden = !far;
    const hasNew = far && state.pendingNew;
    btn.classList.toggle('has-new', hasNew);
    const label = hasNew ? '↓ 有新内容' : '↓ 回到最新';
    if (btn.textContent !== label) btn.textContent = label;
  }

  /* ---------------- 输入区 ---------------- */

  function buildComposer() {
    const el = $('#composer');
    if (!el) return;
    el.innerHTML = `<div class="box">
      <textarea id="msg" rows="1" placeholder="${isMobile() ? '给 Antigravity 下指令…' : '给 Antigravity 下指令…（Enter 发送，Shift+Enter 换行）'}"></textarea>
      <div class="row">
        <span class="hint" id="composer-hint">消息会直接发送到 Antigravity 的这个会话</span>
        <select id="model-select" title="本轮使用的模型"></select>
        <button class="stop-btn" id="btn-stop" style="display:none">停止</button>
        <button class="send-btn" id="btn-send">发送</button>
      </div>
    </div>`;

    const ta = $('#msg');
    ta.addEventListener('input', () => {
      ta.style.height = 'auto';
      ta.style.height = Math.min(190, ta.scrollHeight) + 'px';
    });
    ta.addEventListener('keydown', (e) => {
      const enterToSend = e.key === 'Enter' && !e.shiftKey && !e.isComposing;
      // 手机软键盘的回车用来换行，发送交给按钮
      if (enterToSend && (!isMobile() || e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        send();
      }
    });
    $('#btn-send').addEventListener('click', send);
    $('#btn-stop').addEventListener('click', stopRun);
    fillModels();
  }

  function fillModels() {
    const sel = $('#model-select');
    if (!sel) return;
    if (!state.models.length) {
      sel.innerHTML = '<option value="">默认模型</option>';
      return;
    }
    const current = sel.value || (state.detail && state.detail.progress && state.detail.progress.model) || state.defaultModel;
    sel.innerHTML = state.models.map((m) => {
      const val = m.id;
      return `<option value="${esc(val)}" ${val === current ? 'selected' : ''}>${esc(m.label)}</option>`;
    }).join('');
    if (!sel.value && state.models[0]) sel.value = state.models[0].id;
  }

  async function send() {
    const ta = $('#msg');
    const text = (ta.value || '').trim();
    if (!text) return;
    if (!state.selectedId) return;
    const btn = $('#btn-send');
    btn.disabled = true;
    btn.textContent = '发送中…';
    try {
      await api(`/api/sessions/${state.selectedId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ text, model: $('#model-select').value || undefined }),
      });
      ta.value = '';
      ta.style.height = 'auto';
      toast('指令已下发');
      state.scrollToBottom = true;
      // 立刻重连，让"进行中"状态不用等 8 秒的空闲轮询
      connectStream(state.selectedId);
      setTimeout(() => refreshDetail(true), 400);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = '发送';
    }
  }

  async function stopRun() {
    if (!state.selectedId) return;
    try {
      await api(`/api/sessions/${state.selectedId}/cancel`, { method: 'POST', body: '{}' });
      toast('已发送停止请求');
      connectStream(state.selectedId);
    } catch (err) {
      toast(err.message, true);
    }
  }

  function updateComposer(d) {
    const stop = $('#btn-stop');
    if (stop) stop.style.display = d.progress && d.progress.state === 'working' ? '' : 'none';
    const sel = $('#model-select');
    if (sel && !sel.value && d.progress && d.progress.model) sel.value = d.progress.model;
  }

  /* ---------------- 详情渲染总入口 ---------------- */

  function renderDetail(force) {
    const d = state.detail;
    if (!d) { return; }
    ensureShell();
    renderBanner();
    renderHead(d);
    renderProgress(d);
    renderTimeline(d, force);
    updateComposer(d);

    if (force) {
      const sel = $('#model-select');
      if (sel && d.progress && d.progress.model) sel.value = d.progress.model;
    }
  }

  async function refreshDetail(force) {
    if (!state.selectedId) return;
    const cid = state.selectedId;
    try {
      const d = await api(`/api/sessions/${cid}?limit=${state.limit || 200}`);
      if (state.selectedId !== cid) return; // 期间切到别的会话了，别覆盖
      state.detail = d;
      state.detailCache.set(cid, d);
      while (state.detailCache.size > 3) {
        state.detailCache.delete(state.detailCache.keys().next().value);
      }
      renderDetail(true);
    } catch (err) {
      toast(err.message, true);
    }
  }

  /** 大块内容（输出/diff/完整思考/长回复）按需拉取 */
  async function fetchBody(idx) {
    const key = state.selectedId + ':' + idx;
    if (state.fullEvents.has(key)) return state.fullEvents.get(key);
    const r = await api(`/api/sessions/${state.selectedId}/steps/${idx}`);
    state.fullEvents.set(key, r.event);
    return r.event;
  }

  async function loadCardBody(card, idx) {
    const box = card.querySelector('.tool-body');
    if (!box) return;
    card.dataset.loaded = '1';
    box.innerHTML = '<div class="body-loading">加载中…</div>';
    try {
      const ev = await fetchBody(idx);
      box.innerHTML = toolBody(ev);
    } catch (err) {
      card.dataset.loaded = '';
      box.innerHTML = `<div class="body-loading">加载失败：${esc(err.message)}</div>`;
    }
  }

  /* ---------------- 选择 / 新建 ---------------- */

  async function select(id, opts = {}) {
    const goDetail = opts.navigate !== false && isMobile();
    if (state.selectedId === id && state.detail) {
      if (goDetail) {
        showScreen('detail');
        pushDetailState();
        // 详情可能是在列表状态下预渲染的（当时高度为 0），进来后补一次到底
        scrollToEnd($('#timeline'));
        toggleToBottom();
      }
      return;
    }
    if (goDetail) { showScreen('detail'); pushDetailState(); }
    state.selectedId = id;
    state.detail = state.detailCache.get(id) || null;
    state.openTools.clear();
    state.lastDetailSig = '';
    state.limit = isMobile() ? 80 : 200;
    state.fullEvents.clear();
    state.pendingNew = false;
    state.scrollToBottom = false;
    $('#timeline') && $('#timeline').remove();
    const root = $('#view-root');
    if (root) root.remove();
    renderList(true);
    ensureShell();
    renderBanner();
    if (state.detail) {
      // 缓存命中：先立刻画出来，再去后台刷新，点起来没有等待感
      renderDetail(true);
    } else {
      $('#head').innerHTML = `<div class="head-top">
          <button class="icon-btn back mobile-only" id="btn-back" aria-label="返回">‹</button>
          <div style="flex:1;color:var(--text-3);font-size:12px">载入中…</div>
        </div>`;
      // 骨架屏，避免点进去是一片空白
      const tl = $('#timeline');
      if (tl) tl.innerHTML = '<div class="sk-card"></div>'.repeat(5);
    }
    connectStream(id);
    await refreshDetail(true);
    if (goDetail) pushDetailState();
  }

  const NO_STREAM = new URLSearchParams(location.search).has('nostream');

  function connectStream(sid) {
    if (NO_STREAM) return;
    if (state.es) { state.es.close(); state.es = null; }
    const limit = state.limit || 200;
    const url = '/api/stream' + (sid ? `?session=${encodeURIComponent(sid)}&limit=${limit}` : '');
    const es = new EventSource(url);
    state.es = es;
    es.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (_) { return; }
      apply(d);
    };
    es.onerror = () => {
      setConn({ ok: false, error: '与本地服务的连接中断，正在重试…' });
    };
  }

  /** 合并增量事件：窗口往后滑时丢掉旧的，同 index 以新内容为准 */
  function applyDelta(delta) {
    if (!state.detail || delta.sessionId !== state.selectedId) return false;
    const map = new Map();
    for (const e of state.detail.events) {
      if (e.index >= delta.from) map.set(e.index, e);
    }
    for (const e of delta.events || []) map.set(e.index, e);
    let events = [...map.values()].sort((a, b) => a.index - b.index);
    if (events.length > 400) events = events.slice(-400);

    state.detail = {
      ...state.detail,
      events,
      offset: delta.from,
      totalSteps: delta.totalSteps,
      hasMore: delta.hasMore,
      progress: delta.progress,
    };
    state.detailCache.set(delta.sessionId, state.detail);
    return true;
  }

  function apply(d) {
    if (d.connection) setConn(d.connection);
    if (d.error) setConn({ ok: false, error: d.error });
    if (d.sessions) {
      const prev = state.runningMap;
      const next = new Map();
      const finished = [];
      for (const s of d.sessions) {
        next.set(s.id, s.running);
        // 只提醒"刚才在跑、现在停了"的会话，首屏不响
        if (prev.get(s.id) === true && s.running === false) finished.push(s);
      }
      state.runningMap = next;
      state.sessions = d.sessions;
      renderList();
      updateTitle();
      finished.forEach(notifyDone);
    }
    if (d.detail && d.detail.session && d.detail.session.id === state.selectedId) {
      state.detail = d.detail;
      state.detailCache.set(d.detail.session.id, d.detail);
      renderDetail();
    }
    if (d.detailDelta && applyDelta(d.detailDelta)) {
      renderDetail();
    }
    if (d.detailError && state.selectedId) {
      const el = $('#timeline');
      if (el && !el.dataset.rendered) el.innerHTML = `<div class="empty"><p>${esc(d.detailError)}</p></div>`;
    }
  }

  function setConn(c) {
    state.connection = c;
    const el = $('#conn');
    const txt = $('#conn-text');
    if (!el || !txt) return;
    if (c && c.ok) {
      el.classList.remove('off');
      txt.textContent = c.demo ? '演示模式 · demo data' : `已连接 · PID ${c.pid}`;
    } else {
      el.classList.add('off');
      txt.textContent = '未连接';
    }
    if ($('#banner')) renderBanner();
    if (!c || !c.ok) {
      if (!state.sessions.length && main.querySelector('.empty')) showEmpty();
    }
  }

  /* ---------------- 新建会话弹窗 ---------------- */

  async function openNewModal() {
    const root = $('#modal-root');
    root.innerHTML = `<div class="modal-mask" id="mask">
      <div class="modal">
        <h3>新建会话</h3>
        <div class="sub">在选定的工作目录里开一个全新的 Antigravity 会话</div>
        <div class="fields">
          <div class="field">
            <label>工作目录</label>
            <select id="m-ws"><option>载入中…</option></select>
            <input id="m-path" placeholder="或直接输入绝对路径，如 ~/projects/demo" style="margin-top:6px">
          </div>
          <div class="field">
            <label>模型</label>
            <select id="m-model"><option>载入中…</option></select>
          </div>
          <div class="field">
            <label>第一条指令</label>
            <textarea id="m-text" placeholder="例如：梳理一下这个项目的目录结构，并指出启动入口"></textarea>
          </div>
        </div>
        <div class="foot">
          <button class="btn" id="m-cancel">取消</button>
          <button class="btn primary" id="m-ok">创建并发送</button>
        </div>
      </div>
    </div>`;

    $('#mask').addEventListener('click', (e) => { if (e.target.id === 'mask') closeModal(); });
    $('#m-cancel').addEventListener('click', closeModal);
    $('#m-ok').addEventListener('click', submitNew);

    try {
      const [ws, ms] = await Promise.all([
        api('/api/workspaces').catch(() => ({ workspaces: [] })),
        state.models.length ? Promise.resolve({ models: state.models, default: state.defaultModel }) : api('/api/models'),
      ]);
      state.models = ms.models || state.models;
      state.defaultModel = ms.default || state.defaultModel;

      const sel = $('#m-ws');
      const list = ws.workspaces || [];
      sel.innerHTML = list.length
        ? list.map((w) => `<option value="${esc(w.path)}">${esc(w.name)} — ${esc(w.path)}</option>`).join('')
        : '<option value="">（未找到历史工作区，请在下面填写路径）</option>';

      const msel = $('#m-model');
      msel.innerHTML = state.models.map((m) => `<option value="${esc(m.id)}" ${m.id === state.defaultModel ? 'selected' : ''}>${esc(m.label)}</option>`).join('');
      $('#m-text').focus();
    } catch (err) {
      toast(err.message, true);
    }
  }

  function closeModal() { $('#modal-root').innerHTML = ''; }

  /* ---------------- 手机访问 ---------------- */

  async function openNetModal() {
    const root = $('#modal-root');
    root.innerHTML = `<div class="modal-mask" id="mask">
      <div class="modal">
        <h3>手机访问</h3>
        <div class="sub">把手机和电脑连到同一个 Tailscale 网络（或同一 Wi-Fi），然后在手机上打开下面的地址</div>
        <div class="fields">
          <div class="field">
            <label>可用地址</label>
            <div class="net-list" id="net-list">
              <div class="net-row"><span class="url">正在检测…</span></div>
            </div>
          </div>
          <div class="field">
            <label>配对码</label>
            <div class="pin-box">
              <div class="pin" id="pin">—</div>
              <button class="btn" id="copy-pin">复制</button>
            </div>
          </div>
          <div class="note" id="net-note">首次打开需要输入配对码，之后这台设备会自动登录。</div>
        </div>
        <div class="foot">
          <button class="btn" id="m-close">关闭</button>
          <button class="btn primary" id="copy-link">复制免密链接</button>
        </div>
      </div>
    </div>`;

    $('#mask').addEventListener('click', (e) => { if (e.target.id === 'mask') closeModal(); });
    $('#m-close').addEventListener('click', closeModal);

    let info = null;
    try {
      info = await api('/api/net');
    } catch (err) {
      $('#net-list').innerHTML = `<div class="net-row"><span class="url">读取失败：${esc(err.message)}</span></div>`;
      return;
    }

    const list = (info.urls || []).filter((u) => !u.url.includes('127.0.0.1'));
    const rows = (list.length ? list : info.urls || []).map((u) => `
      <div class="net-row">
        <span class="tag" title="${esc(u.iface || '')}">${esc(u.label)}</span>
        <span class="url">${esc(u.url)}</span>
        <button class="btn" data-copy="${esc(u.url)}">复制</button>
      </div>`).join('');

    const ts = info.tailscale || {};
    let warn = '';
    if (ts.installed && !ts.connected) {
      warn = `<div class="net-warn">Tailscale 当前没有连上（电脑端需要先登录）。在那之前，手机只能通过同一个 Wi-Fi 访问。</div>`;
    } else if (!ts.installed) {
      warn = `<div class="net-warn">这台电脑没有检测到 Tailscale，手机只能通过同一个 Wi-Fi 访问。</div>`;
    } else if (ts.connected) {
      warn = `<div class="net-warn ok">Tailscale 已连接，手机加入同一 tailnet 后，用上面的 Tailscale 地址最稳（跨网络也能用）。</div>`;
    }

    $('#net-list').innerHTML = warn + (rows || '<div class="net-row"><span class="url">没有检测到局域网地址</span></div>');

    const code = String(info.code || '');
    $('#pin').textContent = code ? `${code.slice(0, 3)} ${code.slice(3)}` : '—';
    $('#copy-pin').addEventListener('click', () => copyText(code, '配对码已复制'));

    const best = (list[0] || (info.urls || [])[0] || {}).url || location.origin + '/';
    const link = best + '?t=' + encodeURIComponent(info.token || '');
    $('#copy-link').addEventListener('click', () => copyText(link, '免密链接已复制，发到手机上点开即可'));

    if (!window.isSecureContext) {
      $('#net-note').textContent = '首次打开需要输入配对码，之后这台设备会自动登录。当前是 HTTP 访问，浏览器不会弹出系统通知，改用声音和页面提示。';
    }

    $('#net-list').addEventListener('click', (e) => {
      const b = e.target.closest('[data-copy]');
      if (b) copyText(b.dataset.copy, '地址已复制');
    });
  }

  async function submitNew() {
    const btn = $('#m-ok');
    const ws = ($('#m-path').value || '').trim() || $('#m-ws').value;
    const text = ($('#m-text').value || '').trim();
    const model = $('#m-model').value || undefined;
    if (!ws) { toast('请选择或填写工作目录', true); return; }
    btn.disabled = true;
    btn.textContent = '创建中…';
    try {
      const r = await api('/api/sessions', {
        method: 'POST',
        body: JSON.stringify({ workspace: ws, text, model }),
      });
      closeModal();
      toast('会话已创建');
      await loadSessions(true);
      await select(r.cascadeId);
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
      btn.textContent = '创建并发送';
    }
  }

  /* ---------------- 初始化 ---------------- */

  async function loadSessions(force) {
    try {
      const d = await api('/api/sessions');
      state.sessions = d.sessions || [];
      state.runningMap = new Map(state.sessions.map((s) => [s.id, s.running]));
      if (d.connection) setConn(d.connection);
      renderList(true);
      updateTitle();
    } catch (err) {
      setConn({ ok: false, error: err.message });
      if (!state.sessions.length) {
        listEl.innerHTML = `<div style="padding:24px 12px;text-align:center;color:var(--text-3);font-size:12px;line-height:1.7">
          没有连上 Antigravity<br>${esc(err.message)}</div>`;
      }
    }
  }

  function bindGlobal() {
    listEl.addEventListener('click', (e) => {
      const item = e.target.closest('.item');
      if (item) select(item.dataset.id);
    });

    $('#tabs').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-filter]');
      if (!b) return;
      state.filter = b.dataset.filter;
      [...$('#tabs').children].forEach((x) => x.classList.toggle('on', x === b));
      renderList(true);
    });

    let qTimer;
    $('#search').addEventListener('input', (e) => {
      state.query = e.target.value;
      clearTimeout(qTimer);
      qTimer = setTimeout(() => renderList(true), 120);
    });

    $('#btn-refresh').addEventListener('click', async () => {
      const b = $('#btn-refresh');
      b.classList.add('spin');
      await loadSessions(true);
      if (state.selectedId) await refreshDetail(true);
      setTimeout(() => b.classList.remove('spin'), 500);
    });

    $('#btn-new').addEventListener('click', openNewModal);
    $('#btn-bell').addEventListener('click', toggleNotify);
    $('#btn-net').addEventListener('click', openNetModal);
    document.addEventListener('pointerdown', unlockAudio, { once: true });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeModal();
    });

    // 系统返回键（WebView）/ 浏览器返回：详情 → 列表
    window.addEventListener('popstate', () => {
      if (document.body.dataset.screen === 'detail') showScreen('list');
    });

    // 切到后台就断开实时流，省电省流量；回来再连
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (state.es) { state.es.close(); state.es = null; }
        stopTick();
      } else {
        connectStream(state.selectedId);
        loadSessions(true);
      }
    });

    // 时间线滚动时更新"回到最新"浮标（scroll 不冒泡，用捕获）
    document.addEventListener('scroll', () => toggleToBottom(), true);
    // 兜底：WebView 里滚动事件偶尔不触发，低频校准一次
    setInterval(() => { if (!document.hidden) toggleToBottom(); }, 1200);

    document.addEventListener('toggle', (e) => {
      const el = e.target;
      if (!el || !el.classList || !el.dataset || !el.dataset.idx) return;
      if (el.open) state.openTools.add(el.dataset.idx);
      else state.openTools.delete(el.dataset.idx);

      // 思考过程被截断时，展开再去取全文
      if (el.open && el.dataset.body === '1' && el.dataset.loaded !== '1') {
        el.dataset.loaded = '1';
        fetchBody(Number(el.dataset.step))
          .then((ev) => {
            const inner = el.querySelector('.inner');
            if (inner) inner.textContent = ev.thinking || '';
          })
          .catch((err) => {
            el.dataset.loaded = '';
            toast('加载失败：' + err.message, true);
          });
      }
    }, true);

    document.addEventListener('click', (e) => {
      const toBottom = e.target.closest('#to-bottom');
      if (toBottom) {
        const el = $('#timeline');
        if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
        state.pendingNew = false;
        setTimeout(toggleToBottom, 300);
        return;
      }

      const copyCmd = e.target.closest('[data-copy-cmd]');
      if (copyCmd) {
        copyText(copyCmd.dataset.copyCmd, '命令已复制');
        return;
      }

      const back = e.target.closest('#btn-back');
      if (back) {
        goBackToList();
        return;
      }

      const more = e.target.closest('[data-more]');
      if (more) {
        const idx = Number(more.dataset.more);
        more.disabled = true;
        more.textContent = '加载中…';
        fetchBody(idx)
          .then(() => renderDetail(true))
          .catch((err) => {
            more.disabled = false;
            more.textContent = '展开全文';
            toast('加载失败：' + err.message, true);
          });
        return;
      }

      const head = e.target.closest('.tool-head');
      if (head) {
        const card = head.parentElement;
        const idx = Number(card.dataset.idx);
        const willOpen = !card.classList.contains('open');
        card.classList.toggle('open');
        if (willOpen) {
          state.openTools.add(idx);
          if (card.dataset.body === '1' && card.dataset.loaded !== '1') loadCardBody(card, idx);
        } else {
          state.openTools.delete(idx);
        }
        return;
      }
      const older = e.target.closest('#older');
      if (older) {
        state.limit = Math.min(1500, (state.limit || 200) * 2);
        connectStream(state.selectedId);
        refreshDetail(true);
        return;
      }
      const flink = e.target.closest('.flink');
      if (flink) {
        e.preventDefault();
        const p = flink.dataset.copy || '';
        navigator.clipboard && navigator.clipboard.writeText(p);
        toast('已复制路径：' + shortPath(p, 3));
      }
    });
  }

  async function boot() {
    bindGlobal();
    await loadSessions(true);
    api('/api/models').then((d) => {
      state.models = d.models || [];
      state.defaultModel = d.default || '';
      fillModels();
      if (state.detail) updateComposer(state.detail);
    }).catch(() => {});
    connectStream(null);
    if (state.sessions.length && !state.selectedId) {
      const target = state.sessions.find((s) => s.running) || state.sessions[0];
      if (target) select(target.id, { navigate: false });
    }
  }

  boot();
})();
