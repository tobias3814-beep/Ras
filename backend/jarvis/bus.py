"""In-process asyncio event bus: feeds publish, websocket clients (and later agents) subscribe."""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from weakref import WeakSet

Event = dict[str, Any]
log = logging.getLogger("jarvis.bus")
FAULT_WINDOW_S = 600  # one alert per part per 10 minutes; /health stays unhealthy for as long


class Bus:
    def __init__(self) -> None:
        self._subs: WeakSet[asyncio.Queue[Event]] = WeakSet()  # a crashed consumer's queue goes away with it
        # latest event per key, replayed to new subscribers so the UI fills instantly
        self.snapshot: dict[str, Event] = {}
        self.failures: dict[str, dict[str, Any]] = {}  # part of Ras -> {count, at (s), error}

    def publish(self, event: Event, key: str | None = None) -> None:
        if key:
            self.snapshot[key] = event
        for q in self._subs:
            if q.full():  # slow client: drop its oldest event rather than block feeds
                q.get_nowait()
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        for e in self.snapshot.values():
            q.put_nowait(e)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subs.discard(q)

    # ---------- faults ----------
    def failed(self, name: str, err: BaseException | str) -> None:
        """Part of Ras failed: log it, count it for /health, and tell the user (voice, Telegram, the UI)."""
        now, f = time.time(), self.failures.setdefault(name, {"count": 0, "at": 0.0, "error": ""})
        tell = now - f["at"] >= FAULT_WINDOW_S
        f.update(count=f["count"] + 1, at=now, error=repr(err)[:200])
        log.error("%s failed: %r", name, err)
        if tell:
            self.publish({"type": "fault", "name": name, "error": f["error"], "created": int(now * 1000)})
            self.publish_health()

    def healthy(self) -> bool:
        return all(time.time() - f["at"] >= FAULT_WINDOW_S for f in self.failures.values())

    def publish_health(self) -> None:
        recent = [n for n, f in self.failures.items() if time.time() - f["at"] < FAULT_WINDOW_S]
        self.publish({"type": "status", "feed": "ras", "state": "error" if recent else "live",
                      "detail": f"failed: {', '.join(recent)}" if recent else ""}, key="status:ras")

    async def supervise(self, name: str, fn: Callable[..., Awaitable[Any]], *args: Any) -> None:
        """Run a background loop. If it crashes, report that and start it again, backing off up to a minute."""
        delay = 1.0
        while True:
            began = time.monotonic()
            try:
                await fn(*args)
                return
            except Exception as e:
                self.failed(name, e)
            delay = 1.0 if time.monotonic() - began > 60 else min(delay * 2, 60.0)
            await asyncio.sleep(delay)
