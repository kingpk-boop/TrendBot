# TrendBot

A trading bot that runs on your own computer and follows price trends on **Binance**, **Bybit**
(crypto) or **Alpaca** (US stocks). You control it from a web page in your browser or on your phone.

> **Please read first:** TrendBot does not guarantee profit. Trend following wins when prices move
> strongly in one direction and loses, usually in small amounts, when prices go sideways. Backtests
> show how the rules did in the past, not what will happen next. Start with paper trading, and only
> ever trade money you can afford to lose.

## What it does

- Watches one market per bot, for example BTC/USDT on 4-hour candles.
- **Buys** when the 20-candle average price (fast EMA) crosses above the 50-candle average (slow EMA),
  which is a sign that an uptrend is starting.
- **Sells** when the fast EMA drops back below the slow EMA, or earlier if the price falls to a
  *trailing stop* (3 × ATR below the highest price since buying). The stop only ever moves up.
- Safety limits: a fixed amount per trade (default **20 USDT**), a **daily loss cap** (default
  **10 USDT**; no new buys after that much loss in a day), spot only (no leverage, no shorting).
- Three modes:
  - **Paper** (the default): pretend money against real live prices. No account or keys needed for crypto.
  - **Testnet**: real orders on the exchange's practice site, with fake balances.
  - **Live**: real money. You have to type `LIVE` to switch a bot to it.
- Picks up where it left off if the computer restarts: running bots start again automatically.

## Start it (Windows)

You need Python 3 installed. If you don't have it, get it from
[python.org/downloads](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"**
during install.

1. Get the latest code. In the `Documents\TrendBot` folder, open a terminal
   (right-click in the folder → *Open in Terminal*) and run:
   ```
   git pull
   ```
2. **Double-click `start.bat`.** The first time, it sets up a private Python environment and installs
   what it needs (takes a minute or two). After that it starts straight away.
3. Your browser opens **http://localhost:8765**. If it doesn't, type that address in yourself.

Keep the black TrendBot window open. **Closing it stops the bots.** Bots only trade while it's running
and the computer is awake. To run 24/7, set Windows to never sleep when plugged in
(*Settings → System → Power & battery*).

## Suggested first steps

1. **Backtest:** open the **Backtest** tab, keep the defaults (Binance, BTC/USDT, 4h, 2 years) and
   press *Run backtest*. Look at the *worst drop* and the losing trades, not only the return.
2. **Paper trade:** press *Create bot from these settings*, then **Start**. Let it run for a few
   weeks. Signals are rare: on 4-hour candles there may be only a couple of trades a month.
3. **Optional testnet:** make practice keys (see below), switch the bot to *Testnet* and check that
   real orders go through.
4. **Live, small:** only if you're comfortable. Add live keys, stop the bot, *Edit* → mode *Live*,
   type `LIVE` to confirm, then Start. Keep the trade size small.

## API keys (only for testnet and live)

TrendBot reads keys from a file called `.env` in the TrendBot folder. The app never shows them, and
`.env` is excluded from git, so it never gets uploaded.

1. Copy `.env.example` and rename the copy to `.env`.
2. Open `.env` in Notepad and paste each key after its `=` sign, for example:
   ```
   BINANCE_TESTNET_API_KEY=abc123...
   BINANCE_TESTNET_API_SECRET=def456...
   ```
3. Save the file, close the TrendBot window and double-click `start.bat` again.
   The **Setup** tab shows "✓ set" for keys it found.

Where to get keys:

| Exchange | Practice (testnet) keys | Real-money keys |
|---|---|---|
| Binance | [testnet.binance.vision](https://testnet.binance.vision/) → log in with GitHub → *Generate HMAC_SHA256 Key* | Binance → Account → API Management |
| Bybit | [testnet.bybit.com](https://testnet.bybit.com/) → API | Bybit → Account → API |
| Alpaca | Free paper account at [alpaca.markets](https://alpaca.markets/) → API keys (also needed for stock *paper* mode) | Alpaca live account → API keys |

**Real-money keys must allow spot trading only.** Never tick *withdrawals*, *futures* or *margin*.
If the exchange lets you restrict the key to your IP address, do it.

## Use it from your phone

1. Add a password line to `.env`: `BOT_UI_PASSWORD=pick-a-long-password`
2. Start TrendBot open to your home network. In a terminal in the TrendBot folder run:
   ```
   start.bat --host 0.0.0.0
   ```
   The window shows an address like `http://192.168.1.20:8765`.
3. On your phone (same Wi-Fi), open that address, log in, then *Share → Add to Home Screen* (iPhone)
   or *⋮ → Add to Home screen* (Android). If Windows asks whether Python may use the network,
   allow **private networks** only.

Don't open TrendBot to the internet (no port forwarding on your router).

On the PC you can also install it like an app: in Chrome or Edge, *⋮ → Install TrendBot*.

## Good to know

- **Don't trade the same coin by hand** on the same account while a live bot holds it. The bot only
  sells what it bought, but manual trades confuse its bookkeeping.
- The trailing stop is checked about once a minute. In a sudden crash the sale can happen below the stop.
- **Stop** keeps any open position; it just stops watching it. Use **Sell now** to exit immediately.
- Your bots and their trade history are stored in the `data` folder (never uploaded).
- Profits from trading may be taxable where you live. The trades table lists every fill.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python 3 was not found" | Install Python from python.org with "Add python.exe to PATH" ticked. |
| Package install fails | Check the internet connection. Delete the `.venv` folder and run `start.bat` again. |
| "Something is already using port 8765" | TrendBot is already open in another window. Use that one, or close it first. |
| Backtest or bot says it can't reach Binance/Bybit | Check your internet. Some countries/networks block an exchange; try the other one. |
| "needs API keys" | Add the keys named in the message to `.env` and restart. |
| Changed `.env` but nothing happened | Close the TrendBot window and start it again; `.env` is read at startup. |

## For the curious: what's inside

```
start.bat          Windows launcher (creates .venv, installs packages, runs run.py)
run.py             Starts the web server (options: --host, --port, --no-browser)
app/strategy.py    The trading rules and the backtester (same rules for both)
app/engine.py      Runs each bot once a minute and saves its state in data/bots/
app/exchanges.py   Binance/Bybit (via ccxt), Alpaca, and the paper-trading simulator
app/server.py      The web API the page talks to
web/               The web app (plain HTML, CSS, JavaScript; installable as an app)
tools/             make_icons.py redraws the app icons
```
