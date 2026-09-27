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

## 3. Make a paper bot (pretend money)

1. In the PC app, go to **Bots → + New bot** (or **Create bot from these settings** after a backtest).
2. Leave **Mode: Paper**. Trade size 20 and daily loss cap 10 are sensible starting values.
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
