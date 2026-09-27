"""Claude-powered features: the AI Autopilot trader, a pre-trade check for rule bots, and reviews.

AI Autopilot bots let Claude decide what to buy, when to sell and how much to use, across a watchlist.
Hard limits stay in code and the AI can't change them: spot only (no leverage or shorting), at most the
bot's trade size per buy, a trailing stop on every position, and the daily loss cap.
"""
import json
import os
from typing import Literal

import anthropic
from pydantic import BaseModel

from .config import load_stored_secret, remove_account, save_account

MODEL = "claude-opus-5"
# Server-side refusal fallback: if the model declines, the API reroutes to another model in the same call.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
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
    return {"source": ai_key()[1], "model": MODEL}


def _client(key: str | None = None) -> anthropic.Anthropic:
    key = key or ai_key()[0]
    if not key:
        raise AIError("AI isn't connected. Add an Anthropic API key on the Setup page.")
    return anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)


def _call(fn):
    """Run an API call and turn SDK errors into plain-language AIErrors."""
    try:
        return fn()
    except anthropic.AuthenticationError:
        raise AIError("Anthropic rejected the API key. Check it on console.anthropic.com and connect it again.")
    except anthropic.PermissionDeniedError:
        raise AIError("This Anthropic API key isn't allowed to use the model. Check your Anthropic account.")
    except anthropic.RateLimitError:
        raise AIError("Anthropic's rate limit was reached. Try again in a minute.")
    except anthropic.BadRequestError as e:
        msg = str(getattr(e, "message", e))
        if "credit" in msg.lower() or "billing" in msg.lower():
            raise AIError("Your Anthropic account is out of credit. Add credit on console.anthropic.com.")
        raise AIError(f"The AI request was rejected: {msg[:200]}")
    except anthropic.APIStatusError as e:
        raise AIError(f"Anthropic error {e.status_code}. Try again later.")
    except anthropic.APIConnectionError:
        raise AIError("Couldn't reach Anthropic. Check your internet connection.")


def connect(key: str) -> None:
    """Check the key with a free call (model lookup), then store it on this computer."""
    key = key.strip()
    _call(lambda: _client(key).models.retrieve(MODEL))
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
    client = _client()
    response = _call(lambda: client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=REVIEW_SYSTEM,
        messages=[{"role": "user", "content": "Market data at the moment of the buy signal:\n"
                   + json.dumps(context, separators=(",", ":"))}],
        output_format=TradeReview,
        **FALLBACK,
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
    stop_atr: float      # for "buy": trailing stop distance in ATRs (1.5-5)
    confidence: int      # 0-100
    reason: str
    outlook: str         # one sentence on the market overall


AUTOPILOT_SYSTEM = """You are the trader inside TrendBot's AI Autopilot: a spot trading bot that manages one position at a time for its owner, choosing among a small watchlist of markets. Once per candle you receive fresh data for every market on the watchlist, the current position (if any), the risk budget and the bot's recent trades, and you decide the single next action:

- "buy": open a position in one watchlist market (set symbol, size_pct 10-100 = share of the maximum trade size, stop_atr 1.5-5 = trailing stop distance in ATRs). If a position is already open in a different market, buying means selling it first and switching - only do that when the new market is clearly better, because every switch pays fees twice.
- "sell": close the current position and wait in cash.
- "hold": keep the current position, or keep waiting in cash.

The code enforces the hard limits and you cannot change them: spot only (no leverage, no shorting), the maximum trade size, a trailing stop on every position (checked every minute), and the daily loss cap. Buys below 60 confidence are not executed.

How to decide - aim for the best risk-adjusted growth of the owner's money, not for activity:
- Cash is a position. Most of the time the right answer is "hold". Trade when the evidence lines up.
- Prefer markets in established uptrends (price above a rising 50 and 200 EMA, higher highs) with reasonable volatility, and enter on pullbacks or fresh breakouts rather than after a big vertical move (distance above the 20 EMA of more than ~2-3 ATRs or RSI above ~75 is stretched).
- Avoid falling markets (price below a falling 200 EMA), and be quicker to sell when a held market loses its trend (closes below the 50 EMA with momentum rolling over) - protect gains and cut losers early.
- Size down (size_pct 25-50) when signals are mixed or volatility is high; use a wider stop (3-4 ATR) for strong trends you want to ride and a tighter one (2 ATR) for short-term setups.
- Learn from the recent trades: repeated losses in one market mean it's choppy - stand aside there.
- Be honest about uncertainty. Never claim certainty about future prices.

Write "reason" in one to three plain sentences a beginner can follow, citing the numbers that drove the decision. Write "outlook" as one sentence on the watchlist overall. For "sell"/"hold" set symbol to the held market (or "" when in cash), size_pct to 0 and stop_atr to 0."""


def decide(context: dict) -> TradeDecision:
    client = _client()
    response = _call(lambda: client.with_options(timeout=150.0, max_retries=1).beta.messages.parse(
        model=MODEL,
        max_tokens=12000,
        system=AUTOPILOT_SYSTEM,
        messages=[{"role": "user", "content": "Current data (JSON):\n"
                   + json.dumps(context, separators=(",", ":"), default=str)}],
        output_format=TradeDecision,
        output_config={"effort": "medium"},
        **FALLBACK,
    ))
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise AIError("The AI declined to decide this round.")
    d = response.parsed_output
    d.confidence = max(0, min(100, int(d.confidence)))
    d.size_pct = max(10, min(100, int(d.size_pct or 100)))
    d.stop_atr = max(1.5, min(5.0, float(d.stop_atr or 3.0)))
    d.symbol = (d.symbol or "").strip().upper()
    return d


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
    client = _client()
    response = _call(lambda: client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=ANALYSIS_SYSTEM,
        messages=[{"role": "user", "content": f"{PROMPTS[kind]}\n\nData (JSON):\n"
                   + json.dumps(data, separators=(",", ":"), default=str)}],
        **FALLBACK,
    ))
    if response.stop_reason == "refusal":
        raise AIError("The AI declined to answer this one.")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AIError("The AI returned an empty answer. Try again.")
    return text
