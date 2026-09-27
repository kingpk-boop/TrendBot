/* Markets: backtest the strategy across many coins/stocks at once and rank them. */
import { useMemo, useState } from "react";
import { api } from "../api.js";
import { cls, exchangeLabel, fmtNum, fmtPrice, pct } from "../format.js";
import { AdvancedFields, toBody, useFields } from "../forms.jsx";
import { AIReview, Spinner, useApp } from "../ui.jsx";
import { openBacktest } from "./Backtest.jsx";
import { useOpenBotForm } from "./Bots.jsx";

const memory = { form: null, result: null };
const PERIODS = [[90, "3 months"], [180, "6 months"], [365, "1 year"], [730, "2 years"], [1095, "3 years"]];
const COLUMNS = [
  ["symbol", "Market", false],
  ["score", "Score", true],
  ["strategy_return_pct", "Strategy", true],
  ["hold_return_pct", "Buy & hold", true],
  ["strategy_max_dd_pct", "Worst drop", true],
  ["trades_count", "Trades", true],
  ["win_rate_pct", "Won", true],
  ["trend", "Trend now", false],
];

function ScanTable({ result }) {
  const { meta } = useApp();
  const openForm = useOpenBotForm();
  const [sort, setSort] = useState(["score", -1]);
  const req = result.request;
  const rows = useMemo(() => {
    const ok = result.rows.filter(r => r.ok), bad = result.rows.filter(r => !r.ok);
    const [key, dir] = sort;
    ok.sort((a, b) => {
      const x = a[key], y = b[key];
      if (typeof x === "string") return dir * String(x).localeCompare(String(y));
      return dir * ((x ?? -Infinity) - (y ?? -Infinity));
    });
    return [...ok, ...bad];
  }, [result, sort]);
  const settings = sym => {
    const { days, symbols, ...rest } = req;
    return { ...rest, symbol: sym };
  };

  return (
    <div className="card">
      <div className="card-head"><h2>Results</h2>
        <span className="muted small">{exchangeLabel(meta, req.exchange)} · {req.timeframe} candles · last {req.days} days · click a column to sort</span></div>
      <div className="table-wrap"><table className="scan-table">
        <thead><tr>
          <th>#</th>
          {COLUMNS.map(([key, label, num]) => (
            <th key={key} className={(num ? "r " : "") + "sortable"} onClick={() => setSort(([k, d]) => [key, k === key ? -d : -1])}>
              {label}{sort[0] === key ? (sort[1] < 0 ? " ▼" : " ▲") : ""}</th>
          ))}
          <th></th>
        </tr></thead>
        <tbody>{rows.map((r, i) => r.ok ? (
          <tr key={r.symbol}>
            <td className="muted">{i + 1}</td>
            <td><b>{r.symbol}</b><div className="muted small">{fmtPrice(r.last_price)}</div></td>
            <td className={"r " + cls(r.score)}><b>{fmtNum(r.score, 2)}</b></td>
            <td className={"r " + cls(r.strategy_return_pct)}>{pct(r.strategy_return_pct)}</td>
            <td className={"r " + cls(r.hold_return_pct)}>{pct(r.hold_return_pct)}</td>
            <td className="r neg">{pct(r.strategy_max_dd_pct)}</td>
            <td className="r">{r.trades_count}</td>
            <td className="r">{r.win_rate_pct === null ? "—" : fmtNum(r.win_rate_pct, 0) + "%"}</td>
            <td>{r.trend === "up" ? <span className="pos">▲ Up</span> : <span className="neg">▼ Down</span>}
              {r.fresh_signal && <span className="badge signal" title="The fast EMA crossed above the slow EMA in the last 3 candles">new buy signal</span>}</td>
            <td className="r nowrap">
              <button className="btn sm" onClick={() => openBacktest({ ...settings(r.symbol), days: req.days })}>Backtest</button>
              {!meta.backtest_only && <> <button className="btn sm primary" onClick={() => openForm(null, { ...settings(r.symbol), mode: "paper", name: `${r.symbol} ${req.timeframe}` })}>Create bot</button></>}
            </td>
          </tr>
        ) : (
          <tr key={r.symbol}><td className="muted">–</td><td><b>{r.symbol}</b></td><td colSpan={COLUMNS.length} className="muted wrap">Skipped: {r.error}</td></tr>
        ))}</tbody>
      </table></div>
      <p className="muted small" style={{ marginBottom: 0 }}><b>Score</b> = strategy return ÷ worst drop, so steady gains rank above lucky, bumpy ones.
        A high score in the past doesn't mean the next trend will come. "New buy signal" means the bot would have just bought.</p>
    </div>
  );
}

export function MarketsPage() {
  const { meta } = useApp();
  const [v, set] = useFields(memory.form || { ...meta.defaults, days: 365, symbols: "" });
  const [result, setResult] = useState(memory.result);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  memory.form = v;
  const kind = meta.exchanges[v.exchange]?.kind || "crypto";
  const defaults = meta.scan_symbols?.[kind] || [];

  const run = async e => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { name, mode, symbol, ai_filter, ...rest } = toBody(v);
      const symbols = String(v.symbols || "").split(/[\s,]+/).map(x => x.trim().toUpperCase()).filter(Boolean);
      const r = await api("/scan", { method: "POST", body: { ...rest, symbols } });
      memory.result = r;
      setResult(r);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head"><div className="grow"><h1>Markets</h1>
        <div className="muted small">Backtests the strategy on many markets at once and ranks them, so you can see where trend following has worked best.</div></div></div>
      <div className="card">
        <form id="scan-form" noValidate onSubmit={run}>
          <div className="form-grid">
            <label className="field">Exchange
              <select name="exchange" value={v.exchange} onChange={set("exchange")}>
                {Object.entries(meta.exchanges).map(([k, e]) => <option key={k} value={k}>{e.label}</option>)}
              </select></label>
            <label className="field">Candle size
              <select name="timeframe" value={v.timeframe} onChange={set("timeframe")}>
                {meta.timeframes.map(t => <option key={t}>{t}</option>)}
              </select></label>
            <label className="field">Period <select name="days" value={v.days} onChange={set("days")}>
              {PERIODS.map(([d, l]) => <option key={d} value={d}>{l}</option>)}
            </select></label>
            <label className="field wide">Markets <span className="hint">leave empty for the popular list, or type your own separated by spaces (up to 20)</span>
              <input name="symbols" value={v.symbols} onChange={set("symbols")} placeholder={defaults.join(" ")} autoCapitalize="characters" spellCheck="false" /></label>
          </div>
          <AdvancedFields v={v} set={set} />
          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn primary" type="submit" disabled={busy}>Scan markets</button>
          </div>
        </form>
      </div>
      {busy ? <Spinner>Backtesting {String(v.symbols).trim() ? "your markets" : `${defaults.length} markets`}… this can take up to a minute.</Spinner>
        : error ? <div className="alert error">{error}</div>
          : result && <>
            <ScanTable result={result} />
            <AIReview kind="scan" data={result} label="Ask AI which look best" />
          </>}
    </>
  );
}

