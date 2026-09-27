/* Canvas line chart with a crosshair tooltip (framework-free; wrapped by <ChartView>). */
import { cssVar, esc, fmtNum, fmtPrice, fmtTime } from "./format.js";


/**
 * Small canvas line chart with a crosshair tooltip.
 * data = { times: [ms], series: [{name, values, color (css var), width, dash, fmt}],
 *          markers: [{i, price, side}], hlines: [{value, color, label}], fmtY }
 */
export class Chart {
  constructor(el) {
    this.el = el;
    this.el.innerHTML = `<canvas></canvas><div class="tip hidden"></div>`;
    this.canvas = el.querySelector("canvas");
    this.tip = el.querySelector(".tip");
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

/** Index of the last candle that opened at or before t. */
export function candleIndex(times, t) {
  let lo = 0, hi = times.length - 1, ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

