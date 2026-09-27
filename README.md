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
- **Markets scanner:** ranks a dozen coins (or stocks) by how well the strategy did on each.
- **Optional Claude AI:** plain-language reviews of backtests, scans and bots, plus an optional
  *AI check before each buy* that can skip trades that look like false starts. The AI can only skip
  buys; the safety limits above stay in charge. Needs your own Anthropic API key (a few cents per use).

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
3. **Optional testnet:** connect a testnet account in **Setup**, switch the bot to *Testnet* and check that
   real orders go through.
4. **Live, small:** only if you're comfortable. Connect your real account in Setup, stop the bot, *Edit* → mode *Live*,
   type `LIVE` to confirm, then Start. Keep the trade size small.

## Connecting your exchange accounts (only for testnet and live)

Binance, Bybit and Alpaca don't let personal apps log in with your exchange password. Instead you make
a **trading key** for TrendBot: it can place trades but can't withdraw money, and you can delete it on
the exchange at any time to cut TrendBot off.

1. Open the **Setup** tab and press **Connect** next to the account (for example *Binance → Testnet*).
2. Follow the steps in the window to create the key on the exchange, and paste the key and secret.
3. Press **Check & connect**. TrendBot logs in to check the key and shows your balance.
   It **refuses Binance and Bybit keys that allow withdrawals**.

Connected keys are saved only on your computer (`data/accounts.json`, never uploaded) and are never
shown again. **Disconnect** makes TrendBot forget a key; also delete it on the exchange to revoke it.
For safety, accounts can only be connected on the computer running TrendBot (or over https), not
from your phone over home Wi-Fi.

| Exchange | Practice keys | Real-money keys |
|---|---|---|
| Binance | [testnet.binance.vision](https://testnet.binance.vision/) → log in with GitHub → *Generate HMAC_SHA256 Key* | Binance → Account → API Management. Tick *Enable Spot & Margin Trading* only. |
| Bybit | [testnet.bybit.com](https://testnet.bybit.com/) → API | Bybit → Account → API. *Read-Write*, Spot trade only, no Withdraw. |
| Alpaca | Free paper account at [alpaca.markets](https://alpaca.markets/) → API keys (also needed for stock *paper* mode) | Alpaca live account → API keys |

Advanced: you can still put keys in a `.env` file instead (copy `.env.example`). Keys in `.env` take
priority over connected accounts.

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

## Run everything from the website (free)

The Vercel website can be the whole app: bots, accounts and AI, with no PC needed. It uses two free services:

1. **A free database (Upstash Redis)** to remember your bots and trades. In the Vercel dashboard:
   *trendbot project → Storage → Create Database → Upstash for Redis → Free → Connect to project*.
   Then redeploy (*Deployments → ⋯ → Redeploy*). Until a database is connected, the website only backtests.
2. **A free timer (cron-job.org)** that wakes the site every minute, so bots check prices around the clock.
   After logging in, the website's **Setup → Bot timer** card shows the exact link to paste and turns green when it works.

**Sign in with Google (recommended):** set `GOOGLE_CLIENT_ID` (a Google Cloud OAuth client for this
site) and `ALLOWED_EMAILS` (your Google address) in the Vercel project. Then only those Google accounts can
get in, and no password is needed.

Without Google sign-in, on first visit the website asks for a **setup code** (the `TRENDBOT_SETUP_CODE` value in the Vercel project's
environment variables) and lets you create your password. Exchange and AI keys are stored encrypted with
`TRENDBOT_SECRET`; don't change that value afterwards, or saved keys can't be read.

Free-plan limits: Upstash's free tier comfortably covers a few bots checked every minute. The website
polls less often than the PC app to stay inside it.

## Run it in the cloud 24/7 (optional)

Running on your PC is free, but the bots stop when the PC sleeps. To keep them running all the time,
put TrendBot on a cloud server. The repository is ready for [Render](https://render.com):

1. Make a Render account and connect your GitHub account to it.
2. In Render choose **New → Blueprint** and pick the TrendBot repository. It reads `render.yaml`
   and sets everything up: a server in **Frankfurt** (Binance and Bybit block US servers), a small
   disk that keeps your bots and trade history, and the settings below.
3. It asks for **BOT_UI_PASSWORD**. Pick a long, random password (16+ characters) that you don't use
   anywhere else, because this page is on the public internet. Leave the key fields empty: start
   with paper trading, and connect accounts later on the app's **Setup** page.
4. After it deploys, Render shows your link, e.g. `https://trendbot-xxxx.onrender.com`. Open it,
   log in, and add it to your phone's home screen.

Cost: it needs a paid instance (Render's cheapest always-on plan with a disk, a few dollars a month;
check their current prices). Free instances fall asleep when nobody is looking, which would stop the bots.

Safety in the cloud: your exchange keys sit on Render's servers, so live keys must allow **spot
trading only, never withdrawals**. Also use the exchange's IP whitelist if it has one: Render lists
its outbound IP addresses under the service's *Connect → Outbound* tab.

Don't run the same bot on your PC and in the cloud at the same time. They'd trade the same money twice.

## Good to know

- **Don't trade the same coin by hand** on the same account while a live bot holds it. The bot only
  sells what it bought, but manual trades confuse its bookkeeping.
- The trailing stop is checked about once a minute. In a sudden crash the sale can happen below the stop.
- **Stop** keeps any open position; it just stops watching it. Use **Sell now** to exit immediately.
- Your bots and their trade history are stored in the `data` folder (never uploaded).
- Profits from trading may be taxable where you live. The trades table lists every fill.

## Changing the web app

The web app is written in React (in `frontend/`). The ready-built copy lives in `web/`, so running
TrendBot never needs Node.js. To change the app, install [Node.js](https://nodejs.org/), then:

```
cd frontend
npm install
npm run dev      # live preview at http://localhost:5173 (start TrendBot too, for the data)
npm run build    # writes the finished app into ../web
```

Commit both `frontend/` and `web/`. Pushing to `main` updates the online site automatically.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python 3 was not found" | Install Python from python.org with "Add python.exe to PATH" ticked. |
| Package install fails | Check the internet connection. Delete the `.venv` folder and run `start.bat` again. |
| "Something is already using port 8765" | TrendBot is already open in another window. Use that one, or close it first. |
| Backtest or bot says it can't reach Binance/Bybit | Check your internet. Some countries/networks block an exchange; try the other one. |
| "isn't connected yet" | Connect that account on the **Setup** page. |
| Changed `.env` but nothing happened | Close the TrendBot window and start it again; `.env` is read at startup. |

## For the curious: what's inside

```
Dockerfile         Cloud server image; render.yaml sets it up on Render
start.bat          Windows launcher (creates .venv, installs packages, runs run.py)
run.py             Starts the web server (options: --host, --port, --no-browser)
app/strategy.py    The trading rules and the backtester (same rules for both)
app/engine.py      Runs each bot once a minute and saves its state in data/bots/
app/ai.py          Optional Claude AI: pre-trade check and plain-language reviews
app/exchanges.py   Binance/Bybit (via ccxt), Alpaca, and the paper-trading simulator
app/server.py      The web API the page talks to
frontend/          The web app's source (React + Vite; installable as an app)
web/               The built web app the server serves (made by `npm run build` in frontend/)
tools/             make_icons.py redraws the app icons
```
