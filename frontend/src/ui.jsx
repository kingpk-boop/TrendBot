/* Shared React building blocks: app context, dialogs, toasts, tiles, charts and hooks. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { Chart } from "./chart.js";
import { exchangeLabel, fmtNum, modeLabel, quoteOf } from "./format.js";

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

// ---------------------------------------------------------------------------- hooks

export function useHash() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

/** Runs fn now and every `ms` while the component is mounted. */
export function usePolling(fn, ms, deps = []) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    ref.current();
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
}

// ---------------------------------------------------------------------------- toasts

export function useToasts() {
  const [toasts, setToasts] = useState([]);
  const toast = useCallback((msg, isError = false) => {
    const id = Math.random();
    setToasts(t => [...t, { id, msg, isError }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), isError ? 6000 : 3000);
  }, []);
  const view = (
    <div className="toasts" aria-live="polite">
      {toasts.map(t => <div key={t.id} className={"toast" + (t.isError ? " error" : "")}>{t.msg}</div>)}
    </div>
  );
  return [toast, view];
}

// ---------------------------------------------------------------------------- dialogs

/** A native <dialog> shown as a modal; Esc or the backdrop's cancel calls onClose. */
export function Modal({ onClose, children }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const d = ref.current;
    if (!d.open) d.showModal();
    const cancel = e => { e.preventDefault(); closeRef.current(); };
    d.addEventListener("cancel", cancel);
    return () => d.removeEventListener("cancel", cancel);
  }, []);
  return <dialog ref={ref}>{children}</dialog>;
}

/**
 * Promise-based modals: `await modal.open(close => <Something close={close} />)` resolves with
 * whatever the dialog passes to close().
 */
export function useModalHost() {
  const [stack, setStack] = useState([]);
  const open = useCallback(render => new Promise(resolve => {
    const id = Math.random();
    const close = value => {
      setStack(s => s.filter(x => x.id !== id));
      resolve(value);
    };
    setStack(s => [...s, { id, render, close }]);
  }), []);
  const confirm = useCallback(({ title, text, okLabel = "OK", danger = false }) => open(close => (
    <Modal onClose={() => close(false)}>
      <div className="dlg-head"><h2>{title}</h2></div>
      <div className="dlg-body"><p>{text}</p></div>
      <div className="dlg-foot">
        <button className="btn" onClick={() => close(false)}>Cancel</button>
        <button className={"btn " + (danger ? "danger-solid" : "primary")} onClick={() => close(true)}>{okLabel}</button>
      </div>
    </Modal>
  )), [open]);
  const api = useMemo(() => ({ open, confirm }), [open, confirm]);
  const view = stack.map(m => <div key={m.id}>{m.render(m.close)}</div>);
  return [api, view];
}

/** The extra gate before a bot is switched to real money: the user must type LIVE. */
export function LiveConfirm({ cfg, close }) {
  const { meta } = useApp();
  const [text, setText] = useState("");
  const q = quoteOf(meta, cfg);
  const okay = text.trim() === "LIVE";
  return (
    <Modal onClose={() => close(false)}>
      <form onSubmit={e => { e.preventDefault(); if (okay) close(true); }}>
        <div className="dlg-head"><h2>Switch to live trading?</h2></div>
        <div className="dlg-body">
          <div className="alert error"><b>Real money.</b> In live mode the bot places real market orders on your{" "}
            {exchangeLabel(meta, cfg.exchange)} account with no one checking each trade.</div>
          <ul className="small" style={{ paddingLeft: 18 }}>
            <li>Each buy spends up to <b>{fmtNum(cfg.trade_size)} {q}</b>.</li>
            <li>The daily loss cap is <b>{cfg.daily_loss_cap > 0 ? `${fmtNum(cfg.daily_loss_cap)} ${q}` : "OFF"}</b> - it stops new buys after that much loss in a day, but a single trade can still lose more.</li>
            <li>Profit is not guaranteed. Trend strategies often have many small losing trades.</li>
            <li>Your API key should allow <b>trading only - never withdrawals</b>.</li>
          </ul>
          <label className="field"><span>Type <code>LIVE</code> to confirm</span>
            <input autoComplete="off" autoCapitalize="characters" spellCheck="false" value={text}
              onChange={e => setText(e.target.value)} autoFocus /></label>
        </div>
        <div className="dlg-foot">
          <button type="button" className="btn" onClick={() => close(false)}>Cancel</button>
          <button type="submit" className="btn danger-solid" id="live-ok" disabled={!okay}>Use real money</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------- small pieces

export function Tile({ label, value, sub }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="sub">{sub}</div>
    </div>
  );
}

export function ModeBadge({ mode, exchange }) {
  return <span className={"badge " + mode}>{modeLabel(mode, exchange)}</span>;
}

export function Spinner({ children }) {
  return <div className="loading"><span className="spinner" /> {children}</div>;
}

/** items: [label, cssVar, kind?] where kind is "buy" | "sell" | "dash". */
export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map(([label, color, kind]) => (
        <span key={label}>
          {kind === "buy" ? <i className="tri-up" /> : kind === "sell" ? <i className="tri-down" />
            : kind === "dash" ? <i className="dash" style={{ borderColor: `var(${color})` }} />
              : <i style={{ background: `var(${color})` }} />}
          {label}
        </span>
      ))}
    </div>
  );
}

/** Wraps the canvas Chart. Pass `data` to draw, or `message` to show text instead. */
export function ChartView({ data, message, short = false, id }) {
  const el = useRef(null);
  const chart = useRef(null);
  useEffect(() => {
    const redraw = () => chart.current?.draw();
    window.addEventListener("themechange", redraw);
    const mq = matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", redraw);
    return () => {
      window.removeEventListener("themechange", redraw);
      mq.removeEventListener("change", redraw);
      chart.current?.destroy();
      chart.current = null;
    };
  }, []);
  useEffect(() => {
    if (!data) return;
    if (!chart.current) chart.current = new Chart(el.current);
    chart.current.set(data);
  }, [data]);
  return (
    <div className={"chart" + (short ? " short" : "")} id={id}>
      <div ref={el} style={{ position: "absolute", inset: 0 }} />
      {!data && <div className="msg">{message || "Loading chart…"}</div>}
    </div>
  );
}

/**
 * "Ask the AI" button + answer. kind: backtest | scan | bot. Pass `data` (backtest/scan) or `botId`.
 * Hidden on the public website; points to Setup when no AI key is connected.
 */
export function AIReview({ kind, data, botId, label = "Ask AI to review this" }) {
  const { meta } = useApp();
  const [state, setState] = useState({ busy: false, text: "", error: "" });
  if (meta.backtest_only) return null;
  if (!meta.ai?.source) return (
    <p className="muted small ai-hint">Want a plain-language review from Claude AI? <a href="#/setup">Connect AI in Setup</a>.</p>
  );
  const ask = async () => {
    setState({ busy: true, text: "", error: "" });
    try {
      const res = await api("/ai/analyze", { method: "POST", body: { kind, data, bot_id: botId } });
      setState({ busy: false, text: res.text, error: "" });
    } catch (e) {
      setState({ busy: false, text: "", error: e.message });
    }
  };
  return (
    <div className="card ai-card">
      <div className="card-head">
        <h2><span className="ai-mark">AI</span> Claude's take</h2>
        <button className="btn sm" onClick={ask} disabled={state.busy}>
          {state.busy ? <><span className="spinner" /> Thinking…</> : state.text ? "Ask again" : label}</button>
      </div>
      {state.error && <div className="alert error" style={{ margin: 0 }}>{state.error}</div>}
      {state.text ? <>
        <div className="ai-text">{state.text}</div>
        <p className="muted small" style={{ marginBottom: 0 }}>AI can be wrong and can't predict prices. Use this as a second opinion, not advice.</p>
      </> : !state.error && <p className="muted small" style={{ margin: 0 }}>Claude reads these numbers and explains what they mean and what to try next (costs a few cents on your Anthropic account).</p>}
    </div>
  );
}
