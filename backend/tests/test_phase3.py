import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from conftest import NOW, frames
from jarvis.bus import Bus
from jarvis.feeds.macro import compute_dxy, parse_calendar, parse_cot, parse_fred
from jarvis.feeds.news import NewsView, classify_claude, classify_keywords, parse_rss
from jarvis.station import briefing
from jarvis.station.agents import blackout_event, macro, news, risk_officer, sentiment, technical
from jarvis.station.context import Context
from jarvis.station.engine import Station
from jarvis.station.store import Store

NOW_MS = NOW * 1000
EV = lambda title, ccy, dt_min, impact="High": {"title": title, "country": ccy, "time": NOW_MS + dt_min * 60_000,  # noqa: E731
                                                "impact": impact, "forecast": "", "previous": ""}

# ---------- calendar / blackout ----------

def test_parse_calendar_sorts_and_converts_offsets():
    ev = parse_calendar([{"title": "B", "country": "USD", "date": "2026-10-23T08:30:00-04:00", "impact": "High"},
                         {"title": "A", "country": "EUR", "date": "2026-10-23T09:00:00+00:00", "impact": "Low"},
                         {"title": "bad", "date": "nope"}])
    assert [e["title"] for e in ev] == ["A", "B"]
    assert ev[1]["time"] == int(datetime(2026, 10, 23, 12, 30, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)


def test_blackout_window_currency_and_oil():
    cpi, eia = EV("CPI m/m", "USD", 20), EV("Crude Oil Inventories", "USD", 10, "Medium")
    assert blackout_event("EUR_USD", ["EUR", "USD"], [cpi], NOW_MS, 30, 15) is cpi
    assert blackout_event("EUR_USD", ["EUR", "USD"], [EV("CPI", "USD", 40)], NOW_MS, 30, 15) is None   # too early
    assert blackout_event("EUR_USD", ["EUR", "USD"], [EV("CPI", "USD", -16)], NOW_MS, 30, 15) is None  # over
    assert blackout_event("GBP_USD", ["GBP", "USD"], [EV("CPI", "JPY", 5)], NOW_MS, 30, 15) is None    # other ccy
    assert blackout_event("WTI_USD", ["USD"], [eia], NOW_MS, 30, 15) is eia
    assert blackout_event("XAU_USD", ["USD"], [eia], NOW_MS, 30, 15) is None


def test_risk_officer_vetoes_blackout(cfg):
    f = frames(0.05)
    v = technical(f["HOUR_4"], f["HOUR"], f["MINUTE_15"], 0.01, cfg["station"])
    px = v.levels["entry"]
    r = risk_officer(v, f["HOUR"], f["MINUTE_15"], px, px + 0.01, "X", {"pip": 0.01, "max_spread": 4, "ccy": ["USD"]},
                     [EV("Non-Farm Employment Change", "USD", 12)], NOW, cfg["station"])
    assert r.veto and r.reasons[0].startswith("News blackout: USD Non-Farm Employment Change in 12 min")

# ---------- macro data ----------

def test_parse_fred_handles_missing_dots():
    s = parse_fred("observation_date,DGS2,DGS10\n2026-10-20,4.79,.\n2026-10-21,4.81,5.13\n")
    assert list(s["DGS2"]) == [4.79, 4.81] and list(s["DGS10"]) == [5.13]


def test_parse_cot_net_percent_sorted():
    df = parse_cot([{"report_date_as_yyyy_mm_dd": "2026-10-13T00:00:00.000", "noncomm_positions_long_all": "300",
                     "noncomm_positions_short_all": "100", "open_interest_all": "1000"},
                    {"report_date_as_yyyy_mm_dd": "2026-10-06T00:00:00.000", "noncomm_positions_long_all": "100",
                     "noncomm_positions_short_all": "100", "open_interest_all": "1000"}])
    assert list(df.net_pct) == [0.0, 20.0]


def test_compute_dxy_level_and_partial_components():
    one = lambda: pd.DataFrame({"time": [1, 2], "close": [1.0, 1.0]})  # noqa: E731
    full = compute_dxy({s: one() for s in ("EUR_USD", "USD_JPY", "GBP_USD", "USD_CAD", "USD_SEK", "USD_CHF")})
    assert np.allclose(full.close, 50.14348112)
    part = compute_dxy({"EUR_USD": pd.DataFrame({"time": [1, 2], "close": [1.0, 1.1]})})
    assert part.close.iloc[1] < part.close.iloc[0]  # stronger euro = weaker dollar
    assert compute_dxy({"USD_JPY": one()}) is None


def _usd_up_ctx() -> Context:
    dxy = pd.DataFrame({"time": range(30), "close": np.linspace(100, 101, 30)})  # +~0.8% over 24h
    idx = pd.date_range("2026-10-01", periods=10)
    y = {"DGS2": pd.Series(np.linspace(4.5, 4.8, 10), idx), "DGS10": pd.Series(np.linspace(5.0, 5.2, 10), idx),
         "DFII10": pd.Series(np.linspace(2.5, 2.7, 10), idx)}
    return Context(dxy=dxy, yields=y)


def test_macro_maps_usd_strength_per_instrument(cfg):
    ctx, ins, sc = _usd_up_ctx(), {"ccy": ["USD"]}, cfg["station"]
    dirs = {s: macro(s, ctx, {}, ins, sc, NOW_MS).direction for s in ("EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD", "XAG_USD")}
    assert dirs == {"EUR_USD": "SELL", "GBP_USD": "SELL", "USD_JPY": "BUY", "XAU_USD": "SELL", "XAG_USD": "SELL"}
    assert macro("EUR_USD", Context(), {}, ins, sc, NOW_MS).direction == "HOLD"


def test_macro_flags_intervention_and_next_event(cfg):
    ctx = _usd_up_ctx()
    ctx.calendar = [EV("BoJ Policy Rate", "JPY", 180)]
    v = macro("USD_JPY", ctx, {"USD_JPY": {"bid": 160.0}}, {"ccy": ["USD", "JPY"]}, cfg["station"], NOW_MS)
    base = macro("USD_JPY", ctx, {"USD_JPY": {"bid": 150.0}}, {"ccy": ["USD", "JPY"]}, cfg["station"], NOW_MS)
    assert v.confidence == base.confidence - 15 and any("intervention" in r for r in v.reasons)
    assert any("BoJ Policy Rate in 3h 00m" in r for r in v.reasons)

# ---------- sentiment ----------

def test_sentiment_contrarian_retail_and_cot():
    assert sentiment("EUR_USD", Context(retail={"EUR_USD": 85})).direction == "SELL"
    assert sentiment("EUR_USD", Context(retail={"EUR_USD": 55})).direction == "HOLD"
    cot = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=60, freq="W"), "net_pct": list(range(60))})
    assert sentiment("XAU_USD", Context(cot={"XAU_USD": cot})).direction == "SELL"   # specs at 3y max long
    assert sentiment("USD_JPY", Context(cot={"USD_JPY": cot})).direction == "BUY"    # yen longs max = USDJPY contrarian buy
    assert sentiment("X", Context()).direction == "HOLD"

# ---------- news ----------

RSS = """<rss><channel>
<item><title><![CDATA[Fed signals more hikes]]></title><link>https://x/1</link><pubDate>Fri, 23 Oct 2026 14:41:05 GMT</pubDate></item>
<item><title>No date</title><link>https://x/2</link></item>
</channel></rss>"""


def test_parse_rss():
    items = parse_rss(RSS, "FX")
    assert len(items) == 1 and items[0]["title"] == "Fed signals more hikes" and items[0]["source"] == "FX"


def test_keyword_classifier():
    hs = [{"title": t} for t in ("Missile attack escalates conflict", "Sanctions widen as war drags on",
                                  "OPEC+ agrees deeper output cut", "OPEC+ extends supply curbs", "Fed hawkish: hike likely")]
    v = classify_keywords(hs)
    by = {x["symbol"]: x for x in v["instruments"]}
    assert v["risk_sentiment"] == "risk_off"
    assert by["XAU_USD"]["reason"] == "keyword score +1.0"  # two risk-off headlines (+2) minus hawkish Fed (-1)
    assert by["WTI_USD"]["bias"] == "BUY" and by["USD_JPY"]["bias"] == "NEUTRAL"


def test_news_vote_and_staleness():
    view = {"source": "claude", "risk_sentiment": "risk_off", "geopolitical_alert": "", "updated": NOW_MS,
            "instruments": [{"symbol": "XAU_USD", "bias": "BUY", "confidence": 65, "reason": "safe haven", "headlines": ["h1"]}]}
    v = news("XAU_USD", Context(news_view=view), NOW_MS)
    assert (v.direction, v.confidence) == ("BUY", 65) and "“h1”" in v.reasons
    assert news("XAU_USD", Context(news_view=view), NOW_MS + 2 * 3_600_000).direction == "HOLD"


def test_classify_claude_clamps_and_filters_ids():
    parsed = NewsView(risk_sentiment="neutral", geopolitical_alert="", instruments=[
        {"symbol": "XAU_USD", "bias": "BUY", "confidence": 140, "reason": "r", "headline_ids": [0, 7]}])

    class Msgs:
        async def parse(self, **kw):
            assert kw["model"] == "claude-haiku-5-5" and "<headlines>" in kw["messages"][0]["content"]
            return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)

    view = asyncio.run(classify_claude(SimpleNamespace(messages=Msgs()), "claude-haiku-5-5",
                                       [{"title": "t", "source": "s", "time": NOW_MS}]))
    assert view["instruments"][0]["confidence"] == 100 and view["instruments"][0]["headline_ids"] == [0]

# ---------- briefings ----------

def test_briefings(cfg, tmp_path):
    bus = Bus()
    ins = {"XAU_USD": {"pip": 0.1, "max_spread": 6, "ccy": ["USD"]}}
    st = Station(bus, Store(tmp_path / "s.sqlite"), ins, cfg["station"])
    tz = ZoneInfo("Asia/Bahrain")
    now = datetime.now(tz)
    st.frames["XAU_USD"] = frames(0.05)
    st.calls["XAU_USD"] = {"call": "BUY", "lean": "BUY", "confidence": 72, "hold_why": [], "reasons": ["trend"]}
    st.ctx.calendar = [{"title": "CPI m/m", "country": "USD", "impact": "High", "time": int((now + timedelta(minutes=5)).timestamp() * 1000)}]
    m = briefing.morning(st, now)
    assert "Gold BUY 72" in m["text"] and m["moves"][0]["symbol"] == "XAU_USD"
    if now.date() == (now + timedelta(minutes=5)).date():
        assert "USD CPI m/m" in m["text"]

    sig = st.store.insert({"symbol": "XAU_USD", "mode": "intraday", "call": "BUY", "confidence": 72,
                           "created": int(now.timestamp() * 1000), "levels": {}})
    st.store.close(sig, "hit_tp", 2.0, int(now.timestamp() * 1000))
    e = briefing.eod(st, now)
    assert e["accuracy"] == 100 and e["total_r"] == 2.0 and "1 won" in e["text"]

    b = briefing.generate(st, bus, "eod", "Asia/Bahrain")
    assert st.store.briefings(1)[0]["id"] == b["id"] and bus.snapshot["briefing"]["kind"] == "eod"
