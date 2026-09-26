"""HTTP API + static web app."""
import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time
from contextlib import asynccontextmanager

import ccxt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .config import DATA_DIR, EXCHANGES, MODES, POLL_SECONDS, TIMEFRAMES, WEB_DIR, keys_status, load_dotenv
from .engine import BotManager
from .exchanges import ALPACA_BARS_PER_DAY, MarketError, make_market
from .strategy import Params, backtest, warmup_bars

load_dotenv()
PASSWORD = os.environ.get("BOT_UI_PASSWORD", "")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
manager = BotManager()


def _secret() -> bytes:
    path = DATA_DIR / ".ui_secret"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip().encode()


AUTH_TOKEN = hmac.new(_secret(), PASSWORD.encode(), hashlib.sha256).hexdigest() if PASSWORD else ""


@asynccontextmanager
async def lifespan(_app):
    manager.resume()
    yield
    manager.shutdown()


app = FastAPI(title="TrendBot", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if not PASSWORD and host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Set BOT_UI_PASSWORD to use the app from another device."}, 403)
        if request.method != "GET" and request.headers.get("x-trendbot") != "1":
            return JSONResponse({"detail": "Missing app header."}, 403)  # blocks cross-site form posts
        if PASSWORD and path not in ("/api/login", "/api/meta") and \
                not hmac.compare_digest(request.cookies.get("tb_auth", ""), AUTH_TOKEN):
            return JSONResponse({"detail": "Login required."}, 401)
    return await call_next(request)


@app.exception_handler(MarketError)
async def market_error(_req, exc: MarketError):
    return JSONResponse({"detail": str(exc)}, 400)


@app.exception_handler(ccxt.BaseError)
async def exchange_error(_req, exc: ccxt.BaseError):
    return JSONResponse({"detail": f"Exchange error: {str(exc)[:300]}"}, 502)


@app.exception_handler(KeyError)
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
    trade_size: float = Field(20.0, gt=0, le=1_000_000)
    daily_loss_cap: float = Field(10.0, ge=0, le=1_000_000)

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
    confirm_live: bool = False

    @model_validator(mode="after")
    def _check_mode(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self.name = self.name.strip() or f"{self.symbol} {self.timeframe}"
        return self

    def to_config(self) -> dict:
        return self.model_dump(exclude={"confirm_live"})


class BacktestIn(StrategyFields):
    days: int = Field(730, ge=30, le=2000)


class LoginIn(BaseModel):
    password: str


def _require_live_confirmation(body: BotConfigIn, previous_mode: str | None):
    if body.mode == "live" and previous_mode != "live" and not body.confirm_live:
        raise HTTPException(400, "Switching to live trading must be confirmed.")


# ---------------------------------------------------------------------------- routes

@app.get("/api/meta")
def meta(request: Request):
    return {
        "exchanges": EXCHANGES, "timeframes": list(TIMEFRAMES), "modes": list(MODES),
        "keys": keys_status(), "poll_seconds": POLL_SECONDS,
        "auth_required": bool(PASSWORD),
        "logged_in": not PASSWORD or hmac.compare_digest(request.cookies.get("tb_auth", ""), AUTH_TOKEN),
        "defaults": BotConfigIn().to_config(),
    }


@app.post("/api/login")
async def login(body: LoginIn):
    if not PASSWORD or not hmac.compare_digest(body.password, PASSWORD):
        await asyncio.sleep(1.5)  # slow down guessing
        raise HTTPException(401, "Wrong password.")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("tb_auth", AUTH_TOKEN, max_age=30 * 86400, httponly=True, samesite="strict")
    return resp


@app.get("/api/bots")
def list_bots():
    return manager.list()


@app.post("/api/bots")
def create_bot(body: BotConfigIn):
    _require_live_confirmation(body, None)
    return manager.create(body.to_config()).summary()


@app.get("/api/bots/{bot_id}")
def get_bot(bot_id: str):
    return manager.get(bot_id).detail()


@app.put("/api/bots/{bot_id}")
def update_bot(bot_id: str, body: BotConfigIn):
    _require_live_confirmation(body, manager.get(bot_id).config["mode"])
    return manager.update(bot_id, body.to_config()).summary()


@app.delete("/api/bots/{bot_id}")
def delete_bot(bot_id: str):
    manager.delete(bot_id)
    return {"ok": True}


@app.post("/api/bots/{bot_id}/start")
def start_bot(bot_id: str):
    return manager.start(bot_id).summary()


@app.post("/api/bots/{bot_id}/stop")
def stop_bot(bot_id: str):
    bot = manager.get(bot_id)
    bot.stop()
    return bot.summary()


@app.post("/api/bots/{bot_id}/close")
def close_position(bot_id: str):
    bot = manager.get(bot_id)
    bot.close_now()
    return bot.summary()


@app.get("/api/bots/{bot_id}/chart")
def bot_chart(bot_id: str):
    return manager.get(bot_id).chart()


_history_cache: dict[tuple, tuple[float, list]] = {}


def _history(exchange: str, symbol: str, timeframe: str, since_ms: int) -> list:
    key = (exchange, symbol, timeframe, since_ms // 3_600_000)
    hit = _history_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    market = make_market(exchange, "paper")
    market.validate(symbol, 1e9)  # symbol check only; trade size isn't the point here
    candles = market.fetch_history(symbol, timeframe, since_ms)
    _history_cache.clear()  # keep just the latest series in memory
    _history_cache[key] = (time.time(), candles)
    return candles


@app.post("/api/backtest")
def run_backtest(body: BacktestIn):
    p = Params(body.fast, body.slow, body.atr_period, body.atr_mult)
    tf_s = TIMEFRAMES[body.timeframe]
    if EXCHANGES[body.exchange]["kind"] == "stocks":
        warm_days = warmup_bars(p) / ALPACA_BARS_PER_DAY[body.timeframe] * 1.45 + 10
    else:
        warm_days = warmup_bars(p) * tf_s / 86400 + 2
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - body.days * 86_400_000
    candles = _history(body.exchange, body.symbol, body.timeframe, int(start_ms - warm_days * 86_400_000))
    closed = [c for c in candles if c[0] + tf_s * 1000 <= now_ms]
    try:
        result = backtest(closed, p, body.trade_size, EXCHANGES[body.exchange]["fee"],
                          body.daily_loss_cap, start_ts=start_ms)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    result["fee_rate"] = EXCHANGES[body.exchange]["fee"]
    result["request"] = body.model_dump()
    return result


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
