"""HTTP API + static web app."""
import asyncio
import functools
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import ccxt
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import ai
from .config import (BACKTEST_ONLY, DATA_DIR, EXCHANGES, MODES, POLL_SECONDS, TIMEFRAMES, WEB_DIR, api_keys, key_source,
                     keys_status, load_dotenv, remove_account, save_account)
from .engine import BotManager, BotNotFound
from .store import CLOUD, StoreError, store
from . import github_timer
from .exchanges import ALPACA_BARS_PER_DAY, MarketError, make_market, verify_account
from .strategy import Params, backtest, compute, entry_signal, warmup_bars

load_dotenv()
PASSWORD = os.environ.get("BOT_UI_PASSWORD", "")
SETUP_CODE = os.environ.get("TRENDBOT_SETUP_CODE", "")  # website: needed once, to create the password
CRON_SECRET = os.environ.get("CRON_SECRET", "")          # website: authorizes the minute-by-minute check
# "Sign in with Google": the OAuth client ID of your Google Cloud project, and the only accounts allowed in.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
GOOGLE_LOGIN = bool(GOOGLE_CLIENT_ID and ALLOWED_EMAILS)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
OPEN_PATHS = ("/api/login", "/api/login/google", "/api/meta", "/api/setup")
manager = BotManager()


# ---------------------------------------------------------------------------- login

def _secret() -> bytes:
    if CLOUD:
        return os.environ.get("TRENDBOT_SECRET", "").encode()
    path = DATA_DIR / ".ui_secret"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip().encode()


_password_cache: tuple[float, dict | None] = (0.0, None)


def _password_record() -> dict | None:
    """Website only: the password hash created on first visit, cached briefly."""
    global _password_cache
    if not CLOUD:
        return None
    if time.time() - _password_cache[0] > 30:
        _password_cache = (time.time(), store.get_json("auth"))
    return _password_cache[1]


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def _sign(value: str) -> str:
    return hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()


def _password_basis() -> str:
    """What password logins are checked against: the .env password, the password created on the website,
    or - on the website before its database exists - the one-time setup code."""
    if PASSWORD:
        return "env:" + PASSWORD
    rec = _password_record()
    if rec:
        return "hash:" + rec["hash"]
    if BACKTEST_ONLY and os.environ.get("VERCEL") and SETUP_CODE:
        return "code:" + SETUP_CODE
    return ""


def _email_ok(email: str) -> bool:
    return not ALLOWED_EMAILS or email.strip().lower() in ALLOWED_EMAILS


def _password_token(email: str) -> str:
    basis = _password_basis()
    return _sign(f"login:{email.strip().lower()}:{basis}") if basis else ""


def valid_tokens() -> list[str]:
    """Cookie values that prove a login. Empty list = no login configured (PC app on localhost)."""
    tokens = [_sign("google:" + email) for email in sorted(ALLOWED_EMAILS)] if GOOGLE_LOGIN else []
    if _password_basis():
        tokens += [_password_token(e) for e in (sorted(ALLOWED_EMAILS) or [""])]
    return tokens


def needs_setup() -> bool:
    """Website without Google sign-in: the password must be created on first visit."""
    return CLOUD and not GOOGLE_LOGIN and not PASSWORD and _password_record() is None


def _cookie_ok(request: Request, tokens: list[str]) -> bool:
    cookie = request.cookies.get("tb_auth", "")
    return any(hmac.compare_digest(cookie, t) for t in tokens)


def _logged_in(request: Request) -> bool:
    tokens = valid_tokens()
    return not tokens or _cookie_ok(request, tokens)


def _auth_response(request: Request, token: str) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie("tb_auth", token, max_age=30 * 86400, httponly=True, samesite="strict", secure=https)
    return resp


def managed(write: bool = False):
    """Run a route inside a bot session (on the website: load bots fresh; save and lock for changes)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with manager.session(write=write):
                return fn(*args, **kwargs)
        return wrapper
    return deco


@asynccontextmanager
async def lifespan(_app):
    if not BACKTEST_ONLY:
        manager.resume()
    yield
    manager.shutdown()


app = FastAPI(title="TrendBot", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/cron/tick":  # the scheduler authenticates with CRON_SECRET
        if BACKTEST_ONLY and path.startswith(("/api/bots", "/api/accounts", "/api/ai")):
            return JSONResponse({"detail": "This online version only runs backtests. Bots and exchange accounts "
                                           "live in TrendBot on your PC."}, 403)
        try:
            tokens, setup = await asyncio.to_thread(lambda: (valid_tokens(), needs_setup()))
        except (StoreError, OSError) as exc:
            return JSONResponse({"detail": f"The database isn't reachable right now. ({exc})"}, 503)
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        # The backtest-only site holds nothing private, so it may be public without a password.
        if not tokens and not BACKTEST_ONLY and not CLOUD and host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Set BOT_UI_PASSWORD to use the app from another device."}, 403)
        if request.method != "GET" and request.headers.get("x-trendbot") != "1":
            return JSONResponse({"detail": "Missing app header."}, 403)  # blocks cross-site form posts
        if setup and path not in OPEN_PATHS:
            return JSONResponse({"detail": "Create your password first."}, 401)
        if tokens and path not in OPEN_PATHS and not _cookie_ok(request, tokens):
            return JSONResponse({"detail": "Login required."}, 401)
    return await call_next(request)


@app.exception_handler(MarketError)
async def market_error(_req, exc: MarketError):
    return JSONResponse({"detail": str(exc)}, 400)


@app.exception_handler(ccxt.NetworkError)
async def exchange_unreachable(_req, exc: ccxt.NetworkError):
    return JSONResponse({"detail": "Couldn't reach the exchange. Check your internet connection and try again (some exchanges "
                                   "also block certain countries). "
                                   f"({type(exc).__name__})"}, 502)


@app.exception_handler(ccxt.BaseError)
async def exchange_error(_req, exc: ccxt.BaseError):
    return JSONResponse({"detail": f"Exchange error: {str(exc)[:300]}"}, 502)


@app.exception_handler(ai.AIError)
async def ai_error(_req, exc: ai.AIError):
    return JSONResponse({"detail": str(exc)}, 400)


@app.exception_handler(StoreError)
async def store_error(_req, exc: StoreError):
    return JSONResponse({"detail": str(exc)}, 503)


@app.exception_handler(BotNotFound)
async def not_found(_req, _exc):
    return JSONResponse({"detail": "Bot not found."}, 404)


# ---------------------------------------------------------------------------- models

class StrategyFields(BaseModel):
    exchange: str = "binance"
    symbol: str = Field("BTC/USDT", min_length=1, max_length=24)
    timeframe: str = "4h"
    fast: int = Field(20, ge=2, le=200)
    slow: int = Field(50, ge=3, le=400)
    atr_period: int = Field(14, ge=2, le=100)
    atr_mult: float = Field(3.0, ge=0.5, le=10)
    trend_filter: bool = True  # only buy above the 200-candle EMA
    reentry: bool = False      # re-enter on a 20-candle closing high while the trend is up
    adx_min: float = Field(20.0, ge=0, le=60)  # only buy when trend strength (ADX 14) is at least this; 0 = off
    trade_size: float = Field(5.0, gt=0, le=1_000_000)
    daily_loss_cap: float = Field(3.0, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _check(self):
        if self.exchange not in EXCHANGES:
            raise ValueError(f"exchange must be one of {', '.join(EXCHANGES)}")
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
        if self.slow <= self.fast:
            raise ValueError("The slow EMA must be longer than the fast EMA.")
        sym = self.symbol.strip().upper()
        if EXCHANGES[self.exchange]["kind"] == "crypto":
            if not re.fullmatch(r"[A-Z0-9]{2,12}/[A-Z0-9]{2,12}", sym):
                raise ValueError("Crypto symbols look like BTC/USDT.")
        elif not re.fullmatch(r"[A-Z][A-Z.]{0,9}", sym):
            raise ValueError("Stock symbols look like AAPL.")
        self.symbol = sym
        return self


class BotConfigIn(StrategyFields):
    name: str = Field("", max_length=40)
    mode: str = "paper"
    brain: str = "ai"  # "ai" = Claude decides (AI Autopilot); "rules" = EMA crossover rules
    style: str = "balanced"  # AI Autopilot: careful | balanced | aggressive
    ai_model: str = "sonnet"  # AI Autopilot: "sonnet" (Sonnet 5, medium effort, cheaper) | "opus" (Opus 5.5/5, high)
    decide_every_min: int = 3  # AI Autopilot: scan every N minutes (asks Claude only on changes); 0 = each candle
    watchlist: list[str] = Field(default_factory=list, max_length=8)  # markets the AI Autopilot may trade
    ai_filter: bool = False  # rules bots: ask the AI to approve each buy signal
    confirm_live: bool = False

    @model_validator(mode="after")
    def _check_mode(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if self.brain not in ("ai", "rules"):
            raise ValueError("brain must be 'ai' or 'rules'")
        if self.style not in ("careful", "balanced", "aggressive"):
            raise ValueError("style must be careful, balanced or aggressive")
        if self.ai_model not in ("opus", "sonnet"):
            raise ValueError("ai_model must be opus or sonnet")
        if self.decide_every_min not in (0, 3, 5, 15, 30, 60):
            raise ValueError("decide_every_min must be 0, 3, 5, 15, 30 or 60")
        if self.brain == "ai":
            kind = EXCHANGES[self.exchange]["kind"]
            pattern = r"[A-Z0-9]{2,12}/[A-Z0-9]{2,12}" if kind == "crypto" else r"[A-Z][A-Z.]{0,9}"
            clean = []
            for raw in self.watchlist or SCAN_SYMBOLS[kind][:6]:
                sym = raw.strip().upper()
                if sym and sym not in clean:
                    if not re.fullmatch(pattern, sym):
                        raise ValueError(f"'{raw}' isn't a valid symbol here "
                                         + ("(like BTC/USDT)." if kind == "crypto" else "(like AAPL)."))
                    clean.append(sym)
            if not clean:
                raise ValueError("Add at least one market to the watchlist.")
            self.watchlist, self.symbol, self.ai_filter = clean[:8], clean[0], False
            self.name = self.name.strip() or "AI Autopilot"
        else:
            self.watchlist = []
            self.name = self.name.strip() or f"{self.symbol} {self.timeframe}"
        return self

    def to_config(self) -> dict:
        return self.model_dump(exclude={"confirm_live"})


class BacktestIn(StrategyFields):
    days: int = Field(730, ge=30, le=2000)


class LoginIn(BaseModel):
    email: str = Field("", max_length=200)
    password: str = Field(max_length=200)


class SetupIn(BaseModel):
    email: str = Field("", max_length=200)
    code: str
    password: str = Field(min_length=10, max_length=200)


class AccountIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)
    api_secret: str = Field(min_length=8, max_length=300)


def _require_live_confirmation(body: BotConfigIn, previous_mode: str | None):
    if body.mode == "live" and previous_mode != "live" and not body.confirm_live:
        raise HTTPException(400, "Switching to live trading must be confirmed.")


# ---------------------------------------------------------------------------- routes

@app.get("/api/meta")
def meta(request: Request):
    return {
        "exchanges": EXCHANGES, "timeframes": list(TIMEFRAMES), "modes": list(MODES),
        "keys": keys_status(), "poll_seconds": POLL_SECONDS,
        "auth_required": bool(valid_tokens()), "backtest_only": BACKTEST_ONLY, "cloud": CLOUD,
        "google_client_id": GOOGLE_CLIENT_ID if GOOGLE_LOGIN else None,
        "password_login": bool(_password_basis()),
        "email_login": bool(ALLOWED_EMAILS),
        "code_login": _password_basis().startswith("code:"),
        "needs_setup": needs_setup(),
        "ai": {"source": None, "model": None} if BACKTEST_ONLY else ai.ai_status(),
        "scan_symbols": SCAN_SYMBOLS,
        "storage_setup_url": os.environ.get("TRENDBOT_STORAGE_URL") or None,
        "logged_in": _logged_in(request) and not needs_setup(),
        "defaults": BotConfigIn().to_config(),
    }


def _password_ok(password: str) -> bool:
    basis = _password_basis()
    if basis.startswith("env:"):
        return hmac.compare_digest(password, PASSWORD)
    if basis.startswith("hash:"):
        rec = _password_record()
        return hmac.compare_digest(_hash_password(password, rec["salt"]), rec["hash"])
    if basis.startswith("code:"):
        return hmac.compare_digest(password.strip(), SETUP_CODE)
    return False


@app.post("/api/login")
async def login(body: LoginIn, request: Request):
    email = body.email.strip().lower()
    if not _email_ok(email) or not await asyncio.to_thread(_password_ok, body.password):
        await asyncio.sleep(1.5)  # slow down guessing
        raise HTTPException(401, "Wrong email or password.")
    return _auth_response(request, await asyncio.to_thread(_password_token, email))


class GoogleLoginIn(BaseModel):
    credential: str = Field(min_length=20, max_length=5000)


def _verify_google(credential: str) -> str:
    """Check a Google ID token with Google and return the signed-in email if it's allowed."""
    try:
        r = requests.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": credential}, timeout=15)
    except requests.RequestException:
        raise HTTPException(502, "Couldn't reach Google. Try again.")
    info = r.json() if r.status_code == 200 else {}
    email = str(info.get("email", "")).lower()
    if (info.get("aud") != GOOGLE_CLIENT_ID or info.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
            or str(info.get("email_verified")).lower() != "true" or int(info.get("exp", 0)) < time.time()):
        raise HTTPException(401, "Google sign-in failed. Try again.")
    if email not in ALLOWED_EMAILS:
        raise HTTPException(403, f"{email} isn't allowed to use this TrendBot.")
    return email


@app.post("/api/login/google")
async def login_google(body: GoogleLoginIn, request: Request):
    if not GOOGLE_LOGIN:
        raise HTTPException(404, "Google sign-in isn't set up on this server.")
    email = await asyncio.to_thread(_verify_google, body.credential)
    return _auth_response(request, _sign("google:" + email))


@app.post("/api/setup")
async def setup(body: SetupIn, request: Request):
    """Website, first visit only: create the login password. Needs the one-time setup code."""
    global _password_cache
    if GOOGLE_LOGIN:
        raise HTTPException(400, "This TrendBot uses Google sign-in. Sign in with your Google account instead.")
    if not await asyncio.to_thread(needs_setup):
        raise HTTPException(400, "A password already exists. Log in instead.")
    if not _email_ok(body.email):
        await asyncio.sleep(1.5)
        raise HTTPException(403, "That email isn't allowed to use this TrendBot.")
    if not SETUP_CODE or not hmac.compare_digest(body.code.strip(), SETUP_CODE):
        await asyncio.sleep(1.5)
        raise HTTPException(401, "That setup code isn't right.")
    salt = secrets.token_hex(16)
    rec = {"salt": salt, "hash": _hash_password(body.password, salt), "created": time.time()}
    await asyncio.to_thread(store.set_json, "auth", rec)
    _password_cache = (time.time(), rec)
    return _auth_response(request, await asyncio.to_thread(_password_token, body.email))


@app.api_route("/api/cron/tick", methods=["GET", "POST"])
def cron_tick(request: Request, key: str = ""):
    """Website only: the scheduler (cron-job.org, Vercel Cron...) calls this once a minute."""
    if not CLOUD:
        raise HTTPException(404, "Only used by the website version.")
    supplied = key or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    ok = bool(CRON_SECRET) and hmac.compare_digest(supplied, CRON_SECRET)
    if not ok and supplied.count(".") == 2:  # a GitHub Actions timer in this app's repository (no secret needed)
        ok = github_timer.verify(supplied)
    if not ok:
        raise HTTPException(401, "Wrong or missing key.")
    return manager.cron_tick()


@app.get("/api/cron/info")
def cron_info(request: Request):
    """For the logged-in owner: the scheduler URL to paste into cron-job.org, and when it last ran."""
    if not CLOUD:
        raise HTTPException(404, "Only used by the website version.")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return {"url": f"https://{host}/api/cron/tick?key={CRON_SECRET}" if CRON_SECRET else None,
            "last_run": store.cmd("GET", "trendbot:cron_last")}


@app.get("/api/bots")
@managed()
def list_bots():
    return manager.list()


@app.post("/api/bots")
@managed(write=True)
def create_bot(body: BotConfigIn):
    _require_live_confirmation(body, None)
    return manager.create(body.to_config()).summary()


@app.get("/api/bots/{bot_id}")
@managed()
def get_bot(bot_id: str):
    return manager.get(bot_id).detail()


@app.put("/api/bots/{bot_id}")
@managed(write=True)
def update_bot(bot_id: str, body: BotConfigIn):
    _require_live_confirmation(body, manager.get(bot_id).config["mode"])
    return manager.update(bot_id, body.to_config()).summary()


@app.delete("/api/bots/{bot_id}")
@managed(write=True)
def delete_bot(bot_id: str):
    manager.delete(bot_id)
    return {"ok": True}


@app.post("/api/bots/{bot_id}/start")
@managed(write=True)
def start_bot(bot_id: str):
    return manager.start(bot_id).summary()


@app.post("/api/bots/{bot_id}/stop")
@managed(write=True)
def stop_bot(bot_id: str):
    bot = manager.get(bot_id)
    bot.stop()
    return bot.summary()


class BuyIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    amount: float = Field(gt=0, le=1_000_000)


@app.post("/api/bots/{bot_id}/buy")
@managed(write=True)
def buy_now(bot_id: str, body: BuyIn):
    bot = manager.get(bot_id)
    bot.buy_now(body.symbol, body.amount)
    return bot.summary()


class HoldIn(BaseModel):
    hold: bool = True
    take_profit_pct: float = Field(0.0, ge=0, le=1000)  # sell when this far above the buy price; 0 = no target
    stop_atr: float = Field(6.0, ge=2, le=12)            # safety stop distance below the high, in ATRs


@app.post("/api/bots/{bot_id}/hold")
@managed(write=True)
def hold_position(bot_id: str, body: HoldIn):
    bot = manager.get(bot_id)
    bot.set_hold(body.hold, body.take_profit_pct, body.stop_atr)
    return bot.summary()


@app.post("/api/bots/{bot_id}/close")
@managed(write=True)
def close_position(bot_id: str):
    bot = manager.get(bot_id)
    bot.close_now()
    return bot.summary()


@app.get("/api/bots/{bot_id}/chart")
@managed()
def bot_chart(bot_id: str):
    return manager.get(bot_id).chart()


def _account_target(exchange: str, mode: str) -> None:
    if exchange not in EXCHANGES or mode not in ("testnet", "live"):
        raise HTTPException(404, "Unknown account.")


def _bots_blocking(exchange: str, mode: str) -> list[str]:
    """Running bots that use this account (Alpaca paper bots also read prices through it)."""
    return [b.config["name"] for b in manager.bots.values() if b.running and b.config["exchange"] == exchange
            and (b.config["mode"] == mode or exchange == "alpaca")]


@app.post("/api/accounts/{exchange}/{mode}")
@managed()
def connect_account(exchange: str, mode: str, body: AccountIn, request: Request):
    _account_target(exchange, mode)
    _require_secure(request)
    if key_source(exchange, mode) == "env":
        raise HTTPException(400, "This account's keys are set in the .env file. Remove them there to connect here.")
    blocking = _bots_blocking(exchange, mode)
    if blocking:
        raise HTTPException(400, f"Stop these bots first: {', '.join(blocking)}.")
    key, secret = body.api_key.strip(), body.api_secret.strip()
    result = verify_account(exchange, mode, key, secret)
    save_account(exchange, mode, key, secret)
    for bot in manager.bots.values():
        if bot.config["exchange"] == exchange and not bot.running:
            bot.reset_connections()
    return result


@app.get("/api/accounts/{exchange}/{mode}")
def account_balance(exchange: str, mode: str):
    """Re-check a connected account and show its balances."""
    _account_target(exchange, mode)
    key, secret = api_keys(exchange, mode)
    if not key:
        raise HTTPException(404, "Not connected.")
    return verify_account(exchange, mode, key, secret)


@app.delete("/api/accounts/{exchange}/{mode}")
@managed()
def disconnect_account(exchange: str, mode: str):
    _account_target(exchange, mode)
    if key_source(exchange, mode) == "env":
        raise HTTPException(400, "These keys come from the .env file. Delete them there and restart TrendBot.")
    blocking = _bots_blocking(exchange, mode)
    if blocking:
        raise HTTPException(400, f"Stop these bots first: {', '.join(blocking)}.")
    remove_account(exchange, mode)
    for bot in manager.bots.values():
        if bot.config["exchange"] == exchange and not bot.running:
            bot.reset_connections()
    return {"ok": True}


_history_cache: dict[tuple, tuple[float, list]] = {}
HISTORY_CACHE_SIZE = 40


def _history(exchange: str, symbol: str, timeframe: str, since_ms: int, market=None) -> list:
    key = (exchange, symbol, timeframe, since_ms // 3_600_000)
    hit = _history_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    if market is None:
        market = make_market(exchange, "paper")
        market.validate(symbol, 1e9)  # symbol check only; trade size isn't the point here
    candles = market.fetch_history(symbol, timeframe, since_ms)
    _history_cache[key] = (time.time(), candles)
    while len(_history_cache) > HISTORY_CACHE_SIZE:  # drop the oldest entries
        del _history_cache[min(_history_cache, key=lambda k: _history_cache[k][0])]
    return candles


def _simulate(f: StrategyFields, symbol: str, days: int, market=None) -> tuple[dict, list, Params]:
    """Backtest one market. Returns (result, closed candles, params)."""
    p = Params(f.fast, f.slow, f.atr_period, f.atr_mult, f.trend_filter, f.reentry, f.adx_min)
    tf_s = TIMEFRAMES[f.timeframe]
    if EXCHANGES[f.exchange]["kind"] == "stocks":
        warm_days = warmup_bars(p) / ALPACA_BARS_PER_DAY[f.timeframe] * 1.45 + 10
    else:
        warm_days = warmup_bars(p) * tf_s / 86400 + 2
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86_400_000
    candles = _history(f.exchange, symbol, f.timeframe, int(start_ms - warm_days * 86_400_000), market)
    closed = [c for c in candles if c[0] + tf_s * 1000 <= now_ms]
    try:
        result = backtest(closed, p, f.trade_size, EXCHANGES[f.exchange]["fee"], f.daily_loss_cap, start_ts=start_ms)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result, closed, p


@app.post("/api/backtest")
def run_backtest(body: BacktestIn):
    result, _, _ = _simulate(body, body.symbol, body.days)
    result["fee_rate"] = EXCHANGES[body.exchange]["fee"]
    result["request"] = body.model_dump()
    return result


# ---------------------------------------------------------------------------- market scanner

SCAN_SYMBOLS = {
    "crypto": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT",
               "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT", "TRX/USDT"],
    "stocks": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD"],
}


class ScanIn(StrategyFields):
    days: int = Field(365, ge=30, le=1100)
    symbols: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _check_symbols(self):
        kind = EXCHANGES[self.exchange]["kind"]
        pattern = r"[A-Z0-9]{2,12}/[A-Z0-9]{2,12}" if kind == "crypto" else r"[A-Z][A-Z.]{0,9}"
        clean = []
        for raw in self.symbols or SCAN_SYMBOLS[kind]:
            sym = raw.strip().upper()
            if sym and sym not in clean:
                if not re.fullmatch(pattern, sym):
                    raise ValueError(f"'{raw}' isn't a valid symbol here " + ("(like BTC/USDT)." if kind == "crypto" else "(like AAPL)."))
                clean.append(sym)
        self.symbols = clean
        return self


def _scan_one(body: ScanIn, symbol: str, market) -> dict:
    try:
        r, closed, p = _simulate(body, symbol, body.days, market)
    except HTTPException as exc:
        return {"symbol": symbol, "ok": False, "error": str(exc.detail)}
    except Exception as exc:  # unknown symbol, exchange hiccup... one bad row shouldn't sink the scan
        msg = str(exc) if isinstance(exc, MarketError) else f"{type(exc).__name__}: {str(exc)[:120]}"
        return {"symbol": symbol, "ok": False, "error": msg}
    ind = compute(closed, p)
    last = len(closed) - 1
    fresh = any(entry_signal(ind, i) for i in range(max(1, last - 2), last + 1))
    dd = r["strategy_max_dd_pct"]
    return {
        "symbol": symbol, "ok": True,
        "strategy_return_pct": r["strategy_return_pct"], "hold_return_pct": r["hold_return_pct"],
        "strategy_max_dd_pct": dd, "hold_max_dd_pct": r["hold_max_dd_pct"],
        "trades_count": r["trades_count"], "win_rate_pct": r["win_rate_pct"],
        "time_in_market_pct": r["time_in_market_pct"], "total_pnl": r["total_pnl"],
        "open_position": r["open_position"] is not None,
        "trend": "up" if ind["fast"][last] > ind["slow"][last] else "down",
        "fresh_signal": fresh, "last_price": closed[last][4],
        # Return earned per unit of pain: rewards steady gains over lucky, bumpy ones.
        "score": r["strategy_return_pct"] / max(5.0, abs(dd)),
    }


@app.post("/api/scan")
def scan(body: ScanIn):
    market = make_market(body.exchange, "paper")
    if EXCHANGES[body.exchange]["kind"] == "crypto":
        market._markets()  # load the exchange's market list once, shared by all threads
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda sym: _scan_one(body, sym, market), body.symbols))
    rows.sort(key=lambda r: (not r["ok"], -(r.get("score") or 0)))
    return {"request": body.model_dump(), "rows": rows, "fee_rate": EXCHANGES[body.exchange]["fee"]}


# ---------------------------------------------------------------------------- AI (PC app only)

class AIKeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=300)


class AnalyzeIn(BaseModel):
    kind: str
    data: dict | None = None
    bot_id: str | None = None


def _require_secure(request: Request) -> None:
    client = request.client.host if request.client else ""
    if not (client in ("127.0.0.1", "::1") or request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"):
        raise HTTPException(403, "For safety, connect keys on the computer running TrendBot (or over https), "
                                 "not over your home network.")


@app.post("/api/ai/key")
def connect_ai(body: AIKeyIn, request: Request):
    _require_secure(request)
    if ai.ai_key()[1] == "env":
        raise HTTPException(400, "The AI key is set in the .env file. Remove it there to connect here.")
    ai.connect(body.api_key)
    return ai.ai_status()


@app.delete("/api/ai/key")
def disconnect_ai():
    if ai.ai_key()[1] == "env":
        raise HTTPException(400, "The AI key comes from the .env file. Delete it there and restart TrendBot.")
    ai.disconnect()
    return ai.ai_status()


def _trim_for_ai(kind: str, data: dict) -> dict:
    data = dict(data or {})
    if kind == "backtest":
        data.pop("curve", None)
        data["trades"] = (data.get("trades") or [])[-80:]
    if kind == "scan":
        data["rows"] = (data.get("rows") or [])[:25]
    if len(json.dumps(data, default=str)) > 60_000:
        raise HTTPException(400, "That's too much data to send to the AI.")
    return data


@app.post("/api/ai/analyze")
@managed()
def ai_analyze(body: AnalyzeIn):
    if body.kind == "bot":
        if not body.bot_id:
            raise HTTPException(400, "Missing bot.")
        d = manager.get(body.bot_id).detail()
        d["trades"], d["log"] = d["trades"][:30], d["log"][:30]
        data = d
    else:
        data = _trim_for_ai(body.kind, body.data)
    return {"text": ai.analyze(body.kind, data)}


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
