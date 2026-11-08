"""Self-learning after every closed trade, transparent and bounded:
- intraday: agent weights move toward weight = default x accuracy/50 (a 50% agent keeps its default weight);
- scalp: each strategy earns a score adjustment from its recent expectancy (losers need more confluence to fire).
Every change is logged; learning can be frozen or reset. Technical has no weight — it times the trade."""
import logging
import time
from typing import Any

from jarvis import performance
from jarvis.bus import Bus
from jarvis.station.store import Store

log = logging.getLogger("jarvis.learning")


class Learning:
    def __init__(self, store: Store, bus: Bus, station_cfg: dict[str, Any], cfg: dict[str, Any], tz: str) -> None:
        self.store, self.bus, self.cfg, self.tz = store, bus, cfg, tz
        self.weights: dict[str, float] = station_cfg["weights"]  # the commander's live dict — updated in place
        self.defaults = dict(self.weights)
        self.weights.update(store.get_setting("weights", {}))
        self.frozen: bool = store.get_setting("weights_frozen", False)
        # strategy -> score points added to its setups (live dict, read by the FX scalp desk)
        self.adjust: dict[str, int] = store.get_setting("strategy_adj", {})

    def _save(self) -> None:
        self.store.set_setting("weights", self.weights)
        self.store.set_setting("strategy_adj", self.adjust)
        self.bus.publish({"type": "learning", **self.state()}, key="learning")

    def update(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        now_ms = now_ms or int(time.time() * 1000)
        if self.frozen:
            return []
        c = self.cfg
        acc = performance.agents(performance.from_signals(self.store.recent(c["lookback"], "intraday")))
        changes = []
        for agent, w in list(self.weights.items()):
            a = acc.get(agent)
            if agent == "technical" or not a or a["graded"] < c["min_samples"]:
                continue
            # converge toward a target instead of stepping each time: re-running on the same history can't ratchet
            target = max(c["min_weight"], min(c["max_weight"], self.defaults.get(agent, w) * a["accuracy"] / 50))
            if agent == "news":  # its value is unproven: it may lose weight but never gain it
                target = min(target, self.defaults.get(agent, w))
            delta = max(-c["max_step"], min(c["max_step"], c["lr"] * (target - w)))
            new = round(max(c["min_weight"], min(c["max_weight"], w + delta)), 3)
            if new != w:
                reason = f"accuracy {a['accuracy']}% over {a['graded']} graded votes"
                self.store.log_weight(now_ms, agent, w, new, reason)
                self.weights[agent] = new
                changes.append({"agent": agent, "old": w, "new": new, "reason": reason})
        changes += self._strategies(now_ms)
        if changes:
            log.info("learning updated: %s", changes)
        if changes:
            self.store.set_setting("evaluation_since", now_ms)  # tuned on live results: the clean evaluation starts over
        self._save()
        return changes

    def _strategies(self, now_ms: int) -> list[dict[str, Any]]:
        """Scalp strategy score adjustment: expectancy x 20 points, -15..+10, moving at most 3 points per trade."""
        c, changes = self.cfg, []
        trades = performance.from_signals(self.store.recent(c["lookback"], "scalp"))
        for name, st in performance.group(trades, "strategies").items():
            if st["trades"] < c.get("scalp_min_samples", 10):
                continue
            old = self.adjust.get(name, 0)
            target = max(-15, min(10, round(st["expectancy"] * 20)))
            new = old + max(-3, min(3, target - old))
            if new != old:
                reason = f"expectancy {st['expectancy']:+.2f}R over {st['trades']} scalps"
                self.store.log_weight(now_ms, f"scalp:{name}", old, new, reason)
                self.adjust[name] = new
                changes.append({"agent": f"scalp:{name}", "old": old, "new": new, "reason": reason})
        return changes

    def reset(self) -> None:
        now_ms = int(time.time() * 1000)
        for name, adj in self.adjust.items():
            if adj:
                self.store.log_weight(now_ms, f"scalp:{name}", adj, 0, "reset to config")
        self.adjust.clear()
        self.store.set_setting("evaluation_since", now_ms)
        for agent, w in self.defaults.items():
            if self.weights.get(agent) != w:
                self.store.log_weight(now_ms, agent, self.weights.get(agent, w), w, "reset to config")
        self.weights.update(self.defaults)
        self._save()

    def freeze(self, frozen: bool) -> None:
        if self.frozen and not frozen:
            self.store.set_setting("evaluation_since", int(time.time() * 1000))
        self.frozen = frozen
        self.store.set_setting("weights_frozen", frozen)
        self._save()

    def state(self) -> dict[str, Any]:
        trades = performance.from_signals(self.store.recent(self.cfg["lookback"], "intraday"))
        return {"weights": dict(self.weights), "defaults": self.defaults, "frozen": self.frozen,
                "agents": performance.agents(trades), "strategies": dict(self.adjust), "log": self.store.weight_log(30), "min_samples": self.cfg["min_samples"]}

    async def run(self) -> None:
        """Learn the moment an intraday or scalp signal closes with a result."""
        self.bus.publish({"type": "learning", **self.state()}, key="learning")
        q = self.bus.subscribe()
        while True:
            e = await q.get()
            try:
                # closed_at recent: skip closed signals replayed from the bus snapshot at startup
                if e.get("type") == "signal" and e.get("mode") in ("intraday", "scalp") and e.get("result_r") is not None \
                        and e["status"] != "active" and time.time() * 1000 - (e.get("closed_at") or 0) < 120_000:
                    self.update()
            except Exception as ex:
                self.bus.failed("learning", ex)
