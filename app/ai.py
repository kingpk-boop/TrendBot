"""Claude-powered features: the AI Autopilot trader, a pre-trade check for rule bots, and reviews.

AI Autopilot bots let Claude decide what to buy, when to sell and how much to use, across a watchlist.
Hard limits stay in code and the AI can't change them: spot only (no leverage or shorting), at most the
bot's trade size per buy, a trailing stop on every position, and the daily loss cap.
"""
import json
import os
import time
from typing import Literal

import anthropic
from pydantic import BaseModel

from .config import load_stored_secret, remove_account, save_account

# Only Opus models, always at high effort. Opus 5.5 and Opus 5 take turns and each backs the other up if
# it's busy or unavailable. If both are unavailable the request fails and the bot simply holds that round.
PRIMARY_MODELS = ("claude-opus-5-5", "claude-opus-5")
MODEL = PRIMARY_MODELS[0]
EFFORT = "high"
MODEL_NAMES = {"claude-opus-5-5": "Claude Opus 5.5", "claude-opus-5": "Claude Opus 5"}
_turn = [0]  # which Opus model goes first next time (alternates)
PROVIDER = "anthropic"  # key in data/accounts.json


class AIError(Exception):
    """A problem the user can see and fix (no key, key rejected, network...)."""


# ---------------------------------------------------------------------------- key handling

def ai_key() -> tuple[str | None, str | None]:
    """(key, source) where source is 'env' or 'app'."""
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        return env, "env"
    stored = load_stored_secret(PROVIDER, "live")
    return (stored, "app") if stored else (None, None)


def ai_status() -> dict:
    return {"source": ai_key()[1], "model": "Claude Opus 5.5 / Opus 5 (high effort)"}


def _client(key: str | None = None) -> anthropic.Anthropic:
    key = key or ai_key()[0]
    if not key:
        raise AIError("AI isn't connected. Add an Anthropic API key on the Setup page.")
    return anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)


def _friendly(e: Exception) -> AIError:
    """A plain-language AIError for an SDK error."""
    if isinstance(e, anthropic.AuthenticationError):
        return AIError("Anthropic rejected the API key. Check it on console.anthropic.com and connect it again.")
    if isinstance(e, anthropic.PermissionDeniedError):
        return AIError("This Anthropic API key isn't allowed to use the model. Check your Anthropic account.")
    if isinstance(e, anthropic.RateLimitError):
        return AIError("Anthropic's rate limit was reached. Try again in a minute.")
    if isinstance(e, anthropic.BadRequestError):
        msg = str(getattr(e, "message", e))
        if "credit" in msg.lower() or "billing" in msg.lower():
            return AIError("Your Anthropic account is out of credit. Add credit on console.anthropic.com.")
        return AIError(f"The AI request was rejected: {msg[:200]}")
    if isinstance(e, anthropic.APIStatusError):
        return AIError(f"Anthropic error {e.status_code}. Try again later.")
    return AIError("Couldn't reach Anthropic. Check your internet connection.")


def _call(fn):
    """Run an API call and turn SDK errors into plain-language AIErrors."""
    try:
        return fn()
    except anthropic.APIError as e:
        raise _friendly(e) from None


def _could_other_model_help(e: anthropic.APIError) -> bool:
    if isinstance(e, anthropic.AuthenticationError):
        return False  # a bad key fails on every model
    if isinstance(e, (anthropic.RateLimitError, anthropic.NotFoundError, anthropic.PermissionDeniedError,
                      anthropic.APIConnectionError)):  # includes timeouts
        return True
    if isinstance(e, anthropic.BadRequestError):
        msg = str(getattr(e, "message", e)).lower()
        return any(w in msg for w in ("credit", "billing", "limit", "quota", "model"))
    return isinstance(e, anthropic.APIStatusError) and e.status_code >= 500  # overloaded / server error


def _run(make_request, budget_s: float = 280.0, turn: int | None = None):
    """Run a request on the model chain. make_request(model) -> response.
    Returns (response, model). Opus 5.5 and Opus 5 alternate who goes first (by `turn` when given)."""
    if turn is None:
        turn = _turn[0]
        _turn[0] += 1
    first = turn % 2
    chain = [PRIMARY_MODELS[first], PRIMARY_MODELS[1 - first]]
    start, reasons = time.time(), []
    for model in chain:
        if reasons and time.time() - start > budget_s - 60:
            break  # not enough time left for another attempt
        try:
            response = make_request(model)
        except anthropic.APIError as e:
            if not _could_other_model_help(e):
                raise _friendly(e) from None
            reasons.append(f"{MODEL_NAMES[model]}: {_friendly(e)}")
            continue
        if response.stop_reason == "refusal":  # let the other Opus model try; never switch to other models
            reasons.append(f"{MODEL_NAMES[model]} declined.")
            continue
        return response, model
    raise AIError("Neither Claude Opus model was available right now. " + " ".join(reasons))


def connect(key: str) -> None:
    """Check the key with a free call (model lookup), then store it on this computer."""
    key = key.strip()
    _call(lambda: _client(key).models.retrieve(PRIMARY_MODELS[1]))
    save_account(PROVIDER, "live", key, "")


def disconnect() -> None:
    remove_account(PROVIDER, "live")


# ---------------------------------------------------------------------------- pre-trade check

class TradeReview(BaseModel):
    decision: Literal["buy", "skip"]
    confidence: int
    reason: str


REVIEW_SYSTEM = """You are the risk filter inside TrendBot, a small spot trading bot that follows trends \
using an EMA crossover (buy when the fast EMA crosses above the slow EMA; exit on the cross back down or \
an ATR trailing stop). The strategy has just produced a BUY signal. Your only power is to let it through \
("buy") or veto it ("skip"). You cannot change the trade size or the stop, and a skipped signal is simply \
missed - the bot waits for the next one.

Judge whether this signal looks like the start of a real trend or a likely false breakout. Useful evidence:
- how extended price already is (distance above the slow EMA measured in ATRs; very stretched entries often fail),
- whether the longer trend agrees (the long EMA and 30-day change),
- volatility (ATR as % of price; spikes after crashes produce whipsaws),
- this bot's recent trades on this market (repeated recent losses suggest a choppy market),
- the shape of recent closes (clean higher highs vs. a spike).

Trend following makes its money from a few large winners, and skipping those is costly, so only skip when the \
evidence against the trade is clear. When unsure, choose "buy" - the trailing stop limits the loss. \
Give confidence 0-100 in your decision and one or two plain sentences of reasoning a beginner can follow. \
Never claim certainty about future prices."""


def review_entry(context: dict) -> TradeReview:
    client = _client().with_options(max_retries=0)
    response, _ = _run(lambda model: client.beta.messages.parse(
        model=model,
        max_tokens=16000,
        system=REVIEW_SYSTEM,
        messages=[{"role": "user", "content": "Market data at the moment of the buy signal:\n"
                   + json.dumps(context, separators=(",", ":"))}],
        output_format=TradeReview,
        output_config={"effort": EFFORT},
    ))
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise AIError("The AI declined to review this trade.")
    review = response.parsed_output
    review.confidence = max(0, min(100, int(review.confidence)))
    return review


# ---------------------------------------------------------------------------- AI Autopilot

class TradeDecision(BaseModel):
    action: Literal["buy", "sell", "hold"]
    symbol: str          # for "buy": which watchlist market; otherwise the held one or ""
    size_pct: int        # for "buy": % of the bot's maximum trade size to use (10-100)
    stop_atr: float      # "buy": trailing stop distance in ATRs (1.5-5); "hold" while holding: tighten to this (0 = keep)
    confidence: int      # 0-100
    reason: str
    outlook: str         # one sentence on the market overall


AUTOPILOT_SYSTEM = """You are the trader inside TrendBot's AI Autopilot: a spot trading bot that manages one position at a time for its owner, choosing among a small watchlist of markets. Once per candle you receive fresh data for every market on the watchlist, the current position (if any), the risk budget and the bot's recent trades, and you decide the single next action:

- "buy": open a position in one watchlist market (set symbol, size_pct 10-100 = share of the maximum trade size, stop_atr 1.5-5 = trailing stop distance in ATRs). If a position is already open in a different market, buying means selling it first and switching - only do that when the new market is clearly better, because every switch pays fees twice.
- "sell": close the current position and wait in cash.
- "hold": keep the current position, or keep waiting in cash. While holding you may also tighten the trailing stop by setting stop_atr (1-5): the stop moves up to (highest price since buying - stop_atr x ATR) if that is higher than the current stop. The code never loosens a stop. Use it to lock in gains after a strong run or when momentum fades; set stop_atr 0 to leave the stop as it is.

The code enforces the hard limits and you cannot change them: spot only (no leverage, no shorting), the maximum trade size, a trailing stop on every position (checked every minute), and the daily loss cap. Buys below 60 confidence are not executed.

What you get for each market: trend (EMA 20/50/200 and their slopes), trend strength (ADX14; above ~20-25 means a real trend), momentum (RSI14, MACD histogram and whether it's rising), volatility (ATR %, Bollinger width), how stretched price is (distance above the 20 EMA in ATRs, drawdown from the 90-candle high), volume vs its average, performance vs BTC, a daily-candle view (the longer trend) and "tested_rules": what the bot's backtested trend rules say right now. Across markets you get the breadth (how many are above their 200 EMA - a weak market lifts few boats) and your own track record per market.

Evidence from 5-year backtests on these coins: buying only above the 200 EMA, and when ADX shows a real trend, clearly improved results and roughly halved drawdowns; buying in downtrends was the main source of losses. Treat tested_rules as a strong, well-tested prior and go against it only with clear reasons.

How to decide - aim for the best risk-adjusted growth of the owner's money, not for activity:
- Cash is a position. Most of the time the right answer is "hold". Trade when the evidence lines up.
- Prefer markets in established uptrends (price above a rising 50 and 200 EMA, higher highs) with reasonable volatility, and enter on pullbacks or fresh breakouts rather than after a big vertical move (distance above the 20 EMA of more than ~2-3 ATRs or RSI above ~75 is stretched).
- Avoid falling markets (price below a falling 200 EMA), and be quicker to sell when a held market loses its trend (closes below the 50 EMA with momentum rolling over) - protect gains and cut losers early.
- Size down (size_pct 25-50) when signals are mixed or volatility is high; use a wider stop (3-4 ATR) for strong trends you want to ride and a tighter one (2 ATR) for short-term setups.
- Learn from the recent trades: repeated losses in one market mean it's choppy - stand aside there.
- Be honest about uncertainty. Never claim certainty about future prices.

Write "reason" in one to three plain sentences a beginner can follow, citing the numbers that drove the decision. Write "outlook" as one sentence on the watchlist overall. For "sell"/"hold" set symbol to the held market (or "" when in cash) and size_pct to 0; stop_atr is 0 unless you are tightening the stop."""


def decide(context: dict, budget_s: float = 280.0, turn: int | None = None) -> tuple["TradeDecision", str]:
    """Returns (decision, model id that made it). `turn` picks which Opus model goes first."""
    client = _client().with_options(timeout=min(170.0, budget_s - 30), max_retries=0)
    response, model = _run(lambda model: client.beta.messages.parse(
        model=model,
        max_tokens=32000,
        system=AUTOPILOT_SYSTEM,
        messages=[{"role": "user", "content": "Current data (JSON):\n"
                   + json.dumps(context, separators=(",", ":"), default=str)}],
        output_format=TradeDecision,
        output_config={"effort": EFFORT},
    ), budget_s, turn)
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise AIError("The AI declined to decide this round.")
    d = response.parsed_output
    d.confidence = max(0, min(100, int(d.confidence)))
    d.size_pct = max(10, min(100, int(d.size_pct or 100)))
    if d.action == "buy":
        d.stop_atr = max(1.5, min(5.0, float(d.stop_atr or 3.0)))
    else:  # 0 = leave the stop alone; otherwise a request to tighten (applied only if tighter)
        d.stop_atr = 0.0 if not d.stop_atr or d.stop_atr <= 0 else max(1.0, min(5.0, float(d.stop_atr)))
    d.symbol = (d.symbol or "").strip().upper()
    return d, model


# ---------------------------------------------------------------------------- reviews for the web app

ANALYSIS_SYSTEM = """You are the analyst inside TrendBot, a beginner-friendly trend-following trading app \
(EMA crossover entries, EMA cross-down or ATR trailing-stop exits, spot only, fixed trade size). Explain \
results to a beginner in plain, friendly language.

Be honest and specific: cite the numbers you're given, say what looks good and what looks risky, and \
suggest concrete things to try in the app (another market, candle size, period, EMA lengths, stop \
distance, trade size, paper trading first). Point out overfitting risk when settings look tuned to the past. \
Never promise profit or predict prices; past results don't guarantee future ones.

Format: plain text only (no markdown headings, tables or bold). Use short paragraphs and lines starting \
with "- " for lists. Keep it under 250 words."""

PROMPTS = {
    "backtest": "Review this backtest result and tell me what it means and what to try next.",
    "scan": "These are backtests of the same strategy across several markets. Which look most promising "
            "for this strategy and why, which to avoid, and what should I do next?",
    "bot": "Review how this bot is doing: its current position, trend, trades and log. Anything I should "
           "change or watch?",
}


def analyze(kind: str, data: dict) -> str:
    if kind not in PROMPTS:
        raise AIError("Unknown analysis type.")
    client = _client().with_options(max_retries=0)
    response, _ = _run(lambda model: client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=ANALYSIS_SYSTEM,
        messages=[{"role": "user", "content": f"{PROMPTS[kind]}\n\nData (JSON):\n"
                   + json.dumps(data, separators=(",", ":"), default=str)}],
        output_config={"effort": EFFORT},
    ))
    if response.stop_reason == "refusal":
        raise AIError("The AI declined to answer this one.")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AIError("The AI returned an empty answer. Try again.")
    return text
