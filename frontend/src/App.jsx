/* App shell: header, hash routing, login, and the shared context every page reads. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, setUnauthorizedHandler } from "./api.js";
import { BacktestPage } from "./pages/Backtest.jsx";
import { BotPage, BotsPage, OnlineBots } from "./pages/Bots.jsx";
import { MarketsPage } from "./pages/Markets.jsx";
import { SetupPage } from "./pages/Setup.jsx";
import { AppContext, Spinner, useHash, useModalHost, useToasts } from "./ui.jsx";

function Login({ onDone }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async e => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/login", { method: "POST", body: { password } });
      onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };
  return (
    <div className="card login">
      <h2>Log in</h2>
      <p className="muted small">This TrendBot is protected with the password set in <code>BOT_UI_PASSWORD</code>.</p>
      <form id="login-form" onSubmit={submit}>
        <label className="field">Password <input type="password" name="password" autoComplete="current-password" required autoFocus
          value={password} onChange={e => setPassword(e.target.value)} /></label>
        {error && <div className="alert error" id="login-err" style={{ marginTop: 12 }}>{error}</div>}
        <div style={{ marginTop: 14 }}><button className="btn primary" type="submit" disabled={busy} style={{ width: "100%" }}>Log in</button></div>
      </form>
    </div>
  );
}

function currentTheme() {
  return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("tb-theme", next); } catch { /* private mode */ }
  window.dispatchEvent(new Event("themechange"));
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [metaError, setMetaError] = useState("");
  const [toast, toastView] = useToasts();
  const [modal, modalView] = useModalHost();
  const hash = useHash();

  const reloadMeta = useCallback(async () => {
    try { setMeta(await api("/meta")); setMetaError(""); } catch (e) { setMetaError(e.message); }
  }, []);
  useEffect(() => {
    reloadMeta();
    setUnauthorizedHandler(() => setMeta(m => (m ? { ...m, logged_in: false } : m)));
  }, [reloadMeta]);

  const parts = (hash.replace(/^#\/?/, "") || (meta?.backtest_only ? "backtest" : "bots")).split("/");
  const tab = parts[0];
  useEffect(() => { window.scrollTo(0, 0); }, [hash]);

  const ctx = useMemo(() => ({ meta, reloadMeta, toast, modal }), [meta, reloadMeta, toast, modal]);

  let page;
  if (!meta) page = metaError
    ? <div className="card"><div className="alert error">{metaError}</div><button className="btn" onClick={reloadMeta}>Try again</button></div>
    : <Spinner>Loading…</Spinner>;
  else if (!meta.logged_in) page = <Login onDone={reloadMeta} />;
  else if (tab === "backtest") page = <BacktestPage />;
  else if (tab === "markets") page = <MarketsPage />;
  else if (tab === "setup") page = <SetupPage />;
  else if (meta.backtest_only) page = <OnlineBots />;
  else if (parts[1]) page = <BotPage key={parts[1]} id={decodeURIComponent(parts[1])} />;
  else page = <BotsPage />;

  const showTabs = meta?.logged_in;
  return (
    <AppContext.Provider value={ctx}>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#/bots"><img src="icons/icon.svg" alt="" />TrendBot</a>
          <nav className="tabs" id="tabs">
            {[["bots", "Bots"], ["markets", "Markets"], ["backtest", "Backtest"], ["setup", "Setup"]].map(([k, label]) => (
              <a key={k} href={`#/${k}`} data-tab={k} className={showTabs && tab === k ? "active" : ""}>{label}</a>
            ))}
          </nav>
          <div className="spacer" />
          <button className="icon-btn" id="theme-btn" type="button" title="Switch light / dark theme" aria-label="Switch theme" onClick={toggleTheme}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M8 1.75a6.25 6.25 0 0 1 0 12.5z" fill="currentColor" /></svg>
          </button>
        </div>
      </header>
      <main id="view">{page}</main>
      <footer className="disclaimer">
        TrendBot is a tool, not financial advice. Trading is risky: the bot can and will lose money on some trades,
        and past or backtested results don't guarantee future profit. Only trade money you can afford to lose.
      </footer>
      {toastView}
      {modalView}
    </AppContext.Provider>
  );
}
