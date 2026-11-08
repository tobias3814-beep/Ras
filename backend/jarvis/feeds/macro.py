"""Free macro data: ForexFactory calendar, FRED yields, CFTC COT, DXY from FX crosses."""
import asyncio
import io
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import numpy as np
import pandas as pd

from jarvis.bus import Bus
from jarvis.station.context import Context

log = logging.getLogger("jarvis.macro")
UA = {"User-Agent": "Mozilla/5.0 (Ras local research station)"}
FRED_IDS = ("DGS2", "DGS10", "DFII10")  # US 2Y, 10Y, 10Y real (TIPS)
# ICE DXY weights; exponent sign: + if USD is base
DXY_W = {"EUR_USD": -0.576, "USD_JPY": 0.136, "GBP_USD": -0.119, "USD_CAD": 0.091, "USD_SEK": 0.042, "USD_CHF": 0.036}


def parse_calendar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        try:
            t = int(datetime.fromisoformat(r["date"]).timestamp() * 1000)
        except (KeyError, ValueError):
            continue
        out.append({"title": r.get("title", ""), "country": r.get("country", ""), "time": t, "impact": r.get("impact", ""),
                    "forecast": r.get("forecast", ""), "previous": r.get("previous", "")})
    return sorted(out, key=lambda e: e["time"])


def parse_fred(csv: str) -> dict[str, pd.Series]:
    df = pd.read_csv(io.StringIO(csv), index_col=0, parse_dates=True, na_values=".")
    return {c: df[c].dropna() for c in df.columns}


def parse_cot(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["date", "net_pct"])
    num = lambda c: pd.to_numeric(df[c])  # noqa: E731
    return pd.DataFrame({"date": pd.to_datetime(df.report_date_as_yyyy_mm_dd),
                         "net_pct": 100 * (num("noncomm_positions_long_all") - num("noncomm_positions_short_all"))
                         / num("open_interest_all")}).sort_values("date").reset_index(drop=True)


def compute_dxy(closes: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """DXY from whichever components we have (1H closes joined on time)."""
    # shortcut: missing components are dropped and weights renormalised — direction is right, level is a proxy
    have = {s: w for s, w in DXY_W.items() if s in closes and not closes[s].empty}
    if "EUR_USD" not in have:
        return None
    df = pd.concat({s: closes[s].set_index("time").close for s in have}, axis=1, join="inner").dropna()
    scale = sum(abs(w) for w in DXY_W.values()) / sum(abs(w) for w in have.values())
    log_dxy = sum(w * scale * np.log(df[s]) for s, w in have.items())
    return pd.DataFrame({"time": df.index, "close": 50.14348112 * np.exp(log_dxy)}).reset_index(drop=True)


async def _every(minutes: float, name: str, fn: Callable[[], Any]) -> None:
    while True:
        try:
            await fn()
        except Exception as e:
            log.warning("%s failed: %r", name, e)
        await asyncio.sleep(minutes * 60)


async def run(bus: Bus, ctx: Context, cfg: dict[str, Any], instruments: dict[str, Any]) -> None:
    client = httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True)

    async def calendar() -> None:
        try:
            r = await client.get(cfg["calendar_url"])
            r.raise_for_status()
            events = parse_calendar(r.json())
            if not events:
                raise ValueError("calendar came back empty")
        except Exception as e:  # without it there are no news blackouts, so say so
            bus.publish({"type": "status", "feed": "calendar", "state": "error", "detail": "news blackouts may be missing"},
                        key="status:calendar")
            bus.failed("news calendar", e)
            return
        ctx.calendar = events
        bus.publish({"type": "calendar", "events": events}, key="calendar")
        bus.publish({"type": "status", "feed": "calendar", "state": "live", "detail": ""}, key="status:calendar")

    async def yields() -> None:
        start = (pd.Timestamp.utcnow() - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
        r = await client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": ",".join(FRED_IDS), "cosd": start})
        r.raise_for_status()
        ctx.yields = parse_fred(r.text)
        bus.publish({"type": "yields", **{k: {"date": str(s.index[-1].date()), "value": float(s.iloc[-1])}
                                          for k, s in ctx.yields.items() if len(s)}}, key="yields")

    async def cot() -> None:
        for sym, ins in instruments.items():
            r = await client.get("https://publicreporting.cftc.gov/resource/6dca-aqww.json", params={
                "$where": f"cftc_contract_market_code='{ins['cot']}'", "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 156,
                "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all"})
            r.raise_for_status()
            ctx.cot[sym] = parse_cot(r.json())

    await asyncio.gather(_every(cfg["calendar_poll_minutes"], "calendar", calendar),
                         _every(cfg["yields_poll_minutes"], "yields", yields),
                         _every(cfg["cot_poll_hours"] * 60, "cot", cot))
