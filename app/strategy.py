"""Trend-following strategy: EMA cross entries, EMA cross-down or ATR trailing-stop exits.

Optional filters, tested on 5 years of Binance data for 8 major coins, split into two halves:
- trend filter (default on): only buy while the price is above its 200-candle EMA. It improved
  risk-adjusted results on most coins in both halves, on every candle size, and cut drawdowns.
- re-entry: while the fast EMA is above the slow one, a close above the previous 20 candles' closes
  re-enters, so a trend isn't missed after a stop-out. It helped on 1d candles but not on 4h.
- trend strength (ADX): only buy when ADX(14) is at least a minimum (e.g. 20). With the trend filter
  it gave the steadiest results on 4h candles (drawdowns roughly halved).

The live engine and the backtester both use `compute`, `entry_signal` and `exit_signal`
so a backtest trades exactly the rules the bot trades.
Candles are [open_time_ms, open, high, low, close, volume].
"""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Params:
    fast: int = 20
    slow: int = 50
    atr_period: int = 14
    atr_mult: float = 3.0
    trend_filter: bool = True
    reentry: bool = False
    adx_min: float = 0.0

    @classmethod
    def from_config(cls, cfg: dict) -> "Params":
        return cls(int(cfg["fast"]), int(cfg["slow"]), int(cfg["atr_period"]), float(cfg["atr_mult"]),
                   bool(cfg.get("trend_filter", True)), bool(cfg.get("reentry", False)),
                   float(cfg.get("adx_min", 0) or 0))


def ema(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else prev + k * (v - prev)
        out.append(prev)
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    """Wilder's average true range."""
    out, prev = [], None
    for i in range(len(closes)):
        if i == 0:
            tr = highs[0] - lows[0]
        else:
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        prev = tr if prev is None else prev + (tr - prev) / period
        out.append(prev)
    return out


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Wilder's average directional index: how strong the trend is (0-100), whatever its direction."""
    def wilder(xs):
        out, prev = [], None
        for x in xs:
            prev = x if prev is None else prev + (x - prev) / period
            out.append(prev)
        return out
    plus, minus, tr = [0.0], [0.0], [highs[0] - lows[0]] if highs else []
    for i in range(1, len(closes)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    dx = []
    for a, pl, mi in zip(wilder(tr), wilder(plus), wilder(minus)):
        pdi, mdi = (100 * pl / a, 100 * mi / a) if a else (0.0, 0.0)
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    return wilder(dx)


def warmup_bars(p: Params) -> int:
    """Bars needed before the EMAs/ATR are trustworthy."""
    return max(p.slow, p.atr_period, TREND_EMA if p.trend_filter else 0) * 2


TREND_EMA = 200
BREAKOUT_BARS = 20


def compute(candles: list, p: Params) -> dict:
    closes = [c[4] for c in candles]
    return {
        "fast": ema(closes, p.fast),
        "slow": ema(closes, p.slow),
        "atr": atr([c[2] for c in candles], [c[3] for c in candles], closes, p.atr_period),
        "trend": ema(closes, TREND_EMA) if p.trend_filter else None,
        "adx": adx([c[2] for c in candles], [c[3] for c in candles], closes) if p.adx_min else None,
        "close": closes,
        "p": p,
    }


def entry_signal(ind: dict, i: int) -> bool:
    """Buy on candle i: a fresh fast-over-slow EMA cross (or, with re-entry, a 20-candle closing high
    while the fast EMA is above the slow one) - and, with the trend filter, only above the 200 EMA."""
    f, s, c, p = ind["fast"], ind["slow"], ind["close"], ind["p"]
    if i < 1:
        return False
    signal = f[i - 1] <= s[i - 1] and f[i] > s[i]
    if not signal and p.reentry and i >= BREAKOUT_BARS:
        signal = f[i] > s[i] and c[i] > max(c[i - BREAKOUT_BARS:i])
    return (signal and (not p.trend_filter or c[i] > ind["trend"][i])
            and (not p.adx_min or ind["adx"][i] >= p.adx_min))


def exit_signal(ind: dict, i: int) -> bool:
    """Fast EMA is below slow EMA (state-based, so a missed candle can't skip the exit)."""
    return ind["fast"][i] < ind["slow"][i]


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _max_drawdown(values: list[float]) -> float:
    peak, worst = values[0] if values else 0, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def backtest(candles: list, p: Params, trade_size: float, fee: float, daily_loss_cap: float,
             start_ts: int | None = None, max_points: int = 600) -> dict:
    """Simulate the live rules candle by candle.

    Each trade spends a fixed `trade_size` (like the live bot) - that gives the $ P&L and the
    daily loss cap. The equity curve compounds each trade's % return from 100, so it is directly
    comparable to buying and holding 100 over the same period.
    """
    ind = compute(candles, p)
    fast, slow, atrs = ind["fast"], ind["slow"], ind["atr"]
    start = warmup_bars(p)
    if start_ts is not None:
        start = max(start, next((i for i, c in enumerate(candles) if c[0] >= start_ts), len(candles)))
    if start >= len(candles) - 1:
        raise ValueError("Not enough price history for this period - try more days or a shorter EMA.")

    trades, pos = [], None
    day_pnl: dict[str, float] = {}
    skipped = 0
    fees_paid = 0.0
    comp_cash, comp_qty = 100.0, 0.0
    first_close = candles[start][4]
    hold_qty = 100.0 * (1 - fee) / first_close
    curve = []
    bars_in_market = 0

    def close_position(i, price, reason):
        nonlocal pos, comp_cash, comp_qty, fees_paid
        gross = pos["qty"] * price
        exit_fee = gross * fee
        proceeds = gross - exit_fee
        pnl = proceeds - trade_size
        fees_paid += exit_fee
        day = _day(candles[i][0])
        day_pnl[day] = day_pnl.get(day, 0.0) + pnl
        comp_cash = comp_qty * price * (1 - fee)
        comp_qty = 0.0
        trades.append({
            "entry_time": pos["entry_time"], "entry_price": pos["entry_price"],
            "exit_time": candles[i][0], "exit_price": price, "reason": reason,
            "pnl": pnl, "pnl_pct": pnl / trade_size * 100,
        })
        pos = None

    for i in range(start, len(candles)):
        ts, o, h, low, c, _ = candles[i]
        if pos:
            bars_in_market += 1
            # Stop is checked against this candle before the candle's high can raise it.
            if o <= pos["stop"]:
                close_position(i, o, "Trailing stop (gap)")
            elif low <= pos["stop"]:
                close_position(i, pos["stop"], "Trailing stop")
            else:
                pos["high"] = max(pos["high"], h)
                pos["stop"] = max(pos["stop"], pos["high"] - p.atr_mult * atrs[i])
                if exit_signal(ind, i):
                    close_position(i, c, "EMA cross down")
        elif entry_signal(ind, i):
            if daily_loss_cap > 0 and day_pnl.get(_day(ts), 0.0) <= -daily_loss_cap:
                skipped += 1
            else:
                entry_fee = trade_size * fee
                fees_paid += entry_fee
                pos = {
                    "entry_time": ts, "entry_price": c, "qty": (trade_size - entry_fee) / c,
                    "high": c, "stop": c - p.atr_mult * atrs[i],
                }
                comp_qty = comp_cash * (1 - fee) / c
                comp_cash = 0.0
        strat_value = comp_cash + comp_qty * c
        curve.append((ts, strat_value, hold_qty * c, c, fast[i], slow[i]))

    open_position = None
    if pos:
        last = candles[-1][4]
        open_position = {
            "entry_time": pos["entry_time"], "entry_price": pos["entry_price"], "stop": pos["stop"],
            "unrealized": pos["qty"] * last * (1 - fee) - trade_size,
        }

    strat_vals = [pt[1] for pt in curve]
    hold_vals = [pt[2] for pt in curve]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    step = max(1, len(curve) // max_points)
    sampled = curve[::step]
    if sampled[-1] is not curve[-1]:
        sampled.append(curve[-1])

    return {
        "start": candles[start][0], "end": candles[-1][0], "bars": len(curve),
        "strategy_return_pct": strat_vals[-1] - 100,
        "hold_return_pct": hold_vals[-1] - 100,
        "strategy_max_dd_pct": _max_drawdown(strat_vals) * 100,
        "hold_max_dd_pct": _max_drawdown(hold_vals) * 100,
        "trades_count": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else None,
        "avg_win_pct": (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else None,
        "avg_loss_pct": (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else None,
        "total_pnl": sum(t["pnl"] for t in trades),
        "fees_paid": fees_paid,
        "skipped_by_daily_cap": skipped,
        "time_in_market_pct": bars_in_market / len(curve) * 100 if curve else 0,
        "open_position": open_position,
        "trades": trades,
        "curve": [
            {"t": t, "strategy": round(s, 4), "hold": round(hd, 4), "price": pr, "fast": f, "slow": sl}
            for t, s, hd, pr, f, sl in sampled
        ],
    }
