/* App shell: header, hash routing, login, and the shared context every page reads. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, setUnauthorizedHandler } from "./api.js";
import { BacktestPage } from "./pages/Backtest.jsx";
import { BotPage, BotsPage, OnlineBots } from "./pages/Bots.jsx";
import { MarketsPage } from "./pages/Markets.jsx";
import { SetupPage } from "./pages/Setup.jsx";
import { AppContext, setPollFloor, Spinner, useApp, useHash, useModalHost, useToasts } from "./ui.jsx";

/** "Sign in with Google" using Google Identity Services; the server checks the account is allowed. */
function GoogleButton({ clientId, onDone, onError }) {
  const el = useRef(null);
  useEffect(() => {
    let cancelled = false;
    const render = () => {
      if (cancelled || !window.google?.accounts?.id || !el.current) return;
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: async ({ credential }) => {
          try {
            await api("/login/google", { method: "POST", body: { credential } });
            onDone();
          } catch (err) { onError(err.message); }
        },
      });
      window.google.accounts.id.renderButton(el.current, { theme: "outline", size: "large", shape: "pill", text: "signin_with", width: 280 });
    };
    if (window.google?.accounts?.id) render();
    else {
      let tag = document.getElementById("gsi-script");
      if (!tag) {
        tag = document.createElement("script");
        tag.id = "gsi-script";
        tag.src = "https://accounts.google.com/gsi/client";
        tag.async = true;
        document.head.append(tag);
      }
      tag.addEventListener("load", render);
      tag.addEventListener("error", () => onError("Couldn't load Google sign-in. Check your connection."));
    }
    return () => { cancelled = true; };
  }, [clientId, onDone, onError]);
  return <div ref={el} id="google-button" style={{ display: "flex", justifyContent: "center", minHeight: 44 }} />;
}

function savedEmail() {
  try { return localStorage.getItem("tb-email") || ""; } catch { return ""; }
}

function rememberEmail(email) {
  try { localStorage.setItem("tb-email", email.trim().toLowerCase()); } catch { /* private mode */ }
}

function Login({ onDone }) {
  const { meta } = useApp();
  const [email, setEmail] = useState(savedEmail);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const google = meta.google_client_id;
  const showPassword = !google || meta.password_login;
  const submit = async e => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/login", { method: "POST", body: { email, password } });
      if (meta.email_login) rememberEmail(email);
      onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };
  return (
    <div className="card login">
      <h2>Log in</h2>
      {google && <>
        <p className="muted small">This TrendBot is private. Sign in with the Google account it belongs to.</p>
        <GoogleButton clientId={google} onDone={onDone} onError={setError} />
      </>}
      {google && showPassword && <p className="muted small" style={{ textAlign: "center", margin: "14px 0 6px" }}>or use your password</p>}
      {showPassword && <form id="login-form" onSubmit={submit}>
        {!google && <p className="muted small">This TrendBot is private. Sign in to continue.</p>}
        {meta.email_login && <label className="field">Email <input type="email" name="email" autoComplete="username" required
          value={email} onChange={e => setEmail(e.target.value)} autoFocus={!google && !email} /></label>}
        <label className="field" style={{ marginTop: meta.email_login ? 10 : 0 }}>Password
          {meta.code_login && <span className="hint">until setup is finished, your password is your setup code</span>}
          <input type="password" name="password" autoComplete="current-password" required autoFocus={!google && !!email}
            value={password} onChange={e => setPassword(e.target.value)} /></label>
        <div style={{ marginTop: 14 }}><button className="btn primary" type="submit" disabled={busy} style={{ width: "100%" }}>Log in</button></div>
      </form>}
      {error && <div className="alert error" id="login-err" style={{ marginTop: 12 }}>{error}</div>}
    </div>
  );
}

/** Website, first visit: create the login password using the one-time setup code. */
function FirstSetup({ onDone }) {
  const { meta } = useApp();
  const [email, setEmail] = useState(savedEmail);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async e => {
    e.preventDefault();
    if (password !== again) return setError("The two passwords don't match.");
    if (password.length < 10) return setError("Use at least 10 characters.");
    setBusy(true);
    setError("");
    try {
      await api("/setup", { method: "POST", body: { email, code, password } });
      if (meta.email_login) rememberEmail(email);
      onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };
  return (
    <div className="card login" style={{ maxWidth: 440 }}>
      <h2>Welcome to TrendBot</h2>
      <p className="muted small">Create the password that protects your bots and exchange accounts on this website. You'll need the
        one-time <b>setup code</b> Claude gave you (it's also in your Vercel project settings as <code>TRENDBOT_SETUP_CODE</code>).</p>
      <form id="setup-form" onSubmit={submit} noValidate>
        {meta.email_login && <label className="field" style={{ marginBottom: 10 }}>Your email
          <input type="email" name="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="username" required /></label>}
        <label className="field">Setup code <input name="code" value={code} onChange={e => setCode(e.target.value)} autoComplete="off" spellCheck="false" required /></label>
        <label className="field" style={{ marginTop: 10 }}>New password <span className="hint">at least 10 characters; don't reuse one from elsewhere</span>
          <input name="password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" required /></label>
        <label className="field" style={{ marginTop: 10 }}>Password again
          <input name="password2" type="password" value={again} onChange={e => setAgain(e.target.value)} autoComplete="new-password" required /></label>
        {error && <div className="alert error" id="setup-err" style={{ marginTop: 12 }}>{error}</div>}
        <div style={{ marginTop: 14 }}><button className="btn primary" type="submit" disabled={busy} style={{ width: "100%" }}>Create password</button></div>
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
    try {
      const m = await api("/meta");
      setPollFloor(m.cloud ? 30000 : 0);
      setMeta(m);
      setMetaError("");
    } catch (e) { setMetaError(e.message); }
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
  else if (meta.needs_setup) page = <FirstSetup onDone={reloadMeta} />;
  else if (!meta.logged_in) page = <Login onDone={reloadMeta} />;
  else if (tab === "backtest") page = <BacktestPage />;
  else if (tab === "markets") page = <MarketsPage />;
  else if (tab === "setup") page = <SetupPage />;
  else if (meta.backtest_only) page = <OnlineBots />;
  else if (parts[1]) page = <BotPage key={parts[1]} id={decodeURIComponent(parts[1])} />;
  else page = <BotsPage />;

  const showTabs = meta?.logged_in && !meta?.needs_setup;
  return (
    <AppContext.Provider value={ctx}>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#/bots"><img src="icons/icon.svg" alt="" />TrendBot</a>
          {showTabs && <nav className="tabs" id="tabs">
            {[["bots", "Bots"], ["markets", "Markets"], ["backtest", "Backtest"], ["setup", "Setup"]].map(([k, label]) => (
              <a key={k} href={`#/${k}`} data-tab={k} className={showTabs && tab === k ? "active" : ""}>{label}</a>
            ))}
          </nav>}
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
