/* Setup & safety: exchange accounts, recommended path, running it, and what the bot can't do. */
import { useState } from "react";
import { api } from "../api.js";
import { accountName, fmtQty } from "../format.js";
import { ConnectDialog } from "../forms.jsx";
import { useApp } from "../ui.jsx";

const TUTORIAL = "https://github.com/kingpk-boop/TrendBot/blob/main/TUTORIAL.md";

function Balances({ res }) {
  const rows = res.balances || [];
  return (
    <>
      <div className="small">{rows.length
        ? <>Balance: {rows.map((b, i) => <span key={b.asset}>{i > 0 && " · "}<b>{fmtQty(b.total)}</b> {b.asset}</span>)}</>
        : "Connected. The account is empty for now."}</div>
      {(res.warnings || []).map(w => <div key={w} className="alert warn" style={{ margin: "8px 0 0" }}>{w}</div>)}
    </>
  );
}

function AccountRow({ ex, mode }) {
  const { meta, modal, toast, reloadMeta } = useApp();
  const src = meta.keys[ex][mode];
  const [bal, setBal] = useState(null);  // {res} | {error} | "loading"
  const title = mode === "live" ? (ex === "alpaca" ? "Live account" : "Real account") : (ex === "alpaca" ? "Paper account" : "Testnet (practice)");

  const connect = async () => {
    const res = await modal.open(close => <ConnectDialog ex={ex} mode={mode} close={close} />);
    if (!res) return;
    toast(`${accountName(meta, ex, mode)} connected.`);
    await reloadMeta();
    setBal({ res });
  };
  const disconnect = async () => {
    if (!(await modal.confirm({ title: "Disconnect account?", okLabel: "Disconnect", danger: true,
      text: `TrendBot will forget the ${accountName(meta, ex, mode)} key. To fully revoke it, also delete the key on the exchange.` }))) return;
    try {
      await api(`/accounts/${ex}/${mode}`, { method: "DELETE" });
      toast("Disconnected.");
      setBal(null);
      await reloadMeta();
    } catch (err) { toast(err.message, true); }
  };
  const check = async () => {
    setBal("loading");
    try { setBal({ res: await api(`/accounts/${ex}/${mode}`) }); } catch (err) { setBal({ error: err.message }); }
  };

  return (
    <div className="acct-row" id={`acct-${ex}-${mode}`}>
      <div className="acct-main">
        <div><b>{title}</b> {mode === "live" && <span className="badge live">real money</span>}</div>
        <div>{src ? <><span className="key-ok">✓ Connected</span>{src === "env" && <span className="muted small"> (from .env)</span>}</>
          : <span className="key-no">Not connected</span>}</div>
      </div>
      <div className="btn-row">
        {src ? <>
          <button className="btn sm" data-check={`${ex}/${mode}`} disabled={bal === "loading"} onClick={check}>Show balance</button>
          {src === "app" && <button className="btn sm danger" data-disconnect={`${ex}/${mode}`} onClick={disconnect}>Disconnect</button>}
        </> : <button className="btn sm primary" data-connect={`${ex}/${mode}`} onClick={connect}>Connect</button>}
      </div>
      <div className="acct-bal">
        {bal === "loading" ? <span className="small muted"><span className="spinner" /> Checking…</span>
          : bal?.error ? <div className="alert error" style={{ margin: "8px 0 0" }}>{bal.error}</div>
            : bal?.res ? <Balances res={bal.res} /> : null}
      </div>
    </div>
  );
}

function AICard() {
  const { meta, modal, toast, reloadMeta } = useApp();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const src = meta.ai?.source;

  const connect = async e => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/ai/key", { method: "POST", body: { api_key: key.trim() } });
      setKey("");
      toast("AI connected.");
      await reloadMeta();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };
  const disconnect = async () => {
    if (!(await modal.confirm({ title: "Disconnect AI?", okLabel: "Disconnect", danger: true,
      text: "TrendBot will forget the Anthropic key. Bots with AI check on will trade with the normal strategy." }))) return;
    try { await api("/ai/key", { method: "DELETE" }); toast("AI disconnected."); await reloadMeta(); }
    catch (err) { toast(err.message, true); }
  };

  return (
    <div className="card" id="ai-card">
      <h2><span className="ai-mark">AI</span> Claude AI (optional)</h2>
      <p className="small">Connect Claude, Anthropic's AI, to get plain-language reviews of backtests, market scans and bots, and an optional
        <b> AI check before each buy</b> that can skip trades that look like false starts. The AI can only skip buys. Trade size, the daily
        loss cap and the trailing stop stay in charge, and if the AI is unreachable the bot simply follows its normal rules.</p>
      {meta.backtest_only ? (
        <div className="alert info" style={{ margin: 0 }}>AI features are available in TrendBot on your PC (so your key stays private).</div>
      ) : src ? (
        <div className="acct-main" style={{ alignItems: "center" }}>
          <div><span className="key-ok">✓ Connected</span>{src === "env" && <span className="muted small"> (from .env)</span>}
            <span className="muted small"> · model {meta.ai.model}</span></div>
          {src === "app" && <button className="btn sm danger" id="ai-disconnect" onClick={disconnect}>Disconnect</button>}
        </div>
      ) : (
        <form onSubmit={connect} noValidate>
          <ol className="small" style={{ paddingLeft: 18 }}>
            <li>Create an account at <a href="https://console.anthropic.com/" target="_blank" rel="noopener">console.anthropic.com ↗</a> and add a little credit (for example $5).</li>
            <li>Go to <b>API Keys → Create Key</b> and copy it.</li>
            <li>Paste it below. TrendBot checks it and stores it only on this computer.</li>
          </ol>
          <div className="btn-row" style={{ alignItems: "flex-end" }}>
            <label className="field" style={{ flex: 1, minWidth: 220 }}>Anthropic API key
              <input name="ai_key" type="password" value={key} onChange={e => setKey(e.target.value)} autoComplete="off" spellCheck="false" placeholder="sk-ant-…" /></label>
            <button className="btn primary" type="submit" disabled={busy || key.trim().length < 20}>
              {busy ? <><span className="spinner" /> Checking…</> : "Connect AI"}</button>
          </div>
          {error && <div className="alert error" id="ai-err" style={{ margin: "12px 0 0" }}>{error}</div>}
          <p className="muted small" style={{ marginBottom: 0 }}>Cost: roughly 1-3 US cents per review or trade check, billed by Anthropic. You can set a spending limit in the Anthropic console.</p>
        </form>
      )}
    </div>
  );
}

export function SetupPage() {
  const { meta } = useApp();
  return (
    <>
      <div className="page-head"><div className="grow"><h1>Setup &amp; safety</h1>
        <div className="muted small">Everything runs on your own computer. Connected keys are stored only there and are never shown in this app.</div></div></div>

      <div className="card">
        <h2>Your exchange accounts</h2>
        {meta.backtest_only ? (
          <div className="alert info" style={{ margin: 0 }}>For your safety, accounts can only be connected in TrendBot on your PC, never on this
            public website. Open <code>start.bat</code> on your PC, go to <b>http://localhost:8765</b> → Setup, and connect there.</div>
        ) : <>
          <p className="muted small">Paper trading on Binance or Bybit needs no account at all. Connect an account to trade on the exchange's practice site
            (testnet) or with real money. Stocks need a free Alpaca paper account even for paper trading, because prices come from Alpaca.</p>
          <div className="acct-grid">
            {Object.entries(meta.exchanges).map(([ex, e]) => (
              <div className="acct-card" key={ex}><h3>{e.label}</h3><AccountRow ex={ex} mode="testnet" /><AccountRow ex={ex} mode="live" /></div>
            ))}
          </div>
        </>}
      </div>

      <AICard />

      <div className="card prose">
        <h2>Recommended path</h2>
        <p className="small"><a href={TUTORIAL} target="_blank" rel="noopener">Read the full step-by-step tutorial ↗</a></p>
        <ol className="steps">
          <li><b>Backtest.</b> Open the Backtest tab and run BTC/USDT, 4h, 2 years. Look at the worst drop and the number of losing trades, not just the return.</li>
          <li><b>Paper trade for a few weeks.</b> Create a bot (it starts in paper mode) and press Start. Leave the app running. It uses real live prices but pretend money.</li>
          <li><b>Optional: testnet.</b> Connect a testnet account above and switch the bot to Testnet to check that real orders work.</li>
          <li><b>Live, small.</b> Only if you're comfortable: connect your real account and switch the bot to Live (you'll type LIVE to confirm). Keep the trade size small - the default is 20 USDT per trade with a 10 USDT daily loss cap.</li>
        </ol>
        <div className="alert warn"><b>Key safety:</b> TrendBot refuses Binance and Bybit keys that allow withdrawals. A trading-only key can't move money out of
          your account. You can delete it on the exchange at any time to cut TrendBot off instantly.</div>
        <p className="muted small">Advanced: keys can also go in the <code>.env</code> file (see <code>.env.example</code>). Those take priority over connected accounts.</p>
      </div>

      <div className="card prose">
        <h2>Keeping it running &amp; using it on your phone</h2>
        <ul>
          <li>The bot only trades while TrendBot is running and the computer is awake. Set Windows to never sleep while plugged in
            (Settings → System → Power) if you want it to run 24/7. If the PC restarts, double-click <code>start.bat</code> - running bots pick up where they left off.</li>
          <li><b>Desktop app:</b> in Chrome or Edge, open the ⋮ menu → "Install TrendBot" (or "Apps → Install this site as an app").</li>
          <li><b>Phone on the same Wi-Fi:</b> add <code>BOT_UI_PASSWORD=choose-a-long-password</code> to <code>.env</code>, then start with{" "}
            <code>start.bat --host 0.0.0.0</code>. The window prints an address like <code>http://192.168.1.20:8765</code>; open that on your phone,
            log in, and use "Add to Home Screen". Windows may ask to allow Python through the firewall - allow it on private networks only.</li>
          <li>Never expose TrendBot to the open internet (no port forwarding). It's meant for your home network.</li>
        </ul>
        <p className="muted small">You're currently connected to <code>{location.host}</code>.</p>
      </div>

      <div className="card prose">
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
      </div>
    </>
  );
}
