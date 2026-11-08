"""Trading sessions in exchange-local time (zoneinfo handles DST), queried in UTC ms."""
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ACTIVE = ("london_open", "ny_open", "overlap")  # where scalps may fire; USD/JPY also gets tokyo


def window(spec: dict[str, str], day_utc: datetime) -> tuple[int, int]:
    """(start, end) UTC ms of a session on the local date that contains day_utc."""
    tz = ZoneInfo(spec["tz"])
    local = day_utc.astimezone(tz).date()
    s = datetime.combine(local, datetime.strptime(spec["start"], "%H:%M").time(), tz)
    e = datetime.combine(local, datetime.strptime(spec["end"], "%H:%M").time(), tz)
    return int(s.timestamp() * 1000), int(e.timestamp() * 1000)


def active(sessions: dict[str, Any], now_ms: int) -> list[str]:
    now = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    on = [n for n, spec in sessions.items() if (w := window(spec, now))[0] <= now_ms < w[1]]
    if "london" in on and "new_york" in on:
        on.append("overlap")
    return on


def scalp_session(sym: str, sessions: dict[str, Any], now_ms: int) -> str | None:
    """The active scalp window for this instrument, or None."""
    on = active(sessions, now_ms)
    allowed = ACTIVE + (("tokyo",) if sym == "USD_JPY" else ())
    return next((s for s in ("overlap", "london_open", "ny_open", "tokyo") if s in on and s in allowed), None)


def near_rollover(now_ms: int, minutes: float) -> bool:
    """Within `minutes` of the 17:00 New York daily rollover."""
    ny = ZoneInfo("America/New_York")
    now = datetime.fromtimestamp(now_ms / 1000, ny)
    roll = now.replace(hour=17, minute=0, second=0, microsecond=0)
    return any(abs((now - r).total_seconds()) <= minutes * 60 for r in (roll, roll - timedelta(days=1), roll + timedelta(days=1)))


def today(sessions: dict[str, Any], now_ms: int, tz: str) -> list[dict[str, Any]]:
    """Today's session windows, with start/end formatted in the user's time zone (for the session clock)."""
    now = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    on = active(sessions, now_ms)
    out = []
    for name, spec in sessions.items():
        s, e = window(spec, now)
        fmt = lambda ms: datetime.fromtimestamp(ms / 1000, ZoneInfo(tz)).strftime("%H:%M")  # noqa: E731
        out.append({"name": name, "start": s, "end": e, "start_local": fmt(s), "end_local": fmt(e), "open": name in on})
    return out
