/* Bot list, bot detail page, and the explanation shown on the backtest-only website. */
import { useCallback, useState } from "react";
import { api } from "../api.js";
import { candleIndex } from "../chart.js";
import { ago, cls, exchangeLabel, fmtNum, fmtPrice, fmtQty, fmtTime, pct, quoteOf, signed } from "../format.js";
import { BotForm } from "../forms.jsx";
import { AIReview, ChartView, FinishSetup, Legend, Modal, ModeBadge, Spinner, Tile, useApp, usePolling } from "../ui.jsx";

/** "Hold for profit": resolves with {hold, take_profit_pct, stop_atr} or nothing when cancelled. */
function HoldDialog({ bot, close }) {
  const pos = bot.position;
  const [target, setTarget] = useState("10");
  const [width, setWidth] = useState("6");
  const t = parseFloat(target) || 0, w = parseFloat(width) || 6;
  return (
    <Modal onClose={() => close()}>
      <form onSubmit={e => { e.preventDefault(); close({ hold: true, take_profit_pct: t, stop_atr: w }); }}>
        <div className="dlg-head"><h2>Hold {bot.active_symbol} for profit</h2></div>
        <div className="dlg-body">
          <p className="small" style={{ marginTop: 0 }}>The bot keeps this coin through dips: Claude and the strategy won't sell or switch it.
            It sells only at your profit target or if the price falls to a wide safety stop.</p>
          <div className="form-grid">
            <label className="field">Profit target (%) <span className="hint">sell at +this % from {fmtPrice(pos.entry_price)}; 0 = none</span>
              <input type="number" min="0" step="any" value={target} onChange={e => setTarget(e.target.value)} autoFocus /></label>
            <label className="field">Safety stop width <span className="hint">× ATR below the highest price; 4-8 is wide</span>
              <input type="number" min="2" max="12" step="0.5" value={width} onChange={e => setWidth(e.target.value)} /></label>
          </div>
          {t > 0 && <p className="small">Sells at about <b>{fmtPrice(pos.entry_price * (1 + t / 100))}</b> or above.</p>}
          <p className="muted small" style={{ marginBottom: 0 }}>Keep the bot running so the target and safety stop are watched. You can turn Hold off any time.</p>
        </div>
        <div className="dlg-foot">
          <button type="button" className="btn" onClick={() => close()}>Cancel</button>
          <button type="submit" className="btn primary">Hold for profit</button>
        </div>
      </form>
    </Modal>
  );
}

/** "Buy now": pick a market and amount; resolves with {symbol, amount} or nothing when cancelled. */
function BuyDialog({ bot, close }) {
  const { meta } = useApp();
  const c = bot.config, q = quoteOf(meta, c);
  const markets = c.brain === "ai" ? (c.watchlist || [c.symbol]) : [c.symbol];
  const [symbol, setSymbol] = useState(markets[0]);
  const [amount, setAmount] = useState(String(c.trade_size));
  const n = parseFloat(amount);
  const ok = n > 0 && n <= c.trade_size;
  const money = c.mode === "paper" ? "Paper trade - no real money." : c.mode === "live" ? "This spends REAL money on your account." : "This uses your testnet account.";
  return (
    <Modal onClose={() => close()}>
      <form onSubmit={e => { e.preventDefault(); if (ok) close({ symbol, amount: n }); }}>
        <div className="dlg-head"><h2>Buy now</h2></div>
        <div className="dlg-body">
          <div className="form-grid">
            <label className="field">Market
              <select value={symbol} onChange={e => setSymbol(e.target.value)}>{markets.map(m => <option key={m}>{m}</option>)}</select></label>
            <label className="field">Amount ({q}) <span className="hint">up to {fmtNum(c.trade_size)}</span>
              <input type="number" step="any" min="0" max={c.trade_size} value={amount} onChange={e => setAmount(e.target.value)} autoFocus /></label>
          </div>
          <p className="small" style={{ marginBottom: 0 }}>Buys at the market price. The bot then manages it like its own trades: a trailing stop
            checked every minute{c.brain === "ai" ? ", and Claude can hold, tighten the stop or sell" : " and the strategy's exit rules"}. <b>{money}</b></p>
        </div>
        <div className="dlg-foot">
          <button type="button" className="btn" onClick={() => close()}>Cancel</button>
          <button type="submit" className={"btn " + (c.mode === "live" ? "danger-solid" : "primary")} disabled={!ok}>Buy {symbol}</button>
        </div>
      </form>
    </Modal>
  );
}

/** Opens the bot form and goes to the saved bot. */
export function useOpenBotForm() {
  const { modal, toast } = useApp();
  return useCallback(async (bot = null, preset = null) => {
    const res = await modal.open(close => <BotForm bot={bot} preset={preset} close={close} />);
    if (!res) return false;
    toast(bot ? "Saved." : "Bot created. Press Start when you're ready.");
    location.hash = `#/bots/${encodeURIComponent(res.id)}`;
    return true;
  }, [modal, toast]);
}

export function OnlineBots() {
  return (
    <>
      <div className="page-head"><div className="grow"><h1>Your bots</h1></div></div>
      <FinishSetup />
      <p className="muted small">Meanwhile you can already use <a href="#/backtest">Backtest</a> and <a href="#/markets">Markets</a>.</p>
    </>
  );
}

function BotCard({ b }) {
  const { meta } = useApp();
  const c = b.config;
  const dot = b.error ? "err" : b.running ? "on" : "";
  return (
    <a className="bot-card" href={`#/bots/${encodeURIComponent(b.id)}`}>
      <div className="top"><span className={"dot " + dot} title={b.running ? "Running" : "Stopped"} />
        <span className="name">{c.name}</span>{c.brain === "ai" ? <span className="badge ai" title="Claude makes the trading decisions">AI Autopilot</span>
          : c.ai_filter && <span className="badge ai" title="AI checks each buy">AI</span>}<ModeBadge mode={c.mode} exchange={c.exchange} /></div>
      <div className="meta">{c.brain === "ai" ? `${(c.watchlist || [c.symbol]).length} markets` : c.symbol} · {exchangeLabel(meta, c.exchange)} · {c.timeframe} candles · {fmtNum(c.trade_size)} {quoteOf(meta, c)}/trade</div>
      <div className="figs">
        <div><div className="label">{b.active_symbol || "Price"}</div><div className="v">{fmtPrice(b.price)}</div></div>
        <div><div className="label">Position</div><div className="v">{b.position
          ? <span className={cls(b.unrealized)}>{b.unrealized === null ? "Holding" : signed(b.unrealized)}</span>
          : <span className="muted">None</span>}</div></div>
        <div><div className="label">Total P&amp;L</div><div className={"v " + cls(b.total_pnl)}>{signed(b.total_pnl)}</div></div>
      </div>
      <div className="status">{!b.running && <b>Stopped. </b>}{b.error ? "Problem: " + b.error : b.status}</div>
    </a>
  );
}

export function BotsPage() {
  const openForm = useOpenBotForm();
  const [bots, setBots] = useState(null);
  const [error, setError] = useState("");
  usePolling(async () => {
    try { setBots(await api("/bots")); setError(""); } catch (e) { setError(e.message); }
  }, 10000);

  let body;
  if (error && !bots) body = <div className="alert error">{error}</div>;
  else if (!bots) body = <Spinner>Loading bots…</Spinner>;
  else if (!bots.length) body = (
    <div className="card empty">
      <h2>No bots yet</h2>
      <p>Try the strategy on past prices in <b>Backtest</b> first, then create a bot. It starts in paper mode,
        so it trades pretend money against real live prices until you decide otherwise.</p>
      <div className="btn-row" style={{ justifyContent: "center" }}>
        <a className="btn" href="#/backtest">Run a backtest</a>
        <button className="btn primary" id="new-bot-2" onClick={() => openForm()}>Create a paper bot</button>
      </div>
    </div>
  );
  else {
    const sum = k => bots.reduce((t, b) => t + (b[k] || 0), 0);
    body = <>
      <div className="tiles">
        <Tile label="Running" value={`${bots.filter(b => b.running).length} of ${bots.length}`} sub="bots watching the market" />
        <Tile label="Open positions" value={String(bots.filter(b => b.position).length)} sub={bots.some(b => b.position) ? `open P&L ${signed(sum("unrealized"))}` : "none right now"} />
        <Tile label="Today" value={<span className={cls(sum("today_pnl"))}>{signed(sum("today_pnl"))}</span>} sub="closed trades, all bots" />
        <Tile label="Total P&L" value={<span className={cls(sum("total_pnl"))}>{signed(sum("total_pnl"))}</span>} sub={`${sum("trades_count")} trades, ${sum("wins")} won`} />
      </div>
      <div className="bot-grid">{bots.map(b => <BotCard key={b.id} b={b} />)}</div>
    </>;
  }

  return (
    <>
      <div className="page-head">
        <div className="grow"><h1>Your bots</h1>
          <div className="muted small">AI Autopilot bots let Claude pick what to trade across a watchlist; rule bots follow the EMA trend strategy on one market. New bots start in paper mode (simulated money).</div></div>
        <div className="btn-row"><button className="btn primary" id="new-bot" onClick={() => openForm()}>+ New bot</button></div>
      </div>
      <div id="bot-list">{body}</div>
    </>
  );
}

function BotChart({ id, bot }) {
  const [data, setData] = useState(null);
  const [msg, setMsg] = useState("");
  const [extra, setExtra] = useState({ items: [], note: "" });
  const c = bot.config;
  usePolling(async () => {
    try {
      const d = await api(`/bots/${encodeURIComponent(id)}/chart`);
      const times = d.candles.map(k => k[0]);
      const hlines = [];
      if (d.entry) hlines.push({ value: d.entry, color: "--ink-3", label: "Entry" });
      if (d.stop) hlines.push({ value: d.stop, color: "--neg", label: "Trailing stop" });
      setData({
        times, hlines,
        markers: d.markers.map(m => ({ i: candleIndex(times, m.t), price: m.price, side: m.side })),
        series: [
          { name: "Close", values: d.candles.map(k => k[4]), color: "--c-price", width: 1.5 },
          { name: `EMA ${c.fast}`, values: d.fast, color: "--c-fast", width: 2 },
          { name: `EMA ${c.slow}`, values: d.slow, color: "--c-slow", width: 2 },
        ],
      });
      const items = [["Close price", "--c-price"], [`Fast EMA ${c.fast}`, "--c-fast"], [`Slow EMA ${c.slow}`, "--c-slow"], ["Buy", "", "buy"], ["Sell", "", "sell"]];
      if (d.stop) items.push(["Trailing stop", "--neg", "dash"]);
      if (d.entry) items.push(["Entry price", "--ink-3", "dash"]);
      setExtra({ items, note: `last ${times.length} × ${d.timeframe} candles` });
    } catch (e) {
      setMsg("Chart unavailable: " + e.message);
    }
  }, 60000, [id, bot.position?.entry_price, bot.trades_count]);
  return (
    <div className="card">
      <div className="card-head"><h2>Price &amp; signals{bot.config.brain === "ai" && bot.active_symbol ? ` · ${bot.active_symbol}` : ""}</h2><span className="muted small">{extra.note}</span></div>
      <Legend items={extra.items} />
      <ChartView data={data} message={msg} id="bot-chart" />
    </div>
  );
}

export function BotPage({ id }) {
  const { meta, modal, toast } = useApp();
  const openForm = useOpenBotForm();
  const [bot, setBot] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    try { setBot(await api(`/bots/${encodeURIComponent(id)}`)); setError(""); } catch (e) { setError(e.message); }
  }, [id]);
  usePolling(load, 10000, [id]);

  if (!bot) return (
    <>
      <a className="crumb" href="#/bots">← All bots</a>
      {error ? <div className="alert error" style={{ marginTop: 12 }}>{error}</div> : <Spinner>Loading bot…</Spinner>}
    </>
  );

  const c = bot.config, q = quoteOf(meta, c), pos = bot.position, ind = bot.indicators;
  const exLabel = exchangeLabel(meta, c.exchange);

  const act = async name => {
    if (name === "edit") return openForm(bot);
    let body;
    if (name === "buy") {
      body = await modal.open(cl => <BuyDialog bot={bot} close={cl} />);
      if (!body) return;
    }
    if (name === "hold") {
      if (pos?.hold) body = { hold: false };
      else {
        body = await modal.open(cl => <HoldDialog bot={bot} close={cl} />);
        if (!body) return;
      }
    }
    if (name === "delete" && !(await modal.confirm({ title: "Delete bot?", text: `Delete "${c.name}" and its trade history? This can't be undone.`, okLabel: "Delete", danger: true }))) return;
    if (name === "close") {
      const real = c.mode === "paper" ? "(paper trade - no real money)" : c.mode === "live" ? "This sells REAL coins/shares at the market price." : "This sells on your testnet account.";
      if (!(await modal.confirm({ title: "Sell now?", text: `Sell the whole ${bot.active_symbol || c.symbol} position at the current market price? ${real}`, okLabel: "Sell now", danger: true }))) return;
    }
    if (name === "start" && c.mode === "live" &&
      !(await modal.confirm({ title: "Start live trading?", text: `This bot will place real orders with real money: up to ${fmtNum(c.trade_size)} ${q} per trade on ${exLabel}.`, okLabel: "Start live bot", danger: true }))) return;
    setBusy(name);
    try {
      if (name === "delete") {
        await api(`/bots/${encodeURIComponent(id)}`, { method: "DELETE" });
        toast("Bot deleted.");
        location.hash = "#/bots";
        return;
      }
      await api(`/bots/${encodeURIComponent(id)}/${name}`, { method: "POST", body });
      toast({ start: "Bot started.", stop: "Bot stopped.", close: "Position sold.", buy: "Bought.", hold: body?.hold ? "Holding for profit." : "Back to normal management." }[name]);
      await load();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy("");
    }
  };
  const btn = (name, label, klass = "", disabled = false, title = undefined) => (
    <button className={"btn " + klass} data-act={name} disabled={disabled || !!busy} title={title} onClick={() => act(name)}>
      {busy === name ? <span className="spinner" /> : label}</button>
  );

  return (
    <>
      <a className="crumb" href="#/bots">← All bots</a>
      <div className="page-head" style={{ marginTop: 6 }}>
        <div className="grow"><h1 id="bot-title">{c.name} <ModeBadge mode={c.mode} exchange={c.exchange} />{c.brain === "ai"
          ? <> <span className="badge ai" title="Claude makes the trading decisions">AI Autopilot</span></>
          : c.ai_filter && <> <span className="badge ai" title="AI checks each buy">AI check</span></>}</h1>
          {c.brain === "ai" ? <div className="muted small">Watching {(c.watchlist || [c.symbol]).join(", ")} on {exLabel} · {c.style || "balanced"} style · {c.ai_model === "sonnet" ? "Claude Sonnet 5" : "Claude Opus"} · {c.decide_every_min ? `checks every ${c.decide_every_min} min (asks Claude on changes)` : `decides every ${c.timeframe}`} · up to {fmtNum(c.trade_size)} {q} per buy · daily loss cap {c.daily_loss_cap > 0 ? `${fmtNum(c.daily_loss_cap)} ${q}` : "off"}</div>
          : <div className="muted small">{c.symbol} on {exLabel} · {c.timeframe} candles · EMA {c.fast}/{c.slow} · stop {c.atr_mult}× ATR({c.atr_period}) · {fmtNum(c.trade_size)} {q} per trade · daily loss cap {c.daily_loss_cap > 0 ? `${fmtNum(c.daily_loss_cap)} ${q}` : "off"}</div>}</div>
        <div className="btn-row" id="bot-actions">
          {bot.running ? btn("stop", "■ Stop") : btn("start", "▶ Start", "go")}
          {pos && btn("hold", pos.hold ? "Stop holding" : "Hold for profit")}
          {pos ? btn("close", "Sell now", "danger") : btn("buy", "Buy now")}
          {btn("edit", "Edit", "", bot.running, bot.running ? "Stop the bot to edit it" : undefined)}
          {btn("delete", "Delete", "danger", bot.running || !!pos, bot.running || pos ? "Stop the bot and close its position first" : undefined)}
        </div>
      </div>

      {c.mode === "live" && <div className="alert warn"><b>Live mode:</b> this bot trades real money on your {exLabel} account.</div>}
      {bot.error && <div className="alert error"><b>Problem:</b> {bot.error}{bot.running ? " The bot keeps retrying every minute." : ""}</div>}
      {pos && !bot.running && <div className="alert warn">The bot is stopped but still holds a position. Nothing watches its trailing stop until you start it again or sell.</div>}

      <div className="tiles">
        <Tile label="Status" value={<><span className={"dot " + (bot.error ? "err" : bot.running ? "on" : "")} /> {bot.running ? "Running" : "Stopped"}</>}
          sub={bot.last_tick ? "checked " + ago(bot.last_tick) : ""} />
        <Tile label={"Price" + (c.brain === "ai" && bot.active_symbol ? ` · ${bot.active_symbol}` : "")} value={fmtPrice(bot.price)} sub={bot.price_time ? ago(bot.price_time) : ""} />
        {c.brain === "ai"
          ? <Tile label="Claude's last call" value={bot.ai_decision ? bot.ai_decision.action.toUpperCase() + (bot.ai_decision.symbol && bot.ai_decision.action !== "hold" ? " " + bot.ai_decision.symbol : "") : "—"}
            sub={bot.ai_decision ? `${bot.ai_decision.confidence}% sure · ${bot.ai_decision.model || "AI"} · ${ago(bot.ai_decision.time)}` : "at the next candle"} />
          : <Tile label="Trend (EMA)" value={!ind ? "—" : ind.fast > ind.slow ? "Up" : "Down"}
            sub={ind ? `fast ${fmtPrice(ind.fast)} / slow ${fmtPrice(ind.slow)}` : "after first candle"} />}
        <Tile label="Position" value={pos ? `${fmtQty(pos.qty)}${c.brain === "ai" && pos.symbol ? " " + pos.symbol.split("/")[0] : ""}` : "None"}
          sub={pos ? `bought at ${fmtPrice(pos.entry_price)}${pos.hold ? " · holding for profit" + (pos.take_profit ? ` → ${fmtPrice(pos.take_profit)}` : "") : ""}` : c.brain === "ai" ? "in cash" : "waiting for a buy signal"} />
        <Tile label="Trailing stop" value={pos ? fmtPrice(pos.stop) : "—"}
          sub={pos && bot.price ? `${fmtNum((bot.price / pos.stop - 1) * 100, 1)}% below price` : ""} />
        <Tile label="Open P&L" value={<span className={cls(bot.unrealized)}>{signed(bot.unrealized)}</span>} sub={pos ? q + " after sell fee" : ""} />
        <Tile label="Today" value={<span className={cls(bot.today_pnl)}>{signed(bot.today_pnl)}</span>} sub={`${q} closed trades`} />
        <Tile label="Total P&L" value={<span className={cls(bot.total_pnl)}>{signed(bot.total_pnl)}</span>} sub={`${bot.trades_count} trades, ${bot.wins} won`} />
      </div>

      {c.brain === "ai" && bot.ai_decision && <div className="card ai-card" id="ai-decision">
        <div className="card-head"><h2><span className="ai-mark">AI</span> Claude's latest decision</h2>
          <span className="muted small">{bot.ai_decision.model ? bot.ai_decision.model + " · " : ""}{fmtTime(bot.ai_decision.time)}</span></div>
        <p style={{ margin: "0 0 6px" }}><b>{bot.ai_decision.action.toUpperCase()}{bot.ai_decision.symbol ? " " + bot.ai_decision.symbol : ""}</b>
          {" "}· {bot.ai_decision.confidence}% sure{bot.ai_decision.action === "buy" ? ` · ${bot.ai_decision.size_pct}% of max size · stop ${bot.ai_decision.stop_atr}× ATR` : ""}</p>
        <div className="ai-text">{bot.ai_decision.reason}</div>
        {bot.ai_decision.outlook && <p className="muted small" style={{ marginBottom: 0 }}>Market view: {bot.ai_decision.outlook}</p>}
      </div>}
      <BotChart id={id} bot={bot} />
      <AIReview kind="bot" botId={id} label="Ask AI about this bot" />

      <div className="two-col">
        <div className="card"><h2>Trades</h2>
          {bot.trades?.length ? (
            <div className="table-wrap"><table>
              <thead><tr><th>Time</th><th>Side</th>{c.brain === "ai" && <th>Market</th>}<th className="r">Price</th><th className="r">Amount</th><th className="r">P&amp;L</th><th>Reason</th></tr></thead>
              <tbody>{bot.trades.map(t => (
                <tr key={t.id}><td>{fmtTime(t.time)}</td>
                  <td><b className={t.side === "buy" ? "pos" : "neg"}>{t.side.toUpperCase()}</b>{t.mode !== c.mode && <> <span className="badge">{t.mode}</span></>}</td>
                  {c.brain === "ai" && <td>{t.symbol || c.symbol}</td>}
                  <td className="r">{fmtPrice(t.price)}</td>
                  <td className="r">{fmtNum(t.quote)} {q}</td>
                  <td className={"r " + cls(t.pnl)}>{t.pnl === undefined ? "" : <>{signed(t.pnl)}{t.pnl_pct != null && <span className="small"> ({pct(t.pnl_pct)})</span>}</>}</td>
                  <td className="wrap muted">{t.reason}</td></tr>
              ))}</tbody></table></div>
          ) : <p className="muted small">{c.brain === "ai" ? "No trades yet. Claude only buys when a market on the watchlist looks clearly favourable - waiting in cash is often the right call."
            : "No trades yet. The bot buys only on a fresh cross of the fast EMA above the slow EMA, which can take days or weeks."}</p>}
        </div>
        <div className="card"><h2>Activity log</h2>
          <div className="log">{bot.log?.length ? bot.log.map((l, i) => (
            <div key={i} className={"entry " + l.level}><span className="time">{fmtTime(l.time)}</span><span className="msg">{l.msg}</span></div>
          )) : <p className="muted small">Nothing yet.</p>}</div>
        </div>
      </div>
    </>
  );
}
