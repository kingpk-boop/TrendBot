/* Number, time and label formatting shared by every page. */

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const ok = x => x !== null && x !== undefined && isFinite(x);

export function fmtNum(x, digits = 2) {
  if (!ok(x)) return "—";
  return Number(x).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPrice(x) {
  if (!ok(x)) return "—";
  const a = Math.abs(x);
  if (a >= 1000) return fmtNum(x, 2);
  if (a >= 1) return fmtNum(x, a >= 100 ? 2 : 4);
  if (a === 0) return "0";
  return Number(x).toPrecision(5);
}

export function fmtQty(x) {
  if (!ok(x)) return "—";
  return Number(x) >= 1 ? fmtNum(x, 4) : String(Number(Number(x).toPrecision(5)));
}

export function signed(x, digits = 2) {
  if (!ok(x)) return "—";
  return (x > 0 ? "+" : x < 0 ? "−" : "") + fmtNum(Math.abs(x), digits);
}

export function pct(x, digits = 1) {
  return ok(x) ? signed(x, digits) + "%" : "—";
}

export const cls = x => (x > 0 ? "pos" : x < 0 ? "neg" : "");

export const toMs = t => (typeof t === "number" ? t : Date.parse(t));

export function fmtTime(t) {
  const d = new Date(toMs(t));
  if (isNaN(d)) return "—";
  const year = d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {};
  return d.toLocaleString(undefined, { ...year, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtDate(t) {
  const d = new Date(toMs(t));
  return isNaN(d) ? "—" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function ago(t) {
  const s = Math.round((Date.now() - toMs(t)) / 1000);
  if (!isFinite(s)) return "";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function quoteOf(meta, cfg) {
  if (meta?.exchanges?.[cfg.exchange]?.kind === "stocks") return "USD";
  return (cfg.symbol || "").split("/")[1] || "";
}

export function modeLabel(mode, exchange) {
  if (mode === "testnet") return exchange === "alpaca" ? "Alpaca paper" : "Testnet";
  return { paper: "Paper", live: "Live" }[mode] || mode;
}

export function exchangeLabel(meta, ex) {
  return meta.exchanges[ex]?.label || ex;
}

export function accountName(meta, ex, mode) {
  if (ex === "alpaca") return `Alpaca ${mode === "live" ? "live account" : "paper account"}`;
  return `${exchangeLabel(meta, ex)} ${mode === "live" ? "account (real money)" : "testnet (practice)"}`;
}
