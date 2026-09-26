"""Paths, constants and API-key lookup.

Keys come from the environment / .env, or from accounts connected in the app (stored in
data/accounts.json on this computer only). Their values are never sent back to the browser.
"""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Vercel (and TRENDBOT_BACKTEST_ONLY=1) run a public, backtest-only site: serverless hosts can't keep
# bots running or store their state, so bots and exchange accounts live in the PC app only.
BACKTEST_ONLY = bool(os.environ.get("VERCEL") or os.environ.get("TRENDBOT_BACKTEST_ONLY"))

# Cloud hosts point this at a persistent disk; locally it is the data/ folder.
# Vercel's filesystem is read-only except /tmp.
DATA_DIR = Path(os.environ.get("TRENDBOT_DATA_DIR") or ("/tmp/trendbot" if os.environ.get("VERCEL") else ROOT / "data"))
BOTS_DIR = DATA_DIR / "bots"
WEB_DIR = ROOT / "web"

POLL_SECONDS = 60  # how often a running bot checks price / trailing stop

EXCHANGES = {
    "binance": {"label": "Binance", "kind": "crypto", "fee": 0.001, "example": "BTC/USDT"},
    "bybit": {"label": "Bybit", "kind": "crypto", "fee": 0.001, "example": "BTC/USDT"},
    "alpaca": {"label": "Alpaca (US stocks)", "kind": "stocks", "fee": 0.0, "example": "AAPL"},
}
TIMEFRAMES = {"1h": 3600, "4h": 14400, "1d": 86400}
MODES = ("paper", "testnet", "live")


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def key_env_names(exchange: str, mode: str) -> tuple[str, str]:
    prefix = exchange.upper() + ("_TESTNET" if mode == "testnet" else "")
    return f"{prefix}_API_KEY", f"{prefix}_API_SECRET"


ACCOUNTS_FILE = DATA_DIR / "accounts.json"
_accounts_lock = threading.Lock()


def _load_accounts() -> dict:
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_account(exchange: str, mode: str, key: str, secret: str) -> None:
    """Remember keys connected in the app. Owner-only file permissions where the OS supports it."""
    with _accounts_lock:
        data = _load_accounts()
        data.setdefault(exchange, {})[mode] = {"api_key": key, "api_secret": secret}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ACCOUNTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, ACCOUNTS_FILE)


def remove_account(exchange: str, mode: str) -> None:
    with _accounts_lock:
        data = _load_accounts()
        if data.get(exchange, {}).pop(mode, None) is not None:
            ACCOUNTS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _env_keys(exchange: str, mode: str) -> tuple[str, str]:
    key_name, secret_name = key_env_names(exchange, mode)
    return os.environ.get(key_name, "").strip(), os.environ.get(secret_name, "").strip()


def api_keys(exchange: str, mode: str) -> tuple[str | None, str | None]:
    key, secret = _env_keys(exchange, mode)
    if not (key and secret):
        stored = _load_accounts().get(exchange, {}).get(mode) or {}
        key, secret = stored.get("api_key", ""), stored.get("api_secret", "")
    return (key, secret) if key and secret else (None, None)


def key_source(exchange: str, mode: str) -> str | None:
    """'env' (.env / environment), 'app' (connected in the app) or None."""
    if all(_env_keys(exchange, mode)):
        return "env"
    return "app" if api_keys(exchange, mode)[0] else None


def keys_status() -> dict:
    """Where each account's keys come from - never the values."""
    return {ex: {mode: key_source(ex, mode) for mode in ("testnet", "live")} for ex in EXCHANGES}
