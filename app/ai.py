"""Optional Claude-powered features: a pre-trade check for bots, and plain-language reviews.

The AI never gets more power than the rules already have: it can only *skip* a buy the strategy
wants to make. Trade size, the daily loss cap and the trailing stop are enforced in code, and the
AI cannot change them. If the AI is unavailable, bots follow the normal strategy.
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
