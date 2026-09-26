"""Vercel entrypoint: the public, backtest-only TrendBot site (see BACKTEST_ONLY in app/config.py).
On your PC, use start.bat / run.py instead."""
from app.server import app  # noqa: F401
