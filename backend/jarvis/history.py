"""Candle history in Parquet (data/history/<source>/<symbol>_<tf>.parquet). DuckDB can query these files directly.
CLI:  uv run python -m jarvis.history crypto perp:BTC/USDT:USDT 1m 30
      uv run python -m jarvis.history capital XAU_USD MINUTE 30"""
import asyncio
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt.pro as ccxtpro
import httpx
import pandas as pd

from jarvis.config import ROOT, load_config
from jarvis.feeds import capital
from jarvis.scalp.crypto import to_df

log = logging.getLogger("jarvis.history")
DIR = ROOT / "data" / "history"
TF_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "MINUTE": 60, "MINUTE_5": 300, "MINUTE_15": 900, "HOUR": 3600, "HOUR_4": 14400, "DAY": 86400}


def path(source: str, symbol: str, tf: str) -> Path:
    return DIR / source / f"{re.sub(r'[/:]', '_', symbol)}_{tf}.parquet"


def load(source: str, symbol: str, tf: str, start_s: float | None = None) -> pd.DataFrame:
    p = path(source, symbol, tf)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    return df[df.time >= start_s].reset_index(drop=True) if start_s else df


def save(source: str, symbol: str, tf: str, new: pd.DataFrame) -> pd.DataFrame:
    p = path(source, symbol, tf)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = pd.concat([load(source, symbol, tf), new]).drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    df.to_parquet(p, index=False)
    return df


async def _crypto(market: str, symbol: str, tf: str, since_s: float, exchanges: dict[str, str]) -> pd.DataFrame:
    ex = getattr(ccxtpro, exchanges[market])({"enableRateLimit": True})
    try:
        rows: list[list[float]] = []
        since = int(since_s * 1000)
        while since < time.time() * 1000 - TF_S[tf] * 1000:
            batch = await ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
            if not batch:
                break
            rows += batch
            since = batch[-1][0] + TF_S[tf] * 1000
        return to_df(rows, tf, time.time())
    finally:
        await ex.close()


def _error_code(e: httpx.HTTPStatusError) -> str:
    try:
        return str(e.response.json().get("errorCode", ""))
    except ValueError:
        return ""


async def _get_retrying(session: Any, path: str, params: dict[str, Any], tries: int = 4) -> Any:
    """Capital.com sometimes times out on a long download: retry the same window a few times before giving up."""
    for k in range(tries):
        try:
            return await session.get(path, params)
        except (httpx.TimeoutException, httpx.TransportError):
            if k == tries - 1:
                raise
            log.info("Capital.com timed out on %s, retrying (%d/%d)", path, k + 1, tries - 1)
            await asyncio.sleep(1.5 * (k + 1))


async def _capital(epic: str, tf: str, since_s: float, env: str, session: Any = None, allow_empty: bool = False) -> pd.DataFrame:
    """Download in windows Capital.com accepts. Its per-request date-range limit isn't documented: start just under
    1000 candles and halve the window whenever it answers error.invalid.max.daterange."""
    session = session or capital.Session(env)
    frames, start, empty, bars = [], since_s, 0, 999
    fmt = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731
    while start < time.time() - TF_S[tf]:
        end = min(start + bars * TF_S[tf], time.time())
        try:
            body = await _get_retrying(session, f"/api/v1/prices/{epic}", {"resolution": tf, "max": 1000, "from": fmt(start), "to": fmt(end)})
            frames.append(capital.parse_prices(body, tf, time.time()))
        except httpx.HTTPStatusError as e:
            code = _error_code(e)
            if code == "error.invalid.max.daterange" and bars > 25:
                bars //= 2  # window too wide for Capital.com: retry the same start with half
                continue
            if code != "error.prices.not-found":  # anything else is a real problem: say so, don't hide it
                raise RuntimeError(f"Capital.com refused {epic} {tf} history from {fmt(start)}: {e.response.text[:200]}") from e
            empty += 1  # nothing traded in this window (weekend, holiday, older than Capital.com keeps)
        start = end
        await asyncio.sleep(0.15)  # stay under 10 req/s
    log.info("%s %s: %d windows of %d candles, %d empty", epic, tf, len(frames) + empty, bars, empty)
    if not frames:
        if allow_empty:
            return pd.DataFrame()
        raise RuntimeError(f"Capital.com has no {tf} history for {epic} in that period")
    return pd.concat(frames)


async def ensure(symbol: str, tf: str, days: float, cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    """Return `days` of history, downloading whatever is missing at the start or the end."""
    cfg = cfg or load_config()
    source = "capital" if symbol in cfg["capital"]["instruments"] else "crypto"
    start = time.time() - days * 86400
    have = load(source, symbol, tf)

    async def fetch(since: float, allow_empty: bool = False) -> pd.DataFrame:
        if source == "capital":
            if not capital.has_credentials():
                raise RuntimeError("Capital.com credentials needed to download FX/metals/oil history")
            return await _capital(cfg["capital"]["instruments"][symbol]["epic"], tf, since, cfg["capital"].get("env", "demo"),
                                  allow_empty=allow_empty)
        market, sym = symbol.split(":", 1)
        return await _crypto(market, sym, tf, since, cfg["crypto"]["scanner"]["markets"])

    if have.empty or have.time.iloc[0] > start + TF_S[tf] * 2:
        have = save(source, symbol, tf, await fetch(start))
    elif have.time.iloc[-1] < time.time() - TF_S[tf] * 2:
        # topping up to now: nothing new on a weekend or holiday is fine — keep what we have
        new = await fetch(float(have.time.iloc[-1]), allow_empty=True)
        if not new.empty:
            have = save(source, symbol, tf, new)
    return have[have.time >= start].reset_index(drop=True)


def catalog() -> list[dict[str, Any]]:
    out = []
    for p in sorted(DIR.glob("*/*.parquet")):
        df = pd.read_parquet(p, columns=["time"])
        out.append({"source": p.parent.name, "file": p.name, "rows": len(df),
                    "from": int(df.time.min()) if len(df) else None, "to": int(df.time.max()) if len(df) else None})
    return out


if __name__ == "__main__":
    _, src, sym, tf, days = sys.argv
    df = asyncio.run(ensure(sym, tf, float(days)))
    print(f"{len(df)} candles of {sym} {tf} -> {path(src, sym, tf)}")
