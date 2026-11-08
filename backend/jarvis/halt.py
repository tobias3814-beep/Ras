"""Kill-switch state. While active, every desk vetoes new signals. Persisted so a restart stays halted."""
import time
from typing import Any

from jarvis.station.store import Store

REASON = "Kill switch active — new signals paused until you resume"


class Halt:
    def __init__(self) -> None:
        self.store: Store | None = None
        self.active, self.since, self.why = False, 0, ""

    def bind(self, store: Store) -> None:
        self.store = store
        s = store.get_setting("halt", {})
        self.active, self.since, self.why = s.get("active", False), s.get("since", 0), s.get("why", "")

    def _save(self) -> None:
        if self.store:
            self.store.set_setting("halt", self.state())

    def activate(self, why: str) -> None:
        self.active, self.since, self.why = True, int(time.time() * 1000), why
        self._save()

    def resume(self) -> None:
        self.active, self.why = False, ""
        self._save()

    def state(self) -> dict[str, Any]:
        return {"active": self.active, "since": self.since, "why": self.why}


halt = Halt()  # one per process; desks import it
