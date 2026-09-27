"""Market data + order adapters.

- CcxtMarket: Binance / Bybit spot through ccxt.
- AlpacaMarket: US stocks through Alpaca's REST API.
- PaperBroker: fills simulated at the live price, used for paper mode.

Every adapter exposes the same methods, so the engine never cares which one it has.
"""
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import ccxt
import requests

from .config import EXCHANGES, TIMEFRAMES, api_keys, key_env_names


class MarketError(Exception):
    """A problem the user can fix (bad symbol, missing keys, order too small...)."""


class NothingToSell(MarketError):
    """The bot thinks it holds coins/shares but the account has none left."""


@dataclass
class Fill:
    qty: float     # base asset received (buy) or sold (sell), after base-denominated fees
    price: float   # average fill price
    quote: float   # quote spent (buy) or received after fees (sell)
    fee: float     # fees in quote terms (best effort)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _missing_keys_error(exchange: str, mode: str) -> MarketError:
    key_name, secret_name = key_env_names(exchange, mode)
    return MarketError(f"{EXCHANGES[exchange]['label']} {mode} isn't connected yet. Connect it on the Setup "
                       f"page (or set {key_name} and {secret_name} in the .env file and restart).")


# --------------------------------------------------------------------------- crypto (ccxt)

# Public market-data endpoints. Reading price and candles directly is far lighter than ccxt, which
# downloads the exchange's whole market list first - that matters when checks run once a minute on a
# serverless host. ccxt is still used for orders and as the fallback.
PUBLIC_API = {
    ("binance", False): "https://api.binance.com", ("binance", True): "https://testnet.binance.vision",
    ("bybit", False): "https://api.bybit.com", ("bybit", True): "https://api-testnet.bybit.com",
}
BYBIT_INTERVAL = {"1h": "60", "4h": "240", "1d": "D"}
_http = requests.Session()


def _public_get(url: str, **params):
    r = _http.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


class CcxtMarket:
    kind = "crypto"

    def __init__(self, exchange: str, mode: str):
        self.exchange_id = exchange
        self.public = PUBLIC_API[(exchange, mode == "testnet")]
        opts = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if mode in ("testnet", "live"):
            key, secret = api_keys(exchange, mode)
            if not key:
                raise _missing_keys_error(exchange, mode)
            opts.update(apiKey=key, secret=secret)
        self.ex = getattr(ccxt, exchange)(opts)
        if mode == "testnet":
            self.ex.set_sandbox_mode(True)
        self._markets_loaded = False

    def _markets(self):
        if not self._markets_loaded:
            self.ex.load_markets()
            self._markets_loaded = True
        return self.ex.markets

    def _market(self, symbol: str) -> dict:
        m = self._markets().get(symbol)
        if not m:
            raise MarketError(f"{symbol} isn't listed on {EXCHANGES[self.exchange_id]['label']}. "
                              "Use the form BASE/QUOTE, e.g. BTC/USDT.")
        return m

    def market_open(self) -> bool:
        return True

    def validate(self, symbol: str, trade_size: float) -> None:
        m = self._market(symbol)
        if not m.get("spot"):
            raise MarketError(f"{symbol} isn't a spot market.")
        if not m.get("active", True):
            raise MarketError(f"{symbol} is not currently trading.")
        min_cost = (m.get("limits", {}).get("cost") or {}).get("min")
        if min_cost and trade_size < min_cost:
            raise MarketError(f"Trade size must be at least {min_cost} {m['quote']} on this market.")

    def _fast_price(self, symbol: str) -> float:
        market_id = symbol.replace("/", "")
        if self.exchange_id == "binance":
            return float(_public_get(self.public + "/api/v3/ticker/price", symbol=market_id)["price"])
        d = _public_get(self.public + "/v5/market/tickers", category="spot", symbol=market_id)
        return float(d["result"]["list"][0]["lastPrice"])

    def _fast_candles(self, symbol: str, timeframe: str, limit: int) -> list:
        market_id = symbol.replace("/", "")
        if self.exchange_id == "binance":
            rows = _public_get(self.public + "/api/v3/klines", symbol=market_id, interval=timeframe, limit=min(limit, 1000))
        else:
            d = _public_get(self.public + "/v5/market/kline", category="spot", symbol=market_id,
                            interval=BYBIT_INTERVAL[timeframe], limit=min(limit, 1000))
            rows = list(reversed(d["result"]["list"]))  # Bybit sends newest first
        out = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows]
        if not out:
            raise ValueError("no candles")
        return out

    def last_price(self, symbol: str) -> float:
        try:
            price = self._fast_price(symbol)
            if price > 0:
                return price
        except Exception:
            pass  # fall back to ccxt
        t = self.ex.fetch_ticker(symbol)
        price = t.get("last") or t.get("close")
        if not price:
            raise MarketError(f"No price available for {symbol}.")
        return float(price)

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list:
        try:
            return self._fast_candles(symbol, timeframe, limit)
        except Exception:
            return self.ex.fetch_ohlcv(symbol, timeframe, limit=limit)

    def fetch_history(self, symbol: str, timeframe: str, since_ms: int) -> list:
        tf_ms = TIMEFRAMES[timeframe] * 1000
        out, since = [], since_ms
        for _ in range(200):  # hard stop on pagination
            batch = self.ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            batch = [b for b in batch if not out or b[0] > out[-1][0]]
            if not batch:
                break
            out.extend(batch)
            since = batch[-1][0] + tf_ms
            if since > _now_ms():
                break
        return out

    def _free(self, asset: str) -> float:
        return float(self.ex.fetch_balance().get("free", {}).get(asset) or 0.0)

    def _refresh(self, order: dict, symbol: str) -> dict:
        params = {"acknowledged": True} if self.exchange_id == "bybit" else {}
        for _ in range(5):
            if order.get("status") == "closed" and order.get("filled"):
                return order
            time.sleep(1)
            try:
                order = self.ex.fetch_order(order["id"], symbol, params)
            except Exception:
                pass
        return order

    @staticmethod
    def _fees_in(order: dict, currency: str) -> float:
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        return sum(float(f.get("cost") or 0) for f in fees if f and f.get("currency") == currency)

    def buy(self, symbol: str, quote_amount: float) -> Fill:
        m = self._market(symbol)
        base, quote = m["base"], m["quote"]
        free_quote = self._free(quote)
        if free_quote < quote_amount:
            raise MarketError(f"Not enough {quote}: need {quote_amount}, have {free_quote:.2f}.")
        before = self._free(base)
        order = self._refresh(self.ex.create_market_buy_order_with_cost(symbol, quote_amount), symbol)
        filled = float(order.get("filled") or 0)
        qty = filled - self._fees_in(order, base)
        received = self._free(base) - before
        if qty <= 0 < received:
            qty = received                     # order details missing: trust the balance change
        elif received > 0:
            qty = min(qty, received)           # never claim more than actually arrived
        if qty <= 0:
            raise MarketError("Buy order sent but no fill was reported - check the exchange.")
        cost = float(order.get("cost") or quote_amount)
        price = float(order.get("average") or (cost / filled if filled else self.last_price(symbol)))
        return Fill(qty=qty, price=price, quote=cost, fee=self._fees_in(order, quote) + (filled - qty) * price)

    def sell(self, symbol: str, qty: float) -> Fill:
        m = self._market(symbol)
        base, quote = m["base"], m["quote"]
        qty = min(qty, self._free(base))       # only ever sell what the bot bought
        amount = float(self.ex.amount_to_precision(symbol, qty)) if qty > 0 else 0.0
        min_amount = (m.get("limits", {}).get("amount") or {}).get("min") or 0
        if amount <= 0 or amount < min_amount:
            raise NothingToSell(f"No {base} left to sell (account has {qty:g}).")
        order = self._refresh(self.ex.create_market_sell_order(symbol, amount), symbol)
        filled = float(order.get("filled") or amount)
        price = float(order.get("average") or self.last_price(symbol))
        cost = float(order.get("cost") or filled * price)
        fee = self._fees_in(order, quote)
        return Fill(qty=filled, price=price, quote=cost - fee, fee=fee)


# --------------------------------------------------------------------------- stocks (Alpaca)

ALPACA_TF = {"1h": "1Hour", "4h": "4Hour", "1d": "1Day"}
ALPACA_BARS_PER_DAY = {"1h": 7, "4h": 2, "1d": 1}


class AlpacaMarket:
    kind = "stocks"
    DATA_URL = "https://data.alpaca.markets"

    def __init__(self, mode: str):
        self.feed = os.environ.get("ALPACA_FEED", "iex")
        if mode == "live":
            key, secret = api_keys("alpaca", "live")
            base = "https://api.alpaca.markets"
        elif mode == "testnet":
            key, secret = api_keys("alpaca", "testnet")
            base = "https://paper-api.alpaca.markets"
        else:  # paper sim only reads data: any Alpaca keys will do
            key, secret = api_keys("alpaca", "testnet")
            base = "https://paper-api.alpaca.markets"
            if not key:
                key, secret = api_keys("alpaca", "live")
                base = "https://api.alpaca.markets"
        if not key:
            raise MarketError("Stock data comes from Alpaca, which needs a free account. Create a free Alpaca "
                              "paper account and connect it on the Setup page (Alpaca, Paper account).")
        self.base = base
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._clock = (0.0, False)

    def _get(self, url: str, **params):
        r = self.s.get(url, params=params, timeout=20)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise MarketError(f"Alpaca error {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self.s.post(self.base + path, json=body, timeout=20)
        if r.status_code >= 400:
            raise MarketError(f"Alpaca rejected the order ({r.status_code}): {r.text[:200]}")
        return r.json()

    def market_open(self) -> bool:
        checked, is_open = self._clock
        if time.time() - checked > 60:
            is_open = bool((self._get(self.base + "/v2/clock") or {}).get("is_open"))
            self._clock = (time.time(), is_open)
        return is_open

    def _asset(self, symbol: str) -> dict:
        a = self._get(self.base + f"/v2/assets/{symbol}")
        if not a:
            raise MarketError(f"{symbol} isn't a stock Alpaca knows. Use a ticker like AAPL.")
        return a

    def validate(self, symbol: str, trade_size: float) -> None:
        a = self._asset(symbol)
        if not a.get("tradable") or a.get("status") != "active":
            raise MarketError(f"{symbol} is not tradable on Alpaca.")
        if trade_size < 1:
            raise MarketError("Alpaca's minimum order is $1.")

    def last_price(self, symbol: str) -> float:
        d = self._get(self.DATA_URL + f"/v2/stocks/{symbol}/trades/latest", feed=self.feed)
        price = ((d or {}).get("trade") or {}).get("p")
        if not price:
            raise MarketError(f"No price available for {symbol}.")
        return float(price)

    def fetch_history(self, symbol: str, timeframe: str, since_ms: int) -> list:
        start = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out, token = [], None
        for _ in range(100):
            params = {"timeframe": ALPACA_TF[timeframe], "start": start, "limit": 10000,
                      "adjustment": "all", "feed": self.feed}
            if token:
                params["page_token"] = token
            d = self._get(self.DATA_URL + f"/v2/stocks/{symbol}/bars", **params) or {}
            for b in d.get("bars") or []:
                t = int(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp() * 1000)
                out.append([t, b["o"], b["h"], b["l"], b["c"], b["v"]])
            token = d.get("next_page_token")
            if not token:
                break
        return out

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list:
        days = math.ceil(limit / ALPACA_BARS_PER_DAY[timeframe] * 7 / 5) + 10
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return self.fetch_history(symbol, timeframe, int(since.timestamp() * 1000))[-limit:]

    def _position_qty(self, symbol: str) -> float:
        p = self._get(self.base + f"/v2/positions/{symbol}")
        return float(p["qty"]) if p else 0.0

    def _wait_fill(self, order: dict) -> dict:
        for _ in range(30):
            if order.get("status") in ("filled", "canceled", "expired", "rejected"):
                return order
            time.sleep(1)
            order = self._get(self.base + f"/v2/orders/{order['id']}") or order
        self.s.delete(self.base + f"/v2/orders/{order['id']}", timeout=20)
        time.sleep(2)
        return self._get(self.base + f"/v2/orders/{order['id']}") or order

    def buy(self, symbol: str, quote_amount: float) -> Fill:
        a = self._asset(symbol)
        body = {"symbol": symbol, "side": "buy", "type": "market", "time_in_force": "day"}
        if a.get("fractionable"):
            body["notional"] = f"{quote_amount:.2f}"
        else:
            shares = math.floor(quote_amount / self.last_price(symbol))
            if shares < 1:
                raise MarketError(f"{symbol} can't be bought in fractions and one share costs more "
                                  f"than the ${quote_amount} trade size.")
            body["qty"] = str(shares)
        order = self._wait_fill(self._post("/v2/orders", body))
        qty = float(order.get("filled_qty") or 0)
        if qty <= 0:
            raise MarketError(f"Buy order was not filled (status: {order.get('status')}).")
        price = float(order["filled_avg_price"])
        return Fill(qty=qty, price=price, quote=qty * price, fee=0.0)

    def sell(self, symbol: str, qty: float) -> Fill:
        qty = min(qty, self._position_qty(symbol))
        qty = math.floor(qty * 1e9) / 1e9
        if qty <= 0:
            raise NothingToSell(f"No {symbol} shares left to sell.")
        order = self._wait_fill(self._post("/v2/orders", {
            "symbol": symbol, "side": "sell", "type": "market", "time_in_force": "day", "qty": str(qty)}))
        filled = float(order.get("filled_qty") or 0)
        if filled <= 0:
            raise MarketError(f"Sell order was not filled (status: {order.get('status')}).")
        price = float(order["filled_avg_price"])
        return Fill(qty=filled, price=price, quote=filled * price, fee=0.0)


# --------------------------------------------------------------------------- paper trading

class PaperBroker:
    """Simulated fills at the current live price, with the exchange's normal fee."""

    def __init__(self, market, fee: float):
        self.market, self.fee = market, fee

    def buy(self, symbol: str, quote_amount: float) -> Fill:
        price = self.market.last_price(symbol)
        fee = quote_amount * self.fee
        return Fill(qty=(quote_amount - fee) / price, price=price, quote=quote_amount, fee=fee)

    def sell(self, symbol: str, qty: float) -> Fill:
        price = self.market.last_price(symbol)
        gross = qty * price
        fee = gross * self.fee
        return Fill(qty=qty, price=price, quote=gross - fee, fee=fee)


# --------------------------------------------------------------------------- connecting accounts

def _crypto_balances(balance: dict) -> list[dict]:
    totals = {k: v for k, v in (balance.get("total") or {}).items() if v}
    order = {"USDT": 0, "USDC": 1, "BTC": 2, "ETH": 3}
    assets = sorted(totals, key=lambda a: (order.get(a, 9), -totals[a]))[:6]
    return [{"asset": a, "free": float((balance.get("free") or {}).get(a) or 0), "total": float(totals[a])}
            for a in assets]


def _check_binance_permissions(ex, warnings: list) -> None:
    r = ex.sapi_get_account_apirestrictions()
    if r.get("enableWithdrawals"):
        raise MarketError("This key allows withdrawals. For your safety TrendBot won't use it: edit the key on "
                          "Binance, untick 'Enable Withdrawals', then connect again.")
    if not r.get("enableSpotAndMarginTrading"):
        raise MarketError("This key can't trade. Edit it on Binance and tick 'Enable Spot & Margin Trading'.")
    if not r.get("ipRestrict"):
        warnings.append("Tip: restrict this key to your IP address on Binance for extra safety.")


def _check_bybit_permissions(ex, warnings: list) -> None:
    r = (ex.private_get_v5_user_query_api() or {}).get("result") or {}
    perms = r.get("permissions") or {}
    if any("withdraw" in str(p).lower() for p in perms.values()):
        raise MarketError("This key allows withdrawals. For your safety TrendBot won't use it: create a key "
                          "without the Withdraw permission, then connect again.")
    if str(r.get("readOnly")) == "1" or "SpotTrade" not in (perms.get("Spot") or []):
        raise MarketError("This key can't trade spot. Edit it on Bybit: choose 'Read-Write' and tick Spot trading.")
    if not [ip for ip in (r.get("ips") or []) if ip and ip != "*"]:
        warnings.append("Tip: restrict this key to your IP address on Bybit for extra safety.")


def verify_account(exchange: str, mode: str, key: str, secret: str) -> dict:
    """Log in with the given keys and check them. Raises MarketError with a plain-language reason."""
    warnings: list[str] = []
    if exchange == "alpaca":
        base = "https://api.alpaca.markets" if mode == "live" else "https://paper-api.alpaca.markets"
        r = requests.get(base + "/v2/account", timeout=20,
                         headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        if r.status_code in (401, 403):
            other = "live" if mode != "live" else "paper"
            raise MarketError(f"Alpaca rejected these keys. Check they were copied fully and that they're "
                              f"{'live' if mode == 'live' else 'paper'}-account keys, not {other} keys.")
        if r.status_code >= 400:
            raise MarketError(f"Alpaca error {r.status_code}: {r.text[:200]}")
        acct = r.json()
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            raise MarketError("Alpaca says this account is blocked from trading.")
        return {"balances": [{"asset": "USD cash", "free": float(acct.get("cash") or 0),
                              "total": float(acct.get("equity") or 0)}], "warnings": warnings}

    ex = getattr(ccxt, exchange)({"apiKey": key, "secret": secret, "enableRateLimit": True,
                                  "options": {"defaultType": "spot"}})
    if mode == "testnet":
        ex.set_sandbox_mode(True)
    try:
        balance = ex.fetch_balance()
    except ccxt.AuthenticationError:
        where = "the testnet site" if mode == "testnet" else "your real account"
        raise MarketError(f"{EXCHANGES[exchange]['label']} rejected this key. Check it was copied fully and that "
                          f"it was made on {where}. Testnet and real keys are different.")
    except ccxt.PermissionDenied as exc:
        raise MarketError(f"{EXCHANGES[exchange]['label']} refused access: {str(exc)[:200]}")
    try:
        if exchange == "binance" and mode == "live":
            _check_binance_permissions(ex, warnings)
        elif exchange == "bybit":
            _check_bybit_permissions(ex, warnings)
    except MarketError:
        raise
    except Exception:
        if mode == "live":
            warnings.append("Couldn't read this key's permissions. Please check on the exchange that "
                            "withdrawals are switched OFF for it.")
    return {"balances": _crypto_balances(balance), "warnings": warnings}


def make_market(exchange: str, mode: str):
    """Data (and, for testnet/live, order) access. Paper crypto uses real public prices, no keys."""
    if exchange not in EXCHANGES:
        raise MarketError(f"Unknown exchange {exchange}.")
    if exchange == "alpaca":
        return AlpacaMarket(mode)
    return CcxtMarket(exchange, "paper" if mode == "paper" else mode)


def make_broker(exchange: str, mode: str, market):
    return PaperBroker(market, EXCHANGES[exchange]["fee"]) if mode == "paper" else market
