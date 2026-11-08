"""Ras's brain: remembers every trade, learns lessons from repeated mistakes, and estimates each setup's chance.

Nothing here is trusted on its word. A lesson is found on older trades only and must then hold on newer trades it never
saw before it may act; a win-chance model must beat a plain base rate on trades it was not trained on before it gets a
say. Everything acts on paper trading only, can be switched off, and every skipped trade is written to the journal.

Memory comes from Ras's own trades: closed paper signals (live) and the trades of its Backtest-tab replays, which run the
same signal logic on history. Each trade is described by the same conditions, computed from the hourly candles that had
closed before it opened, so live and replayed trades can be compared."""
import logging
import math
import time
from collections.abc import Callable
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from jarvis.indicators import atr, ema

log = logging.getLogger("jarvis.brain")

MODES = ("intraday", "scalp", "trend")
HOLDOUT = 0.3            # the newest 30% of memory is kept back to test what was learnt on the rest
FIND = {"n": 40, "edge": -0.10, "t": -3.0}  # a pattern must lose clearly and often on the older trades
CHECK = {"n": 15, "gap": 0.05}  # and still lose, by 0.05R a trade more than the rest, on at least this many newer trades
MODEL = {"auc": 0.55, "z": 2.0, "skill": 0.0}
FEATURES = ("symbol", "side", "strategy", "session", "trend", "volatility", "stretch", "weekday", "confidence")

NAMES = {
    "side": {"BUY": "buys", "SELL": "sells"},
    "session": {"asia": "in Asian hours", "london": "in the London morning", "overlap": "in the London-New York overlap",
                "newyork": "in the New York afternoon", "late": "late in the US evening"},
    "trend": {"with": "with the hourly trend", "against": "against the hourly trend"},
    "volatility": {"quiet": "when the market is quiet", "normal": "at normal volatility", "wild": "when the market is wild"},
    "stretch": {"near": "near the 20-hour average", "stretched": "far from the 20-hour average"},
    "weekday": {d: f"on {d}s" for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Sunday", "Saturday")},
    "confidence": {"low": "at low confidence (under 60)", "mid": "at confidence 60-74", "high": "at confidence 75+"},
}
SYMBOL_NAME = {"XAU_USD": "gold", "XAG_USD": "silver", "WTI_USD": "oil", "EUR_USD": "EUR/USD", "GBP_USD": "GBP/USD",
               "USD_JPY": "USD/JPY"}


# ---------- conditions ----------

def session(ms: int) -> str:
    h = time.gmtime(ms / 1000).tm_hour
    return "asia" if h < 7 else "london" if h < 12 else "overlap" if h < 16 else "newyork" if h < 21 else "late"


def conditions(hour: pd.DataFrame | None, ms: int, side: int, symbol: str, strategy: str, confidence: float | None) -> dict[str, str]:
    """The trade's conditions from hourly candles that closed before it opened (no peeking at its own bar)."""
    f = {"symbol": symbol, "side": "BUY" if side > 0 else "SELL", "strategy": strategy, "session": session(ms),
         "weekday": time.strftime("%A", time.gmtime(ms / 1000)),
         "confidence": "" if confidence is None else "low" if confidence < 60 else "mid" if confidence < 75 else "high"}
    if hour is not None and len(hour):
        h = hour[hour.time + 3600 <= ms / 1000]
        if len(h) >= 120 and ms / 1000 - float(h.time.iat[-1]) < 3.5 * 86_400:  # stale candles (gap in history) say nothing
            h = h.iloc[-300:]
            c, e20, e50 = float(h.close.iat[-1]), float(ema(h.close, 20).iat[-1]), float(ema(h.close, 50).iat[-1])
            a = atr(h, 14)
            now, usual = float(a.iat[-1]), float(a.iloc[-100:].median())
            f["trend"] = "with" if (c > e50) == (side > 0) else "against"
            f["volatility"] = "" if not usual > 0 else "quiet" if now < 0.8 * usual else "wild" if now > 1.25 * usual else "normal"
            f["stretch"] = "" if not now > 0 else "stretched" if abs(c - e20) / now > 1.5 else "near"
    return {k: v for k, v in f.items() if v}


def strategy_of(trade: dict[str, Any]) -> str:
    s = trade.get("strategies") or []
    return ",".join(s) if s else {"intraday": "technical"}.get(trade.get("mode", ""), trade.get("mode", ""))


# ---------- memory ----------

def from_backtests(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trades from Backtest-tab replays, once each (overlapping runs repeat the same trades)."""
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for res in results:
        for t in res.get("trades") or []:
            if t.get("mode") in MODES and t.get("r") is not None:
                seen[(t["symbol"], t["mode"], t["time"], t["call"])] = {
                    "source": "backtest", "symbol": t["symbol"], "mode": t["mode"], "side": 1 if t["call"] == "BUY" else -1,
                    "time": int(t["time"]), "r": float(t["r"]), "strategy": strategy_of(t), "confidence": t.get("confidence"),
                    "status": t.get("status", "")}
    return list(seen.values())


def from_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Closed paper signals (FX, metals and oil)."""
    out = []
    for s in signals:
        if s.get("mode") in MODES and s.get("result_r") is not None:
            strat = [r.split(":")[0] for r in s.get("reasons", []) if ":" in r and not r.startswith(("Regime", "News", "Spread"))]
            out.append({"source": "live", "id": s.get("id"), "symbol": s["symbol"], "mode": s["mode"], "side": 1 if s["call"] == "BUY" else -1,
                        "time": int(s["created"]), "r": float(s["result_r"]), "confidence": s.get("confidence"), "status": s.get("status", ""),
                        "strategy": ",".join(strat) if s["mode"] == "scalp" and strat else {"intraday": "technical"}.get(s["mode"], s["mode"])})
    return out


def describe(m: dict[str, Any]) -> str:
    c = m["conditions"]
    parts = [f"{'Won' if m['r'] > 0 else 'Lost'} {m['r']:+.2f}R on a {m['mode']} {c['side'].lower()} of {SYMBOL_NAME.get(m['symbol'], m['symbol'])}",
             f"({c['strategy']})", NAMES["session"].get(c.get("session", ""), "")]
    parts += [NAMES[k][c[k]] for k in ("trend", "volatility", "stretch") if c.get(k)]
    return " ".join(p for p in parts if p) + "."


# ---------- lessons ----------

def _t(x: np.ndarray) -> float:
    if len(x) < 3:
        return 0.0
    sd = float(x.std(ddof=1))
    return float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 0 else (-math.inf if x.mean() < 0 else math.inf if x.mean() > 0 else 0.0)


def lesson_text(when: dict[str, str]) -> str:
    mode = when["mode"]
    what = NAMES["side"].get(when.get("side", ""), "trades")
    where = SYMBOL_NAME.get(when["symbol"], when["symbol"]) if "symbol" in when else ""
    strat = f" from {when['strategy']}" if "strategy" in when else ""
    rest = [NAMES[k][v] for k, v in when.items() if k in NAMES and k != "side" and v in NAMES[k]]
    return f"Skip {mode} {what}{' of ' + where if where else ''}{strat} {' and '.join(rest)}".strip().replace("  ", " ") + "."


def find_lessons(memory: list[dict[str, Any]], cut_ms: int) -> list[dict[str, Any]]:
    """Patterns that lost clearly on trades before `cut_ms`, then checked on the trades after it."""
    old = [m for m in memory if m["time"] < cut_ms]
    new = [m for m in memory if m["time"] >= cut_ms]
    out = []
    for mode in MODES:
        o = [m for m in old if m["mode"] == mode]
        n = [m for m in new if m["mode"] == mode]
        if len(o) < FIND["n"]:
            continue
        base = float(np.mean([m["r"] for m in o]))
        keys = sorted({(k, v) for m in o for k, v in m["conditions"].items() if k != "confidence" or v})
        found: list[dict[str, Any]] = []
        for size in (1, 2):
            for combo in combinations(keys, size):
                if len({k for k, _ in combo}) < size:
                    continue
                if any(all(item in combo for item in f["_combo"]) for f in found):
                    continue  # a narrower copy of a pattern already found
                match = np.array([m["r"] for m in o if all(m["conditions"].get(k) == v for k, v in combo)])
                if len(match) < FIND["n"] or match.mean() > min(0.0, base + FIND["edge"]) or _t(match) > FIND["t"]:
                    continue
                found.append({"_combo": combo, "find": {"trades": len(match), "avg_r": round(float(match.mean()), 3),
                                                        "win_rate": round(float((match > 0).mean() * 100), 1), "base_r": round(base, 3)}})
        for f in found:
            combo = f.pop("_combo")
            hit = np.array([m["r"] for m in n if all(m["conditions"].get(k) == v for k, v in combo)])
            rest = np.array([m["r"] for m in n if not all(m["conditions"].get(k) == v for k, v in combo)])
            check = {"trades": len(hit), "avg_r": round(float(hit.mean()), 3) if len(hit) else None,
                     "rest_r": round(float(rest.mean()), 3) if len(rest) else None}
            status = ("watching" if len(hit) < CHECK["n"]
                      else "active" if hit.mean() < 0 and (not len(rest) or hit.mean() < rest.mean() - CHECK["gap"]) else "rejected")
            when = {"mode": mode, **dict(combo)}
            out.append({"id": "|".join(f"{k}={v}" for k, v in when.items()), "when": when, "text": lesson_text(when),
                        "status": status, **f, "check": check,
                        "saves_r": round(-float(hit.sum()), 2) if status == "active" else None})
    order = {"active": 0, "watching": 1, "rejected": 2}
    return sorted(out, key=lambda x: (order[x["status"]], x["find"]["avg_r"]))


def matches(lesson: dict[str, Any], mode: str, cond: dict[str, str]) -> bool:
    return lesson["when"]["mode"] == mode and all(cond.get(k) == v for k, v in lesson["when"].items() if k != "mode")


# ---------- win-chance model ----------

class Chance:
    """Logistic regression on the trade's conditions (one-hot), L2-regularised. Small, readable, and judged on trades
    it did not train on."""

    def __init__(self) -> None:
        self.cols: list[str] = []
        self.w: np.ndarray = np.zeros(0)
        self.b = 0.0

    def _x(self, rows: list[dict[str, Any]]) -> np.ndarray:
        idx = {c: i for i, c in enumerate(self.cols)}
        x = np.zeros((len(rows), len(self.cols)))
        for r, m in enumerate(rows):
            for c in (f"mode={m['mode']}", *(f"{k}={v}" for k, v in m["conditions"].items())):
                if c in idx:
                    x[r, idx[c]] = 1.0
        return x

    def fit(self, rows: list[dict[str, Any]], l2: float = 1.0, steps: int = 600) -> "Chance":
        self.cols = sorted({f"mode={m['mode']}" for m in rows} | {f"{k}={v}" for m in rows for k, v in m["conditions"].items()})
        x, y = self._x(rows), np.array([1.0 if m["r"] > 0 else 0.0 for m in rows])
        w, b = np.zeros(x.shape[1]), float(np.log((y.mean() + 1e-3) / (1 - y.mean() + 1e-3)))
        for _ in range(steps):
            p = 1 / (1 + np.exp(-(x @ w + b)))
            w -= 0.5 * (x.T @ (p - y) / len(y) + l2 * w / len(y))
            b -= 0.5 * float((p - y).mean())
        self.w, self.b = w, b
        return self

    def predict(self, rows: list[dict[str, Any]]) -> np.ndarray:
        return 1 / (1 + np.exp(-(self._x(rows) @ self.w + self.b))) if rows and self.cols else np.full(len(rows), 0.5)

    def top(self, n: int = 6) -> list[dict[str, Any]]:
        order = np.argsort(self.w)
        pick = [*order[:n], *order[::-1][:n]]
        return [{"condition": self.cols[i], "weight": round(float(self.w[i]), 3)} for i in pick]


def auc(p: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return None
    ranks = pd.Series(np.r_[pos, neg]).rank().to_numpy()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def judge_model(memory: list[dict[str, Any]], cut_ms: int) -> dict[str, Any]:
    """Train on older trades, judge on newer ones: does it beat 'every trade wins at the usual rate'?"""
    old = [m for m in memory if m["time"] < cut_ms]
    new = [m for m in memory if m["time"] >= cut_ms]
    if len(old) < 200 or len(new) < 60:
        return {"earned": False, "why": "not enough trades yet to judge it", "trades": len(memory)}
    model = Chance().fit(old)
    p = model.predict(new)
    y = np.array([1.0 if m["r"] > 0 else 0.0 for m in new])
    r = np.array([m["r"] for m in new])
    base = float(np.mean([1.0 if m["r"] > 0 else 0.0 for m in old]))
    brier, brier0 = float(((p - y) ** 2).mean()), float(((base - y) ** 2).mean())
    skill = 1 - brier / brier0 if brier0 > 0 else 0.0
    a = auc(p, y)
    cut = float(np.quantile(model.predict(old), 0.2))
    low, rest = r[p <= cut], r[p > cut]
    buckets = []
    for lo, hi in zip(np.quantile(p, [0, .2, .4, .6, .8]), np.quantile(p, [.2, .4, .6, .8, 1])):
        sel = (p >= lo) & (p <= hi)
        if sel.any():
            buckets.append({"predicted": round(float(p[sel].mean()) * 100, 1), "actual": round(float(y[sel].mean()) * 100, 1),
                            "avg_r": round(float(r[sel].mean()), 3), "trades": int(sel.sum())})
    n1, n0 = int(y.sum()), int(len(y) - y.sum())
    z = (a - 0.5) / math.sqrt((n1 + n0 + 1) / (12 * n1 * n0)) if a is not None and n1 and n0 else 0.0  # is the ranking more than luck?
    earned = bool(a is not None and a >= MODEL["auc"] and z >= MODEL["z"] and skill > MODEL["skill"] and len(low) >= 15
                  and len(rest) and low.mean() < rest.mean())
    why = ("it beat the usual win rate on newer trades, and the setups it rated lowest did worse" if earned else
           f"on newer trades it {'ranked winners no better than chance' if (a or 0) < MODEL['auc'] or z < MODEL['z'] else 'did not beat the usual win rate' if skill <= MODEL['skill'] else 'could not pick out the worst setups'}")
    return {"earned": earned, "why": why, "auc": None if a is None else round(a, 3), "z": round(z, 2), "skill": round(skill, 4),
            "trained_on": len(old), "judged_on": len(new), "skip_below": round(cut * 100, 1), "buckets": buckets,
            "skipped_r": round(float(low.mean()), 3) if len(low) else None, "kept_r": round(float(rest.mean()), 3) if len(rest) else None,
            "skipped": int(len(low))}


# ---------- the brain ----------

class Brain:
    """Rebuilds memory, lessons and the model from the store; answers 'should paper take this signal?'."""

    def __init__(self, store: Any, bus: Any, hourly: Callable[[str], pd.DataFrame | None], announce: Callable[[str], None] | None = None) -> None:
        self.store, self.bus, self.hourly, self.announce = store, bus, hourly, announce
        self.memory: list[dict[str, Any]] = []
        self.lessons: list[dict[str, Any]] = []
        self.model_report: dict[str, Any] = {"earned": False, "why": "not built yet"}
        self.model: Chance | None = None
        self.built_at = 0
        self.settings: dict[str, Any] = {"enabled": True, "use_model": True, "off": [], **store.get_setting("brain", {})}
        self._hour_cache: dict[str, pd.DataFrame | None] = {}
        self.skips: list[dict[str, Any]] = store.get_setting("brain_skips", [])
        self.log: list[dict[str, Any]] = store.get_setting("brain_log", [])  # everything it has learnt or remembered, newest first

    def _save_settings(self) -> None:
        self.store.set_setting("brain", self.settings)

    def _hour(self, sym: str) -> pd.DataFrame | None:
        if sym not in self._hour_cache:
            self._hour_cache[sym] = self.hourly(sym)
        return self._hour_cache[sym]

    def remember(self, m: dict[str, Any]) -> dict[str, Any]:
        m["conditions"] = conditions(self._hour(m["symbol"]), m["time"], m["side"], m["symbol"], m["strategy"], m.get("confidence"))
        m["note"] = describe(m)
        return m

    def rebuild(self, signals: list[dict[str, Any]], backtests: list[dict[str, Any]], now_ms: int | None = None) -> dict[str, Any]:
        now_ms = now_ms or int(time.time() * 1000)
        self._hour_cache = {}
        live = from_signals(signals)
        live_keys = {(m["symbol"], m["mode"], m["time"] // 60_000) for m in live}
        replay = [m for m in from_backtests(backtests) if (m["symbol"], m["mode"], m["time"] // 60_000) not in live_keys]
        self.memory = sorted((self.remember(m) for m in live + replay), key=lambda m: m["time"])
        before = {x["id"]: x["status"] for x in self.lessons}
        if self.memory:
            times = np.array([m["time"] for m in self.memory])
            cut = int(np.quantile(times, 1 - HOLDOUT))
            self.lessons = find_lessons(self.memory, cut)
            self.model_report = judge_model(self.memory, cut)
            self.model = Chance().fit(self.memory) if self.model_report.get("earned") else None
            if self.model:
                self.model_report["skip_below"] = round(float(np.quantile(self.model.predict(self.memory), 0.2)) * 100, 1)
                self.model_report["top"] = self.model.top()
        self.built_at = now_ms
        self._record(now_ms)
        known = self.settings.get("announced", [])
        fresh = [x for x in self.lessons if x["status"] == "active" and x["id"] not in known]
        if fresh:
            self.settings["announced"] = sorted({*known, *(x["id"] for x in fresh)})
            self._save_settings()
        if not before:  # first build after start-up: remember what is already known, announce nothing
            fresh = []
        for x in fresh[:3]:
            if self.announce:
                self.announce(f"New lesson learnt: {x['text']}")
        self.bus.publish({"type": "brain", **self.summary()}, key="brain")
        log.info("brain: %d memories, %d active lessons, model %s", len(self.memory),
                 sum(x["status"] == "active" for x in self.lessons), "on" if self.model else "off")
        return self.summary()

    def _record(self, now_ms: int) -> None:
        """Compare with what it knew after the last build (kept across restarts) and write each change to the log."""
        seen = self.store.get_setting("brain_seen", None)
        first = seen is None
        seen = seen or {"lessons": {}, "losses": [], "model": False}
        events: list[dict[str, Any]] = []
        add = lambda kind, text, **k: events.append({"time": now_ms, "kind": kind, "text": text, **k})
        if first:
            add("awake", f"Brain started with {len(self.memory):,} trades in memory.")
        was = seen["lessons"]
        for x in self.lessons:
            old = was.get(x["id"])
            if old == x["status"]:
                continue
            if x["status"] == "active":
                add("learnt", f"Learnt: {x['text']}", lesson=x["id"],
                    why=f"lost {x['find']['avg_r']:+.2f}R a trade on {x['find']['trades']} older trades and still lost on newer ones")
            elif x["status"] == "watching" and old is None:
                add("noticed", f"Noticed a losing pattern, waiting for more trades: {x['text']}", lesson=x["id"])
            elif x["status"] == "rejected" and old == "active":
                add("dropped", f"Dropped: {x['text']}", lesson=x["id"], why="it stopped holding on newer trades")
            elif x["status"] == "rejected":
                add("rejected", f"Checked and rejected: {x['text']}", lesson=x["id"], why="it lost on older trades but not on newer ones, so it was luck")
        now_ids = {x["id"] for x in self.lessons}
        for lid, st in was.items():
            if lid not in now_ids and st == "active":
                add("dropped", f"Forgot a lesson that no longer shows up: {lid}", lesson=lid)
        known = set(seen["losses"])
        fresh = [m for m in self.memory if m["source"] == "live" and m["r"] < 0 and str(m.get("id")) not in known]
        if not first:
            for m in fresh[-20:]:
                add("mistake", f"Remembered a mistake: {m['note']}", symbol=m["symbol"], r=m["r"])
        if bool(self.model) != bool(seen["model"]):
            add("model", "The win-chance model earned a say." if self.model else "The win-chance model lost its say: " + self.model_report.get("why", ""))
        self.store.set_setting("brain_seen", {"lessons": {x["id"]: x["status"] for x in self.lessons},
                                              "losses": sorted(known | {str(m.get("id")) for m in fresh}), "model": bool(self.model)})
        if events:
            self.log = (list(reversed(events)) + self.log)[:500]
            self.store.set_setting("brain_log", self.log)
            self.bus.publish({"type": "brain_learn", "events": events}, key="brain_learn")

    def graph(self) -> dict[str, Any]:
        """Everything for the brain map: lessons, every mistake in memory, and the learning log."""
        act = self.active()
        idx = {x["id"]: i for i, x in enumerate(self.lessons)}
        mistakes = [{"time": m["time"], "symbol": m["symbol"], "mode": m["mode"], "r": m["r"], "source": m["source"], "note": m["note"],
                     "strategy": m["conditions"].get("strategy", ""),
                     "lessons": [idx[x["id"]] for x in act if matches(x, m["mode"], m["conditions"])]}
                    for m in reversed(self.memory) if m["r"] < 0]
        wins = sum(1 for m in self.memory if m["r"] > 0)
        return {"built_at": self.built_at, "memory": len(self.memory), "wins": wins,
                "lessons": [{k: x[k] for k in ("id", "text", "status", "when", "find", "check")} | {"off": x["id"] in self.settings["off"]} for x in self.lessons],
                "mistakes": mistakes, "log": self.log, "model": {"earned": bool(self.model), "auc": self.model_report.get("auc")}}

    def active(self) -> list[dict[str, Any]]:
        return [x for x in self.lessons if x["status"] == "active" and x["id"] not in self.settings["off"]]

    def check(self, signal: dict[str, Any], hour: pd.DataFrame | None = None) -> str | None:
        """None to let paper take the signal, or the reason the brain skips it. A fault here never blocks a trade."""
        try:
            why = self._check(signal, hour)
        except Exception as e:
            log.warning("brain check failed, letting the trade through: %r", e)
            return None
        if why:
            self.skips = [{"time": int(time.time() * 1000), "symbol": signal["symbol"], "mode": signal["mode"],
                           "call": signal.get("call"), "why": why}, *self.skips][:30]
            self.store.set_setting("brain_skips", self.skips)
            self.bus.publish({"type": "brain", **self.summary()}, key="brain")
        return why

    def _check(self, signal: dict[str, Any], hour: pd.DataFrame | None) -> str | None:
        if not self.settings["enabled"] or signal.get("mode") not in MODES:
            return None
        m = from_signals([{**signal, "result_r": 0.0}])
        if not m:
            return None
        m = m[0]
        cond = conditions(hour if hour is not None else self.hourly(m["symbol"]), m["time"], m["side"], m["symbol"], m["strategy"], m.get("confidence"))
        for x in self.active():
            if matches(x, m["mode"], cond):
                return f"lesson — {x['text'][:-1].lower()} (lost {x['find']['avg_r']:+.2f}R a trade on {x['find']['trades']} older trades, " \
                       f"{x['check']['avg_r']:+.2f}R on {x['check']['trades']} newer ones)"
        if self.model and self.settings["use_model"] and self.model_report.get("skip_below") is not None:
            p = float(self.model.predict([{**m, "conditions": cond}])[0]) * 100
            if p <= self.model_report["skip_below"]:
                return f"win chance {p:.0f}%, in the lowest fifth it has seen (skips below {self.model_report['skip_below']:.0f}%)"
        return None

    def set(self, enabled: bool | None = None, use_model: bool | None = None, lesson: str | None = None, on: bool | None = None) -> dict[str, Any]:
        if enabled is not None:
            self.settings["enabled"] = enabled
        if use_model is not None:
            self.settings["use_model"] = use_model
        if lesson is not None and on is not None:
            off = set(self.settings["off"])
            off.discard(lesson) if on else off.add(lesson)
            self.settings["off"] = sorted(off)
        self._save_settings()
        self.bus.publish({"type": "brain", **self.summary()}, key="brain")
        return self.summary()

    def summary(self) -> dict[str, Any]:
        live = [m for m in self.memory if m["source"] == "live"]
        r = np.array([m["r"] for m in self.memory]) if self.memory else np.zeros(0)
        losses = [m for m in reversed(self.memory) if m["r"] < 0]
        return {"built_at": self.built_at, "settings": {k: v for k, v in self.settings.items() if k != "announced"}, "skips": self.skips,
                "memory": {"total": len(self.memory), "live": len(live), "backtest": len(self.memory) - len(live),
                           "avg_r": round(float(r.mean()), 3) if len(r) else None, "win_rate": round(float((r > 0).mean() * 100), 1) if len(r) else None},
                "lessons": [{**x, "off": x["id"] in self.settings["off"]} for x in self.lessons][:60],
                "model": self.model_report,
                "recent_mistakes": [{"time": m["time"], "source": m["source"], "symbol": m["symbol"], "mode": m["mode"], "r": m["r"],
                                     "note": m["note"], "lessons": [x["text"] for x in self.active() if matches(x, m["mode"], m["conditions"])]}
                                    for m in losses[:25]]}
