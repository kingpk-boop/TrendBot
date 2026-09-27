"""Bot runtime: one thread per running bot, state persisted to data/bots/<id>.json.

Each minute a running bot: checks the price against its ATR trailing stop, and when a new
candle has closed, recomputes the EMAs and acts on a cross. It resumes after a restart.
"""
import copy
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from . import ai
from .config import BOTS_DIR, EXCHANGES, POLL_SECONDS, TIMEFRAMES
from .exchanges import MarketError, NothingToSell, make_broker, make_market
from .strategy import Params, compute, ema, entry_signal, exit_signal, warmup_bars

MAX_LOG = 300

MAX_TRADES = 5000
DUST_USD = 1.0  # a leftover worth less than this after a sell counts as fully closed


class BotNotFound(Exception):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt(x: float) -> str:
    return f"{x:,.2f}" if abs(x) >= 1 else f"{x:.6g}"


def new_state() -> dict:
    return {"running": False, "position": None, "last_candle": None, "indicators": None,
            "trades": [], "log": []}


class Bot:
    def __init__(self, bot_id: str, config: dict, state: dict | None = None, created: str | None = None):
        self.id = bot_id
        self.config = config
        self.state = {**new_state(), **(state or {})}
        self.created = created or utc_now_iso()
        self.lock = threading.RLock()      # guards config / state / runtime (held briefly)
        self.op_lock = threading.Lock()    # serializes trading actions (tick, manual close)
        self._save_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.market = self.broker = None
        self._chart_market = None
        self._chart_cache: tuple[float, dict | None] = (0.0, None)
        self._last_error: str | None = None
        self.runtime = {"price": None, "price_time": None, "error": None, "market_open": None,
                        "last_tick": None}

    # ------------------------------------------------------------------ persistence / logging

    @property
    def path(self):
        return BOTS_DIR / f"{self.id}.json"

    def save(self) -> None:
        with self.lock:
            text = json.dumps({"id": self.id, "created": self.created, "config": self.config,
                               "state": self.state}, indent=1)
        with self._save_lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    def log(self, msg: str, level: str = "info") -> None:
        with self.lock:
            self.state["log"].append({"time": utc_now_iso(), "level": level, "msg": msg})
            del self.state["log"][:-MAX_LOG]
        self.save()

    def _error(self, exc: Exception) -> None:
        msg = str(exc) if isinstance(exc, MarketError) else f"{type(exc).__name__}: {exc}"
        with self.lock:
            self.runtime["error"] = msg
        if msg != self._last_error:  # don't flood the log with the same failure every minute
            self._last_error = msg
            self.log(msg, "error")

    def _clear_error(self) -> None:
        if self._last_error:
            self._last_error = None
            with self.lock:
                self.runtime["error"] = None
            self.log("Connection OK again.")

    # ------------------------------------------------------------------ lifecycle

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def reset_connections(self) -> None:
        self.market = self.broker = self._chart_market = None
        self._chart_cache = (0.0, None)

    def _connect(self) -> None:
        cfg = self.config
        market = make_market(cfg["exchange"], cfg["mode"])
        market.validate(cfg["symbol"], float(cfg["trade_size"]))
        self.market, self.broker = market, make_broker(cfg["exchange"], cfg["mode"], market)

    def start(self, validate: bool = True) -> None:
        if self._thread and self._thread.is_alive():
            if not self._stop.is_set():
                return
            self._thread.join(30)
        if validate:
            self._connect()  # raises MarketError straight back to the UI
        self._stop.clear()
        with self.lock:
            self.state["running"] = True
        self.save()
        self._thread = threading.Thread(target=self._run, name=f"bot-{self.id}", daemon=True)
        self._thread.start()

    def stop(self, note: bool = True) -> None:
        self._stop.set()
        with self.lock:
            self.state["running"] = False
            has_pos = self.state["position"] is not None
        if note:
            self.log("Bot stopped." + (" The open position is kept but its trailing stop is no longer "
                                       "watched." if has_pos else ""))
        else:
            self.save()

    def _run(self) -> None:
        cfg = self.config
        self.log(f"Started in {cfg['mode'].upper()} mode: {cfg['symbol']} on "
                 f"{EXCHANGES[cfg['exchange']]['label']}, {cfg['timeframe']} candles, "
                 f"{fmt(cfg['trade_size'])} per trade.")
        while not self._stop.is_set():
            try:
                if self.market is None:
                    self._connect()
                with self.op_lock:
                    if self._stop.is_set():
                        break
                    self.tick()
                self._clear_error()
            except Exception as exc:  # keep running through network blips etc.
                self._error(exc)
            self._stop.wait(POLL_SECONDS)

    # ------------------------------------------------------------------ trading

    def tick(self) -> None:
        cfg = self.config
        symbol, tf = cfg["symbol"], cfg["timeframe"]
        p = Params.from_config(cfg)
        m = self.market

        is_open = m.market_open()
        with self.lock:
            self.runtime.update(market_open=is_open, last_tick=utc_now_iso())
        if not is_open:
            return  # stock market closed: nothing can fill

        # 1) Trailing stop - checked every tick, not just at candle close.
        price = m.last_price(symbol)
        with self.lock:
            self.runtime.update(price=price, price_time=utc_now_iso())
            pos, ind = self.state["position"], self.state["indicators"]
            stop_hit = False
            if pos:
                pos["high"] = max(pos["high"], price)
                if ind:
                    pos["stop"] = max(pos["stop"], pos["high"] - p.atr_mult * ind["atr"])
                stop_hit = price <= pos["stop"]
                stop = pos["stop"]
        if stop_hit:
            self._sell(f"Trailing stop hit (price {fmt(price)} at or below stop {fmt(stop)})")

        # 2) Signals - only once a new candle has closed.
        tf_ms = TIMEFRAMES[tf] * 1000
        now_ms = int(time.time() * 1000)
        last = self.state["last_candle"]
        if last is not None and now_ms < last + 2 * tf_ms:
            self.save()
            return
        need = warmup_bars(p)
        candles = m.fetch_candles(symbol, tf, limit=max(300, need + 10))
        closed = [c for c in candles if c[0] + tf_ms <= now_ms]
        if len(closed) < need:
            raise MarketError(f"Only {len(closed)} candles of history for {symbol}; the strategy needs {need}.")
        ts = closed[-1][0]
        if ts == last:
            self.save()
            return
        ind_all = compute(closed, p)
        i = len(closed) - 1
        with self.lock:
            self.state["indicators"] = {"fast": ind_all["fast"][i], "slow": ind_all["slow"][i],
                                        "atr": ind_all["atr"][i], "candle": ts, "close": closed[i][4]}
            pos = self.state["position"]
        if pos and exit_signal(ind_all, i):
            self._sell("EMA cross down")
        elif not pos and entry_signal(ind_all, i):
            cap = float(cfg["daily_loss_cap"])
            today = self.today_pnl()
            if cap > 0 and today <= -cap:
                self.log(f"Buy signal skipped: today's loss ({fmt(today)}) hit the daily cap of {fmt(cap)}.", "warn")
            else:
                reason = "EMA cross up"
                if cfg.get("ai_filter"):
                    review = self._ai_review(closed, ind_all, i, p)
                    if review and review.decision == "skip":
                        self.log(f"AI skipped this buy signal ({review.confidence}% sure): {review.reason}", "warn")
                        with self.lock:
                            self.state["last_candle"] = ts
                        self.save()
                        return
                    if review:
                        reason = f"EMA cross up · AI approved ({review.confidence}%)"
                        self.log(f"AI approved the buy ({review.confidence}% sure): {review.reason}")
                self._buy(ind_all["atr"][i], p, reason)
        with self.lock:
            self.state["last_candle"] = ts
        self.save()

    def _add_trade(self, side: str, fill, reason: str, pnl: float | None = None, cost: float | None = None):
        trade = {"id": uuid.uuid4().hex[:10], "time": utc_now_iso(), "side": side, "price": fill.price,
                 "qty": fill.qty, "quote": fill.quote, "fee": fill.fee, "reason": reason,
                 "mode": self.config["mode"]}
        if pnl is not None:
            trade.update(pnl=pnl, pnl_pct=pnl / cost * 100 if cost else None)
        self.state["trades"].append(trade)
        del self.state["trades"][:-MAX_TRADES]

    def _ai_context(self, closed: list, ind: dict, i: int, p: Params) -> dict:
        """A compact snapshot of the market for the AI's pre-trade check."""
        closes = [c[4] for c in closed]
        close, atr_now = closes[i], ind["atr"][i]
        tf_s = TIMEFRAMES[self.config["timeframe"]]
        bars_30d = max(1, int(30 * 86400 / tf_s))
        long_ema = ema(closes, min(200, len(closes)))
        with self.lock:
            recent = [{"pnl_pct": round(t.get("pnl_pct") or 0, 2), "reason": t["reason"], "time": t["time"]}
                      for t in self._mode_sells()[-6:]]
        return {
            "symbol": self.config["symbol"], "timeframe": self.config["timeframe"],
            "close": close, "fast_ema": round(ind["fast"][i], 6), "slow_ema": round(ind["slow"][i], 6),
            "long_ema_200": round(long_ema[i], 6), "price_above_long_ema": close > long_ema[i],
            "atr_pct_of_price": round(atr_now / close * 100, 3),
            "distance_above_slow_ema_in_atr": round((close - ind["slow"][i]) / atr_now, 2) if atr_now else None,
            "change_pct_30d": round((close / closes[max(0, i - bars_30d)] - 1) * 100, 2),
            "strategy": {"fast": p.fast, "slow": p.slow, "stop_atr_multiple": p.atr_mult},
            "recent_closes_oldest_first": [round(c, 6) for c in closes[max(0, i - 59):i + 1]],
            "this_bot_recent_closed_trades": recent,
        }

    def _ai_review(self, closed: list, ind: dict, i: int, p: Params):
        """Ask the AI about a buy signal. Returns None (= trade normally) if the AI is unavailable."""
        try:
            return ai.review_entry(self._ai_context(closed, ind, i, p))
        except ai.AIError as exc:
            self.log(f"AI check unavailable ({exc}) - following the normal strategy.", "warn")
        except Exception as exc:
            self.log(f"AI check failed ({type(exc).__name__}) - following the normal strategy.", "warn")
        return None

    def _buy(self, atr_value: float, p: Params, reason: str = "EMA cross up") -> None:
        cfg = self.config
        fill = self.broker.buy(cfg["symbol"], float(cfg["trade_size"]))
        with self.lock:
            self.state["position"] = {
                "qty": fill.qty, "entry_price": fill.price, "cost": fill.quote, "entry_time": utc_now_iso(),
                "high": fill.price, "stop": fill.price - p.atr_mult * atr_value,
            }
            self._add_trade("buy", fill, reason)
        self.log(f"BUY {fill.qty:.8g} {cfg['symbol']} at {fmt(fill.price)} for {fmt(fill.quote)}. "
                 f"Stop starts at {fmt(self.state['position']['stop'])}.")

    def _sell(self, reason: str) -> None:
        cfg = self.config
        with self.lock:
            pos = copy.deepcopy(self.state["position"])
        if not pos:
            return
        try:
            fill = self.broker.sell(cfg["symbol"], pos["qty"])
        except NothingToSell as exc:
            with self.lock:
                self.state["position"] = None
            self.log(f"{exc} It was probably sold outside the bot, so the position is marked closed "
                     "without a P&L record.", "warn")
            return
        fraction = min(1.0, fill.qty / pos["qty"]) if pos["qty"] else 1.0
        leftover_value = (pos["qty"] - fill.qty) * fill.price
        closed = fraction >= 0.99 or leftover_value < DUST_USD
        cost_part = pos["cost"] if closed else pos["cost"] * fraction
        pnl = fill.quote - cost_part
        with self.lock:
            self._add_trade("sell", fill, reason, pnl=pnl, cost=cost_part)
            if closed:
                self.state["position"] = None
            else:
                self.state["position"].update(qty=pos["qty"] - fill.qty, cost=pos["cost"] - cost_part)
        self.log(f"SELL {fill.qty:.8g} {cfg['symbol']} at {fmt(fill.price)} ({reason}). "
                 f"P&L {'+' if pnl >= 0 else ''}{fmt(pnl)}." + ("" if closed else " Partial fill - rest still held."),
                 "trade")

    def close_now(self) -> None:
        """Manual 'sell now' from the UI."""
        with self.op_lock:
            if self.broker is None:
                self._connect()
            if not self.market.market_open():
                raise MarketError("The stock market is closed - try again when it opens.")
            self._sell("Manual close")

    # ------------------------------------------------------------------ reporting

    def _mode_sells(self) -> list:
        mode = self.config["mode"]
        return [t for t in self.state["trades"] if t["side"] == "sell" and t.get("mode") == mode]

    def today_pnl(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.lock:
            return sum(t["pnl"] for t in self._mode_sells() if t["time"].startswith(today))

    def summary(self) -> dict:
        with self.lock:
            cfg, st, rt = copy.deepcopy(self.config), self.state, dict(self.runtime)
            sells = self._mode_sells()
            pos = copy.deepcopy(st["position"])
            ind = copy.deepcopy(st["indicators"])
            price = rt["price"]
            fee = EXCHANGES[cfg["exchange"]]["fee"]
            unrealized = pos["qty"] * price * (1 - fee) - pos["cost"] if pos and price else None
            if pos:
                status = "Holding. Sells on an EMA cross down or if price hits the trailing stop."
            elif ind and ind["fast"] > ind["slow"]:
                status = ("Uptrend already under way - waiting for the next fresh cross up "
                          "(the bot doesn't chase an old signal).")
            elif ind:
                status = "Waiting for the fast EMA to cross above the slow EMA."
            else:
                status = "No signal data yet - start the bot to begin watching."
            if rt["market_open"] is False:
                status = "Stock market is closed. " + status
            return {
                "id": self.id, "created": self.created, "config": cfg,
                "running": self.running, "position": pos, "unrealized": unrealized,
                "indicators": ind, "status": status, **rt,
                "today_pnl": self.today_pnl(),
                "total_pnl": sum(t["pnl"] for t in sells),
                "trades_count": len(sells),
                "wins": sum(1 for t in sells if t["pnl"] > 0),
            }

    def detail(self) -> dict:
        data = self.summary()
        with self.lock:
            data["trades"] = copy.deepcopy(self.state["trades"][-300:][::-1])
            data["log"] = copy.deepcopy(self.state["log"][-150:][::-1])
        return data

    def chart(self) -> dict:
        cached_at, cached = self._chart_cache
        if cached and time.time() - cached_at < 30:
            return cached
        cfg = self.config
        if self._chart_market is None:
            # Separate instance from the trading thread's; live crypto charts use public prices.
            self._chart_market = make_market(cfg["exchange"], "testnet" if cfg["mode"] == "testnet" else "paper")
        p = Params.from_config(cfg)
        candles = self._chart_market.fetch_candles(cfg["symbol"], cfg["timeframe"], limit=max(300, warmup_bars(p) + 150))
        if not candles:
            raise MarketError(f"No price history for {cfg['symbol']}.")
        ind = compute(candles, p)
        n = min(150, len(candles))
        start_ms = candles[-n][0]
        with self.lock:
            pos = copy.deepcopy(self.state["position"])
            markers = [
                {"t": int(datetime.fromisoformat(t["time"]).timestamp() * 1000), "side": t["side"], "price": t["price"]}
                for t in self.state["trades"] if t.get("mode") == cfg["mode"]
            ]
        data = {
            "candles": [c[:5] for c in candles[-n:]],
            "fast": ind["fast"][-n:], "slow": ind["slow"][-n:],
            "markers": [mk for mk in markers if mk["t"] >= start_ms],
            "stop": pos["stop"] if pos else None,
            "entry": pos["entry_price"] if pos else None,
            "timeframe": cfg["timeframe"],
        }
        self._chart_cache = (time.time(), data)
        return data


class BotManager:
    def __init__(self):
        BOTS_DIR.mkdir(parents=True, exist_ok=True)
        self.bots: dict[str, Bot] = {}
        for f in sorted(BOTS_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                self.bots[d["id"]] = Bot(d["id"], d["config"], d.get("state"), d.get("created"))
            except Exception as exc:
                print(f"Skipping unreadable bot file {f.name}: {exc}")

    def resume(self) -> None:
        for bot in self.bots.values():
            if bot.state.get("running"):
                bot.start(validate=False)  # connects inside its thread, retrying if offline

    def shutdown(self) -> None:
        for bot in self.bots.values():
            bot._stop.set()  # desired 'running' flag stays set so the bot resumes next launch

    def get(self, bot_id: str) -> Bot:
        if bot_id not in self.bots:
            raise BotNotFound(bot_id)
        return self.bots[bot_id]

    def list(self) -> list[dict]:
        return [b.summary() for b in sorted(self.bots.values(), key=lambda b: b.created)]

    def create(self, config: dict) -> Bot:
        bot = Bot(uuid.uuid4().hex[:8], config)
        self.bots[bot.id] = bot
        bot.log("Bot created.")
        return bot

    def update(self, bot_id: str, config: dict) -> Bot:
        bot = self.get(bot_id)
        if bot.running:
            raise MarketError("Stop the bot before changing its settings.")
        if bot.state["position"] and any(config[k] != bot.config[k] for k in ("exchange", "symbol", "mode")):
            raise MarketError("This bot holds a position. Close it before changing exchange, symbol or mode.")
        with bot.lock:
            bot.config = config
        bot.reset_connections()
        bot.log("Settings updated.")
        return bot

    def start(self, bot_id: str) -> Bot:
        bot = self.get(bot_id)
        if bot.running:
            return bot
        cfg = bot.config
        if cfg["mode"] != "paper":
            for other in self.bots.values():
                oc = other.config
                if other is not bot and other.running and (oc["exchange"], oc["symbol"], oc["mode"]) == \
                        (cfg["exchange"], cfg["symbol"], cfg["mode"]):
                    raise MarketError(f"Bot '{oc['name']}' already trades {cfg['symbol']} on this account. "
                                      "Two bots selling the same coins would interfere.")
        bot.reset_connections()
        bot.start()
        return bot

    def delete(self, bot_id: str) -> None:
        bot = self.get(bot_id)
        if bot.running:
            raise MarketError("Stop the bot before deleting it.")
        if bot.state["position"]:
            raise MarketError("This bot still holds a position. Close it first.")
        del self.bots[bot_id]
        bot.path.unlink(missing_ok=True)
