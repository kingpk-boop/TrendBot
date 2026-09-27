"""Paths, constants and API-key lookup.

Keys come from the environment / .env, or from accounts connected in the app: stored in
data/accounts.json on your PC, or encrypted in the cloud database on the website. Their values are
never sent back to the browser.
"""
import json
import os
import threading
import time
from pathlib import Path

from .store import CLOUD, decrypt, encrypt, store

ROOT = Path(__file__).resolve().parent.parent

# On Vercel with a cloud database (store.CLOUD) the website is the full app: bots are stored in the
# database and a scheduler calls /api/cron/tick every minute. Without a database the website can only
# backtest (TRENDBOT_BACKTEST_ONLY=1 forces that anywhere).
BACKTEST_ONLY = bool(os.environ.get("TRENDBOT_BACKTEST_ONLY") or (os.environ.get("VERCEL") and not CLOUD))

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


_cloud_cache: tuple[float, dict] = (0.0, {})


def _load_accounts() -> dict:
    global _cloud_cache
    if CLOUD:  # {"exchange": {"mode": "<encrypted json>"}}, cached briefly to save database calls
        if time.time() - _cloud_cache[0] > 20:
            raw = store.get_json("accounts", {})
            _cloud_cache = (time.time(), {ex: {m: json.loads(decrypt(v)) for m, v in modes.items()}
                                          for ex, modes in raw.items()})
        return json.loads(json.dumps(_cloud_cache[1]))
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cloud_accounts(data: dict) -> None:
    global _cloud_cache
    store.set_json("accounts", {ex: {m: encrypt(json.dumps(v)) for m, v in modes.items()} for ex, modes in data.items()})
    _cloud_cache = (time.time(), data)


def save_account(exchange: str, mode: str, key: str, secret: str) -> None:
    """Remember keys connected in the app. Owner-only file permissions where the OS supports it."""
    with _accounts_lock:
        data = _load_accounts()
        if CLOUD:
            data.setdefault(exchange, {})[mode] = {"api_key": key, "api_secret": secret}
            return _save_cloud_accounts(data)
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
            if CLOUD:
                return _save_cloud_accounts(data)
            ACCOUNTS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def load_stored_secret(provider: str, mode: str) -> str | None:
    """A single stored key (used for the Anthropic API key, which has no separate secret)."""
    return (_load_accounts().get(provider, {}).get(mode) or {}).get("api_key") or None


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
