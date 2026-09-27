"""Bot runtime.

Each minute a running bot: checks the price against its ATR trailing stop, and when a new
candle has closed, recomputes the EMAs and acts on a cross. It resumes after a restart.

AI Autopilot bots (config brain="ai") instead ask Claude, once per candle, what to do across a
watchlist of markets; the trailing stop and the risk limits stay enforced here in code.

On your PC each running bot has its own thread and state lives in data/bots/<id>.json.
On the website (store.CLOUD) there are no long-lived threads: bots live in the cloud database,
every request loads them fresh, and a scheduler calls cron_tick() once a minute.
"""
import contextlib
import copy
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import ai
from .config import BOTS_DIR, EXCHANGES, POLL_SECONDS, TIMEFRAMES
from .exchanges import MarketError, NothingToSell, make_broker, make_market
from .store import CLOUD, store
from .strategy import Params, compute, ema, entry_signal, exit_signal, warmup_bars

MAX_LOG = 300

MAX_TRADES = 5000
DUST_USD = 1.0  # a leftover worth less than this after a sell counts as fully closed
AI_MIN_CONFIDENCE = 60  # AI Autopilot buys below this confidence are not executed
# On the website one scheduled check must finish within the server's time limit; AI decisions that
# would start after this deadline wait for the next minute's check instead.
AI_DEADLINE: list[float | None] = [None]


def is_ai(cfg: dict) -> bool:
    return cfg.get("brain") == "ai"


def bot_symbols(cfg: dict) -> list[str]:
    """Every market a bot may trade."""
    return list(cfg.get("watchlist") or [cfg["symbol"]]) if is_ai(cfg) else [cfg["symbol"]]


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI of the last close."""
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains, losses = gains + max(d, 0), losses + max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
    return 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)


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
    def __init__(self, bot_id: str, config: dict, state: dict | None = None, created: str | None = None,
                 runtime: dict | None = None):
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
        self.runtime = {"price": None, "price_time": None, "error": None, "market_open": None,
                        "last_tick": None, **(runtime or {})}
        self._last_error: str | None = self.runtime["error"]
        self._dirty = False

    # ------------------------------------------------------------------ persistence / logging

    @property
    def path(self):
        return BOTS_DIR / f"{self.id}.json"

    def doc(self) -> dict:
        with self.lock:
            return copy.deepcopy({"id": self.id, "created": self.created, "config": self.config,
                                  "state": self.state, "runtime": self.runtime})

    def save(self) -> None:
        if CLOUD:  # written to the database once, when the request or tick finishes
            self._dirty = True
            return
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
        if CLOUD:
            return bool(self.state.get("running"))
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def reset_connections(self) -> None:
        self.market = self.broker = self._chart_market = None
        self._chart_cache = (0.0, None)

    def _connect(self) -> None:
        cfg = self.config
        market = make_market(cfg["exchange"], cfg["mode"])
        for symbol in bot_symbols(cfg):
            market.validate(symbol, float(cfg["trade_size"]))
        self.market, self.broker = market, make_broker(cfg["exchange"], cfg["mode"], market)

    def start(self, validate: bool = True) -> None:
        if is_ai(self.config) and not ai.ai_key()[0]:
            raise MarketError("AI Autopilot needs Claude AI connected. Add your Anthropic API key on the Setup page.")
        if CLOUD:
            self._connect()
            with self.lock:
                self.state["running"] = True
            self._log_started()
            return
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

    def _log_started(self) -> None:
        cfg = self.config
        if is_ai(cfg):
            self.log(f"AI Autopilot started in {cfg['mode'].upper()} mode on {EXCHANGES[cfg['exchange']]['label']}: "
                     f"watching {', '.join(bot_symbols(cfg))} on {cfg['timeframe']} candles, up to "
                     f"{fmt(cfg['trade_size'])} per trade. Claude decides at each new candle.")
            return
        self.log(f"Started in {cfg['mode'].upper()} mode: {cfg['symbol']} on "
                 f"{EXCHANGES[cfg['exchange']]['label']}, {cfg['timeframe']} candles, "
                 f"{fmt(cfg['trade_size'])} per trade.")

    def check_once(self) -> None:
        """One monitoring pass; network blips and the like are logged, not raised."""
        try:
            if self.market is None:
                self._connect()
            with self.op_lock:
                self.tick()
            self._clear_error()
        except Exception as exc:
            self._error(exc)

    def _run(self) -> None:
        self._log_started()
        while not self._stop.is_set():
            self.check_once()
            self._stop.wait(POLL_SECONDS)

    # ------------------------------------------------------------------ trading

    def tick(self) -> None:
        if is_ai(self.config):
            return self.tick_ai()
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
                    pos["stop"] = max(pos["stop"], pos["high"] - pos.get("stop_mult", p.atr_mult) * ind["atr"])
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

    def _add_trade(self, side: str, fill, reason: str, pnl: float | None = None, cost: float | None = None,
                   symbol: str | None = None):
        trade = {"id": uuid.uuid4().hex[:10], "time": utc_now_iso(), "side": side,
                 "symbol": symbol or self.config["symbol"], "price": fill.price,
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

    def _buy(self, atr_value: float, p: Params, reason: str = "EMA cross up", symbol: str | None = None,
             quote: float | None = None, stop_mult: float | None = None) -> None:
        cfg = self.config
        symbol = symbol or cfg["symbol"]
        mult = stop_mult or p.atr_mult
        fill = self.broker.buy(symbol, float(quote or cfg["trade_size"]))
        with self.lock:
            self.state["position"] = {
                "symbol": symbol, "qty": fill.qty, "entry_price": fill.price, "cost": fill.quote,
                "entry_time": utc_now_iso(), "high": fill.price, "stop": fill.price - mult * atr_value,
                "atr": atr_value, "stop_mult": mult,
            }
            self._add_trade("buy", fill, reason, symbol=symbol)
        self.log(f"BUY {fill.qty:.8g} {symbol} at {fmt(fill.price)} for {fmt(fill.quote)}. "
                 f"Stop starts at {fmt(self.state['position']['stop'])}.")

    def _sell(self, reason: str) -> None:
        cfg = self.config
        with self.lock:
            pos = copy.deepcopy(self.state["position"])
        if not pos:
            return
        symbol = pos.get("symbol") or cfg["symbol"]
        try:
            fill = self.broker.sell(symbol, pos["qty"])
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
            self._add_trade("sell", fill, reason, pnl=pnl, cost=cost_part, symbol=symbol)
            if closed:
                self.state["position"] = None
            else:
                self.state["position"].update(qty=pos["qty"] - fill.qty, cost=pos["cost"] - cost_part)
        self.log(f"SELL {fill.qty:.8g} {symbol} at {fmt(fill.price)} ({reason}). "
                 f"P&L {'+' if pnl >= 0 else ''}{fmt(pnl)}." + ("" if closed else " Partial fill - rest still held."),
                 "trade")

    # ------------------------------------------------------------------ AI Autopilot

    def _snapshot(self, symbol: str) -> dict:
        """Indicators for one watchlist market, on the bot's candle size."""
        tf_ms = TIMEFRAMES[self.config["timeframe"]] * 1000
        now_ms = int(time.time() * 1000)
        candles = [c for c in self.market.fetch_candles(symbol, self.config["timeframe"], limit=300)
                   if c[0] + tf_ms <= now_ms]
        if len(candles) < 60:
            raise MarketError(f"Not enough history for {symbol}.")
        closes, highs, lows = [c[4] for c in candles], [c[2] for c in candles], [c[3] for c in candles]
        vols = [c[5] for c in candles]
        e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, min(200, len(closes)))
        a = compute(candles, Params())["atr"]
        c, atr_now = closes[-1], a[-1]
        per_day = max(1, 86400 // TIMEFRAMES[self.config["timeframe"]])

        def chg(bars):
            return round((c / closes[max(0, len(closes) - 1 - bars)] - 1) * 100, 2)

        vol_avg = sum(vols[-21:-1]) / 20 if len(vols) > 21 else None
        return {
            "symbol": symbol, "close": c, "atr": atr_now, "atr_pct": round(atr_now / c * 100, 3),
            "ema20": round(e20[-1], 8), "ema50": round(e50[-1], 8), "ema200": round(e200[-1], 8),
            "ema50_rising": e50[-1] > e50[-11], "ema200_rising": e200[-1] > e200[-21],
            "dist_above_ema20_atr": round((c - e20[-1]) / atr_now, 2) if atr_now else None,
            "rsi14": round(rsi(closes[-120:]) or 0, 1),
            "change_pct": {"1_candle": chg(1), "1_day": chg(per_day), "7_days": chg(7 * per_day),
                           "30_days": chg(30 * per_day)},
            "high_30_candles": max(highs[-30:]), "low_30_candles": min(lows[-30:]),
            "volume_vs_20_avg": round(vols[-1] / vol_avg, 2) if vol_avg else None,
            "last_24_closes": [round(x, 8) for x in closes[-24:]],
        }

    def _ai_round_context(self, snaps: dict) -> dict:
        cfg = self.config
        with self.lock:
            pos = copy.deepcopy(self.state["position"])
            last = copy.deepcopy(self.state.get("ai"))
            recent = [{"time": t["time"], "symbol": t.get("symbol", cfg["symbol"]), "side": t["side"],
                       "price": t["price"], "pnl_pct": round(t["pnl_pct"], 2) if t.get("pnl_pct") is not None else None,
                       "reason": t["reason"][:120]}
                      for t in self.state["trades"] if t.get("mode") == cfg["mode"]][-12:]
        held = None
        if pos:
            sym = pos.get("symbol") or cfg["symbol"]
            price = snaps.get(sym, {}).get("close") or self.runtime.get("price") or pos["entry_price"]
            held = {"symbol": sym, "entry_price": pos["entry_price"], "price_now": price,
                    "pnl_pct": round((price / pos["entry_price"] - 1) * 100, 2), "entry_time": pos["entry_time"],
                    "trailing_stop": pos["stop"], "stop_atr": pos.get("stop_mult")}
        cap, today = float(cfg["daily_loss_cap"]), self.today_pnl()
        quote = EXCHANGES[cfg["exchange"]]
        return {
            "time_utc": utc_now_iso(), "exchange": quote["label"], "mode": cfg["mode"],
            "candle": cfg["timeframe"], "fee_per_side_pct": quote["fee"] * 100,
            "max_trade_size": cfg["trade_size"], "position": held,
            "risk": {"today_pnl": round(today, 2), "daily_loss_cap": cap,
                     "new_buys_allowed": not (cap > 0 and today <= -cap)},
            "your_previous_decision": last, "recent_trades": recent,
            "watchlist": [dict((k, v) for k, v in s.items() if k != "atr") for s in snaps.values()],
        }

    def tick_ai(self) -> None:
        cfg = self.config
        m = self.market
        is_open = m.market_open()
        with self.lock:
            self.runtime.update(market_open=is_open, last_tick=utc_now_iso())
        if not is_open:
            return

        # 1) Protect the open position every minute: trailing stop.
        with self.lock:
            pos = self.state["position"]
            held = (pos.get("symbol") or cfg["symbol"]) if pos else None
        if pos:
            price = m.last_price(held)
            with self.lock:
                self.runtime.update(price=price, price_time=utc_now_iso())
                pos = self.state["position"]
                pos["high"] = max(pos["high"], price)
                if pos.get("atr"):
                    pos["stop"] = max(pos["stop"], pos["high"] - pos.get("stop_mult", 3.0) * pos["atr"])
                stop, stop_hit = pos["stop"], price <= pos["stop"]
            if stop_hit:
                self._sell(f"Trailing stop hit (price {fmt(price)} at or below stop {fmt(stop)})")

        # 2) Once per new candle: ask Claude what to do.
        tf_ms = TIMEFRAMES[cfg["timeframe"]] * 1000
        candle = int(time.time() * 1000) // tf_ms * tf_ms
        last = self.state["last_candle"]
        if (last is not None and candle <= last) or (AI_DEADLINE[0] and time.time() > AI_DEADLINE[0]):
            self.save()
            return
        symbols = bot_symbols(cfg)
        snaps, failed = {}, []
        with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as pool:
            for sym, res in zip(symbols, pool.map(self._safe_snapshot, symbols)):
                if isinstance(res, dict):
                    snaps[sym] = res
                else:
                    failed.append(sym)
        if not snaps:
            raise MarketError("Couldn't load prices for any market on the watchlist.")
        if not self.state["position"]:
            with self.lock:
                first = next(iter(snaps.values()))
                self.runtime.update(price=first["close"], price_time=utc_now_iso())
        try:
            d = ai.decide(self._ai_round_context(snaps))
        except ai.AIError as exc:
            self.log(f"AI unavailable this round ({exc}). Holding; the stop still protects any position.", "warn")
            with self.lock:
                self.state["last_candle"] = candle
            self.save()
            return
        with self.lock:
            self.state["ai"] = {"time": utc_now_iso(), "action": d.action, "symbol": d.symbol,
                                "confidence": d.confidence, "size_pct": d.size_pct, "stop_atr": d.stop_atr,
                                "reason": d.reason, "outlook": d.outlook}
            self.state["last_candle"] = candle
        self._apply_decision(d, snaps)
        if failed:
            self.log(f"Couldn't load {', '.join(failed)} this round; decided without them.", "warn")
        self.save()

    def _safe_snapshot(self, symbol: str):
        try:
            return self._snapshot(symbol)
        except Exception as exc:  # one bad market shouldn't stop the round
            return exc

    def _apply_decision(self, d, snaps: dict) -> None:
        cfg = self.config
        with self.lock:
            pos = self.state["position"]
            held = (pos.get("symbol") or cfg["symbol"]) if pos else None
        label = f"AI {d.action.upper()}{' ' + d.symbol if d.symbol else ''} ({d.confidence}% sure): {d.reason}"
        if d.action == "hold" or (d.action == "buy" and d.symbol == held):
            self.log(label)
            return
        if d.action == "sell":
            if held:
                self._sell(f"AI sell ({d.confidence}%): {d.reason}"[:300])
            else:
                self.log(label)
            return
        # buy
        if d.symbol not in snaps:
            self.log(f"{label} - ignored: {d.symbol or 'that market'} isn't on the watchlist.", "warn")
            return
        if d.confidence < AI_MIN_CONFIDENCE:
            self.log(f"{label} - not executed: below the {AI_MIN_CONFIDENCE}% confidence needed to buy.", "warn")
            return
        cap, today = float(cfg["daily_loss_cap"]), self.today_pnl()
        if cap > 0 and today <= -cap:
            self.log(f"{label} - not executed: today's loss ({fmt(today)}) hit the daily cap of {fmt(cap)}.", "warn")
            return
        if held:
            self._sell(f"AI switching to {d.symbol}")
            if self.state["position"]:
                self.log("Couldn't fully sell the old position, so the switch is postponed.", "warn")
                return
        snap = snaps[d.symbol]
        quote = round(float(cfg["trade_size"]) * d.size_pct / 100, 2)
        self.log(label)
        self._buy(snap["atr"], Params(), f"AI buy ({d.confidence}%, {d.size_pct}% size)", symbol=d.symbol,
                  quote=quote, stop_mult=d.stop_atr)

    def active_symbol(self) -> str:
        pos = self.state.get("position")
        return (pos.get("symbol") if pos else None) or self.config["symbol"]

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
            decision = copy.deepcopy(st.get("ai"))
            if is_ai(cfg):
                status = (f"Holding {pos.get('symbol') or cfg['symbol']}. Claude reviews the watchlist at every new "
                          f"{cfg['timeframe']} candle; the trailing stop is checked every minute." if pos else
                          f"In cash, watching {', '.join(bot_symbols(cfg))}. Claude decides at every new "
                          f"{cfg['timeframe']} candle.")
            elif pos:
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
                "id": self.id, "created": self.created, "config": cfg, "ai_decision": decision,
                "active_symbol": (pos.get("symbol") if pos else None) or cfg["symbol"],
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
        with self.lock:
            symbol = self.active_symbol()
        candles = self._chart_market.fetch_candles(symbol, cfg["timeframe"], limit=max(300, warmup_bars(p) + 150))
        if not candles:
            raise MarketError(f"No price history for {symbol}.")
        ind = compute(candles, p)
        n = min(150, len(candles))
        start_ms = candles[-n][0]
        with self.lock:
            pos = copy.deepcopy(self.state["position"])
            markers = [
                {"t": int(datetime.fromisoformat(t["time"]).timestamp() * 1000), "side": t["side"], "price": t["price"]}
                for t in self.state["trades"]
                if t.get("mode") == cfg["mode"] and t.get("symbol", cfg["symbol"]) == symbol
            ]
        data = {
            "candles": [c[:5] for c in candles[-n:]],
            "fast": ind["fast"][-n:], "slow": ind["slow"][-n:],
            "markers": [mk for mk in markers if mk["t"] >= start_ms],
            "stop": pos["stop"] if pos else None,
            "entry": pos["entry_price"] if pos else None,
            "timeframe": cfg["timeframe"], "symbol": symbol,
        }
        self._chart_cache = (time.time(), data)
        return data


class BusyError(MarketError):
    pass


class BotManager:
    def __init__(self):
        self.bots: dict[str, Bot] = {}
        if CLOUD:
            return
        BOTS_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(BOTS_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                self.bots[d["id"]] = Bot(d["id"], d["config"], d.get("state"), d.get("created"))
            except Exception as exc:
                print(f"Skipping unreadable bot file {f.name}: {exc}")

    # ------------------------------------------------------------------ cloud (website) mode

    def _load_cloud(self) -> None:
        self.bots = {bid: Bot(bid, d["config"], d.get("state"), d.get("created"), d.get("runtime"))
                     for bid, d in store.load_bots().items()}

    def _flush_cloud(self, extra: list | None = None) -> None:
        dirty = {b.id: b.doc() for b in self.bots.values() if b._dirty}
        store.save_bots(dirty, extra)
        for b in self.bots.values():
            b._dirty = False

    @contextlib.contextmanager
    def session(self, write: bool = False):
        """Wrap each web request. On the website: load bots fresh, and for changes hold the lock
        (so a change can't collide with the minute-by-minute check) and save afterwards."""
        if not CLOUD:
            yield
            return
        token = store.acquire("bots", ttl_s=50, wait_s=20) if write else None
        if write and token is None:
            raise BusyError("The bots are busy with their scheduled check. Try again in a few seconds.")
        try:
            self._load_cloud()
            yield
            if write:
                self._flush_cloud()
        finally:
            if token:
                store.release("bots", token)

    def cron_tick(self) -> dict:
        """Called by the scheduler once a minute on the website: one check for every running bot."""
        now = utc_now_iso()
        token = store.acquire("bots", ttl_s=290, wait_s=5)
        if token is None:
            return {"ok": False, "detail": "Previous check still running."}
        AI_DEADLINE[0] = time.time() + 150  # leave room for one AI decision within the 300 s limit
        try:
            self._load_cloud()
            running = [b for b in self.bots.values() if b.running]
            for bot in running:
                bot.check_once()
                bot._dirty = True
            self._flush_cloud([["SET", "trendbot:cron_last", now]])
            return {"ok": True, "checked": len(running), "time": now}
        finally:
            AI_DEADLINE[0] = None
            store.release("bots", token)

    # ------------------------------------------------------------------ both modes

    def resume(self) -> None:
        if CLOUD:
            return
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
        held = bot.active_symbol() if bot.state["position"] else None
        if held and (any(config[k] != bot.config[k] for k in ("exchange", "mode")) or held not in bot_symbols(config)):
            raise MarketError("This bot holds a position. Close it before changing the exchange, mode or the "
                              "market it holds.")
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
                shared = set(bot_symbols(oc)) & set(bot_symbols(cfg))
                if other is not bot and other.running and shared and (oc["exchange"], oc["mode"]) == \
                        (cfg["exchange"], cfg["mode"]):
                    raise MarketError(f"Bot '{oc['name']}' already trades {', '.join(sorted(shared))} on this "
                                      "account. Two bots selling the same coins would interfere.")
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
        if CLOUD:
            store.delete_bot(bot_id)
        else:
            bot.path.unlink(missing_ok=True)
