/* Strategy fields shared by the bot form and the backtest form, plus the bot and account dialogs. */
import { useState } from "react";
import { api } from "./api.js";
import { accountName, exchangeLabel, quoteOf } from "./format.js";
import { LiveConfirm, Modal, useApp } from "./ui.jsx";

/** Turns form state into the numbers and upper-case symbol the API expects. */
export function toBody(v) {
  const out = { ...v };
  for (const k of ["fast", "slow", "atr_period", "days"]) if (k in out) out[k] = parseInt(out[k], 10);
  for (const k of ["atr_mult", "trade_size", "daily_loss_cap", "adx_min"]) if (k in out) out[k] = parseFloat(out[k]) || 0;
  for (const k of ["trend_filter", "reentry"]) if (k in out) out[k] = out[k] === true || out[k] === "true";
  if (out.symbol) out.symbol = out.symbol.trim().toUpperCase();
  if (typeof out.watchlist === "string")
    out.watchlist = out.watchlist.split(/[\s,;]+/).map(x => x.trim().toUpperCase()).filter(Boolean);
  return out;
}

/** Form state with a setter per field; switching between crypto and stocks swaps the example symbol. */
export function useFields(initial) {
  const { meta } = useApp();
  const [v, setV] = useState(initial);
  const set = key => e => {
    const value = e.target.value;
    setV(prev => {
      const next = { ...prev, [key]: value };
      // Filter defaults that tested best for each candle size (5 years of Binance data):
      // daily candles -> re-entry on, no ADX filter; 4h/1h -> ADX filter 20, no re-entry.
      if (key === "timeframe") {
        next.reentry = value === "1d";
        next.adx_min = value === "1d" ? 0 : 20;
      }
      if (key === "exchange") {
        const ex = meta.exchanges[value];
        if ((ex.kind === "stocks") === String(prev.symbol).includes("/")) next.symbol = ex.example;
        if (typeof prev.watchlist === "string" && (ex.kind === "stocks") === prev.watchlist.includes("/"))
          next.watchlist = (meta.scan_symbols?.[ex.kind] || [ex.example]).slice(0, 6).join(", ");
      }
      return next;
    });
  };
  return [v, set];
}

// Rough Claude cost per AI Autopilot decision (US$), for the hint in the form.
const AI_COST_PER_DECISION = 0.12;
const DECISIONS_PER_DAY = { "1h": 24, "4h": 6, "1d": 1 };

export function StrategyFields({ v, set, ai = false }) {
  const { meta } = useApp();
  return (
    <>
      <label className="field">Exchange
        <select name="exchange" value={v.exchange} onChange={set("exchange")}>
          {Object.entries(meta.exchanges).map(([k, e]) => <option key={k} value={k}>{e.label}</option>)}
        </select></label>
      {ai ? <label className="field wide">Watchlist <span className="hint">up to 8 markets Claude may trade, separated by commas</span>
        <input name="watchlist" value={v.watchlist} onChange={set("watchlist")} required autoCapitalize="characters" spellCheck="false"
          placeholder={meta.exchanges[v.exchange]?.kind === "stocks" ? "SPY, QQQ, AAPL" : "BTC/USDT, ETH/USDT, SOL/USDT"} /></label>
        : <label className="field">Symbol
          <input name="symbol" value={v.symbol} onChange={set("symbol")} required maxLength={24} autoCapitalize="characters"
            spellCheck="false" placeholder={meta.exchanges[v.exchange]?.example || "BTC/USDT"} /></label>}
      {ai && <label className="field">Trading style <span className="hint">how bold Claude is; limits stay the same</span>
        <select name="style" value={v.style || "balanced"} onChange={set("style")}>
          <option value="careful">Careful - only the clearest setups</option>
          <option value="balanced">Balanced - good setups, no chasing</option>
          <option value="aggressive">Aggressive - trades more, more risk</option>
        </select></label>}
      <label className="field">{ai ? "Decide every" : "Candle size"}
        {ai && <span className="hint">≈ ${(AI_COST_PER_DECISION * (DECISIONS_PER_DAY[v.timeframe] || 1)).toFixed(2)}/day of Claude usage</span>}
        <select name="timeframe" value={v.timeframe} onChange={set("timeframe")}>
          {meta.timeframes.map(t => <option key={t}>{t}</option>)}
        </select></label>
      <label className="field">{ai ? "Max trade size" : "Trade size"} <span className="hint">{ai ? "the most Claude may spend on one buy" : "spent on each buy"} ({quoteOf(meta, v) || "quote"})</span>
        <input name="trade_size" type="number" step="any" min="0" value={v.trade_size} onChange={set("trade_size")} required /></label>
      <label className="field">Daily loss cap <span className="hint">no new buys after this loss in a day; 0 = off</span>
        <input name="daily_loss_cap" type="number" step="any" min="0" value={v.daily_loss_cap} onChange={set("daily_loss_cap")} required /></label>
    </>
  );
}

export function AdvancedFields({ v, set }) {
  return (
    <details className="advanced"><summary>Strategy settings (advanced)</summary>
      <div className="form-grid">
        <label className="field">Fast EMA <input name="fast" type="number" min="2" max="200" value={v.fast} onChange={set("fast")} required /></label>
        <label className="field">Slow EMA <input name="slow" type="number" min="3" max="400" value={v.slow} onChange={set("slow")} required /></label>
        <label className="field">ATR period <input name="atr_period" type="number" min="2" max="100" value={v.atr_period} onChange={set("atr_period")} required /></label>
        <label className="field">Stop distance <span className="hint">× ATR below the high</span>
          <input name="atr_mult" type="number" step="0.1" min="0.5" max="10" value={v.atr_mult} onChange={set("atr_mult")} required /></label>
      </div>
      <div className="form-grid" style={{ marginTop: 10 }}>
        <label className="field">Trend strength (ADX) min <span className="hint">only buy in strong trends; 0 = off</span>
          <input name="adx_min" type="number" min="0" max="60" step="1" value={v.adx_min ?? 0} onChange={set("adx_min")} /></label>
      </div>
      <label className="check"><input type="checkbox" name="trend_filter" checked={v.trend_filter !== false}
        onChange={e => set("trend_filter")({ target: { value: e.target.checked } })} />
        <span><b>Trend filter</b> - only buy while the price is above its 200-candle average (skips downtrends). Recommended.</span></label>
      <label className="check"><input type="checkbox" name="reentry" checked={!!v.reentry}
        onChange={e => set("reentry")({ target: { value: e.target.checked } })} />
        <span><b>Re-entry on breakouts</b> - during an uptrend, buy again on a new 20-candle high (tested best on 1d candles).</span></label>
      <p className="muted small" style={{ marginTop: 10 }}>Buys when the fast EMA crosses above the slow EMA (plus the filters above). Sells when it
        crosses back below, or when price drops to the trailing stop (highest price since buying minus the ATR multiple).
        Defaults are the combinations that did best in tests on 5 years of data for 8 major coins.</p>
    </details>
  );
}

function ModeNote({ v }) {
  const { meta } = useApp();
  const { exchange: ex, mode } = v;
  if (mode === "paper" && ex === "alpaca" && !meta.keys.alpaca.testnet && !meta.keys.alpaca.live)
    return <div className="alert warn">Stock prices come from Alpaca, so even paper mode needs a free Alpaca paper account. Connect it on the <a href="#/setup">Setup</a> page.</div>;
  if (mode === "paper") return <div className="alert info">Paper mode: trades are simulated at real live prices with normal fees. Nothing is bought.</div>;
  if (!meta.keys[ex]?.[mode]) return <div className="alert warn">Your {accountName(meta, ex, mode)} isn't connected yet. Connect it on the <a href="#/setup">Setup</a> page first.</div>;
  if (mode === "live") return <div className="alert error">Live mode trades real money. You'll be asked to type LIVE to confirm.</div>;
  return <div className="alert info">Testnet: real orders on the exchange's practice account with fake balances.</div>;
}

/** Create or edit a bot. close(result) receives the saved bot summary, or nothing when cancelled. */
export function BotForm({ bot, preset, close }) {
  const { meta, modal } = useApp();
  const editing = !!bot;
  const [v, set] = useFields(() => {
    const init = { ...meta.defaults, ...(preset || {}), ...(bot ? bot.config : {}), ...(!bot && !preset ? { name: "" } : {}) };
    init.brain = bot ? (bot.config.brain || "rules") : preset ? "rules" : "ai";
    const kind = meta.exchanges[init.exchange]?.kind || "crypto";
    const list = bot?.config.watchlist?.length ? bot.config.watchlist : (meta.scan_symbols?.[kind] || [init.symbol]).slice(0, 6);
    init.watchlist = list.join(", ");
    return init;
  });
  const isAI = v.brain === "ai";
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async e => {
    e.preventDefault();
    setError("");
    const body = { ...toBody(v), confirm_live: false };
    if (!isAI) delete body.watchlist;
    if (body.mode === "live" && (bot ? bot.config.mode : null) !== "live") {
      if (!(await modal.open(c => <LiveConfirm cfg={body} close={c} />))) return;
      body.confirm_live = true;
    }
    setBusy(true);
    try {
      const res = editing
        ? await api(`/bots/${encodeURIComponent(bot.id)}`, { method: "PUT", body })
        : await api("/bots", { method: "POST", body });
      close(res);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <Modal onClose={() => close()}>
      <form id="bot-form" noValidate onSubmit={submit}>
        <div className="dlg-head"><h2>{editing ? "Edit bot" : "New bot"}</h2></div>
        <div className="dlg-body">
          <div className="brain-pick" role="radiogroup" aria-label="Who makes the trading decisions">
            {[["ai", "AI Autopilot", "Claude studies every market on your watchlist at each new candle and decides what to buy, when to sell and how much to use."],
              ["rules", "Fixed rules", "Classic trend following on one market: buy when the 20 EMA crosses above the 50 EMA, sell on the cross back or the trailing stop."]]
              .map(([k, title, text]) => (
                <label key={k} className={"brain-opt" + (v.brain === k ? " on" : "")}>
                  <input type="radio" name="brain" value={k} checked={v.brain === k} onChange={set("brain")} />
                  <b>{k === "ai" && <span className="ai-mark">AI</span>} {title}</b>
                  <span className="small muted">{text}</span>
                </label>))}
          </div>
          {isAI && !meta.ai?.source && <div className="alert warn" style={{ marginTop: 10 }}>AI Autopilot needs Claude connected: add your Anthropic API key in <a href="#/setup">Setup</a> before starting this bot.</div>}
          <div className="form-grid" style={{ marginTop: 12 }}>
            <label className="field wide">Name <input name="name" value={v.name} onChange={set("name")} maxLength={40} placeholder={isAI ? "AI Autopilot" : "e.g. BTC trend"} /></label>
            <StrategyFields v={v} set={set} ai={isAI} />
            <label className="field wide">Mode
              <select name="mode" value={v.mode} onChange={set("mode")}>
                <option value="paper">Paper - pretend money, real live prices (no keys needed)</option>
                <option value="testnet">Testnet - the exchange's practice account (test keys)</option>
                <option value="live">Live - REAL money (live keys)</option>
              </select></label>
          </div>
          <div id="mode-note" style={{ marginTop: 12 }}><ModeNote v={v} /></div>
          {isAI ? <p className="muted small" style={{ marginTop: 10 }}>Safety limits Claude can't change: spot only (no leverage or
            shorting), never more than the max trade size per buy, a trailing stop on every position checked every minute, and the daily
            loss cap. One position at a time. Profit is never guaranteed; start in paper mode and watch its decisions first.</p> : <>
          <label className="check">
            <input type="checkbox" name="ai_filter" checked={!!v.ai_filter} disabled={!meta.ai?.source && !v.ai_filter}
              onChange={e => set("ai_filter")({ target: { value: e.target.checked } })} />
            <span><b>AI check before each buy</b> - Claude looks at the market when a buy signal appears and can skip trades that look like
              false starts. It can't make trades bigger or remove the stop.{" "}
              {meta.ai?.source ? "About 5-15 US cents per signal on your Anthropic account." : <>Needs AI connected in <a href="#/setup">Setup</a>.</>}</span>
          </label>
          <AdvancedFields v={v} set={set} /></>}
        </div>
        {error && <div className="alert error dlg-error" id="form-err">{error}</div>}
        <div className="dlg-foot">
          <button className="btn" type="button" onClick={() => close()}>Cancel</button>
          <button className="btn primary" type="submit" disabled={busy}>{editing ? "Save" : "Create bot"}</button>
        </div>
      </form>
    </Modal>
  );
}

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

/** Paste a trading key; the server checks it with the exchange before saving. close(result) gets balances. */
export function ConnectDialog({ ex, mode, close }) {
  const { meta } = useApp();
  const g = KEY_GUIDES[ex][mode];
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const isLocal = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname) || location.protocol === "https:";
  const name = exchangeLabel(meta, ex).replace(" (US stocks)", "");

  const submit = async e => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      close(await api(`/accounts/${ex}/${mode}`, { method: "POST", body: { api_key: key.trim(), api_secret: secret.trim() } }));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <Modal onClose={() => close()}>
      <form id="acct-form" noValidate onSubmit={submit}>
        <div className="dlg-head"><h2>Connect {accountName(meta, ex, mode)}</h2></div>
        <div className="dlg-body">
          {mode === "live" && <div className="alert error"><b>Real money.</b> Bots on this account trade with your real balance. Try testnet or paper first.</div>}
          <p className="small">Binance, Bybit and Alpaca don't let personal apps log in with your password. You create a{" "}
            <b>trading key</b> for TrendBot instead: it can place trades but can't withdraw your money, and you can delete it any time.</p>
          <ol className="small" style={{ paddingLeft: 18 }}>{g.steps.map(t => <li key={t}>{t}</li>)}</ol>
          <p className="small"><a href={g.url} target="_blank" rel="noopener">Open {name} ↗</a></p>
          {!isLocal && <div className="alert warn">Connect accounts on the computer running TrendBot. Keys shouldn't be sent across your Wi-Fi.</div>}
          <div className="form-grid" style={{ gridTemplateColumns: "1fr" }}>
            <label className="field">API key <input name="api_key" value={key} onChange={e => setKey(e.target.value)} autoComplete="off" spellCheck="false" required /></label>
            <label className="field">Secret <input name="api_secret" type="password" value={secret} onChange={e => setSecret(e.target.value)} autoComplete="off" spellCheck="false" required /></label>
          </div>
          <p className="muted small" style={{ marginTop: 10 }}>{meta.cloud
            ? "TrendBot checks the key with the exchange, then saves it encrypted in your private TrendBot database. It's never shown again."
            : <>TrendBot checks the key with the exchange, then saves it only on this computer (<code>data/accounts.json</code>). It's never shown again and never uploaded anywhere.</>}</p>
        </div>
        {error && <div className="alert error dlg-error" id="acct-err">{error}</div>}
        <div className="dlg-foot">
          <button className="btn" type="button" onClick={() => close()}>Cancel</button>
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? <><span className="spinner" /> Checking…</> : "Check & connect"}</button>
        </div>
      </form>
    </Modal>
  );
}
