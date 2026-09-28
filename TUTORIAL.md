# TrendBot tutorial

TrendBot comes in two parts:

| | **PC app** (`start.bat` → http://localhost:8765) | **Online site** (Vercel) |
|---|---|---|
| Backtest | ✓ | ✓ from any device |
| Run bots | ✓ while the PC is on | ✗ |
| Connect exchange accounts | ✓ | ✗ (never on a public site) |
| Cost | free | free |

> TrendBot is a tool, not a money machine. It can and will lose on some trades, and nothing here
> guarantees profit. Start with paper trading, and only ever trade money you can afford to lose.

---

## 0. Or use only the website

If the website's database and timer are set up (see "Run everything from the website" in README.md), you
can skip the PC completely: open the website, create your password with the setup code, and follow
steps 2-5 there. Check that **Setup → Bot timer** is green, so your bots are checked every minute.

## 1. Start the PC app

1. Open the `Documents\TrendBot` folder.
2. To get the latest version: right-click in the folder → **Open in Terminal**, type `git pull`, press Enter.
3. Double-click **`start.bat`**. A black window opens and your browser shows TrendBot.
   **Keep the black window open.** Closing it stops the bots.

## 2. Backtest first

Open the **Backtest** tab. You can do this in the PC app or on the online site.

1. Keep the defaults: **Binance, BTC/USDT, 4h candles, 2 years**. Press **Run backtest**.
2. Read the tiles:
   - **Strategy return vs Buy & hold:** did the rules beat simply buying once and holding?
   - **Worst drop:** the biggest fall from a high point. This is how much pain to expect.
   - **Trades / % won:** trend following usually loses on more than half its trades and makes it back
     on a few big winners. That's normal.
3. The **Growth of 100** chart shows 100 invested with the strategy (blue) vs. buy & hold (grey).
4. Try other coins (`ETH/USDT`, `SOL/USDT`) or candle sizes. Be wary of settings tuned until the past
   looks perfect. They rarely work as well in the future.

## 2b. Find the best markets

Open **Markets** and press **Scan markets**. TrendBot backtests the strategy on 12 popular coins (or your
own list) and ranks them by **Score**: return divided by the worst drop, so steady gains rank above lucky,
bumpy ones. A **new buy signal** badge means the bot would have just bought that market. Use **Backtest**
on a row to look closer, or **Create bot** to paper trade it.

## 2c. Optional: connect Claude AI

In the PC app, **Setup → Claude AI** → paste an Anthropic API key (from console.anthropic.com; add a few
dollars of credit). Then:
- **Ask AI** buttons appear under backtests, market scans and bots, for a plain-language review.
- The bot form gets **AI check before each buy**: when a buy signal appears, Claude looks at the market
  (how stretched the price is, the longer trend, volatility, the bot's recent trades) and can **skip** a
  trade that looks like a false start. It can't make trades bigger or remove the stop. If the AI is
  unreachable, the bot follows its normal rules. Each check costs about 5-15 US cents.

The AI is a second opinion, not a crystal ball. It can be wrong, and a backtest can't tell you how the AI
check would have done in the past.

## 2d. AI Autopilot: let Claude trade

With Claude connected, a new bot defaults to **AI Autopilot**. Instead of fixed rules on one coin, Claude
looks at every market on your **watchlist** (up to 8, e.g. BTC/USDT, ETH/USDT, SOL/USDT) at each new candle
and decides: **buy** one of them (and how much, 10-100% of your max trade size, with how wide a stop),
**sell**, **switch** to a better market, or **hold**. It sees trend, momentum (RSI), volatility, recent
moves and volume for each market, plus its own open position, recent trades and past decisions, so it
adapts as markets change. Each decision and its reasoning appear on the bot page.

Limits written in code that Claude can't change: spot only (no leverage or shorting), one position at a
time, never more than your max trade size per buy, a trailing stop on every position (checked every minute),
the daily loss cap, and no buy below 60% confidence. If Claude is unreachable the bot holds and the stop
keeps protecting the position.

Models: every decision runs at high effort on Claude Opus 5.5 or Claude Opus 5 only (they take turns and back
each other up). If both are unavailable, the bot holds that round and tries again at the next candle; the
trailing stop keeps protecting any position. The bot page shows which model made each decision.

Cost: roughly 12 US cents of Anthropic usage per decision - about $2.90/day on 1h candles, $0.70/day on 4h,
$0.12/day on 1d. Profit is never guaranteed: run it in paper mode first and read its decisions.

## 2e. What makes the bots smarter (tested on real data)

Every rule change was tested on 5 years of Binance prices for BTC, ETH, SOL, BNB, XRP, ADA, LINK and
DOGE, split into two halves; only changes that helped in both halves were kept:
- **Trend filter** (on by default): only buy above the 200-candle average. Helped on every candle size and
  cut the worst drops a lot.
- **Trend strength (ADX 20)** (default on 4h/1h): only buy when a real trend is under way. On 4h it
  roughly halved drawdowns.
- **Re-entry on breakouts** (default on 1d): buy again on a new 20-candle high during an uptrend.
You can change these under **Strategy settings (advanced)** and compare them in **Backtest**.

The AI Autopilot now sees, for every coin: trend strength (ADX), MACD momentum, volatility, how far it is
below its recent high, the daily-candle trend, strength vs BTC, how many coins are in uptrends, what the
tested rules say, and its own record per coin. While holding it can also **tighten** the trailing stop to
lock in gains (never loosen it).

## 3. Make a paper bot (pretend money)

1. In the PC app, go to **Bots → + New bot** (or **Create bot from these settings** after a backtest).
2. Leave **Mode: Paper**. Trade size 5 and daily loss cap 3 are sensible starting values (exchanges need at least about $5 per order).
3. Press **Create bot**, then **▶ Start**.

What you'll see on the bot page:
- **Status** says "Running" and "checked just now". It checks prices every minute.
- **Trend (EMA)** says Up or Down. The bot **buys only on a fresh cross up**, so it may wait days or weeks
  for its first trade. That's by design, not a fault.
- The chart shows price (white/black line), fast EMA (blue), slow EMA (orange), buys ▲, sells ▼,
  and the red dashed **trailing stop** while holding.
- **Trades** and **Activity log** list everything the bot did.

Let it paper trade for **a few weeks** before going further.

## 4. Connect an exchange account (optional)

Only needed for testnet (practice orders on the exchange) or live (real money).

1. In the PC app, open **Setup → Your exchange accounts**.
2. Start with **Testnet (practice) → Connect**. The window shows exactly how to make a key.
3. Paste the key and secret → **Check & connect**. TrendBot checks the key and shows your balance.
   It refuses keys that allow withdrawals.
4. Stop your bot, **Edit** → Mode **Testnet** → Save → **▶ Start**.

## 5. Going live (only if you're comfortable)

1. **Setup → Real account → Connect** with a key that has **spot trading only** (no withdrawals).
2. Stop the bot → **Edit** → Mode **Live** → type `LIVE` to confirm → Save → **▶ Start**.
3. Keep the trade size small. Check on it daily.

## Everyday controls

| Button | What it does |
|---|---|
| ▶ Start / ■ Stop | Start or pause watching the market. Stop keeps any coins it holds. |
| Sell now | Sells the bot's position immediately at the market price. |
| Edit | Change settings (stop the bot first). |
| Delete | Remove a bot (stop it and sell first). |

## Using it on your phone

- **Backtests:** just open the online site.
- **Your bots from your phone:** see "Use it from your phone" in `README.md` (same Wi-Fi, password required).

## If something goes wrong

- **"isn't connected yet":** connect that account in Setup.
- **"Couldn't reach the exchange":** check your internet. Some countries block an exchange; try the other one.
- **Bot says Problem:** read the message. The bot keeps retrying every minute.
- **Nothing happens for days:** normal. The bot waits for a fresh trend signal.
