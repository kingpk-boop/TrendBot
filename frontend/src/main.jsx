import { Component, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

/** Shows what went wrong (and a reset button) instead of a blank page if the app crashes. */
class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card login" style={{ maxWidth: 480 }}>
        <h2>Something went wrong</h2>
        <p className="small">TrendBot hit an error. Resetting usually fixes it.</p>
        <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{String(this.state.error?.message || this.state.error)}</pre>
        <button className="btn primary" onClick={() => window.tbReset ? window.tbReset() : location.reload()}>Reset and reload</button>
      </div>
    );
  }
}

window.__tbStarted = true;
createRoot(document.getElementById("root")).render(<StrictMode><ErrorBoundary><App /></ErrorBoundary></StrictMode>);

if ("serviceWorker" in navigator && (location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname))) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* optional */ });
}
