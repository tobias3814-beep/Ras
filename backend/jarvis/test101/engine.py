"""TEST 101 desk: fetches free Yahoo 5-minute bars for the universe, runs rules.run_day on today's closed bars after
every bar, keeps a paper account of its own, backtests the same rules on the last 60 days, and catches up days the Mac
slept through from the same bars (marked "caught up"). Paper only: it never talks to a broker."""
import asyncio
import logging
import time
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import httpx
import numpy as np

from jarvis.test101 import rules
from jarvis.test101.rules import BAR, NY, Day

log = logging.getLogger("jarvis.test101")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Ras/1.1", "Accept": "application/json"}
KEY, BT_KEY = "test101", "test101_backtest"
NEWS_TAGS = {"earnings": r"earnings|results|quarter|guidance|eps", "analyst": r"upgrade|downgrade|price target|analyst",
             "deal": r"acqui|merger|buyout|deal", "halt": r"halt", "regulatory": r"sec |fda|probe|lawsuit|antitrust",
             "offering": r"offering|dilut"}


def _days(js: dict[str, Any]) -> dict[date, Day]:
    """Yahoo chart JSON -> regular-session Day per date (null bars dropped, off-grid live ticks dropped)."""
    res = js["chart"]["result"][0]
    ts = np.array(res.get("timestamp") or [], dtype=np.int64)
    q = res["indicators"]["quote"][0]
    cols = {k: np.array([np.nan if x is None else x for x in q.get(k) or []], dtype=float) for k in ("open", "high", "low", "close", "volume")}
    if not len(ts):
        return {}
    ok = (ts % BAR == 0) & ~np.isnan(cols["close"]) & ~np.isnan(cols["open"])
    out: dict[date, Day] = {}
    dates = np.array([datetime.fromtimestamp(int(t), NY).date() for t in ts])
    for d in sorted(set(dates[ok])):
        o, c = rules.session(d)
        m = ok & (dates == d) & (ts >= o) & (ts < c)
        if m.any():
            out[d] = Day(d, ts[m], cols["open"][m], cols["high"][m], cols["low"][m], cols["close"][m], np.nan_to_num(cols["volume"][m]))
    return out


class Test101:
    def __init__(self, bus: Any, store: Any, halt: Any, cfg: dict[str, Any] | None = None) -> None:
        self.bus, self.store, self.halt = bus, store, halt
        self.state = store.get_setting(KEY, {}) or {}
        self.state.setdefault("config", {})
        self.state.setdefault("days", {})
        self.state.setdefault("start", rules.DEFAULTS["start_balance"])
        self.history: dict[str, dict[date, Day]] = {}
        self.history_day: date | None = None
        self.today: dict[str, Any] | None = None
        self.tags: dict[str, list[str]] = {}
        self.fetched = {"ok": 0, "failed": [], "at": 0}
        self.last_bar = 0
        self.bt_running = False
        self.client: httpx.AsyncClient | None = None

    # ---------- settings and account ----------
    @property
    def cfg(self) -> dict[str, Any]:
        return {**rules.DEFAULTS, **self.state["config"]}

    def save(self) -> None:
        self.store.set_setting(KEY, self.state)

    def set_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        self.state["config"].update(changes)
        self.save()
        return self.cfg

    def balance(self) -> float:
        return round(self.state["start"] + sum(d.get("pnl", 0) for k, d in self.state["days"].items()
                                               if not (self.today and k == self.today["date"])) + (self.today or {}).get("pnl", 0), 2)

    # ---------- data ----------
    async def _chart(self, sym: str, rng: str) -> dict[date, Day] | None:
        assert self.client
        for host in ("query2", "query1"):
            try:
                r = await self.client.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": rng, "interval": "5m"})
                if r.status_code == 429 or r.status_code >= 500:
                    await asyncio.sleep(2)
                    continue
                r.raise_for_status()
                return _days(r.json())
            except Exception as e:  # one bad symbol must not stop the rest
                log.info("test101 chart %s %s failed: %r", sym, host, e)
        return None

    async def fetch(self, rng: str) -> dict[str, dict[date, Day]]:
        self.client = self.client or httpx.AsyncClient(timeout=25, headers=UA, follow_redirects=True)
        sem = asyncio.Semaphore(4)

        async def one(s: str) -> tuple[str, dict[date, Day] | None]:
            async with sem:
                return s, await self._chart(s, rng)

        got = dict(await asyncio.gather(*(one(s) for s in self.cfg["universe"])))
        self.fetched = {"ok": sum(v is not None for v in got.values()), "failed": [s for s, v in got.items() if v is None], "at": int(time.time())}
        return {s: v for s, v in got.items() if v}

    async def news(self, sym: str) -> list[dict[str, Any]]:
        """trader-news, Yahoo part: the stock's latest headlines with the skill's tags (no SEC filings here)."""
        import re
        self.client = self.client or httpx.AsyncClient(timeout=25, headers=UA, follow_redirects=True)
        r = await self.client.get("https://feeds.finance.yahoo.com/rss/2.0/headline", params={"s": sym, "region": "US", "lang": "en-US"})
        r.raise_for_status()
        out = []
        for it in ElementTree.fromstring(r.text).iter("item"):
            title = (it.findtext("title") or "").strip()
            out.append({"title": title, "url": it.findtext("link"), "time": it.findtext("pubDate"),
                        "tags": [k for k, rx in NEWS_TAGS.items() if re.search(rx, title, re.I)]})
        return out[:12]

    def pools_and_tags(self, hist: dict[str, dict[date, Day]], before: date) -> tuple[dict[str, Any], dict[str, list[str]]]:
        """Time machine pools (full sessions before `before`) and the morning scan tags, on closes up to the day before."""
        pools, tags = {}, {}
        cfg = self.cfg
        for s, days in hist.items():
            past = [dy for d, dy in sorted(days.items()) if d < before][-60:]
            pools[s] = rules.pool(past)
            closes = np.array([float(dy.c[-1]) for dy in past])
            vol = float(past[-1].v.sum()) if past else 0
            if len(closes) and closes[-1] >= cfg["min_price"] and vol >= cfg["min_volume"]:
                tags[s] = rules.scan_tags(closes)
        return pools, tags

    # ---------- the day ----------
    def halts_for(self, day: str) -> list[list[int | None]]:
        return self.state["days"].get(day, {}).get("halts", [])

    def simulate(self, hist: dict[str, dict[date, Day]], d: date, now: int | None, cash: float, record: dict[str, Any] | None = None,
                 cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
        days = {s: v[d] for s, v in hist.items() if d in v}
        if not days:
            return None
        pools, tags = self.pools_and_tags(hist, d)
        rec = record or {}
        return rules.run_day(days, pools, cfg or self.cfg, cash, now, rec.get("halts"), rec.get("kill_at"), tags)

    def note_halt(self, now: int) -> None:
        """Keep the spans Ras was halted today, so the replay of today blocks entries in them."""
        if not self.today:
            return
        rec = self.state["days"].setdefault(self.today["date"], {})
        spans = rec.setdefault("halts", [])
        if self.halt.active and (not spans or spans[-1][1] is not None):
            spans.append([now, None])
        elif not self.halt.active and spans and spans[-1][1] is None:
            spans[-1][1] = now

    def flatten(self, now: int, why: str = "kill switch") -> int:
        """Kill switch: whatever TEST 101 holds today is closed at the last price, and no new entries today."""
        if not self.today:
            return 0
        rec = self.state["days"].setdefault(self.today["date"], {})
        rec["kill_at"] = now // 1000 if now > 10**11 else now
        self.save()
        return sum(t["status"] == "open" for t in self.today["trades"])

    def publish(self) -> None:
        self.bus.publish({"type": "test101", "updated": int(time.time() * 1000), "balance": self.balance(),
                          "today": self.today and {k: self.today[k] for k in ("date", "pnl", "open_pnl")}}, key="test101")

    def _alerts(self, old: dict[str, Any] | None, new: dict[str, Any], live_from: int) -> None:
        """trader-alerts, inside Ras: say each new paper fill and each exit once, and only what happened while the desk
        was watching (a replay of the morning after a late start stays quiet)."""
        was = {(t["symbol"], t["trigger_time"]): t["status"] for t in (old or {}).get("trades", [])}
        for t in new["trades"]:
            k = (t["symbol"], t["trigger_time"])
            if old is None and t["trigger_time"] < live_from:
                continue
            if t["status"] in ("open", "closed") and was.get(k) not in ("open", "closed") and t["trigger_time"] >= live_from:
                self.bus.publish({"type": "test101_alert", "created": int(time.time() * 1000), "kind": "entry", "symbol": t["symbol"],
                                  "shares": t["shares"], "fill": t["fill"], "stop": t["plan"]["stop"], "target": t["plan"]["target"]})
            if t["status"] == "closed" and was.get(k) != "closed" and (t["exit_time"] or 0) >= live_from:
                self.bus.publish({"type": "test101_alert", "created": int(time.time() * 1000), "kind": "exit", "symbol": t["symbol"],
                                  "reason": t["reason"], "pnl": t["pnl"]})

    async def ensure_history(self, today: date) -> None:
        if self.history_day != today or not self.history:
            got = await self.fetch("60d")
            if got:
                self.history, self.history_day = got, today
                self.tags = self.pools_and_tags(got, today)[1]

    def catch_up(self, today: date) -> int:
        """Trading days since the desk started that it never finished live (the Mac was asleep or Ras was off): replay
        them from the same bars with the same rules, marked "caught up"."""
        n = 0
        since = date.fromisoformat(self.state.get("since") or today.isoformat())
        for d in sorted({d for v in self.history.values() for d in v}):
            k = d.isoformat()
            if d < since or d >= today or self.state["days"].get(k, {}).get("closed"):
                continue
            rec = self.state["days"].get(k, {})
            cash = self.state["start"] + sum(r.get("pnl", 0) for kk, r in self.state["days"].items() if kk < k)
            res = self.simulate(self.history, d, None, cash, rec)
            if res:
                self.state["days"][k] = {**rec, "trades": res["trades"], "pnl": res["pnl"], "closed": True, "caught_up": True}
                n += 1
        return n

    async def tick(self) -> None:
        now = int(time.time())
        today = datetime.fromtimestamp(now, NY).date()
        self.state.setdefault("since", today.isoformat())
        await self.ensure_history(today)
        if self.catch_up(today):
            self.save()
        if not rules.trading_day(today):
            self.today = None
            return
        k = rules.clock(today, self.cfg)
        if now < k["open"] - 600:
            return
        bar = (now - k["open"]) // BAR
        if bar == self.last_bar and self.today:
            self.note_halt(now)
            return
        got = await self.fetch("1d")
        hist = {s: {**self.history.get(s, {}), **v} for s, v in got.items()}
        for v in hist.values():  # keep closed bars; remember a forming bar's open as the market price now
            dy = v.get(today)
            if dy is not None:
                closed = dy.t + BAR <= now
                if not closed.all():
                    v[today] = Day(today, dy.t[closed], dy.o[closed], dy.h[closed], dy.l[closed], dy.c[closed], dy.v[closed], float(dy.o[~closed][0]))
        self.note_halt(now)
        rec = self.state["days"].get(today.isoformat(), {})
        rec.setdefault("live_from", now)  # trades that triggered before this were replayed, not watched
        cash = self.state["start"] + sum(d.get("pnl", 0) for kk, d in self.state["days"].items() if kk != today.isoformat())
        res = self.simulate(hist, today, now if now < k["close"] + BAR else None, cash, rec)
        if not res:
            return
        for t in res["trades"]:
            t["live"] = t["trigger_time"] >= rec["live_from"]
        old, self.today = self.today, res
        self._alerts(old, res, rec["live_from"])
        self.state["days"][today.isoformat()] = {**rec, "trades": res["trades"], "pnl": res["pnl"], "closed": now >= k["close"] + BAR}
        self.last_bar = bar
        self.save()
        self.publish()

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("test101 tick failed")
                raise
            await asyncio.sleep(30)

    # ---------- backtest ----------
    def backtest(self) -> dict[str, Any]:
        """The same chain on every day of the last 60 that has 20 full sessions before it, account carried over; plus
        the same days without the time-machine gate, at 1R, at double costs, and with random entries."""
        hist = self.history
        if not hist:
            raise RuntimeError("no history yet: Yahoo has not answered")
        dates = sorted({d for v in hist.values() for d in v})
        base = self.cfg

        def go(cfg: dict[str, Any], rule: Any = None) -> dict[str, Any]:
            cash, trades, curve, per_day = cfg["start_balance"], [], [], []
            for d in dates:
                days = {s: v[d] for s, v in hist.items() if d in v}
                if not all(dy.full() for dy in days.values()) and not any(dy.full() for dy in days.values()):
                    continue
                pools, tags = self.pools_and_tags(hist, d)
                if not any(p is not None for p in pools.values()):
                    continue
                days = {s: dy for s, dy in days.items() if dy.full()}
                res = rules.run_day(days, pools, cfg, cash, None, None, None, tags, rule)
                cash += res["pnl"]
                done = [t for t in res["trades"] if t["status"] == "closed"]
                trades += [{**t, "date": res["date"]} for t in res["trades"]]
                per_day.append({"date": res["date"], "pnl": res["pnl"], "trades": len(done)})
                curve.append({"time": rules.session(d)[1], "equity": round(cash, 2)})
            st = rules.stats(trades)
            eq = np.array([cfg["start_balance"]] + [c["equity"] for c in curve])
            st["max_dd_usd"] = round(float((np.maximum.accumulate(eq) - eq).max()), 2) if len(eq) else 0
            st["days"] = len(per_day)
            return {"stats": st, "verdict": rules.verdict(st), "curve": curve, "per_day": per_day, "trades": trades}

        cheap = {**base, "replays": 2000}
        main = go(cheap)
        rng = np.random.default_rng(base["seed"])

        def random_rule(sym: str, dy: Day) -> dict[str, Any]:
            """Same opening range and stop rule, but entered at a random bar in the entry window instead of on the breakout."""
            e = rules.entry(dy, cheap)
            if e["status"] in ("no data", "incomplete", "forming"):
                return e
            k = rules.clock(dy.d, cheap)
            ok = [i for i in range(len(dy.t)) if k["first"] <= int(dy.t[i]) + BAR <= k["last"] and dy.c[i] > (e["or_high"] + e["or_low"]) / 2]
            if not ok or rng.random() > 0.5:
                return {**e, "status": "waiting"}
            i = int(rng.choice(ok))
            return {**e, "status": "triggered", "i": i, "time": int(dy.t[i]) + BAR, "price": float(dy.c[i])}

        variants = {"no_gate": go({**cheap, "tm_gate": False}), "one_r": go({**cheap, "target_r": 1.0}),
                    "double_costs": go({**cheap, "slippage_pct": base["slippage_pct"] * 2}), "random_entries": go({**cheap, "tm_gate": False}, random_rule)}
        half = len(main["per_day"]) // 2
        cut = main["per_day"][half]["date"] if half else None
        split = {"first": rules.stats([t for t in main["trades"] if cut and t["date"] < cut]),
                 "second": rules.stats([t for t in main["trades"] if cut and t["date"] >= cut])}
        return {"created": int(time.time() * 1000), "config": {k: v for k, v in base.items() if k != "universe"}, "universe": base["universe"],
                "range": [main["per_day"][0]["date"], main["per_day"][-1]["date"]] if main["per_day"] else None,
                "main": main, "variants": {k: {"stats": v["stats"], "verdict": v["verdict"]} for k, v in variants.items()}, "split": split,
                "notes": ["Yahoo keeps 60 days of 5-minute bars and the time machine needs 20 sessions before a day, so this covers about 40 days. That is a small sample.",
                          "Entries fill at the next bar's open plus slippage, stops at the stop or worse on a gap, the rest at the 15:50 bar's close.",
                          "The time machine here uses 2,000 replays a trade to keep the run quick; live uses 10,000. Same seed.",
                          "Random entries use the same stocks, days, stops and targets but enter at a random bar above the range midpoint. If the breakout can't beat that, the entry rule adds nothing."]}

    async def run_backtest(self) -> dict[str, Any]:
        if self.bt_running:
            raise RuntimeError("a TEST 101 backtest is already running")
        self.bt_running = True
        try:
            today = datetime.now(NY).date()
            await self.ensure_history(today)
            res = await asyncio.to_thread(self.backtest)
            self.store.set_setting(BT_KEY, res)
            return res
        finally:
            self.bt_running = False

    def snapshot(self) -> dict[str, Any]:
        now = int(time.time())
        today = datetime.fromtimestamp(now, NY).date()
        cfg = self.cfg
        k = rules.clock(today, cfg) if rules.trading_day(today) else None
        phase = "closed today (weekend or NYSE holiday)" if not k else "before the open" if now < k["open"] else \
            "opening range forming" if now < k["or_end"] else "entry window" if now < k["last"] else \
            "no new entries, managing open trades" if now < k["flat"] else "after flat-by" if now < k["close"] else "after the close"
        past = {d: r for d, r in sorted(self.state["days"].items()) if not (self.today and d == self.today["date"])}
        live_trades = [t for r in past.values() if not r.get("caught_up") for t in r.get("trades", [])
                       if r.get("live_from") is not None and t["trigger_time"] >= r["live_from"]]
        return {"config": cfg, "phase": phase, "now": now, "balance": self.balance(), "start": self.state["start"],
                "since": self.state.get("since"), "today": self.today, "tags": self.tags,
                "fetched": self.fetched, "halted": bool(self.halt.active),
                "days": [{"date": d, "pnl": r.get("pnl", 0), "trades": sum(t["status"] == "closed" for t in r.get("trades", [])),
                          "caught_up": bool(r.get("caught_up"))} for d, r in past.items()],
                "forward": {"stats": rules.stats(live_trades), "verdict": rules.verdict(rules.stats(live_trades))},
                "backtest": self.store.get_setting(BT_KEY, None), "bt_running": self.bt_running}
