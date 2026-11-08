"""Telegram push: the same alerts Ras speaks, filtered by priority. Optional — needs a bot token + chat id in the keychain."""
import logging
import time
from typing import Any

import httpx

from jarvis.bus import Bus
from jarvis.config import secret
from jarvis.voice import announce

log = logging.getLogger("jarvis.telegram")


class Telegram:
    def __init__(self, bus: Bus, cfg: dict[str, Any], http: httpx.AsyncClient | None = None) -> None:
        self.bus, self.cfg = bus, cfg
        self.token = self.chat = None  # read lazily, after the keychain preload
        self.http = http or httpx.AsyncClient(timeout=15)
        self.start_ms = int(time.time() * 1000)
        self._said_risk: set[str] = set()

    @property
    def configured(self) -> bool:
        self.token = self.token or secret("telegram_bot_token", block=False)
        self.chat = self.chat or secret("telegram_chat_id", block=False)
        return bool(self.token and self.chat)

    async def send(self, text: str) -> bool:
        if not self.configured:
            return False
        try:
            r = await self.http.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                     json={"chat_id": self.chat, "text": f"Ras · {text}", "disable_web_page_preview": True})
            r.raise_for_status()
            return True
        except httpx.HTTPError as e:  # never let a phone alert break the app
            log.warning("telegram send failed: %s", type(e).__name__)
            return False

    def wants(self, e: dict[str, Any], priority: int) -> bool:
        return e.get("type") in ("execution", "kill") or priority >= self.cfg["min_priority"]

    async def run(self) -> None:
        if not self.configured:
            return
        q = self.bus.subscribe()
        while True:
            e = await q.get()
            try:
                said = announce.classify(e, self._said_risk, self.start_ms)
                if said and self.wants(e, said[1]):
                    await self.send(said[0])
            except Exception as ex:
                self.bus.failed("telegram alerts", ex)
