"""Paths, constants and API-key lookup. Keys only ever come from the environment or .env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Cloud hosts point this at a persistent disk; locally it is the data/ folder.
DATA_DIR = Path(os.environ.get("TRENDBOT_DATA_DIR") or ROOT / "data")
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


def api_keys(exchange: str, mode: str) -> tuple[str | None, str | None]:
    key_name, secret_name = key_env_names(exchange, mode)
    key = os.environ.get(key_name, "").strip()
    secret = os.environ.get(secret_name, "").strip()
    return (key, secret) if key and secret else (None, None)


def keys_status() -> dict:
    """Which keys are configured - booleans only, never the values."""
    return {
        ex: {mode: api_keys(ex, mode)[0] is not None for mode in ("testnet", "live")}
        for ex in EXCHANGES
    }
