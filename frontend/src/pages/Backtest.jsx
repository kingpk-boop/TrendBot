/* Backtest form and results: stats, equity vs buy & hold, price with trades, trade list. */
import { useMemo, useState } from "react";
import { api } from "../api.js";
import { candleIndex } from "../chart.js";
import { cls, exchangeLabel, fmtDate, fmtNum, fmtPrice, fmtTime, pct, quoteOf, signed } from "../format.js";
import { AdvancedFields, StrategyFields, toBody, useFields } from "../forms.jsx";
import { AIReview, ChartView, Legend, Spinner, Tile, useApp } from "../ui.jsx";
import { useOpenBotForm } from "./Bots.jsx";

// Kept outside the component so the form and last result survive switching tabs.
const memory = { form: null, result: null };
/** Open the Backtest tab with these settings filled in (used by the Markets page). */
export function openBacktest(settings) {
  memory.form = { ...memory.form, ...settings };
  memory.result = null;
  location.hash = "#/backtest";
}

const PERIODS = [[90, "3 months"], [180, "6 months"], [365, "1 year"], [730, "2 years"], [1095, "3 years"], [1825, "5 years"]];

function Results({ r }) {
  const { meta } = useApp();
  const openForm = useOpenBotForm();
  const req = r.request, q = quoteOf(meta, req);
  const beat = r.strategy_return_pct - r.hold_return_pct;
  const verdict = r.trades_count === 0 ? "The strategy made no trades in this period."
    : beat >= 0 ? `The strategy beat buy & hold by ${fmtNum(beat, 1)} percentage points over this period.`
      : `Buy & hold did ${fmtNum(-beat, 1)} percentage points better than the strategy over this period.`;

  const charts = useMemo(() => {
    const times = r.curve.map(p => p.t);
    const markers = r.trades.flatMap(t => [
      { i: candleIndex(times, t.entry_time), price: t.entry_price, side: "buy" },
      { i: candleIndex(times, t.exit_time), price: t.exit_price, side: "sell" },
    ]);
    return {
      equity: {
        times, fmtY: y => fmtNum(y, Math.abs(y) >= 100 ? 0 : 1),
        series: [
          { name: "Strategy", values: r.curve.map(p => p.strategy), color: "--c-fast", width: 2 },
          { name: "Buy & hold", values: r.curve.map(p => p.hold), color: "--c-hold", width: 1.75 },
        ],
      },
      price: {
        times, markers,
        series: [
          { name: "Close", values: r.curve.map(p => p.price), color: "--c-price", width: 1.25 },
          { name: `EMA ${req.fast}`, values: r.curve.map(p => p.fast), color: "--c-fast", width: 1.75 },
          { name: `EMA ${req.slow}`, values: r.curve.map(p => p.slow), color: "--c-slow", width: 1.75 },
        ],
      },
    };
  }, [r, req.fast, req.slow]);

  const createBot = () => {
    const { days, ...settings } = req;
    openForm(null, { ...settings, mode: "paper", name: `${req.symbol} ${req.timeframe}` });
  };

  return (
    <div id="bt-result">
      <div className="page-head" style={{ marginTop: 4 }}>
        <div className="grow"><h2>{req.symbol} · {exchangeLabel(meta, req.exchange)} · {req.timeframe} · {fmtDate(r.start)} – {fmtDate(r.end)}</h2>
          <div className="muted small">{verdict} Fees of {fmtNum(r.fee_rate * 100, 2)}% per side included.</div></div>
        {!meta.backtest_only && <div className="btn-row"><button className="btn primary" id="bt-create" onClick={createBot}>Create bot from these settings</button></div>}
      </div>
      <div className="tiles">
        <Tile label="Strategy return" value={<span className={cls(r.strategy_return_pct)}>{pct(r.strategy_return_pct)}</span>} sub="reinvesting each trade" />
        <Tile label="Buy & hold" value={<span className={cls(r.hold_return_pct)}>{pct(r.hold_return_pct)}</span>} sub="bought once at the start" />
        <Tile label="Worst drop" value={<span className="neg">{pct(r.strategy_max_dd_pct)}</span>} sub={`hold: ${pct(r.hold_max_dd_pct)}`} />
        <Tile label="Trades" value={String(r.trades_count)} sub={r.win_rate_pct === null ? "" : `${fmtNum(r.win_rate_pct, 0)}% won`} />
        <Tile label="Avg win / loss" value={<><span className="pos">{pct(r.avg_win_pct)}</span> / <span className="neg">{pct(r.avg_loss_pct)}</span></>} sub="per trade" />
        <Tile label={`P&L at ${fmtNum(req.trade_size)} ${q}/trade`} value={<span className={cls(r.total_pnl)}>{signed(r.total_pnl)}</span>} sub={`${fmtNum(r.fees_paid)} ${q} fees`} />
        <Tile label="Time in market" value={fmtNum(r.time_in_market_pct, 0) + "%"}
          sub={r.skipped_by_daily_cap ? `${r.skipped_by_daily_cap} buys skipped by daily cap` : "rest of the time in cash"} />
        <Tile label="Open at end" value={r.open_position ? "Yes" : "No"} sub={r.open_position ? `open P&L ${signed(r.open_position.unrealized)} ${q}` : ""} />
      </div>
      <div className="card">
        <div className="card-head"><h2>Growth of 100</h2></div>
        <Legend items={[["Strategy", "--c-fast"], ["Buy & hold", "--c-hold"]]} />
        <ChartView data={charts.equity} short id="bt-equity" />
      </div>
      <div className="card">
        <div className="card-head"><h2>Price and trades</h2></div>
        <Legend items={[["Close price", "--c-price"], [`Fast EMA ${req.fast}`, "--c-fast"], [`Slow EMA ${req.slow}`, "--c-slow"], ["Buy", "", "buy"], ["Sell", "", "sell"]]} />
        <ChartView data={charts.price} id="bt-price" />
      </div>
      <div className="card">
        <h2>Trades ({r.trades.length})</h2>
        {r.trades.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Bought</th><th className="r">Buy price</th><th>Sold</th><th className="r">Sell price</th><th className="r">P&amp;L</th><th>Exit reason</th></tr></thead>
            <tbody>{r.trades.slice().reverse().map(t => (
              <tr key={t.entry_time}><td>{fmtTime(t.entry_time)}</td><td className="r">{fmtPrice(t.entry_price)}</td>
                <td>{fmtTime(t.exit_time)}</td><td className="r">{fmtPrice(t.exit_price)}</td>
                <td className={"r " + cls(t.pnl)}>{signed(t.pnl)} <span className="small">({pct(t.pnl_pct)})</span></td>
                <td className="muted">{t.reason}</td></tr>
            ))}</tbody></table></div>
        ) : <p className="muted small">No trades.</p>}
      </div>
      <AIReview kind="backtest" data={r} />
      <div className="alert info">A backtest is a rough guide, not a promise. Real fills can be worse (slippage), and a
        strategy that worked on past prices can lose money in future markets.</div>
    </div>
  );
}

export function BacktestPage() {
  const { meta } = useApp();
  const [v, set] = useFields({ ...meta.defaults, days: 730, ...memory.form });
  const [result, setResult] = useState(memory.result);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  memory.form = v;

  const run = async e => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { name, mode, ai_filter, symbols, ...rest } = toBody(v);
      const r = await api("/backtest", { method: "POST", body: rest });
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
      {meta.backtest_only && <div className="alert info">You're on the <b>online version</b> of TrendBot: backtests only.
        To run bots and connect exchange accounts, use TrendBot on your PC (<code>start.bat</code> → http://localhost:8765).</div>}
      <div className="page-head"><div className="grow"><h1>Backtest</h1>
        <div className="muted small">Replays the exact bot rules on past prices, with fees, and compares the result with simply buying and holding.</div></div></div>
      <div className="card">
        <form id="bt-form" noValidate onSubmit={run}>
          <div className="form-grid">
            <StrategyFields v={v} set={set} />
            <label className="field">Period <select name="days" value={v.days} onChange={set("days")}>
              {PERIODS.map(([d, l]) => <option key={d} value={d}>{l}</option>)}
            </select></label>
          </div>
          <AdvancedFields v={v} set={set} />
          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn primary" type="submit" disabled={busy}>Run backtest</button>
          </div>
        </form>
      </div>
      {busy ? <Spinner>Downloading price history and simulating… this can take up to a minute.</Spinner>
        : error ? <div className="alert error">{error}</div>
          : result && <Results r={result} />}
    </>
  );
}
