/* TrendBot web app - plain JavaScript, no build step. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const app = {
  meta: null,
  timers: [],
  lastBacktest: null,     // kept so the Backtest tab shows the last result when you come back
  backtestForm: null,
  charts: [],
};

// ============================================================================ helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtNum(x, digits = 2) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  return Number(x).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtPrice(x) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  const a = Math.abs(x);
  if (a >= 1000) return fmtNum(x, 2);
  if (a >= 1) return fmtNum(x, a >= 100 ? 2 : 4);
  if (a === 0) return "0";
  return Number(x).toPrecision(5);
}

function fmtQty(x) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  return Number(x) >= 1 ? fmtNum(x, 4) : String(Number(Number(x).toPrecision(5)));
}

function signed(x, digits = 2) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  return (x > 0 ? "+" : x < 0 ? "−" : "") + fmtNum(Math.abs(x), digits);
}

function pct(x, digits = 1) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  return signed(x, digits) + "%";
}

function cls(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }

function toMs(t) { return typeof t === "number" ? t : Date.parse(t); }

function fmtTime(t) {
  const d = new Date(toMs(t));
  if (isNaN(d)) return "—";
  const year = d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {};
  return d.toLocaleString(undefined, { ...year, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function fmtDate(t) {
  const d = new Date(toMs(t));
  if (isNaN(d)) return "—";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function ago(t) {
  const s = Math.round((Date.now() - toMs(t)) / 1000);
  if (!isFinite(s)) return "";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function quoteOf(cfg) {
  const ex = app.meta?.exchanges?.[cfg.exchange];
  if (ex && ex.kind === "stocks") return "USD";
  return (cfg.symbol || "").split("/")[1] || "";
}

function modeLabel(mode, exchange) {
  if (mode === "testnet") return exchange === "alpaca" ? "Alpaca paper" : "Testnet";
  return { paper: "Paper", live: "Live" }[mode] || mode;
}

function modeBadge(mode, exchange) {
  return `<span class="badge ${esc(mode)}">${esc(modeLabel(mode, exchange))}</span>`;
}

function toast(msg, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3000);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ============================================================================ API

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function errorText(data, status) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {  // FastAPI validation errors
    return d.map(e => {
      const field = (e.loc || []).filter(p => p !== "body").join(".");
      const msg = String(e.msg || "Invalid value").replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    }).join(" · ");
  }
  return `Request failed (${status}).`;
}

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch("/api" + path, {
      method,
      credentials: "same-origin",
      headers: { "X-TrendBot": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError("Can't reach the TrendBot app. Is it still running on your computer?", 0);
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) {
    if (res.status === 401 && app.meta && path !== "/login") {
      app.meta.logged_in = false;
      route();
    }
    throw new ApiError(errorText(data, res.status), res.status);
  }
  return data;
}

// ============================================================================ chart

/**
 * Small canvas line chart with a crosshair tooltip.
 * data = { times: [ms], series: [{name, values, color (css var), width, dash, fmt}],
 *          markers: [{i, price, side}], hlines: [{value, color, label}], fmtY }
 */
class Chart {
  constructor(el) {
    this.el = el;
    this.el.innerHTML = '<canvas></canvas><div class="tip hidden"></div>';
    this.canvas = $("canvas", el);
    this.tip = $(".tip", el);
    this.data = null;
    this.hover = null;
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(el);
    const move = e => {
      const r = this.canvas.getBoundingClientRect();
      this.pointer(e.clientX - r.left, e.clientY - r.top);
    };
    this.canvas.addEventListener("pointermove", move);
    this.canvas.addEventListener("pointerdown", move);
    this.canvas.addEventListener("pointerleave", () => { this.hover = null; this.draw(); });
    app.charts.push(this);
  }

  destroy() { this.ro.disconnect(); }

  message(text) {
    this.data = null;
    this.el.innerHTML = `<div class="msg">${esc(text)}</div>`;
  }

  set(data) {
    if (!this.el.contains(this.canvas)) {  // was showing a message
      this.el.innerHTML = "";
      this.el.append(this.canvas, this.tip);
    }
    this.data = data;
    if (this.hover !== null && this.hover >= data.times.length) this.hover = null;
    this.draw();
  }

  layout() {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    const pad = { l: 8, r: this.padR || 60, t: 12, b: 24 };
    return { w, h, pad, pw: Math.max(10, w - pad.l - pad.r), ph: Math.max(10, h - pad.t - pad.b) };
  }

  pointer(x) {
    if (!this.data || this.data.times.length < 2) return;
    const { pad, pw } = this.layout();
    const n = this.data.times.length;
    const i = Math.round((x - pad.l) / pw * (n - 1));
    this.hover = Math.min(n - 1, Math.max(0, i));
    this.draw();
  }

  draw() {
    const d = this.data;
    if (!d || !this.el.contains(this.canvas)) return;
    const { w, h, pad, pw, ph } = this.layout();
    if (w === 0 || h === 0) return;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const n = d.times.length;
    if (n < 2) return;
    const fmtY = d.fmtY || fmtPrice;

    // y range over everything that's drawn
    let lo = Infinity, hi = -Infinity;
    for (const s of d.series) for (const v of s.values) if (v !== null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    for (const l of d.hlines || []) if (l.value !== null && isFinite(l.value)) { lo = Math.min(lo, l.value); hi = Math.max(hi, l.value); }
    for (const m of d.markers || []) { lo = Math.min(lo, m.price); hi = Math.max(hi, m.price); }
    if (!isFinite(lo)) return;
    if (hi === lo) { hi += Math.abs(hi) * 0.01 || 1; lo -= Math.abs(lo) * 0.01 || 1; }
    const span = hi - lo;
    lo -= span * 0.06; hi += span * 0.06;

    // Axis labels drop decimals when the tick step is whole; the right margin fits the widest label.
    ctx.font = "11px system-ui, -apple-system, Segoe UI, sans-serif";
    const ticks = niceTicks(lo, hi, Math.max(3, Math.round(ph / 60)));
    const step = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
    const fmtAxis = step >= 1 && !d.fmtY ? v => fmtNum(v, 0) : fmtY;
    const labels = [...ticks.map(fmtAxis), ...(d.hlines || []).filter(l => l.value != null).map(l => fmtY(l.value))];
    const padR = Math.ceil(Math.max(40, ...labels.map(t => ctx.measureText(t).width)) + 18);
    if (padR !== this.padR) { this.padR = padR; return this.draw(); }

    const X = i => pad.l + (i / (n - 1)) * pw;
    const Y = v => pad.t + (1 - (v - lo) / (hi - lo)) * ph;

    const ink2 = cssVar("--ink-2"), ink3 = cssVar("--ink-3"), grid = cssVar("--grid"), border = cssVar("--border");
    ctx.textBaseline = "middle";

    // grid + y labels
    ctx.strokeStyle = grid; ctx.lineWidth = 1; ctx.fillStyle = ink3; ctx.textAlign = "left";
    for (const t of ticks) {
      const y = Math.round(Y(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
      ctx.fillText(fmtAxis(t), pad.l + pw + 8, y);
    }
    // x labels
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const nx = Math.max(2, Math.floor(pw / 110));
    const spanMs = d.times[n - 1] - d.times[0];
    for (let k = 0; k <= nx; k++) {
      const i = Math.round(k / nx * (n - 1));
      const dt = new Date(d.times[i]);
      const label = spanMs > 200 * 86400e3
        ? dt.toLocaleDateString(undefined, { month: "short", year: "2-digit" })
        : dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      const x = Math.min(Math.max(X(i), pad.l + 24), pad.l + pw - 24);
      ctx.fillText(label, x, pad.t + ph + 7);
    }
    ctx.strokeStyle = border;
    ctx.beginPath(); ctx.moveTo(pad.l, pad.t + ph + 0.5); ctx.lineTo(pad.l + pw, pad.t + ph + 0.5); ctx.stroke();

    // horizontal reference lines (stop, entry)
    ctx.textBaseline = "middle";
    for (const l of d.hlines || []) {
      if (l.value === null || !isFinite(l.value)) continue;
      const y = Math.round(Y(l.value)) + 0.5;
      const c = cssVar(l.color);
      ctx.strokeStyle = c; ctx.lineWidth = 1.25; ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
      ctx.setLineDash([]);
      const text = fmtY(l.value);
      const tw = ctx.measureText(text).width + 8;
      ctx.fillStyle = c;
      roundRect(ctx, pad.l + pw + 4, y - 9, tw, 18, 4); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.textAlign = "left";
      ctx.fillText(text, pad.l + pw + 8, y);
      if (l.label) {
        ctx.fillStyle = c; ctx.textAlign = "right";
        ctx.fillText(l.label, pad.l + pw - 4, y - 9);
      }
    }

    // series
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    for (const s of d.series) {
      ctx.strokeStyle = cssVar(s.color); ctx.lineWidth = s.width || 1.5;
      ctx.setLineDash(s.dash || []);
      ctx.beginPath();
      let started = false;
      s.values.forEach((v, i) => {
        if (v === null || !isFinite(v)) { started = false; return; }
        if (!started) { ctx.moveTo(X(i), Y(v)); started = true; } else ctx.lineTo(X(i), Y(v));
      });
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // buy / sell markers
    const pos = cssVar("--pos"), neg = cssVar("--neg"), surface = cssVar("--surface");
    for (const m of d.markers || []) {
      const x = X(m.i), y = Y(m.price), up = m.side === "buy";
      ctx.fillStyle = up ? pos : neg; ctx.strokeStyle = surface; ctx.lineWidth = 1.5;
      ctx.beginPath();
      if (up) { ctx.moveTo(x, y + 4); ctx.lineTo(x - 6, y + 14); ctx.lineTo(x + 6, y + 14); }
      else { ctx.moveTo(x, y - 4); ctx.lineTo(x - 6, y - 14); ctx.lineTo(x + 6, y - 14); }
      ctx.closePath(); ctx.stroke(); ctx.fill();
    }

    // crosshair + tooltip
    const i = this.hover;
    if (i === null) { this.tip.classList.add("hidden"); return; }
    const x = Math.round(X(i)) + 0.5;
    ctx.strokeStyle = ink2; ctx.globalAlpha = 0.5; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    for (const s of d.series) {
      const v = s.values[i];
      if (v === null || !isFinite(v)) continue;
      ctx.fillStyle = cssVar(s.color); ctx.strokeStyle = surface; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(X(i), Y(v), 3.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
    const rows = d.series.map(s => {
      const v = s.values[i];
      return `<div class="row"><span class="sw" style="background:${esc(cssVar(s.color))}"></span>${esc(s.name)}<b>${esc((s.fmt || fmtY)(v))}</b></div>`;
    });
    for (const m of (d.markers || []).filter(m => m.i === i)) {
      rows.push(`<div class="row"><span class="sw" style="background:${m.side === "buy" ? pos : neg}"></span>${m.side === "buy" ? "Bought" : "Sold"} at<b>${esc(fmtPrice(m.price))}</b></div>`);
    }
    this.tip.innerHTML = `<div class="t">${esc(fmtTime(d.times[i]))}</div>${rows.join("")}`;
    this.tip.classList.remove("hidden");
    const tw = this.tip.offsetWidth;
    let left = X(i) + 14;
    if (left + tw > w - 4) left = X(i) - tw - 14;
    this.tip.style.left = Math.max(4, left) + "px";
    this.tip.style.top = pad.t + 4 + "px";
  }
}

function niceTicks(lo, hi, count) {
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9 * step; v += step) out.push(+v.toPrecision(12));
  return out;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

function legend(items) {
  return `<div class="legend">${items.map(([label, color, kind]) => {
    if (kind === "buy") return `<span><i class="tri-up"></i>${esc(label)}</span>`;
    if (kind === "sell") return `<span><i class="tri-down"></i>${esc(label)}</span>`;
    if (kind === "dash") return `<span><i class="dash" style="border-color:var(${color})"></i>${esc(label)}</span>`;
    return `<span><i style="background:var(${color})"></i>${esc(label)}</span>`;
  }).join("")}</div>`;
}

/** Index of the last candle that opened at or before t. */
function candleIndex(times, t) {
  let lo = 0, hi = times.length - 1, ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

// ============================================================================ routing

function clearTimers() {
  app.timers.forEach(clearInterval);
  app.timers = [];
  app.charts.forEach(c => c.destroy());
  app.charts = [];
}

function every(ms, fn) { app.timers.push(setInterval(fn, ms)); }

async function route() {
  clearTimers();
  const view = $("#view");
  if (!app.meta) {
    try { app.meta = await api("/meta"); } catch (e) {
      view.innerHTML = `<div class="card"><div class="alert error">${esc(e.message)}</div>
        <button class="btn" onclick="location.reload()">Try again</button></div>`;
      return;
    }
  }
  if (!app.meta.logged_in) return renderLogin(view);

  const parts = (location.hash.replace(/^#\/?/, "") || "bots").split("/");
  $$("#tabs a").forEach(a => a.classList.toggle("active", a.dataset.tab === parts[0]));
  window.scrollTo(0, 0);
  if (parts[0] === "bots" && parts[1]) return renderBot(view, decodeURIComponent(parts[1]));
  if (parts[0] === "backtest") return renderBacktest(view);
  if (parts[0] === "setup") return renderSetup(view);
  return renderBots(view);
}

window.addEventListener("hashchange", route);

// ============================================================================ login

function renderLogin(view) {
  $$("#tabs a").forEach(a => a.classList.remove("active"));
  view.innerHTML = `
    <div class="card login">
      <h2>Log in</h2>
      <p class="muted small">This TrendBot is protected with the password set in <code>BOT_UI_PASSWORD</code>.</p>
      <form id="login-form">
        <label class="field">Password <input type="password" name="password" autocomplete="current-password" required autofocus></label>
        <div class="alert error hidden" id="login-err" style="margin-top:12px"></div>
        <div style="margin-top:14px"><button class="btn primary" type="submit" style="width:100%">Log in</button></div>
      </form>
    </div>`;
  $("#login-form").addEventListener("submit", async e => {
    e.preventDefault();
    const btn = $("button", e.target);
    btn.disabled = true;
    try {
      await api("/login", { method: "POST", body: { password: e.target.password.value } });
      app.meta = null;
      route();
    } catch (err) {
      $("#login-err").textContent = err.message;
      $("#login-err").classList.remove("hidden");
      btn.disabled = false;
    }
  });
}

// ============================================================================ bots list

async function renderBots(view) {
  view.innerHTML = `
    <div class="page-head">
      <div class="grow"><h1>Your bots</h1>
        <div class="muted small">Each bot trades one market with the EMA trend strategy. New bots start in paper mode (simulated money).</div></div>
      <div class="btn-row"><button class="btn primary" id="new-bot">+ New bot</button></div>
    </div>
    <div id="bot-list"><div class="loading"><span class="spinner"></span> Loading bots…</div></div>`;
  $("#new-bot").addEventListener("click", () => openBotForm());

  const load = async () => {
    let bots;
    try { bots = await api("/bots"); } catch (e) {
      $("#bot-list").innerHTML = `<div class="alert error">${esc(e.message)}</div>`;
      return;
    }
    const list = $("#bot-list");
    if (!list) return;
    if (!bots.length) {
      list.innerHTML = `
        <div class="card empty">
          <h2>No bots yet</h2>
          <p>Try the strategy on past prices in <b>Backtest</b> first, then create a bot. It starts in paper mode,
             so it trades pretend money against real live prices until you decide otherwise.</p>
          <div class="btn-row" style="justify-content:center">
            <a class="btn" href="#/backtest">Run a backtest</a>
            <button class="btn primary" id="new-bot-2">Create a paper bot</button>
          </div>
        </div>`;
      $("#new-bot-2").addEventListener("click", () => openBotForm());
      return;
    }
    list.innerHTML = `<div class="bot-grid">${bots.map(botCard).join("")}</div>`;
  };
  await load();
  every(10000, load);
}

function botCard(b) {
  const c = b.config, q = quoteOf(c);
  const dot = b.error ? "err" : b.running ? "on" : "";
  const holding = b.position
    ? `<span class="${cls(b.unrealized)}">${b.unrealized === null ? "Holding" : signed(b.unrealized)}</span>`
    : `<span class="muted">None</span>`;
  return `
    <a class="bot-card" href="#/bots/${encodeURIComponent(b.id)}">
      <div class="top"><span class="dot ${dot}" title="${b.running ? "Running" : "Stopped"}"></span>
        <span class="name">${esc(c.name)}</span>${modeBadge(c.mode, c.exchange)}</div>
      <div class="meta">${esc(c.symbol)} · ${esc(app.meta.exchanges[c.exchange]?.label || c.exchange)} · ${esc(c.timeframe)} candles · ${esc(fmtNum(c.trade_size))} ${esc(q)}/trade</div>
      <div class="figs">
        <div><div class="label">Price</div><div class="v">${fmtPrice(b.price)}</div></div>
        <div><div class="label">Position</div><div class="v">${holding}</div></div>
        <div><div class="label">Total P&amp;L</div><div class="v ${cls(b.total_pnl)}">${signed(b.total_pnl)}</div></div>
      </div>
      <div class="status">${b.running ? "" : "<b>Stopped.</b> "}${esc(b.error ? "Problem: " + b.error : b.status)}</div>
    </a>`;
}

// ============================================================================ bot detail

async function renderBot(view, id) {
  view.innerHTML = `<div class="loading"><span class="spinner"></span> Loading bot…</div>`;
  let bot;
  try { bot = await api(`/bots/${encodeURIComponent(id)}`); } catch (e) {
    view.innerHTML = `<a class="crumb" href="#/bots">← All bots</a><div class="alert error" style="margin-top:12px">${esc(e.message)}</div>`;
    return;
  }

  view.innerHTML = `
    <a class="crumb" href="#/bots">← All bots</a>
    <div class="page-head" style="margin-top:6px">
      <div class="grow"><h1 id="bot-title"></h1><div class="muted small" id="bot-sub"></div></div>
      <div class="btn-row" id="bot-actions"></div>
    </div>
    <div id="bot-alerts"></div>
    <div class="tiles" id="bot-tiles"></div>
    <div class="card">
      <div class="card-head"><h2>Price &amp; signals</h2><span class="muted small" id="chart-note"></span></div>
      <div id="chart-legend"></div>
      <div class="chart" id="bot-chart"></div>
    </div>
    <div class="two-col">
      <div class="card"><h2>Trades</h2><div id="bot-trades"></div></div>
      <div class="card"><h2>Activity log</h2><div class="log" id="bot-log"></div></div>
    </div>`;

  const chart = new Chart($("#bot-chart"));
  chart.message("Loading chart…");

  const paint = b => {
    bot = b;
    const c = b.config, q = quoteOf(c);
    $("#bot-title").innerHTML = `${esc(c.name)} ${modeBadge(c.mode, c.exchange)}`;
    $("#bot-sub").textContent = `${c.symbol} on ${app.meta.exchanges[c.exchange]?.label || c.exchange} · ${c.timeframe} candles · EMA ${c.fast}/${c.slow} · stop ${c.atr_mult}× ATR(${c.atr_period}) · ${fmtNum(c.trade_size)} ${q} per trade · daily loss cap ${c.daily_loss_cap > 0 ? fmtNum(c.daily_loss_cap) + " " + q : "off"}`;

    const actions = [];
    if (b.running) actions.push(`<button class="btn" data-act="stop">■ Stop</button>`);
    else actions.push(`<button class="btn go" data-act="start">▶ Start</button>`);
    if (b.position) actions.push(`<button class="btn danger" data-act="close">Sell now</button>`);
    actions.push(`<button class="btn" data-act="edit" ${b.running ? 'disabled title="Stop the bot to edit it"' : ""}>Edit</button>`);
    actions.push(`<button class="btn danger" data-act="delete" ${b.running || b.position ? 'disabled title="Stop the bot and close its position first"' : ""}>Delete</button>`);
    $("#bot-actions").innerHTML = actions.join("");

    const alerts = [];
    if (c.mode === "live") alerts.push(`<div class="alert warn"><b>Live mode:</b> this bot trades real money on your ${esc(app.meta.exchanges[c.exchange]?.label)} account.</div>`);
    if (b.error) alerts.push(`<div class="alert error"><b>Problem:</b> ${esc(b.error)}${b.running ? " The bot keeps retrying every minute." : ""}</div>`);
    if (b.position && !b.running) alerts.push(`<div class="alert warn">The bot is stopped but still holds a position. Nothing watches its trailing stop until you start it again or sell.</div>`);
    $("#bot-alerts").innerHTML = alerts.join("");

    const ind = b.indicators;
    const trend = !ind ? "—" : ind.fast > ind.slow ? "Up" : "Down";
    const pos = b.position;
    $("#bot-tiles").innerHTML = [
      tile("Status", `<span class="dot ${b.error ? "err" : b.running ? "on" : ""}"></span> ${b.running ? "Running" : "Stopped"}`, b.last_tick ? "checked " + ago(b.last_tick) : ""),
      tile("Price", fmtPrice(b.price), b.price_time ? ago(b.price_time) : ""),
      tile("Trend (EMA)", trend, ind ? `fast ${fmtPrice(ind.fast)} / slow ${fmtPrice(ind.slow)}` : "after first candle"),
      tile("Position", pos ? fmtQty(pos.qty) : "None", pos ? `bought at ${fmtPrice(pos.entry_price)}` : "waiting for a buy signal"),
      tile("Trailing stop", pos ? fmtPrice(pos.stop) : "—", pos && b.price ? `${fmtNum((b.price / pos.stop - 1) * 100, 1)}% below price` : ""),
      tile("Open P&L", `<span class="${cls(b.unrealized)}">${signed(b.unrealized)}</span>`, pos ? q + " after sell fee" : ""),
      tile("Today", `<span class="${cls(b.today_pnl)}">${signed(b.today_pnl)}</span>`, `${q} closed trades`),
      tile("Total P&L", `<span class="${cls(b.total_pnl)}">${signed(b.total_pnl)}</span>`, `${b.trades_count} trades, ${b.wins} won`),
    ].join("");

    const trades = b.trades || [];
    $("#bot-trades").innerHTML = trades.length ? `
      <div class="table-wrap"><table>
        <thead><tr><th>Time</th><th>Side</th><th class="r">Price</th><th class="r">Amount</th><th class="r">P&amp;L</th><th>Reason</th></tr></thead>
        <tbody>${trades.map(t => `
          <tr><td>${esc(fmtTime(t.time))}</td>
            <td><b class="${t.side === "buy" ? "pos" : "neg"}">${t.side.toUpperCase()}</b>${t.mode !== c.mode ? ` <span class="badge">${esc(t.mode)}</span>` : ""}</td>
            <td class="r">${fmtPrice(t.price)}</td>
            <td class="r">${fmtNum(t.quote)} ${esc(q)}</td>
            <td class="r ${cls(t.pnl)}">${t.pnl === undefined ? "" : signed(t.pnl) + (t.pnl_pct != null ? ` <span class="small">(${pct(t.pnl_pct)})</span>` : "")}</td>
            <td class="wrap muted">${esc(t.reason)}</td></tr>`).join("")}
        </tbody></table></div>` : `<p class="muted small">No trades yet. The bot buys only on a fresh cross of the fast EMA above the slow EMA, which can take days or weeks.</p>`;

    const log = b.log || [];
    $("#bot-log").innerHTML = log.length ? log.map(l => `
      <div class="entry ${esc(l.level)}"><span class="time">${esc(fmtTime(l.time))}</span><span class="msg">${esc(l.msg)}</span></div>`).join("")
      : `<p class="muted small">Nothing yet.</p>`;
  };

  const loadChart = async () => {
    try {
      const d = await api(`/bots/${encodeURIComponent(id)}/chart`);
      const times = d.candles.map(k => k[0]);
      const markers = d.markers.map(m => ({ i: candleIndex(times, m.t), price: m.price, side: m.side }));
      const hlines = [];
      if (d.entry) hlines.push({ value: d.entry, color: "--ink-3", label: "Entry" });
      if (d.stop) hlines.push({ value: d.stop, color: "--neg", label: "Trailing stop" });
      const c = bot.config;
      chart.set({
        times,
        series: [
          { name: "Close", values: d.candles.map(k => k[4]), color: "--c-price", width: 1.5 },
          { name: `EMA ${c.fast}`, values: d.fast, color: "--c-fast", width: 2 },
          { name: `EMA ${c.slow}`, values: d.slow, color: "--c-slow", width: 2 },
        ],
        markers, hlines,
      });
      const items = [["Close price", "--c-price"], [`Fast EMA ${c.fast}`, "--c-fast"], [`Slow EMA ${c.slow}`, "--c-slow"], ["Buy", "", "buy"], ["Sell", "", "sell"]];
      if (d.stop) items.push(["Trailing stop", "--neg", "dash"]);
      if (d.entry) items.push(["Entry price", "--ink-3", "dash"]);
      $("#chart-legend").innerHTML = legend(items);
      $("#chart-note").textContent = `last ${times.length} × ${d.timeframe} candles`;
    } catch (e) {
      if (!chart.data) chart.message("Chart unavailable: " + e.message);
    }
  };

  $("#bot-actions").addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn || btn.disabled) return;
    const act = btn.dataset.act;
    const c = bot.config;
    if (act === "edit") return openBotForm(bot);
    if (act === "delete") {
      if (!(await confirmDialog("Delete bot?", `Delete "${c.name}" and its trade history? This can't be undone.`, "Delete", true))) return;
    }
    if (act === "close") {
      const real = c.mode === "paper" ? "(paper trade - no real money)" : c.mode === "live" ? "This sells REAL coins/shares at the market price." : "This sells on your testnet account.";
      if (!(await confirmDialog("Sell now?", `Sell the whole ${c.symbol} position at the current market price? ${real}`, "Sell now", true))) return;
    }
    if (act === "start" && c.mode === "live") {
      if (!(await confirmDialog("Start live trading?", `This bot will place real orders with real money: up to ${fmtNum(c.trade_size)} ${quoteOf(c)} per trade on ${app.meta.exchanges[c.exchange]?.label}.`, "Start live bot", true))) return;
    }
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span>`;
    try {
      if (act === "delete") {
        await api(`/bots/${encodeURIComponent(id)}`, { method: "DELETE" });
        toast("Bot deleted.");
        location.hash = "#/bots";
        return;
      }
      await api(`/bots/${encodeURIComponent(id)}/${act}`, { method: "POST" });
      toast({ start: "Bot started.", stop: "Bot stopped.", close: "Position sold." }[act]);
      paint(await api(`/bots/${encodeURIComponent(id)}`));
      if (act !== "stop") loadChart();
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
      btn.innerHTML = old;
    }
  });

  paint(bot);
  loadChart();
  every(10000, async () => {
    try { paint(await api(`/bots/${encodeURIComponent(id)}`)); } catch (e) { /* keep last view */ }
  });
  every(60000, loadChart);
}

function tile(label, value, sub = "") {
  return `<div class="tile"><div class="label">${esc(label)}</div><div class="value">${value}</div><div class="sub">${esc(sub)}</div></div>`;
}

// ============================================================================ dialogs

function openDialog(html) {
  const dlg = document.createElement("dialog");
  dlg.innerHTML = html;
  document.body.append(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.showModal();
  return dlg;
}

function confirmDialog(title, text, okLabel = "OK", danger = false) {
  return new Promise(resolve => {
    const dlg = openDialog(`
      <form method="dialog">
        <div class="dlg-head"><h2>${esc(title)}</h2></div>
        <div class="dlg-body"><p>${esc(text)}</p></div>
        <div class="dlg-foot">
          <button class="btn" value="no">Cancel</button>
          <button class="btn ${danger ? "danger-solid" : "primary"}" value="yes">${esc(okLabel)}</button>
        </div>
      </form>`);
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "yes"));
  });
}

/** The extra gate before a bot is switched to real money: the user must type LIVE. */
function confirmLive(cfg) {
  return new Promise(resolve => {
    const q = quoteOf(cfg);
    const dlg = openDialog(`
      <form method="dialog" id="live-form">
        <div class="dlg-head"><h2>Switch to live trading?</h2></div>
        <div class="dlg-body">
          <div class="alert error"><b>Real money.</b> In live mode the bot places real market orders on your
            ${esc(app.meta.exchanges[cfg.exchange]?.label)} account with no one checking each trade.</div>
          <ul class="small" style="padding-left:18px">
            <li>Each buy spends up to <b>${esc(fmtNum(cfg.trade_size))} ${esc(q)}</b>.</li>
            <li>The daily loss cap is <b>${cfg.daily_loss_cap > 0 ? esc(fmtNum(cfg.daily_loss_cap) + " " + q) : "OFF"}</b> - it stops new buys after that much loss in a day, but a single trade can still lose more.</li>
            <li>Profit is not guaranteed. Trend strategies often have many small losing trades.</li>
            <li>Your API key should allow <b>trading only - never withdrawals</b>.</li>
          </ul>
          <label class="field"><span>Type <code>LIVE</code> to confirm</span>
            <input name="confirm" autocomplete="off" autocapitalize="characters" spellcheck="false"></label>
        </div>
        <div class="dlg-foot">
          <button class="btn" value="no" formnovalidate>Cancel</button>
          <button class="btn danger-solid" value="yes" id="live-ok" disabled>Use real money</button>
        </div>
      </form>`);
    const input = $("input", dlg), ok = $("#live-ok", dlg);
    input.addEventListener("input", () => { ok.disabled = input.value.trim() !== "LIVE"; });
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "yes" && input.value.trim() === "LIVE"));
  });
}

function strategyFields(v) {
  const ex = app.meta.exchanges;
  return `
    <label class="field">Exchange
      <select name="exchange">${Object.entries(ex).map(([k, e]) => `<option value="${esc(k)}" ${k === v.exchange ? "selected" : ""}>${esc(e.label)}</option>`).join("")}</select></label>
    <label class="field">Symbol
      <input name="symbol" value="${esc(v.symbol)}" required maxlength="24" autocapitalize="characters" spellcheck="false" placeholder="${esc(ex[v.exchange]?.example || "BTC/USDT")}"></label>
    <label class="field">Candle size
      <select name="timeframe">${app.meta.timeframes.map(t => `<option ${t === v.timeframe ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
    <label class="field">Trade size <span class="hint">spent on each buy (${esc(quoteOf(v) || "quote")})</span>
      <input name="trade_size" type="number" step="any" min="0" value="${esc(v.trade_size)}" required></label>
    <label class="field">Daily loss cap <span class="hint">no new buys after this loss in a day; 0 = off</span>
      <input name="daily_loss_cap" type="number" step="any" min="0" value="${esc(v.daily_loss_cap)}" required></label>`;
}

function advancedFields(v) {
  return `
    <details class="advanced"><summary>Strategy settings (advanced)</summary>
      <div class="form-grid">
        <label class="field">Fast EMA <input name="fast" type="number" min="2" max="200" value="${esc(v.fast)}" required></label>
        <label class="field">Slow EMA <input name="slow" type="number" min="3" max="400" value="${esc(v.slow)}" required></label>
        <label class="field">ATR period <input name="atr_period" type="number" min="2" max="100" value="${esc(v.atr_period)}" required></label>
        <label class="field">Stop distance <span class="hint">× ATR below the high</span>
          <input name="atr_mult" type="number" step="0.1" min="0.5" max="10" value="${esc(v.atr_mult)}" required></label>
      </div>
      <p class="muted small" style="margin-top:10px">Buys when the fast EMA crosses above the slow EMA. Sells when it crosses back below,
        or when price drops to the trailing stop (highest price since buying minus the ATR multiple).</p>
    </details>`;
}

function readForm(form) {
  const f = Object.fromEntries(new FormData(form));
  for (const k of ["fast", "slow", "atr_period", "days"]) if (k in f) f[k] = parseInt(f[k], 10);
  for (const k of ["atr_mult", "trade_size", "daily_loss_cap"]) if (k in f) f[k] = parseFloat(f[k]);
  if (f.symbol) f.symbol = f.symbol.trim().toUpperCase();
  return f;
}

function wireExchangeField(form) {
  // Suggest a matching symbol when the exchange changes between crypto and stocks.
  form.exchange.addEventListener("change", () => {
    const e = app.meta.exchanges[form.exchange.value];
    form.symbol.placeholder = e.example;
    const looksCrypto = form.symbol.value.includes("/");
    if ((e.kind === "stocks") === looksCrypto) form.symbol.value = e.example;
    form.dispatchEvent(new Event("input"));
  });
}

function openBotForm(bot = null, preset = null) {
  const editing = !!bot;
  const v = { ...app.meta.defaults, ...(preset || {}), ...(bot ? bot.config : {}) };
  if (!editing && !preset) v.name = "";
  const dlg = openDialog(`
    <form id="bot-form" novalidate>
      <div class="dlg-head"><h2>${editing ? "Edit bot" : "New bot"}</h2></div>
      <div class="dlg-body">
        <div class="form-grid">
          <label class="field wide">Name <input name="name" value="${esc(v.name)}" maxlength="40" placeholder="e.g. BTC trend"></label>
          ${strategyFields(v)}
          <label class="field wide">Mode
            <select name="mode">
              <option value="paper">Paper - pretend money, real live prices (no keys needed)</option>
              <option value="testnet">Testnet - the exchange's practice account (test keys)</option>
              <option value="live">Live - REAL money (live keys)</option>
            </select></label>
        </div>
        <div id="mode-note" style="margin-top:12px"></div>
        ${advancedFields(v)}
      </div>
      <div class="alert error dlg-error hidden" id="form-err"></div>
      <div class="dlg-foot">
        <button class="btn" type="button" id="cancel">Cancel</button>
        <button class="btn primary" type="submit">${editing ? "Save" : "Create bot"}</button>
      </div>
    </form>`);
  const form = $("#bot-form", dlg);
  form.mode.value = v.mode;
  $("#cancel", dlg).addEventListener("click", () => dlg.close());
  wireExchangeField(form);

  const note = () => {
    const ex = form.exchange.value, mode = form.mode.value;
    const hasKeys = mode === "paper" ? true : app.meta.keys[ex]?.[mode];
    let html = "";
    if (mode === "paper" && ex === "alpaca" && !app.meta.keys.alpaca.testnet && !app.meta.keys.alpaca.live)
      html = `<div class="alert warn">Stock prices come from Alpaca, so even paper mode needs a free Alpaca paper account. Connect it on the <a href="#/setup">Setup</a> page.</div>`;
    else if (mode === "paper") html = `<div class="alert info">Paper mode: trades are simulated at real live prices with normal fees. Nothing is bought.</div>`;
    else if (!hasKeys) html = `<div class="alert warn">Your ${esc(accountName(ex, mode))} isn't connected yet. Connect it on the <a href="#/setup">Setup</a> page first.</div>`;
    else if (mode === "live") html = `<div class="alert error">Live mode trades real money. You'll be asked to type LIVE to confirm.</div>`;
    else html = `<div class="alert info">Testnet: real orders on the exchange's practice account with fake balances.</div>`;
    $("#mode-note", dlg).innerHTML = html;
  };
  form.addEventListener("input", note);
  form.addEventListener("change", note);
  note();

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("#form-err", dlg);
    err.classList.add("hidden");
    const body = readForm(form);
    const prevMode = bot ? bot.config.mode : null;
    body.confirm_live = false;
    if (body.mode === "live" && prevMode !== "live") {
      if (!(await confirmLive(body))) return;
      body.confirm_live = true;
    }
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    try {
      const res = editing
        ? await api(`/bots/${encodeURIComponent(bot.id)}`, { method: "PUT", body })
        : await api("/bots", { method: "POST", body });
      dlg.close();
      toast(editing ? "Saved." : "Bot created. Press Start when you're ready.");
      const target = `#/bots/${encodeURIComponent(res.id)}`;
      if (location.hash === target) route(); else location.hash = target;
    } catch (ex) {
      err.textContent = ex.message;
      err.classList.remove("hidden");
      btn.disabled = false;
    }
  });
}

// ============================================================================ backtest

function renderBacktest(view) {
  const v = app.backtestForm || { ...app.meta.defaults, days: 730 };
  view.innerHTML = `
    <div class="page-head"><div class="grow"><h1>Backtest</h1>
      <div class="muted small">Replays the exact bot rules on past prices, with fees, and compares the result with simply buying and holding.</div></div></div>
    <div class="card">
      <form id="bt-form" novalidate>
        <div class="form-grid">
          ${strategyFields(v)}
          <label class="field">Period <select name="days">
            ${[[90, "3 months"], [180, "6 months"], [365, "1 year"], [730, "2 years"], [1095, "3 years"], [1825, "5 years"]]
              .map(([d, l]) => `<option value="${d}" ${d === v.days ? "selected" : ""}>${l}</option>`).join("")}
          </select></label>
        </div>
        ${advancedFields(v)}
        <div class="btn-row" style="margin-top:14px">
          <button class="btn primary" type="submit">Run backtest</button>
        </div>
      </form>
    </div>
    <div id="bt-result"></div>`;
  const form = $("#bt-form");
  wireExchangeField(form);
  form.addEventListener("input", () => { app.backtestForm = readForm(form); });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const body = readForm(form);
    app.backtestForm = body;
    const out = $("#bt-result");
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    out.innerHTML = `<div class="loading"><span class="spinner"></span> Downloading price history and simulating… this can take up to a minute.</div>`;
    try {
      app.lastBacktest = await api("/backtest", { method: "POST", body });
      showBacktest(app.lastBacktest);
    } catch (err) {
      out.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
    } finally {
      btn.disabled = false;
    }
  });
  if (app.lastBacktest) showBacktest(app.lastBacktest);
}

function showBacktest(r) {
  app.charts.forEach(c => c.destroy());
  app.charts = [];
  const req = r.request, q = quoteOf(req);
  const beat = r.strategy_return_pct - r.hold_return_pct;
  const out = $("#bt-result");
  const verdict = r.trades_count === 0
    ? "The strategy made no trades in this period."
    : beat >= 0
      ? `The strategy beat buy &amp; hold by ${fmtNum(beat, 1)} percentage points over this period.`
      : `Buy &amp; hold did ${fmtNum(-beat, 1)} percentage points better than the strategy over this period.`;
  out.innerHTML = `
    <div class="page-head" style="margin-top:4px">
      <div class="grow"><h2>${esc(req.symbol)} · ${esc(app.meta.exchanges[req.exchange]?.label)} · ${esc(req.timeframe)} · ${esc(fmtDate(r.start))} – ${esc(fmtDate(r.end))}</h2>
        <div class="muted small">${verdict} Fees of ${fmtNum(r.fee_rate * 100, 2)}% per side included.</div></div>
      <div class="btn-row"><button class="btn primary" id="bt-create">Create bot from these settings</button></div>
    </div>
    <div class="tiles">
      ${tile("Strategy return", `<span class="${cls(r.strategy_return_pct)}">${pct(r.strategy_return_pct)}</span>`, "reinvesting each trade")}
      ${tile("Buy & hold", `<span class="${cls(r.hold_return_pct)}">${pct(r.hold_return_pct)}</span>`, "bought once at the start")}
      ${tile("Worst drop", `<span class="neg">${pct(r.strategy_max_dd_pct)}</span>`, `hold: ${pct(r.hold_max_dd_pct)}`)}
      ${tile("Trades", String(r.trades_count), r.win_rate_pct === null ? "" : `${fmtNum(r.win_rate_pct, 0)}% won`)}
      ${tile("Avg win / loss", `<span class="pos">${pct(r.avg_win_pct)}</span> / <span class="neg">${pct(r.avg_loss_pct)}</span>`, "per trade")}
      ${tile(`P&L at ${fmtNum(req.trade_size)} ${q}/trade`, `<span class="${cls(r.total_pnl)}">${signed(r.total_pnl)}</span>`, `${fmtNum(r.fees_paid)} ${q} fees`)}
      ${tile("Time in market", fmtNum(r.time_in_market_pct, 0) + "%", r.skipped_by_daily_cap ? `${r.skipped_by_daily_cap} buys skipped by daily cap` : "rest of the time in cash")}
      ${tile("Open at end", r.open_position ? "Yes" : "No", r.open_position ? `open P&L ${signed(r.open_position.unrealized)} ${q}` : "")}
    </div>
    <div class="card">
      <div class="card-head"><h2>Growth of 100</h2></div>
      ${legend([["Strategy", "--c-fast"], ["Buy & hold", "--c-hold"]])}
      <div class="chart short" id="bt-equity"></div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Price and trades</h2></div>
      ${legend([["Close price", "--c-price"], [`Fast EMA ${req.fast}`, "--c-fast"], [`Slow EMA ${req.slow}`, "--c-slow"], ["Buy", "", "buy"], ["Sell", "", "sell"]])}
      <div class="chart" id="bt-price"></div>
    </div>
    <div class="card">
      <h2>Trades (${r.trades.length})</h2>
      ${r.trades.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Bought</th><th class="r">Buy price</th><th>Sold</th><th class="r">Sell price</th><th class="r">P&amp;L</th><th>Exit reason</th></tr></thead>
        <tbody>${r.trades.slice().reverse().map(t => `
          <tr><td>${esc(fmtTime(t.entry_time))}</td><td class="r">${fmtPrice(t.entry_price)}</td>
            <td>${esc(fmtTime(t.exit_time))}</td><td class="r">${fmtPrice(t.exit_price)}</td>
            <td class="r ${cls(t.pnl)}">${signed(t.pnl)} <span class="small">(${pct(t.pnl_pct)})</span></td>
            <td class="muted">${esc(t.reason)}</td></tr>`).join("")}
        </tbody></table></div>` : `<p class="muted small">No trades.</p>`}
    </div>
    <div class="alert info">A backtest is a rough guide, not a promise. Real fills can be worse (slippage), and a
      strategy that worked on past prices can lose money in future markets.</div>`;

  const times = r.curve.map(p => p.t);
  new Chart($("#bt-equity")).set({
    times,
    fmtY: y => fmtNum(y, Math.abs(y) >= 100 ? 0 : 1),
    series: [
      { name: "Strategy", values: r.curve.map(p => p.strategy), color: "--c-fast", width: 2 },
      { name: "Buy & hold", values: r.curve.map(p => p.hold), color: "--c-hold", width: 1.75 },
    ],
  });
  const markers = [];
  for (const t of r.trades) {
    markers.push({ i: candleIndex(times, t.entry_time), price: t.entry_price, side: "buy" });
    markers.push({ i: candleIndex(times, t.exit_time), price: t.exit_price, side: "sell" });
  }
  new Chart($("#bt-price")).set({
    times, markers,
    series: [
      { name: "Close", values: r.curve.map(p => p.price), color: "--c-price", width: 1.25 },
      { name: `EMA ${req.fast}`, values: r.curve.map(p => p.fast), color: "--c-fast", width: 1.75 },
      { name: `EMA ${req.slow}`, values: r.curve.map(p => p.slow), color: "--c-slow", width: 1.75 },
    ],
  });
  $("#bt-create").addEventListener("click", () => {
    const { days, ...settings } = req;
    openBotForm(null, { ...settings, mode: "paper", name: `${req.symbol} ${req.timeframe}` });
  });
}

// ============================================================================ setup

// Where to make keys, and which boxes to tick, per exchange and mode.
const KEY_GUIDES = {
  binance: {
    testnet: { url: "https://testnet.binance.vision/", steps: [
      "Open testnet.binance.vision and log in with GitHub.",
      "Click “Generate HMAC_SHA256 Key”, give it any name.",
      "Copy the API Key and Secret Key shown (the secret is shown only once)."] },
    live: { url: "https://www.binance.com/en/my/settings/api-management", steps: [
      "On Binance go to Account → API Management → Create API → System generated.",
      "Edit restrictions: tick “Enable Spot & Margin Trading”. Leave “Enable Withdrawals” OFF.",
      "Optional but safer: “Restrict access to trusted IPs only” and add your home IP.",
      "Copy the API Key and Secret Key."] },
  },
  bybit: {
    testnet: { url: "https://testnet.bybit.com/app/user/api-management", steps: [
      "Make an account on testnet.bybit.com (separate from your real one).",
      "Go to API → Create New Key → System-generated. Choose “Read-Write” and tick Spot trading.",
      "Copy the API Key and Secret."] },
    live: { url: "https://www.bybit.com/app/user/api-management", steps: [
      "On Bybit go to Account → API → Create New Key → System-generated.",
      "Choose “Read-Write”, tick Spot → Trade. Do NOT tick Withdraw or any Wallet transfer permissions.",
      "Optional but safer: only allow your home IP.",
      "Copy the API Key and Secret."] },
  },
  alpaca: {
    testnet: { url: "https://app.alpaca.markets/signup", steps: [
      "Sign up for free at alpaca.markets and open the Paper Trading dashboard.",
      "On the right, under “API Keys”, click Generate New Keys.",
      "Copy the Key and Secret."] },
    live: { url: "https://app.alpaca.markets/", steps: [
      "In your funded Alpaca live account open the dashboard (switch from Paper to Live).",
      "Under “API Keys”, click Generate New Keys.",
      "Copy the Key and Secret."] },
  },
};

function accountName(ex, mode) {
  const label = app.meta.exchanges[ex].label.replace(" (US stocks)", "");
  if (ex === "alpaca") return `Alpaca ${mode === "live" ? "live account" : "paper account"}`;
  return `${label} ${mode === "live" ? "account (real money)" : "testnet (practice)"}`;
}

function balancesHtml(res) {
  const rows = (res.balances || []).map(b => `<b>${esc(fmtQty(b.total))}</b> ${esc(b.asset)}`);
  const bal = rows.length ? `Balance: ${rows.join(" · ")}` : "Connected. The account is empty for now.";
  return `<div class="small">${bal}</div>` +
    (res.warnings || []).map(w => `<div class="alert warn" style="margin:8px 0 0">${esc(w)}</div>`).join("");
}

function openConnectDialog(ex, mode, onDone) {
  const g = KEY_GUIDES[ex][mode];
  const isLocal = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname) || location.protocol === "https:";
  const dlg = openDialog(`
    <form id="acct-form" novalidate>
      <div class="dlg-head"><h2>Connect ${esc(accountName(ex, mode))}</h2></div>
      <div class="dlg-body">
        ${mode === "live" ? `<div class="alert error"><b>Real money.</b> Bots on this account trade with your real balance. Try testnet or paper first.</div>` : ""}
        <p class="small">Binance, Bybit and Alpaca don't let personal apps log in with your password. You create a
          <b>trading key</b> for TrendBot instead: it can place trades but can't withdraw your money, and you can delete it any time.</p>
        <ol class="small" style="padding-left:18px">${g.steps.map(t => `<li>${esc(t)}</li>`).join("")}</ol>
        <p class="small"><a href="${esc(g.url)}" target="_blank" rel="noopener">Open ${esc(app.meta.exchanges[ex].label.replace(" (US stocks)", ""))} ↗</a></p>
        ${isLocal ? "" : `<div class="alert warn">Connect accounts on the computer running TrendBot. Keys shouldn't be sent across your Wi-Fi.</div>`}
        <div class="form-grid" style="grid-template-columns:1fr">
          <label class="field">API key <input name="api_key" autocomplete="off" spellcheck="false" required></label>
          <label class="field">Secret <input name="api_secret" type="password" autocomplete="off" spellcheck="false" required></label>
        </div>
        <p class="muted small" style="margin-top:10px">TrendBot checks the key with the exchange, then saves it only on this
          computer (<code>data/accounts.json</code>). It's never shown again and never uploaded anywhere.</p>
      </div>
      <div class="alert error dlg-error hidden" id="acct-err"></div>
      <div class="dlg-foot">
        <button class="btn" type="button" id="cancel">Cancel</button>
        <button class="btn primary" type="submit">Check &amp; connect</button>
      </div>
    </form>`);
  $("#cancel", dlg).addEventListener("click", () => dlg.close());
  const form = $("#acct-form", dlg);
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("#acct-err", dlg), btn = $("button[type=submit]", form);
    err.classList.add("hidden");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Checking…`;
    try {
      const res = await api(`/accounts/${ex}/${mode}`, { method: "POST",
        body: { api_key: form.api_key.value.trim(), api_secret: form.api_secret.value.trim() } });
      dlg.close();
      toast(`${accountName(ex, mode)} connected.`);
      onDone(res);
    } catch (ex2) {
      err.textContent = ex2.message;
      err.classList.remove("hidden");
      btn.disabled = false;
      btn.textContent = "Check & connect";
    }
  });
}

function renderSetup(view) {
  const m = app.meta;
  const host = location.host;
  const accountRow = (ex, mode) => {
    const src = m.keys[ex][mode];
    const status = src ? `<span class="key-ok">✓ Connected</span>${src === "env" ? ` <span class="muted small">(from .env)</span>` : ""}`
      : `<span class="key-no">Not connected</span>`;
    const btns = src
      ? `<button class="btn sm" data-check="${ex}/${mode}">Show balance</button>${src === "app" ? ` <button class="btn sm danger" data-disconnect="${ex}/${mode}">Disconnect</button>` : ""}`
      : `<button class="btn sm primary" data-connect="${ex}/${mode}">Connect</button>`;
    return `<div class="acct-row" id="acct-${ex}-${mode}">
        <div class="acct-main"><div><b>${esc(mode === "live" ? (ex === "alpaca" ? "Live account" : "Real account") : (ex === "alpaca" ? "Paper account" : "Testnet (practice)"))}</b>
          ${mode === "live" ? `<span class="badge live">real money</span>` : ""}</div><div>${status}</div></div>
        <div class="btn-row">${btns}</div>
        <div class="acct-bal"></div>
      </div>`;
  };
  view.innerHTML = `
    <div class="page-head"><div class="grow"><h1>Setup &amp; safety</h1>
      <div class="muted small">Everything runs on your own computer. Connected keys are stored only there and are never shown in this app.</div></div></div>

    <div class="card">
      <h2>Your exchange accounts</h2>
      <p class="muted small">Paper trading on Binance or Bybit needs no account at all. Connect an account to trade on the exchange's practice site
        (testnet) or with real money. Stocks need a free Alpaca paper account even for paper trading, because prices come from Alpaca.</p>
      <div class="acct-grid">
        ${Object.entries(m.exchanges).map(([ex, e]) => `
          <div class="acct-card"><h3>${esc(e.label)}</h3>${accountRow(ex, "testnet")}${accountRow(ex, "live")}</div>`).join("")}
      </div>
    </div>

    <div class="card prose">
      <h2>Recommended path</h2>
      <ol class="steps">
        <li><b>Backtest.</b> Open the Backtest tab and run BTC/USDT, 4h, 2 years. Look at the worst drop and the number of losing trades, not just the return.</li>
        <li><b>Paper trade for a few weeks.</b> Create a bot (it starts in paper mode) and press Start. Leave the app running. It uses real live prices but pretend money.</li>
        <li><b>Optional: testnet.</b> Connect a testnet account above and switch the bot to Testnet to check that real orders work.</li>
        <li><b>Live, small.</b> Only if you're comfortable: connect your real account and switch the bot to Live (you'll type LIVE to confirm). Keep the trade size small - the default is 20 USDT per trade with a 10 USDT daily loss cap.</li>
      </ol>
      <div class="alert warn"><b>Key safety:</b> TrendBot refuses Binance and Bybit keys that allow withdrawals. A trading-only key can't move money out of
        your account. You can delete it on the exchange at any time to cut TrendBot off instantly.</div>
      <p class="muted small">Advanced: keys can also go in the <code>.env</code> file (see <code>.env.example</code>). Those take priority over connected accounts.</p>
    </div>

    <div class="card prose">
      <h2>Keeping it running &amp; using it on your phone</h2>
      <ul>
        <li>The bot only trades while TrendBot is running and the computer is awake. Set Windows to never sleep while plugged in
          (Settings → System → Power) if you want it to run 24/7. If the PC restarts, double-click <code>start.bat</code> - running bots pick up where they left off.</li>
        <li><b>Desktop app:</b> in Chrome or Edge, open the ⋮ menu → "Install TrendBot" (or "Apps → Install this site as an app").</li>
        <li><b>Phone on the same Wi-Fi:</b> add <code>BOT_UI_PASSWORD=choose-a-long-password</code> to <code>.env</code>, then start with
          <code>start.bat --host 0.0.0.0</code>. The window prints an address like <code>http://192.168.1.20:8765</code>; open that on your phone,
          log in, and use "Add to Home Screen". Windows may ask to allow Python through the firewall - allow it on private networks only.</li>
        <li>Never expose TrendBot to the open internet (no port forwarding). It's meant for your home network.</li>
      </ul>
      <p class="muted small">You're currently connected to <code>${esc(host)}</code>.</p>
    </div>

    <div class="card prose">
      <h2>Important: what this bot can and can't do</h2>
      <ul>
        <li><b>No guaranteed profit.</b> Trend following makes money only when prices trend strongly. In sideways markets it takes many small losses.
          Backtests show the past, not the future.</li>
        <li>It trades spot only (buys and sells the coin itself) - no leverage, no shorting. Your maximum loss per trade is the trade size.</li>
        <li>The trailing stop is checked about once a minute while the app runs. Fast crashes can fill below the stop price.</li>
        <li>Paper results ignore slippage on big moves, so live results are usually a bit worse.</li>
        <li>Don't trade the same coin by hand on the same account while a live bot holds it.</li>
        <li>Crypto trading may be taxable where you live. Keep records - the trades table lists every fill.</li>
      </ul>
    </div>`;

  $(".acct-grid", view).addEventListener("click", async e => {
    const b = e.target.closest("button[data-connect], button[data-disconnect], button[data-check]");
    if (!b) return;
    const [ex, mode] = (b.dataset.connect || b.dataset.disconnect || b.dataset.check).split("/");
    const bal = $(`#acct-${ex}-${mode} .acct-bal`);
    if (b.dataset.connect) {
      return openConnectDialog(ex, mode, async res => {
        app.meta = await api("/meta");
        renderSetup(view);
        $(`#acct-${ex}-${mode} .acct-bal`).innerHTML = balancesHtml(res);
      });
    }
    if (b.dataset.disconnect) {
      if (!(await confirmDialog("Disconnect account?", `TrendBot will forget the ${accountName(ex, mode)} key. To fully revoke it, also delete the key on the exchange.`, "Disconnect", true))) return;
      try {
        await api(`/accounts/${ex}/${mode}`, { method: "DELETE" });
        app.meta = await api("/meta");
        toast("Disconnected.");
        renderSetup(view);
      } catch (err) { toast(err.message, true); }
      return;
    }
    b.disabled = true;
    bal.innerHTML = `<span class="small muted"><span class="spinner"></span> Checking…</span>`;
    try { bal.innerHTML = balancesHtml(await api(`/accounts/${ex}/${mode}`)); }
    catch (err) { bal.innerHTML = `<div class="alert error" style="margin:8px 0 0">${esc(err.message)}</div>`; }
    b.disabled = false;
  });
}

// ============================================================================ theme / boot

function currentTheme() {
  return document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function redrawCharts() { app.charts.forEach(c => c.draw()); }

$("#theme-btn").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("tb-theme", next); } catch (e) { /* private mode */ }
  redrawCharts();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawCharts);

if ("serviceWorker" in navigator && (location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname))) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* optional */ });
}

route();
