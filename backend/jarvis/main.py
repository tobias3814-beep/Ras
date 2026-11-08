"""Ras backend: FastAPI + one websocket pushing every bus event to the UI."""
import asyncio
import hashlib
import json
import os
import logging
import logging.handlers
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import threading
import time
from typing import Literal

import httpx
import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from jarvis import backtest, catchup, history, journal as jmod, parity, performance

from jarvis.brain import Brain
from jarvis.bus import Bus
from jarvis.config import BUNDLED, ROOT, load_config, preload_secrets
from jarvis.feeds import capital, crypto, macro, news
from jarvis.guard import LocalOnly
from jarvis.journal import Journal, review_scheduler, weekly_review
from jarvis.execution import Executor, Rejected
from jarvis.halt import halt
from jarvis.learning import Learning
from jarvis.limits import AccountWatch
from jarvis.notify import Telegram
from jarvis.voice.assistant import Assistant, Claude, Ollama, Tools
from jarvis.voice.jarvis import Voice
from jarvis.paper import Paper
from jarvis.scalp import sessions
from jarvis.scalp.book import Book
from jarvis.scalp.crypto import CryptoDesk
from jarvis.scalp.fx import FxScalpDesk
from jarvis.station import briefing
from jarvis.station.context import Context
from jarvis.station.engine import Station
from jarvis.station.store import Store
from jarvis.lab import books, engine as lab, live as lab_live, ras11
from jarvis.lab.rules import CATALOG as LAB_CATALOG, RESEARCH_GROUPS as LAB_RESEARCH
from jarvis.timemachine import TimeMachine
from jarvis.trend import TrendDesk
from jarvis.test101.engine import Test101

_fmt = '{"ts":"%(asctime)s","lvl":"%(levelname)s","log":"%(name)s","msg":"%(message)s"}'
(ROOT / "logs").mkdir(parents=True, exist_ok=True)
_file = logging.handlers.RotatingFileHandler(ROOT / "logs" / "jarvis.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(level=logging.INFO, format=_fmt, handlers=[logging.StreamHandler(), _file])
for _h in logging.getLogger().handlers:
    _h.setFormatter(logging.Formatter(_fmt))

logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per poll is noise

cfg = load_config()
bus = Bus()
ctx = Context()
store = Store(ROOT / "data" / "jarvis.sqlite")
halt.bind(store)
store.fail_interrupted()
instruments, tz, scan_cfg = cfg["capital"]["instruments"], cfg["briefings"]["tz"], cfg["crypto"]["scanner"]
CRYPTO = cfg["crypto"].get("enabled", False)  # crypto desk off by default: Ras focuses on FX, metals and oil
modes: dict[str, dict[str, bool]] = {s: {"trend": False, **cfg["scalp"]["default_modes"], **store.get_setting("modes", {}).get(s, {})}
                                     for s in instruments}
station = Station(bus, store, instruments, cfg["station"], ctx, modes)
book = Book(bus, store, ("scalp", "crypto"), cfg["scalp"])


trend_desk = TrendDesk(bus, store, instruments, station.frames, station.prices, modes)


def open_signals() -> list[dict[str, object]]:
    return list(station.active.values()) + list(book.active.values()) + list(trend_desk.active.values())


learning = Learning(store, bus, cfg["station"], cfg["learning"], tz)
if not store.get_setting("evaluation_since", None):  # first run with this rule: freeze tuning and start a clean evaluation
    store.set_setting("evaluation_since", int(time.time() * 1000))
    learning.freeze(True)
_config_hash = hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]
store.stamp = lambda: {"weights": dict(learning.weights), "scalp_adjust": dict(learning.adjust), "config": _config_hash,
                       "exits": dict(exits), "filters": dict(trade_filters)}
fx_desk = FxScalpDesk(bus, book, instruments, cfg["scalp"], cfg["station"], ctx, station.prices, modes, open_signals, tz,
                      learning.adjust)
crypto_desk = CryptoDesk(bus, book, scan_cfg, cfg["scalp"], cfg["station"], open_signals, tz)
paper = Paper(bus, store, cfg["paper"], instruments, scan_cfg["fees_pct"], scan_cfg["slippage_pct"])
time_machine = TimeMachine(bus, store, lambda sid: next((s for s in open_signals() if s.get("id") == sid), None), cfg)


def find_candles(symbol: str, tf: str) -> pd.DataFrame | None:
    for frames in (station.frames, fx_desk.frames, crypto_desk.frames):
        if (df := frames.get(symbol, {}).get(tf)) is not None:
            return df
    return None


# exit style shared by intraday signals, scalps, paper and new backtests: partial = fraction banked at TP1 (0 = off)
exits = {"partial": float(store.get_setting("exit_partial", cfg.get("exits", {}).get("partial", 0.0)))}
station.exits = book.exits = paper.exits = exits
# trade filters per "SYM:mode" (trend / chop / hours), chosen from the edge scan; off unless you apply them
trade_filters: dict[str, dict[str, object]] = store.get_setting("filters", {})
station.filters = fx_desk.filters = trade_filters
station.tz = tz
journal = Journal(bus, store, find_candles)
voice = Voice(bus, store, cfg["voice"], ROOT, tz)
_llm = cfg["voice"]["llm"]
_providers = {"claude": lambda: Claude(_llm["claude_model"], _llm["claude_effort"]),
              "ollama": lambda: Ollama(_llm["ollama_model"], _llm["ollama_url"])}


async def _on_fallback(name: str) -> None:
    if name == "ollama":
        voice.say("One moment, sir. Thinking locally.", 5)


executor = Executor(bus, store, cfg["execution"], halt)
telegram = Telegram(bus, cfg["telegram"])


def stand_down(now: int) -> int:
    """End every open signal: no position stands behind it any more, so it gets no outcome (result_r stays empty)."""
    n = 0
    for active, publish in ((station.active, station._publish_signal), (book.active, book._publish),
                            (trend_desk.active, trend_desk._publish)):
        for sym in list(active):
            publish(store.close(active.pop(sym), "killed", None, now))
            n += 1
    return n


def log_control(what: str, now: int, **extra: object) -> None:
    store.set_setting("control_log", store.get_setting("control_log", [])[-199:] + [{"what": what, "at": now, **extra}])
    logging.getLogger("jarvis").warning("%s %s", what, extra)


test101 = Test101(bus, store, halt)


def _hourly(sym: str) -> pd.DataFrame | None:
    """Stored hourly history joined with the live hourly candles, for the brain's trade conditions."""
    frames = []
    try:
        frames.append(history.load("capital", sym, "HOUR"))
    except Exception:
        pass
    frames.append(station.frames.get(sym, {}).get("HOUR"))
    frames = [f[["time", "open", "high", "low", "close"]] for f in frames if f is not None and len(f)]
    return pd.concat(frames).drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True) if frames else None


brain = Brain(store, bus, _hourly, lambda text: bus.publish({"type": "brain_lesson", "text": text}, key="brain_lesson"))
paper.gate = lambda s: brain.check(s, station.frames.get(s["symbol"], {}).get("HOUR"))


def _brain_inputs() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    results = [store.backtest(b["id"]) for b in store.backtests(1000) if b["status"] == "done"]
    return store.recent(20_000, skip_mode="crypto"), [r["result"] for r in results if r and r.get("result")]


async def brain_loop() -> None:
    """Rebuild memory, lessons and the model at start-up and then every hour."""
    await asyncio.sleep(30)  # let the live candles arrive first
    while True:
        await asyncio.to_thread(lambda: brain.rebuild(*_brain_inputs()))
        await asyncio.sleep(3600)


async def kill(source: str) -> dict[str, object]:
    """KILL SWITCH: pause all new signals, close every paper position and end every open signal, cancel every futures
    order and close every futures position on the execution account."""
    now = int(time.time() * 1000)
    halt.activate(f"kill switch ({source})")
    evt = {"type": "kill", "created": now, "source": source, "paper_closed": paper.flatten("kill switch", now) + test101.flatten(now),
           "signals_closed": stand_down(now), "exchange": await executor.flatten_all()}
    log_control("kill", now, source=source, paper_closed=evt["paper_closed"], signals_closed=evt["signals_closed"])
    bus.publish(evt, key="kill")
    bus.publish({"type": "halt", **halt.state()}, key="halt")
    return evt


assistant = Assistant(Tools(bus, store, instruments, cfg, voice.set_quiet), [_providers[p]() for p in _llm["order"]],
                      _llm["history_turns"], _on_fallback, on_kill=lambda: kill("voice"))
if halt.active and halt.why.startswith("kill switch"):  # halted before this version ended signals, or Ras stopped mid-kill
    stand_down(int(time.time() * 1000))
bus.publish({"type": "halt", **halt.state()}, key="halt")
bus.publish({"type": "modes", "modes": modes}, key="modes")
bus.publish(paper.snapshot(), key="paper")


OFFLINE_AFTER_MS = 120_000  # no heartbeat for 2 minutes: Ras was closed, or the laptop slept
offline_since: dict[str, int] = {}  # instrument -> since when its stops and targets still need checking
desks = ((station.active, station.check), (book.active, book.on_price),
         (trend_desk.active, lambda sym, bid, ask, ms: trend_desk.on_price({"symbol": sym, "bid": bid, "ask": ask}, ms)))


def on_frames(sym: str, frames: dict[str, pd.DataFrame]) -> object:
    if bad := capital.bad_bars(frames):  # raising skips this market's desks until the next poll
        bus.failed("candle data", f"{sym} {', '.join(bad)}")
        raise ValueError(f"bad candles in {sym} {bad}")
    if (since := offline_since.pop(sym, None)) is not None:
        try:
            catchup.replay(sym, frames, since, paper, desks)
        except Exception as e:
            bus.failed("offline catch-up", e)
    return station.on_frames(sym, frames)


def note_alive(now: int) -> None:
    """Record the heartbeat; after a gap, log it and queue the missed stops and targets for checking."""
    last = store.get_setting("last_alive", None)
    if last and now - last > OFFLINE_AFTER_MS:
        store.set_setting("offline_log", store.get_setting("offline_log", [])[-199:] + [{"from": last, "to": now}])
        logging.getLogger("jarvis").warning("Ras was offline for %d min", (now - last) // 60_000)
        for sym in instruments:
            offline_since.setdefault(sym, last)
    store.set_setting("last_alive", now)
    if bus.failures:
        bus.publish_health()


watch = AccountWatch(store, cfg["paper"].get("limits"), tz)


async def account_limits() -> None:
    """A broken limit pauses new trades like the kill switch, but leaves open positions on their stops."""
    while True:
        await asyncio.sleep(30)
        now = int(time.time() * 1000)
        if not halt.active and paper.marks_known() and (why := watch.check(paper.snapshot()["accounts"]["fx"]["equity"], now)):
            halt.activate(why)
            log_control("limit", now, why=why)
            bus.publish({"type": "limit", "why": why, "created": now})
            bus.publish({"type": "halt", **halt.state()}, key="halt")


spread_stats: dict[str, dict[str, list[float]]] = store.get_setting("spread_stats", {})  # symbol -> UTC hour -> [ticks, sum of spreads]


async def spread_recorder() -> None:
    """Average spread per market and hour, from Ras's own quotes: what stress tests should use, since no one publishes it."""
    q, saved = bus.subscribe(), time.monotonic()
    while True:
        e = await q.get()
        if e.get("type") == "price":
            cell = spread_stats.setdefault(e["symbol"], {}).setdefault(str(int(time.time()) // 3600 % 24), [0, 0.0])
            cell[0] += 1
            cell[1] += e["ask"] - e["bid"]
            if time.monotonic() - saved > 600:
                store.set_setting("spread_stats", spread_stats)
                saved = time.monotonic()


async def heartbeat() -> None:
    """Every 30s. heartbeat_url in config is optional: an outside monitor (e.g. healthchecks.io) that alerts you when
    the pings stop is the only thing that can report the Mac itself being off."""
    url = cfg.get("heartbeat_url")
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            note_alive(int(time.time() * 1000))
            if url:
                try:
                    await client.get(url)
                except httpx.HTTPError as e:
                    logging.getLogger("jarvis").warning("heartbeat ping failed: %r", e)
            await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks: list[asyncio.Task[None]] = []

    def start(name: str, fn: Callable[..., Awaitable[object]], *args: object) -> None:
        tasks.append(asyncio.create_task(bus.supervise(name, fn, *args)))

    start("heartbeat", heartbeat)
    start("trial log", asyncio.to_thread, store.backfill_trials)
    start("account limits", account_limits)
    start("spread log", spread_recorder)
    start("signal station", station.run)
    start("macro feeds", macro.run, bus, ctx, cfg["macro"], cfg["capital"]["instruments"])
    start("briefings", briefing.scheduler, station, bus, cfg["briefings"])
    start("scalp book", book.run)
    start("paper trading", paper.run)
    start("trade journal", journal.run)
    start("weekly review", review_scheduler, store, bus, cfg["review"], tz)
    start("learning", learning.run)
    start("voice", voice.run, assistant)
    start("trend desk", trend_desk.run)
    start("test 101", test101.run)
    start("brain", brain_loop)
    if CRYPTO:
        start("crypto feed", crypto.run, bus, cfg["crypto"])
        start("crypto desk", crypto_desk.run)
        start("executor", executor.run)

    async def after_secrets() -> None:
        """Everything that needs a key starts once the keychain has been read (may wait for macOS 'Allow')."""
        await asyncio.to_thread(preload_secrets)
        if capital.has_credentials():
            session = capital.Session(cfg["capital"].get("env", "demo"))
            start("capital.com prices", capital.stream, bus, session, cfg["capital"])
            start("capital.com rules", capital.market_rules, session, cfg["capital"], paper.set_rules)
            start("capital.com candles", capital.poll_candles, session, cfg["capital"], ctx, on_frames, fx_desk)
        else:
            capital.status(bus, "no_credentials", "run: uv run python -m jarvis.keys")
        start("news", news.run, bus, ctx, cfg["news"])
        start("telegram", telegram.run)
        start("time machine", time_machine.run)  # it reads Capital.com keys: start after the keychain
        executor._publish_state()

    async def macro_series() -> None:
        """DXY (1H) + US yields (daily) history for the dashboard mini-charts."""
        while True:
            dxy = ctx.dxy.tail(120) if ctx.dxy is not None else None
            bus.publish({"type": "macro_series",
                         "dxy": [[int(r.time), round(float(r.close), 3)] for r in dxy.itertuples()] if dxy is not None else [],
                         "yields": {k: [[int(ts.timestamp()), float(v)] for ts, v in s.tail(90).items()] for k, s in ctx.yields.items()}},
                        key="macro_series")
            await asyncio.sleep(60)

    start("macro charts", macro_series)
    if os.environ.get("JARVIS_EXIT_WHEN_IDLE"):
        tasks.append(asyncio.create_task(exit_when_window_closed()))
    capital.status(bus, "starting", "reading keychain…")
    start("startup", after_secrets)
    yield
    for t in tasks:
        t.cancel()
    if CRYPTO:
        await crypto_desk.close()
        await executor.close()


app = FastAPI(title="Ras", lifespan=lifespan)
# only the local UI (Vite dev / Tauri webview) may call the REST API
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:1420", "tauri://localhost", "http://tauri.localhost"],
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])
app.add_middleware(LocalOnly)


@app.get("/health")
def health() -> dict[str, object]:
    status = {k: v for k, v in bus.snapshot.items() if k.startswith("status:")}
    return {"ok": bus.healthy() and all(v["state"] == "live" for v in status.values()), "faults": bus.failures, "status": status}


@app.get("/api/candles")
def candles(symbol: str, tf: str = "MINUTE_15") -> list[dict[str, float]]:
    """symbol is a query param because crypto keys contain '/' and ':' (e.g. perp:BTC/USDT:USDT)."""
    if (df := find_candles(symbol, tf)) is None:
        raise HTTPException(404, "no candles yet")
    return df[["time", "open", "high", "low", "close"]].to_dict("records")


@app.get("/api/signals")
def signals(symbol: str, limit: int = 50) -> list[dict[str, object]]:
    return store.history(symbol, min(limit, 500))


class ModeUpdate(BaseModel):
    intraday: bool | None = None
    scalp: bool | None = None
    trend: bool | None = None


@app.post("/api/modes/{symbol}")
def set_mode(symbol: str, m: ModeUpdate) -> dict[str, bool]:
    if symbol not in modes:
        raise HTTPException(404, "unknown instrument")
    modes[symbol].update(m.model_dump(exclude_none=True))
    store.set_setting("modes", modes)
    bus.publish({"type": "modes", "modes": modes}, key="modes")
    return modes[symbol]


@app.get("/api/sessions")
def session_clock() -> list[dict[str, object]]:
    return sessions.today(cfg["scalp"]["sessions"], int(time.time() * 1000), tz)


@app.get("/api/paper")
def paper_state() -> dict[str, object]:
    return paper.snapshot()


@app.get("/api/briefings")
def briefings(limit: int = 10) -> list[dict[str, object]]:
    return station.store.briefings(min(limit, 100))


@app.post("/api/briefings/{kind}")
def make_briefing(kind: str) -> dict[str, object]:
    if kind not in ("morning", "eod"):
        raise HTTPException(404, "kind must be morning or eod")
    return briefing.generate(station, bus, kind, cfg["briefings"]["tz"])


# ---------- performance / journal / learning ----------

@app.get("/api/performance")
def perf(mode: str | None = None, days: int = 30) -> dict[str, object]:
    now = int(time.time() * 1000)
    since = now - days * 86_400_000
    trades = [t for t in performance.from_signals(store.between(since, now + 1))
              if mode in (None, "", t["mode"]) and (CRYPTO or t["mode"] != "crypto")]
    paper_closed = sorted((p for p in store.positions(limit=5000) if p.get("closed") and p["closed"] >= since),
                          key=lambda p: p["closed"])
    curves: dict[str, list[dict[str, float]]] = {}
    for div in ("fx", "crypto"):
        mine = [p for p in paper_closed if p["division"] == div and p["opened"] >= paper.since(div)]
        bal = paper.balance(div) - sum(p["pnl"] for p in mine)
        curves[div] = []
        for p in mine:
            bal += p["pnl"]
            curves[div].append({"time": int(p["closed"] / 1000), "equity": round(bal, 2)})
    return {**performance.report(trades, tz), "paper_equity": curves, "days": days, "mode": mode}


@app.get("/api/journal/dashboard")
def journal_dashboard() -> dict[str, object]:
    """Signal Station paper account, all time (the calendar pages through months client-side)."""
    fx = [p for p in store.positions(limit=20_000) if p["division"] == "fx" and p["opened"] >= paper.since("fx")]
    return {**jmod.dashboard(fx, paper.start("fx"), paper.snapshot()["accounts"]["fx"]["equity"], tz), "since": paper.since("fx")}


FORWARD_NOTES = ["The live trend rule was picked because it won the walk-forward on this same history, so its backtest "
                 "figure is flattered by that selection."]


def _trend_runs() -> list[dict[str, object]]:
    """The trend desk's reference: the newest Strategies-tab runs of its rule, per market."""
    out = []
    for r in store.strategy_runs(200):
        if r["status"] == "done" and "trend_following" in r["spec"].get("strategies", []) and (full := store.strategy_run(r["id"])):
            x = next((s for s in (full.get("result") or {}).get("strategies", []) if s["key"] == "trend_following"), None)
            if x:
                out.append({"id": r["id"], "created": r["created"], "spec": {"mode": "trend", "days": r["spec"].get("days"), "folds": r["spec"].get("folds")}, "result": {"out_of_sample": {"by_symbol": {
                    s: {**st, "drawdown_bad": (x.get("drawdown_range") or {}).get("bad")} for s, st in x["by_symbol"].items()}}}})
                if len(out) >= 12:
                    break
    return out


@app.get("/api/spreads")
def spreads() -> dict[str, dict[str, float]]:
    return {s: {h: round(c[1] / c[0], 6) for h, c in sorted(hours.items(), key=lambda x: int(x[0])) if c[0]} for s, hours in spread_stats.items()}


PARITY_DAYS = 30


@app.post("/api/parity")
async def replay_parity() -> dict[str, object]:
    """Rerun the last month's live signals through the replay. Only downloads candles: nothing trades."""
    if not _bt_lock.acquire(blocking=False):
        raise HTTPException(409, "a backtest is already running")
    now = int(time.time() * 1000)
    extra = {"MINUTE_15": 60, "MINUTE": 6, "DAY": 400}

    def run() -> dict[str, object]:
        cache: dict[tuple[str, str], pd.DataFrame] = {}

        def load(sym: str, tf: str) -> pd.DataFrame:
            if (sym, tf) not in cache:
                cache[(sym, tf)] = asyncio.run(history.ensure(sym, tf, PARITY_DAYS + extra[tf], cfg))
            return cache[(sym, tf)]
        return parity.check(store.between(now - PARITY_DAYS * 86_400_000, now + 1), load, cfg)

    try:
        out = {**await asyncio.to_thread(run), "at": now}
    finally:
        _bt_lock.release()
    store.set_setting("parity", out)
    return out


@app.get("/api/forward-test")
def forward_test() -> dict[str, object]:
    """Net paper results since the evaluation began (settings frozen) vs each instrument's latest backtest, also net."""
    since, now = max(paper.since("fx"), store.get_setting("evaluation_since", 0)), int(time.time() * 1000)
    positions = store.positions(limit=5000)
    live = performance.paper_trades(positions, since)
    runs = [r for r in (store.backtest(b["id"]) for b in store.backtests(30) if b["status"] == "done") if r] + _trend_runs()
    ft = cfg.get("forward_test", {})
    signals = [s for s in store.between(since, now + 1) if s["mode"] != "crypto"]
    feasible = performance.feasibility(signals, {p["signal_id"] for p in positions})
    offline = sum(max(0, min(g["to"], now) - max(g["from"], since)) for g in store.get_setting("offline_log", []))
    out = performance.forward_test(live, runs, ft.get("target_trades", 50), ft.get("min_trades", 20), (now - since - offline) / 86_400_000,
                                   {k: f["signals"] for k, f in feasible.items()},
                                   [(m, s) for s, on in modes.items() for m in ("intraday", "scalp", "trend") if on.get(m)])
    for row in out["rows"]:
        row["feasibility"] = feasible.get((row["mode"], row["symbol"]))
    for (mode, sym), f in feasible.items():  # a desk the account couldn't trade at all still shows
        if not any(r["mode"] == mode and r["symbol"] == sym for r in out["rows"]):
            out["rows"].append({"mode": mode, "symbol": sym, "live": performance.stats([]), "range": None, "backtest": None,
                                "verdict": "collecting", "feasibility": f})
    ledger: dict[str, dict[str, float]] = {}
    for t in live:
        d = ledger.setdefault(t["mode"], {"trades": 0, "pnl": 0.0})
        d.update(trades=d["trades"] + 1, pnl=round(d["pnl"] + t["pnl"], 2))
    return {**out, "since": since, "ledger": ledger, "news_trial": performance.news_trial(signals),
            "news_diffs": len(store.get_setting("news_trial_log", [])), "trials": store.trial_count(), "notes": FORWARD_NOTES,
            "parity": store.get_setting("parity", None)}


class PaperReset(BaseModel):
    balance: float = Field(ge=10, le=1_000_000)


@app.post("/api/paper/reset")
def paper_reset(body: PaperReset) -> dict[str, object]:
    """Start a fresh Signal Station paper account (old trades stay in the journal, but stop counting)."""
    snap = paper.reset("fx", body.balance, int(time.time() * 1000))
    bus.publish(snap, key="paper")
    return snap


@app.get("/api/journal")
def journal_list(mode: str | None = None, limit: int = 50) -> list[dict[str, object]]:
    return journal.entries(min(limit, 300), mode or None, None if CRYPTO else "crypto")


class Note(BaseModel):
    notes: str = Field(max_length=5000)


@app.put("/api/journal/{signal_id}")
def journal_note(signal_id: int, n: Note) -> dict[str, object]:
    store.save_journal(signal_id, int(time.time() * 1000), notes=n.notes)
    return store.journal([signal_id])[signal_id]


@app.get("/api/reviews")
def reviews(limit: int = 10) -> list[dict[str, object]]:
    return store.briefings(min(limit, 52), "weekly")


@app.post("/api/reviews")
async def make_review() -> dict[str, object]:
    return await weekly_review(store, bus, cfg["review"], tz)


@app.get("/api/learning")
def learning_state() -> dict[str, object]:
    return learning.state()


@app.post("/api/learning/{action}")
def learning_action(action: Literal["update", "freeze", "unfreeze", "reset"]) -> dict[str, object]:
    if action == "update":
        if learning.frozen:
            raise HTTPException(409, "weights are frozen")
        learning.update()
    elif action == "reset":
        learning.reset()
    else:
        learning.freeze(action == "freeze")
    return learning.state()


# ---------- voice ----------

class Ask(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@app.post("/api/voice/ask")
async def voice_ask(a: Ask) -> dict[str, str]:
    reply, who = await voice.ask(a.text)
    return {"reply": reply, "provider": who}


class Ptt(BaseModel):
    down: bool


@app.post("/api/voice/ptt")
def voice_ptt(p: Ptt) -> dict[str, bool]:
    voice.ptt(p.down)
    return {"down": p.down}


class Quiet(BaseModel):
    minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    unmute: bool = False
    min_priority: int | None = Field(default=None, ge=1, le=5)


@app.post("/api/voice/quiet")
def voice_quiet(q: Quiet) -> dict[str, object]:
    return voice.set_quiet(q.minutes, q.unmute, q.min_priority)


@app.post("/api/voice/stop")
def voice_stop() -> dict[str, bool]:
    voice.stop()
    return {"stopped": True}


# ---------- execution / kill switch / telegram ----------

def _rejected(e: Rejected) -> HTTPException:
    return HTTPException(400, str(e))


@app.get("/api/execution")
def execution_state() -> dict[str, object]:
    return executor.state()


class Phrase(BaseModel):
    phrase: str = Field(max_length=100)


@app.post("/api/execution/enable")
async def execution_enable(p: Phrase) -> dict[str, object]:
    try:
        return await executor.enable(p.phrase)
    except Rejected as e:
        raise _rejected(e)
    except Exception as e:  # bad key, network: say so, stay disabled
        raise HTTPException(400, f"could not reach Binance demo with this key: {type(e).__name__}: {e}")


@app.post("/api/execution/disable")
def execution_disable() -> dict[str, object]:
    return executor.disable()


class Approval(BaseModel):
    stop: float | None = Field(default=None, gt=0)
    tp: float | None = Field(default=None, gt=0)
    risk_pct: float | None = Field(default=None, gt=0)


@app.post("/api/execution/approve/{signal_id}")
async def execution_approve(signal_id: int, a: Approval) -> dict[str, object]:
    try:
        return await executor.approve(signal_id, a.model_dump(exclude_none=True), book.active)
    except Rejected as e:
        raise _rejected(e)


@app.post("/api/execution/reject/{signal_id}")
def execution_reject(signal_id: int) -> dict[str, object]:
    try:
        return executor.reject(signal_id)
    except Rejected as e:
        raise _rejected(e)


class KillConfirm(BaseModel):
    confirm: Literal[True]


@app.post("/api/kill")
async def kill_switch(_: KillConfirm) -> dict[str, object]:
    return await kill("button")


@app.post("/api/resume")
def resume() -> dict[str, object]:
    halt.resume()
    trend_desk.last_bar.clear()  # re-read the trend now rather than at the next daily candle
    watch.reset(paper.snapshot()["accounts"]["fx"]["equity"], int(time.time() * 1000))
    log_control("resume", int(time.time() * 1000))
    bus.publish({"type": "halt", **halt.state()}, key="halt")
    return halt.state()


@app.post("/api/telegram/test")
async def telegram_test() -> dict[str, bool]:
    if not telegram.configured:
        raise HTTPException(400, "no Telegram bot token / chat id in the keychain — run: uv run python -m jarvis.keys")
    return {"sent": await telegram.send("Test message — alerts are working, sir.")}


# ---------- settings: config file + audio devices ----------

REQUIRED_SECTIONS = ("server", "capital", "crypto", "station", "scalp", "paper", "voice", "execution")


@app.get("/api/config")
def config_text() -> dict[str, str]:
    return {"path": str(ROOT / "config.yaml"), "text": (ROOT / "config.yaml").read_text(encoding="utf-8")}


class ConfigText(BaseModel):
    text: str = Field(max_length=200_000)


@app.put("/api/config")
def config_save(c: ConfigText) -> dict[str, str]:
    try:
        parsed = yaml.safe_load(c.text)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"not valid YAML: {e}")
    missing = [s for s in REQUIRED_SECTIONS if not isinstance(parsed, dict) or s not in parsed]
    if missing:
        raise HTTPException(400, f"missing sections: {', '.join(missing)}")
    if parsed["execution"].get("environment") != "demo":
        raise HTTPException(400, "execution.environment must stay 'demo' in this build")
    path = ROOT / "config.yaml"
    path.with_suffix(".yaml.bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")  # one-step undo
    path.write_text(c.text, encoding="utf-8")
    return {"saved": str(path), "note": "restart Ras to apply"}


@app.get("/api/voice/devices")
def voice_devices() -> dict[str, object]:
    try:
        import sounddevice as sd
        devs = [{"index": i, "name": d["name"], "inputs": d["max_input_channels"], "outputs": d["max_output_channels"]}
                for i, d in enumerate(sd.query_devices())]
    except Exception:  # no PortAudio / no devices: the picker just shows "system default"
        devs = []
    return {"devices": devs, "selected": store.get_setting("voice_devices", {})}


class Devices(BaseModel):
    input: str | None = None
    output: str | None = None


@app.put("/api/voice/devices")
def voice_devices_save(d: Devices) -> dict[str, object]:
    store.set_setting("voice_devices", d.model_dump())
    return {"selected": d.model_dump(), "note": "restart Ras to switch devices"}


# ---------- backtests ----------

class BacktestSpec(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=8)
    mode: Literal["scalp", "intraday", "crypto"]
    days: float = Field(gt=0, le=180)
    folds: int = Field(default=3, ge=1, le=6)


_bt_lock = threading.Lock()


@app.post("/api/backtest")
async def start_backtest(spec: BacktestSpec) -> dict[str, object]:
    for s in spec.symbols:
        fx = s in instruments
        if (spec.mode == "crypto") == fx or (not fx and not s.startswith(("spot:", "perp:"))):
            raise HTTPException(400, f"{s} doesn't fit mode {spec.mode}")
    if not _bt_lock.acquire(blocking=False):
        raise HTTPException(409, "a backtest is already running")
    job = {"created": int(time.time() * 1000), "status": "running", "spec": spec.model_dump()}
    store.save_backtest(job)

    async def work() -> None:
        try:
            await _run_job(job)
        finally:
            _bt_lock.release()

    asyncio.create_task(work())
    return {"id": job["id"], "status": "running"}


async def _run_job(job: dict[str, object]) -> None:
    """Run one saved backtest job to completion (the caller holds _bt_lock)."""
    loop = asyncio.get_running_loop()

    def progress(msg: str, p: float) -> None:
        loop.call_soon_threadsafe(bus.publish, {"type": "backtest", "id": job["id"], "status": "running", "msg": msg,
                                                "progress": round(p, 3)}, "backtest")
    try:
        job["result"] = await asyncio.to_thread(backtest.run, job["spec"], {**cfg, "exits": dict(exits)}, progress)
        job["status"] = "done"
    except Exception as e:  # report the failure in the UI instead of losing it
        why = str(e) + (f" — {e.response.text[:200]}" if isinstance(e, httpx.HTTPStatusError) else "")
        job["status"], job["result"] = "failed", {"error": why}
    finally:
        store.save_backtest(job)
        bus.publish({"type": "backtest", "id": job["id"], "status": job["status"], "progress": 1.0,
                     "msg": job["result"].get("error", "done")}, key="backtest")


# ---------- edge scan: every instrument x mode, walk-forward, both exit styles ----------

class EdgeScanSpec(BaseModel):
    intraday_days: float = Field(default=120, gt=0, le=180)
    scalp_days: float = Field(default=30, gt=0, le=180)
    folds: int = Field(default=4, ge=2, le=6)


def _scan_state() -> dict[str, object]:
    s = store.get_setting("edge_scan", None)
    if not s:
        return {"status": "none", "rows": []}
    runs = {i: store.backtest(i) for i in s["runs"]}
    rows = [{"backtest_id": i, **backtest.edge_verdict(r)} for i, r in runs.items() if r and r["status"] == "done"]
    failed = [{"backtest_id": i, "spec": r["spec"], "error": (r.get("result") or {}).get("error")} for i, r in runs.items()
              if r and r["status"] == "failed"]
    return {**s, "rows": rows, "failed": failed, "exits_now": exits["partial"], "modes": modes, "filters": trade_filters,
            "recommend_partial": backtest.recommend_partial(rows)}


@app.get("/api/edge-scan")
def edge_scan_state() -> dict[str, object]:
    return _scan_state()


@app.post("/api/edge-scan")
async def edge_scan(spec: EdgeScanSpec) -> dict[str, object]:
    """Backtest each instrument in each mode, one after another. Changes nothing: the UI asks before applying."""
    if not _bt_lock.acquire(blocking=False):
        raise HTTPException(409, "a backtest is already running")
    jobs = [(s, m) for m in ("intraday", "scalp") for s in instruments]
    state = {"created": int(time.time() * 1000), "status": "running", "runs": [], "total": len(jobs), "spec": spec.model_dump()}
    store.set_setting("edge_scan", state)

    async def work() -> None:
        try:
            for sym, mode in jobs:
                job = {"created": int(time.time() * 1000), "status": "running",
                       "spec": {"symbols": [sym], "mode": mode, "folds": spec.folds,
                                "days": spec.intraday_days if mode == "intraday" else spec.scalp_days}}
                store.save_backtest(job)
                state["runs"].append(job["id"])
                state["current"] = f"{sym} {mode}"
                store.set_setting("edge_scan", state)
                bus.publish({"type": "edge_scan", **state}, key="edge_scan")
                await _run_job(job)
            state["status"] = "done"
        except Exception as e:
            state["status"], state["error"] = "failed", repr(e)
        finally:
            state.pop("current", None)
            store.set_setting("edge_scan", state)
            bus.publish({"type": "edge_scan", **state}, key="edge_scan")
            _bt_lock.release()

    asyncio.create_task(work())
    return state


# ---------- strategy edge scan: every Strategies-tab rule x every instrument, walk-forward per market ----------

SCAN_DAYS, scan_strategies = lab.SCAN_DAYS, lab.scan_strategies


class StrategyScanSpec(BaseModel):
    tfs: list[Literal["MINUTE_15", "HOUR", "HOUR_4", "DAY"]] = Field(default_factory=lambda: ["HOUR", "DAY"], min_length=1, max_length=4)
    folds: int = Field(default=5, ge=2, le=8)
    funding_pct: float = Field(default=2.0, ge=0, le=20)


def _strategy_scan_state() -> dict[str, object]:
    s = store.get_setting("strategy_scan", None)
    if not s:
        return {"status": "none", "rows": [], "failed": []}
    rows, failed = [], []
    for i in s["runs"]:
        r = _run_cache.get(i) or store.strategy_run(i)
        if not r:
            continue
        if r["status"] == "done":
            _run_cache[i] = r
            sym = r["spec"]["symbols"][0]
            for x in r["result"]["strategies"]:
                rows.append({"run": i, "symbol": sym, "tf": r["spec"]["tf"], "days": r["spec"]["days"], "key": x["key"],
                             "name": x["name"], "group": x["group"], "summary": x["summary"], "verdict": x["verdict"],
                             "positive_folds": x["positive_folds"], "folds": len(x["folds"]),
                             "combos_positive": x["combos_positive"], "combos": len(x["combos"]),
                             "params": x["folds"][-1]["params"] if x["folds"] else ""})
        elif r["status"] == "failed":
            failed.append({"run": i, "symbol": r["spec"]["symbols"][0], "tf": r["spec"]["tf"], "error": (r.get("result") or {}).get("error")})
    return {**s, "rows": rows, "failed": failed}


@app.get("/api/strategy-scan")
def strategy_scan_state() -> dict[str, object]:
    return _strategy_scan_state()


@app.post("/api/strategy-scan")
async def strategy_scan(spec: StrategyScanSpec) -> dict[str, object]:
    """Walk-forward every Strategies-tab rule on each instrument separately, per timeframe. Each market is its own
    Strategies run (so its settings are picked on that market's own earlier data). Changes nothing."""
    if not _bt_lock.acquire(blocking=False):
        raise HTTPException(409, "a backtest is already running")
    tfs = list(dict.fromkeys(spec.tfs))
    jobs = [(tf, sym) for tf in tfs for sym in instruments]
    state: dict[str, object] = {"created": int(time.time() * 1000), "status": "running", "runs": [], "total": len(jobs),
                                "spec": {**spec.model_dump(), "tfs": tfs, "days": {tf: SCAN_DAYS[tf] for tf in tfs}}}
    store.set_setting("strategy_scan", state)
    loop = asyncio.get_running_loop()

    async def work() -> None:
        try:
            for tf, sym in jobs:
                job = {"created": int(time.time() * 1000), "status": "running",
                       "spec": {"strategies": scan_strategies(tf), "symbols": [sym], "tf": tf, "days": float(SCAN_DAYS[tf]),
                                "folds": spec.folds, "funding_pct": spec.funding_pct}}
                store.save_strategy_run(job)
                state["runs"].append(job["id"])  # type: ignore[union-attr]
                state["current"] = f"{sym} {tf}"
                store.set_setting("strategy_scan", state)

                def progress(msg: str, p: float, jid: int = job["id"]) -> None:
                    loop.call_soon_threadsafe(bus.publish, {"type": "strategy_bt", "id": jid, "status": "running", "msg": msg,
                                                            "progress": round(p, 3)}, "strategy_bt")
                try:
                    job["result"] = await asyncio.to_thread(run_lab, job, progress)
                    job["status"] = "done"
                except Exception as e:  # one market failing (download hiccup) doesn't stop the scan
                    job["status"], job["result"] = "failed", {"error": str(e) or repr(e)}
                store.save_strategy_run(job)
            state["status"] = "done"
        except Exception as e:
            state["status"], state["error"] = "failed", str(e) or repr(e)
        finally:
            state.pop("current", None)
            store.set_setting("strategy_scan", state)
            _bt_lock.release()
            bus.publish({"type": "strategy_bt", "id": 0, "status": state["status"], "progress": 1.0, "msg": "strategy scan done"},
                        key="strategy_bt")

    asyncio.create_task(work())
    return {k: v for k, v in state.items()}


class FilterSet(BaseModel):
    trend: bool = False
    chop: bool = False
    hours: list[int] = Field(default_factory=list, max_length=24)


@app.get("/api/filters")
def get_filters() -> dict[str, object]:
    return trade_filters


@app.post("/api/filters/{symbol}/{mode}")
def set_filters(symbol: str, mode: Literal["intraday", "scalp"], body: FilterSet) -> dict[str, object]:
    if symbol not in instruments or any(not 0 <= h < 24 for h in body.hours):
        raise HTTPException(400, "unknown instrument or bad hour")
    trade_filters[f"{symbol}:{mode}"] = body.model_dump()
    store.set_setting("filters", trade_filters)
    return trade_filters


class ExitStyle(BaseModel):
    partial: float = Field(ge=0, le=0.9)


@app.get("/api/exits")
def get_exits() -> dict[str, float]:
    return exits


@app.post("/api/exits")
def set_exits(body: ExitStyle) -> dict[str, float]:
    """0 = close everything at TP1; e.g. 0.5 = bank half at TP1, stop to entry, rest to TP2. New and open signals."""
    exits["partial"] = body.partial
    store.set_setting("exit_partial", body.partial)
    return exits


@app.get("/api/backtests")
def backtests() -> list[dict[str, object]]:
    return store.backtests()


@app.get("/api/backtest/{bt_id}")
def backtest_result(bt_id: int) -> dict[str, object]:
    if (bt := store.backtest(bt_id)) is None:
        raise HTTPException(404, "no such backtest")
    return bt


# ---------- strategies tab: indicator / trend / ICT / SMC / ICC backtests ----------

def run_lab(job: dict[str, object], progress: Callable[[str, float], None]) -> dict[str, object]:
    """Broker financing rates where known, the luck checks measured against every variant tried, then log this run's."""
    spec = job["spec"]
    rates = {s: {"long": -r["fin_long"] * 365, "short": -r["fin_short"] * 365} for s, r in paper.rules.items()
             if r.get("fin_long") is not None and r.get("fin_short") is not None}
    fx = cfg["paper"]["fx"]
    spec.update(rates=rates, prior_trials=store.trial_count(), fresh_since=store.get_setting("evaluation_since", 0),
                account={"balance": paper.start("fx"), "risk_pct": fx["risk_pct"], "max_risk_pct": fx.get("max_risk_pct", 2.0),
                         "rules": paper.rules, "limits": cfg["paper"].get("limits")})
    result = lab.run(spec, cfg, progress)
    store.log_trials(job["id"], int(time.time() * 1000), spec, result)
    return result


class StrategySpec(BaseModel):
    strategies: list[str] = Field(min_length=1, max_length=len(LAB_CATALOG))
    symbols: list[str] = Field(min_length=1, max_length=8)
    tf: Literal["MINUTE_3", "MINUTE_15", "HOUR", "HOUR_4", "DAY"]
    days: float = Field(gt=0, le=max(lab.MAX_DAYS.values()))
    folds: int = Field(default=4, ge=2, le=8)
    funding_pct: float = Field(default=2.0, ge=0, le=20)
    options: bool = False  # also price the test trades as bought options (a model; Capital.com has no option prices)
    money: bool = False  # also run the $100 account under the books' money rules


@app.get("/api/strategies")
def strategy_catalog() -> dict[str, object]:
    return {"strategies": [{"key": k, "name": v["name"], "group": v["group"], "tf": v["tf"], "rules": v["rules"],
                            "grid": {g: [list(x) if isinstance(x, tuple) else x for x in vals] for g, vals in v["grid"].items()}}
                           for k, v in LAB_CATALOG.items() if v["group"] not in LAB_RESEARCH],
            "max_days": lab.MAX_DAYS, "notes": lab.NOTES}


@app.get("/api/strategies/runs")
def strategy_runs() -> list[dict[str, object]]:
    return store.strategy_runs()


@app.get("/api/strategies/run/{run_id}")
def strategy_run(run_id: int) -> dict[str, object]:
    if (r := store.strategy_run(run_id)) is None:
        raise HTTPException(404, "no such run")
    return r


@app.post("/api/strategies/backtest")
async def strategy_backtest(spec: StrategySpec) -> dict[str, object]:
    if bad := [k for k in spec.strategies if k not in LAB_CATALOG]:
        raise HTTPException(400, f"unknown strategy: {', '.join(bad)}")
    if bad := [s for s in spec.symbols if s not in instruments]:
        raise HTTPException(400, f"not a Capital.com instrument: {', '.join(bad)}")
    if spec.days > lab.MAX_DAYS[spec.tf]:
        raise HTTPException(400, f"{spec.tf} goes back at most {lab.MAX_DAYS[spec.tf]} days")
    if not _bt_lock.acquire(blocking=False):
        raise HTTPException(409, "a backtest is already running")
    job = {"created": int(time.time() * 1000), "status": "running", "spec": spec.model_dump()}
    store.save_strategy_run(job)
    loop = asyncio.get_running_loop()

    def progress(msg: str, p: float) -> None:
        loop.call_soon_threadsafe(bus.publish, {"type": "strategy_bt", "id": job["id"], "status": "running", "msg": msg,
                                                "progress": round(p, 3)}, "strategy_bt")

    async def work() -> None:
        try:
            job["result"] = await asyncio.to_thread(run_lab, job, progress)
            job["status"] = "done"
        except Exception as e:  # show the failure in the tab instead of losing it
            why = str(e) + (f" — {e.response.text[:200]}" if isinstance(e, httpx.HTTPStatusError) else "")
            job["status"], job["result"] = "failed", {"error": why or repr(e)}
        finally:
            store.save_strategy_run(job)
            _bt_lock.release()
            bus.publish({"type": "strategy_bt", "id": job["id"], "status": job["status"], "progress": 1.0,
                         "msg": job["result"].get("error", "done")}, key="strategy_bt")

    asyncio.create_task(work())
    return {"id": job["id"], "status": "running"}


_run_cache: dict[int, dict[str, object]] = {}


def _finished_runs() -> list[dict[str, object]]:
    """Finished Strategies-tab runs with results, newest first (a finished run never changes, so cache it)."""
    out = []
    for r in store.strategy_runs(limit=40):
        if r["status"] != "done":
            continue
        if r["id"] not in _run_cache:
            _run_cache[r["id"]] = store.strategy_run(r["id"]) or {}
        out.append(_run_cache[r["id"]])
    return out


@app.get("/api/analysis/{symbol}")
def analysis(symbol: str, tf: Literal["MINUTE_15", "HOUR", "HOUR_4"] = "MINUTE_15") -> dict[str, object]:
    """Short-term read of one market: every Strategies-tab rule's current setup on the live candles, and a decision
    that only counts rules with a positive tested record and goes with the daily trend. Analysis only."""
    if symbol not in instruments:
        raise HTTPException(404, "unknown instrument")
    df = station.frames.get(symbol, {}).get(tf)
    if df is None or len(df) < 210:
        raise HTTPException(404, "no candles yet")
    p = station.prices.get(symbol, {})
    mid = (p["bid"] + p["ask"]) / 2 if p.get("bid") and p.get("ask") else None
    trend = (trend_desk.calls.get(symbol) or trend_desk.active.get(symbol) or {}).get("call")
    return lab_live.analyse(symbol, tf, df.reset_index(drop=True), _finished_runs(), trend, mid)


_m3: dict[str, tuple[float, pd.DataFrame | None]] = {}
_m3_lock = asyncio.Lock()


async def _minute3(symbol: str) -> pd.DataFrame | None:
    """3-minute candles for the live readout, refreshed at most every 3 minutes per market and one market at a time,
    so the readout never floods Capital.com's request limit (it also serves the backtests)."""
    async with _m3_lock:
        at, df = _m3.get(symbol, (0.0, None))
        if time.time() - at >= 180:
            try:
                df = lab.to_minutes(await history.ensure(symbol, "MINUTE", 2, cfg), 3)
            except Exception as e:
                logging.getLogger("jarvis").info("3-minute candles %s: %r", symbol, e)
            _m3[symbol] = (time.time(), df)
        return df


@app.get("/api/strategy-live/{key}")
async def strategy_live(key: str, symbol: str, tf: Literal["MINUTE_3", "MINUTE_15", "HOUR", "HOUR_4", "DAY"] = "HOUR") -> dict[str, object]:
    """One Strategies-tab rule on one market's live candles: its chart, its setups and its own BUY / SELL / HOLD."""
    if key not in LAB_CATALOG or symbol not in instruments:
        raise HTTPException(404, "unknown strategy or instrument")
    if tf == "MINUTE_3":  # no live 3-minute feed: build them from the stored 1-minute history, topped up to now
        df = await _minute3(symbol)
    else:
        df = station.frames.get(symbol, {}).get(tf)
    if df is None or len(df) < 210:
        raise HTTPException(404, "no candles yet")
    df = df.reset_index(drop=True)
    df.attrs["symbol"] = symbol
    return lab_live.single(key, symbol, tf, df, _finished_runs())


class Test101Config(BaseModel):
    """The limits a user may change from the TEST 101 page; everything else stays at the skills' defaults."""
    max_loss_per_trade_usd: float | None = Field(default=None, gt=0, le=1000)
    max_daily_loss_usd: float | None = Field(default=None, gt=0, le=5000)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=10)
    target_r: float | None = Field(default=None, ge=1, le=5)
    tm_gate: bool | None = None
    scan_filter: bool | None = None


@app.get("/api/test101")
def test101_state() -> dict[str, object]:
    """TEST 101: the day-trade skill chain as a paper algorithm for US stocks. Its own paper account; never a broker."""
    return test101.snapshot()


@app.post("/api/test101/config")
def test101_config(c: Test101Config) -> dict[str, object]:
    changes = {k: v for k, v in c.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(400, "nothing to change")
    return test101.set_config(changes)


@app.post("/api/test101/backtest")
async def test101_backtest() -> dict[str, object]:
    if test101.bt_running:
        raise HTTPException(409, "a TEST 101 backtest is already running")

    async def work() -> None:
        try:
            await test101.run_backtest()
        except Exception as e:
            logging.getLogger("jarvis").warning("test101 backtest failed: %r", e)
        bus.publish({"type": "test101", "updated": int(time.time() * 1000), "backtest": "done"}, key="test101")

    asyncio.create_task(work())
    return {"status": "running"}


@app.get("/api/test101/news/{symbol}")
async def test101_news(symbol: str) -> list[dict[str, object]]:
    if symbol not in test101.cfg["universe"]:
        raise HTTPException(404, "not in the TEST 101 universe")
    try:
        return await test101.news(symbol)
    except Exception as e:
        raise HTTPException(502, f"Yahoo news did not answer: {e!r}")


@app.get("/api/books")
def books_page() -> dict[str, object]:
    """Rules from Elder's Trading for a Living and Murphy's Technical Analysis, with Market Wizards' money rules."""
    return books.page(_finished_runs())


@app.post("/api/books/test/{key}")
async def books_test(key: str) -> dict[str, object]:
    if key not in books.STRATEGIES:
        raise HTTPException(404, "unknown strategy")
    s = books.STRATEGIES[key]
    return await strategy_backtest(StrategySpec(strategies=[key], symbols=s["markets"], tf=s["tf"], days=s["days"], folds=4, money=True))


class BrainSettings(BaseModel):
    enabled: bool | None = None
    use_model: bool | None = None


@app.get("/api/brain")
def brain_state() -> dict[str, object]:
    """What Ras remembers, the lessons it has tested, its win-chance model and the paper trades it skipped."""
    return brain.summary()


@app.get("/api/brain/graph")
def brain_graph() -> dict[str, object]:
    """Lessons, every mistake in memory and the learning log, for the brain map, plus every strategy Ras has tested."""
    from jarvis.lab.rules import CATALOG
    studied: dict[str, dict[str, object]] = {}
    for r in _finished_runs():  # newest first: keep each strategy's latest finished test
        if r.get("status") != "done":
            continue
        for x in (r.get("result") or {}).get("strategies", []):
            if x["key"] not in studied:
                studied[x["key"]] = {"key": x["key"], "name": CATALOG.get(x["key"], {}).get("name", x["key"]),
                                     "group": CATALOG.get(x["key"], {}).get("group", ""), "verdict": x.get("verdict"),
                                     "avg_r": (x.get("summary") or {}).get("expectancy"), "trades": (x.get("summary") or {}).get("trades"),
                                     "time": r.get("finished") or r.get("created")}
    return {**brain.graph(), "studied": list(studied.values())}


@app.post("/api/brain/settings")
def brain_settings(s: BrainSettings) -> dict[str, object]:
    return brain.set(enabled=s.enabled, use_model=s.use_model)


@app.post("/api/brain/lesson")
def brain_lesson(lesson: str, on: bool) -> dict[str, object]:
    if lesson not in {x["id"] for x in brain.lessons}:
        raise HTTPException(404, "unknown lesson")
    return brain.set(lesson=lesson, on=on)


@app.post("/api/brain/rebuild")
async def brain_rebuild() -> dict[str, object]:
    return await asyncio.to_thread(lambda: brain.rebuild(*_brain_inputs()))


@app.get("/api/ras11")
def ras11_page() -> dict[str, object]:
    """The researched strategies, what the sources claim, and how each did in Ras's own test."""
    return ras11.page(_finished_runs())


@app.post("/api/ras11/test/{key}")
async def ras11_test(key: str) -> dict[str, object]:
    """Run one researched strategy through the normal walk-forward test, on its own markets and timeframe."""
    if key not in ras11.STRATEGIES:
        raise HTTPException(404, "unknown strategy")
    s = ras11.STRATEGIES[key]
    return await strategy_backtest(StrategySpec(strategies=[key], symbols=s["markets"], tf=s["tf"], days=s["days"], folds=4,
                                                options=s.get("options", False)))


@app.get("/api/history")
def history_catalog() -> list[dict[str, object]]:
    return history.catalog()


_clients = {"now": 0, "ever": False, "idle_since": time.monotonic()}


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    q = bus.subscribe()
    _clients.update(now=_clients["now"] + 1, ever=True)
    try:
        while True:
            await socket.send_json(await q.get())
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q)
        _clients["now"] -= 1
        _clients["idle_since"] = time.monotonic()


async def exit_when_window_closed(grace_s: float = 60) -> None:
    """Portable Windows build: no app shell to stop us, so quit once the Ras window has been gone for a minute."""
    while True:
        await asyncio.sleep(5)
        if _clients["ever"] and _clients["now"] == 0 and time.monotonic() - _clients["idle_since"] > grace_s:
            logging.getLogger("jarvis").info("window closed — shutting down")
            os._exit(0)  # every store write is already committed


# the portable build ships the dashboard next to the backend; serve it on the same port (routes above win)
if (BUNDLED / "web").is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=BUNDLED / "web", html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg["server"]["host"], port=cfg["server"]["port"])
