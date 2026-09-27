"""Cloud storage for the online version: a free Redis database (Upstash REST API or a plain REDIS_URL).

On your PC TrendBot keeps everything in the data/ folder. On the website (Vercel) there is no lasting
disk, so bots, connected accounts and the login live in a free Redis database instead. Secrets
(exchange and AI keys) are encrypted with TRENDBOT_SECRET before they're stored.
"""
import base64
import hashlib
import json
import os
import secrets
import time

import requests
from cryptography.fernet import Fernet, InvalidToken

# Vercel's Upstash integration sets KV_REST_API_*; a database made on upstash.com uses UPSTASH_REDIS_REST_*.
REDIS_URL = (os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or ""
# Other Redis providers on Vercel's marketplace (e.g. Redis Cloud) give a normal redis:// or rediss:// URL.
REDIS_TCP_URL = os.environ.get("REDIS_URL") or os.environ.get("KV_URL") or ""
CLOUD = bool((REDIS_URL and REDIS_TOKEN) or REDIS_TCP_URL)

PREFIX = "trendbot:"
BOTS_KEY = PREFIX + "bots"

UNLOCK_SCRIPT = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class StoreError(Exception):
    pass


class RedisStore:
    def __init__(self, url: str, token: str):
        self.url, self.s = url, requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}"})

    def cmd(self, *args):
        r = self.s.post(self.url, json=[str(a) for a in args], timeout=15)
        data = r.json() if r.content else {}
        if r.status_code >= 400 or "error" in data:
            raise StoreError(f"Database error: {data.get('error') or r.status_code}")
        return data.get("result")

    def pipeline(self, commands: list[list]) -> list:
        if not commands:
            return []
        r = self.s.post(self.url + "/pipeline", json=[[str(a) for a in c] for c in commands], timeout=15)
        data = r.json() if r.content else []
        if r.status_code >= 400 or not isinstance(data, list):
            raise StoreError(f"Database error: {r.status_code}")
        return [d.get("result") for d in data]

    # ---------------------------------------------------------------- bots

    def load_bots(self) -> dict:
        flat = self.cmd("HGETALL", BOTS_KEY) or []
        return {flat[i]: json.loads(flat[i + 1]) for i in range(0, len(flat), 2)}

    def save_bots(self, docs: dict, extra: list | None = None) -> None:
        cmds = [["HSET", BOTS_KEY, bid, json.dumps(doc, separators=(",", ":"))] for bid, doc in docs.items()]
        self.pipeline(cmds + (extra or []))

    def delete_bot(self, bot_id: str) -> None:
        self.cmd("HDEL", BOTS_KEY, bot_id)

    # ---------------------------------------------------------------- simple values

    def get_json(self, name: str, default=None):
        raw = self.cmd("GET", PREFIX + name)
        return json.loads(raw) if raw else default

    def set_json(self, name: str, value) -> None:
        self.cmd("SET", PREFIX + name, json.dumps(value, separators=(",", ":")))

    # ---------------------------------------------------------------- lock

    def acquire(self, name: str, ttl_s: int, wait_s: float) -> str | None:
        """Take a lock that expires by itself after ttl_s. Returns a token, or None if still busy."""
        token, deadline = secrets.token_hex(8), time.time() + wait_s
        while True:
            if self.cmd("SET", PREFIX + "lock:" + name, token, "NX", "EX", ttl_s) == "OK":
                return token
            if time.time() >= deadline:
                return None
            time.sleep(0.4)

    def release(self, name: str, token: str) -> None:
        try:
            self.cmd("EVAL", UNLOCK_SCRIPT, 1, PREFIX + "lock:" + name, token)
        except (StoreError, requests.RequestException):
            pass  # the lock expires on its own


class TcpRedisStore(RedisStore):
    """The same store over a normal Redis connection (redis:// or rediss:// URL)."""

    def __init__(self, url: str):
        import redis
        self.errors = (redis.RedisError,)
        self.r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=15, socket_connect_timeout=10,
                                      health_check_interval=30)

    @staticmethod
    def _raw(v):
        """Give replies the same shapes as the REST API: "OK" for success, flat lists for HGETALL."""
        if v is True:
            return "OK"
        if isinstance(v, dict):
            return [x for kv in v.items() for x in kv]
        return v

    def cmd(self, *args):
        try:
            return self._raw(self.r.execute_command(*[str(a) for a in args]))
        except self.errors as e:
            raise StoreError(f"Database error: {e}") from None

    def pipeline(self, commands: list[list]) -> list:
        if not commands:
            return []
        try:
            p = self.r.pipeline(transaction=False)
            for c in commands:
                p.execute_command(*[str(a) for a in c])
            return [self._raw(v) for v in p.execute()]
        except self.errors as e:
            raise StoreError(f"Database error: {e}") from None


if REDIS_URL and REDIS_TOKEN:
    store = RedisStore(REDIS_URL, REDIS_TOKEN)
elif REDIS_TCP_URL:
    store = TcpRedisStore(REDIS_TCP_URL)
else:
    store = None


# ---------------------------------------------------------------- encryption for secrets

def _fernet() -> Fernet:
    secret = os.environ.get("TRENDBOT_SECRET", "")
    if len(secret) < 16:
        raise StoreError("TRENDBOT_SECRET isn't set on the server, so keys can't be stored safely.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise StoreError("A stored key can't be decrypted (was TRENDBOT_SECRET changed?). Connect it again.")
