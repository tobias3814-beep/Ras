"""Capital.com: one REST session shared by the live bid/ask stream and the candle poller.
Prices only — this module never places orders."""
import asyncio
import json
import logging
import ssl
import time
from collections.abc import Callable
from typing import Any

import certifi
import httpx
import pandas as pd
import websockets

from jarvis.bus import Bus
from jarvis.config import secret
from jarvis.feeds.macro import compute_dxy
from jarvis.station.context import Context

log = logging.getLogger("jarvis.capital")

REST = {"demo": "https://demo-api-capital.backend-capital.com", "live": "https://api-capital.backend-capital.com"}
WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"
PING_EVERY = 300  # session dies after 10 min idle
CRED_KEYS = ("capital_api_key", "capital_identifier", "capital_password")
SENTIMENT_EVERY = 900
# certifi's CA list, like httpx uses: the frozen app (and python.org builds without "Install Certificates") has no system one
TLS = ssl.create_default_context(cafile=certifi.where())
TF_SECONDS = {"MINUTE": 60, "MINUTE_5": 300, "MINUTE_15": 900, "HOUR": 3600, "HOUR_4": 14400, "DAY": 86400}


def has_credentials() -> bool:
    return all(secret(k) for k in CRED_KEYS)


def parse_quote(msg: dict[str, Any], epic_to_symbol: dict[str, str]) -> dict[str, Any] | None:
    if msg.get("destination") != "quote":
        return None
    p = msg["payload"]
    symbol = epic_to_symbol.get(p["epic"])
    if symbol is None:
        return None
    return {"type": "price", "symbol": symbol, "bid": p["bid"], "ask": p["ofr"], "ts": p["timestamp"]}


def parse_prices(body: dict[str, Any], tf: str, now_s: float) -> pd.DataFrame:
    """Capital.com /prices -> CLOSED candles only (time = open time, UTC seconds): mid OHLC for indicators,
    plus bid_/ask_ OHLC for scalp entries, exits and spread."""
    ks = ("open", "high", "low", "close")
    rows = [{
        "time": int(pd.Timestamp(p["snapshotTimeUTC"], tz="UTC").timestamp()),
        **{k: (p[f"{k}Price"]["bid"] + p[f"{k}Price"]["ask"]) / 2 for k in ks},
        "volume": p.get("lastTradedVolume", 0),  # tick volume
        **{f"{side}_{k}": p[f"{k}Price"][side] for side in ("bid", "ask") for k in ks},
    } for p in body["prices"]]
    df = pd.DataFrame(rows, columns=["time", *ks, "volume", *(f"{s}_{k}" for s in ("bid", "ask") for k in ks)])
    return df[df.time + TF_SECONDS[tf] <= now_s].reset_index(drop=True)


MAX_JUMP = 0.05  # a tick this far from the last one needs a second tick to agree


class QuoteCheck:
    """Drop impossible ticks before anything acts on them. A big jump is held back until the next tick confirms it, because
    real gaps happen (Monday opens) but one bad tick must not trigger a stop."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self.held: dict[str, float] = {}

    def ok(self, p: dict[str, Any]) -> bool:
        sym, bid, ask = p["symbol"], p["bid"], p["ask"]
        if not (bid > 0 and ask > 0) or bid > ask:
            return False
        mid, last = (bid + ask) / 2, self.last.get(sym)
        if last is not None and abs(mid / last - 1) > MAX_JUMP:
            held = self.held.get(sym)
            if held is None or abs(mid / held - 1) > 0.005:
                self.held[sym] = mid
                return False
        self.held.pop(sym, None)
        self.last[sym] = mid
        return True


def bad_bars(frames: dict[str, pd.DataFrame], recent: int = 5) -> list[str]:
    """Timeframes whose latest candles are impossible. Decisions wait for clean data; exits run on quotes and carry on."""
    out = []
    for tf, df in frames.items():
        x = df.tail(recent)
        hl = x[["open", "high", "low", "close"]]
        if hl.isna().any().any() or (hl <= 0).any().any() or (x.high < x[["open", "close"]].max(axis=1)).any() or (x.low > x[["open", "close"]].min(axis=1)).any():
            out.append(tf)
    return out


def status(bus: Bus, state: str, detail: str = "") -> None:
    bus.publish({"type": "status", "feed": "capital", "state": state, "detail": detail}, key="status:capital")


class Session:
    def __init__(self, env: str) -> None:
        self.base = REST[env]
        self.client = httpx.AsyncClient(timeout=15)
        self.cst = self.token = ""
        self._lock = asyncio.Lock()
        self._retry_at, self._backoff, self.last_error = 0.0, 30.0, ""

    async def login(self) -> None:
        async with self._lock:
            if self.cst:  # another task logged in while we waited for the lock
                return
            if time.monotonic() < self._retry_at:  # after a failed login, don't hammer POST /session (429s)
                raise RuntimeError(f"Capital.com login backing off: {self.last_error}")
            r = await self.client.post(f"{self.base}/api/v1/session",
                                       headers={"X-CAP-API-KEY": secret("capital_api_key") or ""},
                                       json={"identifier": secret("capital_identifier"), "password": secret("capital_password")})
            if r.is_error:
                self.last_error = f"HTTP {r.status_code} {r.text[:120]}"
                self._retry_at = time.monotonic() + self._backoff
                self._backoff = min(self._backoff * 2, 900)
                r.raise_for_status()
            self._backoff = 30.0
            self.cst, self.token = r.headers["CST"], r.headers["X-SECURITY-TOKEN"]
            await asyncio.sleep(1)  # POST /session is limited to 1/s

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in (0, 1):
            if not self.cst:
                await self.login()
            r = await self.client.get(f"{self.base}{path}", params=params,
                                      headers={"CST": self.cst, "X-SECURITY-TOKEN": self.token})
            if r.status_code == 401 and attempt == 0:
                self.cst = ""  # expired session: log in again once
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")


async def stream(bus: Bus, session: Session, cfg: dict[str, Any]) -> None:
    epic_to_symbol = {ins["epic"]: sym for sym, ins in cfg["instruments"].items()}
    backoff = 1
    checker = QuoteCheck()
    while True:
        try:
            session.cst = ""
            await session.login()
            async with websockets.connect(WS_URL, ssl=TLS) as ws:
                auth = {"cst": session.cst, "securityToken": session.token}
                await ws.send(json.dumps({"destination": "marketData.subscribe", "correlationId": "1",
                                          **auth, "payload": {"epics": list(epic_to_symbol)}}))
                status(bus, "live")
                backoff = 1
                last_ping = time.monotonic()
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        raw = None  # markets closed / quiet — just fall through to ping check
                    if raw is not None and (price := parse_quote(json.loads(raw), epic_to_symbol)):
                        if checker.ok(price):
                            bus.publish(price, key=f"price:{price['symbol']}")
                        else:
                            log.warning("dropped suspect quote: %s", price)
                    if time.monotonic() - last_ping > PING_EVERY:
                        await ws.send(json.dumps({"destination": "ping", "correlationId": "p", **auth}))
                        last_ping = time.monotonic()
        except httpx.HTTPStatusError as e:
            hint = " — check identifier (login email), API key custom password, and that the key was made in DEMO mode" \
                if "invalid.details" in e.response.text else ""
            status(bus, "error", f"login failed: HTTP {e.response.status_code}{hint}")
            log.warning("capital login failed: %s", e.response.text)
        except Exception as e:  # network drop etc. — reconnect
            if session.last_error and "backing off" in str(e):  # login is failing: show why, not "RuntimeError"
                hint = " — check identifier (login email), API key custom password, and that the key was made in DEMO mode" \
                    if "invalid.details" in session.last_error else ""
                status(bus, "error", f"login failed: {session.last_error[:60]}{hint}")
            else:
                status(bus, "reconnecting", type(e).__name__)
            log.warning("capital stream dropped: %r", e)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


def parse_rules(body: dict[str, Any]) -> dict[str, float | None]:
    """GET /markets/{epic} -> what a real account must respect: min size, size step, margin (% of notional)."""
    dr, ins = body.get("dealingRules") or {}, body.get("instrument") or {}
    val = lambda d: (d or {}).get("value")  # noqa: E731
    margin = ins.get("marginFactor") if ins.get("marginFactorUnit", "PERCENTAGE") == "PERCENTAGE" else None
    fee = ins.get("overnightFee") or {}  # % of notional per night, signed: negative is a charge
    return {"min_size": val(dr.get("minDealSize")), "step": val(dr.get("minSizeIncrement")), "margin_pct": margin,
            "fin_long": fee.get("longRate"), "fin_short": fee.get("shortRate")}


async def market_rules(session: Session, cfg: dict[str, Any], on_rules: Callable[[dict[str, dict[str, Any]]], Any]) -> None:
    """Fetch every instrument's dealing rules once a day (they rarely change) for realistic paper sizing."""
    while True:
        rules = {}
        for sym, ins in cfg["instruments"].items():
            try:
                rules[sym] = parse_rules(await session.get(f"/api/v1/markets/{ins['epic']}", {}))
            except Exception as e:
                log.warning("market rules %s failed: %r", sym, e)
        if rules:
            log.info("capital dealing rules: %s", rules)
            on_rules(rules)
        await asyncio.sleep(86_400)


async def poll_candles(session: Session, cfg: dict[str, Any], ctx: Context,
                       on_frames: Callable[[str, dict[str, pd.DataFrame]], Any], scalp: Any = None) -> None:
    """Intraday frames (4H / 1H / 15M) for every instrument, scalp frames (5M / 1M) where scalp mode is on,
    DXY components and retail sentiment. Runs just after each minute closes."""
    last_sentiment = 0.0
    while True:
        h1: dict[str, pd.DataFrame] = {}
        for sym, ins in cfg["instruments"].items():
            try:
                frames = {}
                for tf in ("DAY", "HOUR_4", "HOUR", "MINUTE_15"):  # DAY: the intraday trend filter
                    body = await session.get(f"/api/v1/prices/{ins['epic']}", {"resolution": tf, "max": 300})
                    frames[tf] = parse_prices(body, tf, time.time())
                h1[sym] = frames["HOUR"]
                on_frames(sym, frames)
                if scalp is not None and scalp.wants(sym):
                    sf = {"MINUTE_15": frames["MINUTE_15"], "HOUR": frames["HOUR"]}  # HOUR: scalp trend / chop filters
                    for tf in ("MINUTE_5", "MINUTE"):
                        sf[tf] = parse_prices(await session.get(f"/api/v1/prices/{ins['epic']}", {"resolution": tf, "max": 300}),
                                              tf, time.time())
                    scalp.on_frames(sym, sf)
            except Exception as e:
                log.warning("candles %s failed: %r", sym, e)
        for sym, epic in cfg.get("dxy_extra", {}).items():
            try:
                h1[sym] = parse_prices(await session.get(f"/api/v1/prices/{epic}", {"resolution": "HOUR", "max": 300}),
                                       "HOUR", time.time())
            except Exception as e:
                log.warning("dxy component %s failed: %r", sym, e)
        ctx.dxy = compute_dxy(h1)
        if time.time() - last_sentiment > SENTIMENT_EVERY:
            try:
                epic_to_symbol = {ins["epic"]: s for s, ins in cfg["instruments"].items()}
                body = await session.get("/api/v1/clientsentiment", {"marketIds": ",".join(epic_to_symbol)})
                ctx.retail = {epic_to_symbol[x["marketId"]]: float(x["longPositionPercentage"])
                              for x in body.get("clientSentiments", []) if x["marketId"] in epic_to_symbol}
                last_sentiment = time.time()
            except Exception as e:
                log.warning("client sentiment failed: %r", e)
        period = cfg.get("candle_poll_seconds", 60)
        await asyncio.sleep(period - time.time() % period + 2)  # wake 2s after the candle closes
