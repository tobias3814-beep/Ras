"""SQLite signal store + outcome rules (active -> hit_tp / hit_sl / expired / invalidated)."""
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

Signal = dict[str, Any]


def check_outcome(sig: Signal, bid: float, ask: float, now_ms: int, partial: float = 0.0) -> tuple[str, float] | None:
    """Price-based exits. Levels-based R of the part still open; spread/slippage not deducted here.
    partial > 0 (and a TP2): at TP1 bank that fraction, move the stop to entry, let the rest run to TP2 —
    returns ("tp1_partial", R) and marks the signal, which stays open. Callers blend R with blended()."""
    lv, b = sig["levels"], 1 if sig["call"] == "BUY" else -1
    px = bid if b > 0 else ask  # the side you'd exit on
    risk = abs(lv["entry"] - lv["stop"])
    taken = sig.get("tp1_taken", False)
    if (px - (lv["entry"] if taken else lv["stop"])) * b <= 0:
        return ("breakeven", 0.0) if taken else ("hit_sl", -1.0)
    if taken and (px - lv["tp2"]) * b >= 0:
        return "hit_tp2", round((lv["tp2"] - lv["entry"]) * b / risk, 2)
    if not taken and (px - lv["tp1"]) * b >= 0:
        r1 = round((lv["tp1"] - lv["entry"]) * b / risk, 2)
        if partial and lv.get("tp2"):
            sig.update(tp1_taken=True, banked_r=round(partial * r1, 3), remaining=1 - partial)
            return "tp1_partial", r1
        return "hit_tp", r1
    if now_ms >= sig["expires"]:
        return "expired", round((px - lv["entry"]) * b / risk, 2)
    return None


def blended(sig: Signal, r: float) -> float:
    """Whole-trade R: what was banked at TP1 plus the rest at r (just r when no partial was taken)."""
    return round(sig.get("banked_r", 0.0) + sig.get("remaining", 1.0) * r, 2)


def check_invalidation(sig: Signal, h1_close_time_ms: int, h1_close: float) -> tuple[str, float] | None:
    """Invalidated if a 1H candle that closed after the signal closes beyond the invalidation level."""
    lv, b = sig["levels"], 1 if sig["call"] == "BUY" else -1
    if h1_close_time_ms <= sig["created"] or (h1_close - sig["invalidation"]) * b >= 0:
        return None
    return "invalidated", round((h1_close - lv["entry"]) * b / abs(lv["entry"] - lv["stop"]), 2)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY, symbol TEXT, mode TEXT, call TEXT, created INTEGER,
            status TEXT DEFAULT 'active', closed_at INTEGER, result_r REAL, data TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_sig ON signals(symbol, created)")
        self.db.execute("CREATE TABLE IF NOT EXISTS briefings (id INTEGER PRIMARY KEY, kind TEXT, day TEXT, created INTEGER, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS journal (signal_id INTEGER PRIMARY KEY, notes TEXT DEFAULT '', snapshot TEXT, updated INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS weight_log (id INTEGER PRIMARY KEY, ts INTEGER, agent TEXT, old REAL, new REAL, reason TEXT)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS executions (id INTEGER PRIMARY KEY, signal_id INTEGER UNIQUE,
            symbol TEXT, status TEXT, opened INTEGER, closed INTEGER, pnl REAL, data TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS backtests (id INTEGER PRIMARY KEY, created INTEGER, status TEXT, spec TEXT, result TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS strategy_runs (id INTEGER PRIMARY KEY, created INTEGER, status TEXT, spec TEXT, result TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS trial_log (id INTEGER PRIMARY KEY, at INTEGER, run_id INTEGER, key TEXT,
            params TEXT, symbols TEXT, tf TEXT, days REAL, trades INTEGER, expectancy REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS paper (id INTEGER PRIMARY KEY, division TEXT, signal_id INTEGER,
            symbol TEXT, opened INTEGER, closed INTEGER, pnl REAL, data TEXT)""")
        if "trade_log" not in {r[1] for r in self.db.execute("PRAGMA table_info(journal)")}:  # added after v0.1 data existed
            self.db.execute("ALTER TABLE journal ADD COLUMN trade_log TEXT")
        self.db.commit()
        self.stamp: Callable[[], dict[str, Any]] | None = None  # settings saved with each new signal, for replay and audit

    # ---------- trial log: append-only, every variant ever backtested ----------
    def log_trials(self, run_id: int, at: int, spec: dict[str, Any], result: dict[str, Any]) -> None:
        syms, rows = ",".join(sorted(spec["symbols"])), []
        for s in result.get("strategies", []):
            rows += [(at, run_id, s["key"], c["params"], syms, spec["tf"], spec["days"], c["trades"], c["expectancy"]) for c in s.get("combos", [])]
        self.db.executemany("INSERT INTO trial_log(at, run_id, key, params, symbols, tf, days, trades, expectancy) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()

    def trial_count(self) -> int:
        """Distinct variants tried so far: the number luck has to be measured against."""
        return self.db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT key, params, symbols, tf FROM trial_log)").fetchone()[0]

    def backfill_trials(self) -> None:
        """Runs from before the log existed count too."""
        if self.db.execute("SELECT 1 FROM trial_log LIMIT 1").fetchone():
            return
        for r in self.strategy_runs(100_000):
            if r["status"] == "done" and (full := self.strategy_run(r["id"])) and full.get("result"):
                self.log_trials(r["id"], r["created"], full["spec"], full["result"])

    def insert(self, d: Signal) -> Signal:
        if self.stamp:
            d = {**d, "stamp": self.stamp()}
        cur = self.db.execute("INSERT INTO signals(symbol, mode, call, created, data) VALUES (?,?,?,?,?)",
                              (d["symbol"], d["mode"], d["call"], d["created"], json.dumps(d)))
        self.db.commit()
        return {**d, "id": cur.lastrowid, "status": "active", "closed_at": None, "result_r": None}

    def update(self, sig: Signal) -> None:
        """Persist an open signal's changed fields (e.g. TP1 banked) so a restart keeps them."""
        self.db.execute("UPDATE signals SET data=? WHERE id=?",
                        (json.dumps({k: v for k, v in sig.items() if k not in ("id", "status", "closed_at", "result_r")}), sig["id"]))
        self.db.commit()

    def close(self, sig: Signal, status: str, result_r: float | None, now_ms: int) -> Signal:
        self.db.execute("UPDATE signals SET status=?, closed_at=?, result_r=? WHERE id=?", (status, now_ms, result_r, sig["id"]))
        self.db.commit()
        return {**sig, "status": status, "closed_at": now_ms, "result_r": result_r}

    def _rows(self, where: str, args: tuple[Any, ...]) -> list[Signal]:
        rows = self.db.execute(f"SELECT id, status, closed_at, result_r, data FROM signals WHERE {where}", args).fetchall()
        return [{**json.loads(data), "id": i, "status": s, "closed_at": c, "result_r": r} for i, s, c, r, data in rows]

    def active(self) -> list[Signal]:
        return self._rows("status='active'", ())

    def history(self, symbol: str, limit: int = 50) -> list[Signal]:
        return self._rows("symbol=? ORDER BY created DESC LIMIT ?", (symbol, limit))

    def recent(self, limit: int = 100, mode: str | None = None, skip_mode: str | None = None) -> list[Signal]:
        return self._rows("(? IS NULL OR mode = ?) AND (? IS NULL OR mode != ?) ORDER BY created DESC LIMIT ?",
                          (mode, mode, skip_mode, skip_mode, limit))

    def between(self, start_ms: int, end_ms: int) -> list[Signal]:
        return self._rows("created >= ? AND created < ? ORDER BY created", (start_ms, end_ms))

    def get_setting(self, key: str, default: Any) -> Any:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)))
        self.db.commit()

    def save_position(self, p: dict[str, Any]) -> dict[str, Any]:
        if "id" not in p:
            cur = self.db.execute("INSERT INTO paper(division, signal_id, symbol, opened, data) VALUES (?,?,?,?,?)",
                                  (p["division"], p["signal_id"], p["symbol"], p["opened"], json.dumps(p)))
            p = {**p, "id": cur.lastrowid}
        self.db.execute("UPDATE paper SET closed=?, pnl=?, data=? WHERE id=?", (p.get("closed"), p.get("pnl"), json.dumps(p), p["id"]))
        self.db.commit()
        return p

    def positions(self, open_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.execute(f"SELECT data FROM paper {'WHERE closed IS NULL' if open_only else ''} ORDER BY opened DESC LIMIT ?",
                               (limit,)).fetchall()
        return [json.loads(d) for (d,) in rows]

    def realised(self, division: str, since: int = 0) -> float:
        return self.db.execute("SELECT COALESCE(SUM(pnl), 0) FROM paper WHERE division=? AND closed IS NOT NULL AND opened >= ?",
                               (division, since)).fetchone()[0]

    # ---------- journal ----------
    def journal(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        rows = self.db.execute(f"SELECT signal_id, notes, snapshot, updated, trade_log FROM journal WHERE signal_id IN ({','.join('?' * len(ids))})",
                               ids).fetchall()
        return {i: {"notes": n or "", "snapshot": json.loads(s) if s else None, "updated": u, "trade_log": t or ""}
                for i, n, s, u, t in rows}

    def save_journal(self, signal_id: int, now_ms: int, notes: str | None = None, snapshot: list[Any] | None = None,
                     trade_log: str | None = None) -> None:
        self.db.execute("INSERT INTO journal(signal_id, updated) VALUES (?, ?) ON CONFLICT(signal_id) DO NOTHING", (signal_id, now_ms))
        if notes is not None:
            self.db.execute("UPDATE journal SET notes=?, updated=? WHERE signal_id=?", (notes, now_ms, signal_id))
        if snapshot is not None:
            self.db.execute("UPDATE journal SET snapshot=? WHERE signal_id=?", (json.dumps(snapshot), signal_id))
        if trade_log is not None:
            self.db.execute("UPDATE journal SET trade_log=? WHERE signal_id=?", (trade_log, signal_id))
        self.db.commit()

    def get(self, signal_id: int) -> Signal | None:
        rows = self._rows("id=?", (signal_id,))
        return rows[0] if rows else None

    def positions_for(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        rows = self.db.execute(f"SELECT signal_id, data FROM paper WHERE signal_id IN ({','.join('?' * len(ids))})", ids).fetchall()
        return {i: json.loads(d) for i, d in rows}

    # ---------- learning ----------
    def log_weight(self, ts: int, agent: str, old: float, new: float, reason: str) -> None:
        self.db.execute("INSERT INTO weight_log(ts, agent, old, new, reason) VALUES (?,?,?,?,?)", (ts, agent, old, new, reason))
        self.db.commit()

    def weight_log(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT ts, agent, old, new, reason FROM weight_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": a, "agent": b, "old": c, "new": d, "reason": e} for a, b, c, d, e in rows]

    # ---------- executions ----------
    def save_execution(self, x: dict[str, Any]) -> dict[str, Any]:
        if "id" not in x:
            cur = self.db.execute("INSERT INTO executions(signal_id, symbol, status, opened, data) VALUES (?,?,?,?,?)",
                                  (x["signal_id"], x["symbol"], x["status"], x["opened"], json.dumps(x)))
            x = {**x, "id": cur.lastrowid}
        self.db.execute("UPDATE executions SET status=?, closed=?, pnl=?, data=? WHERE id=?",
                        (x["status"], x.get("closed"), x.get("pnl"), json.dumps(x), x["id"]))
        self.db.commit()
        return x

    def executions(self, open_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        where = "WHERE status IN ('submitting', 'open')" if open_only else ""
        rows = self.db.execute(f"SELECT id, data FROM executions {where} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**json.loads(d), "id": i} for i, d in rows]

    def execution_for(self, signal_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT id, data FROM executions WHERE signal_id=?", (signal_id,)).fetchone()
        return {**json.loads(row[1]), "id": row[0]} if row else None

    def signal(self, signal_id: int) -> Signal | None:
        rows = self._rows("id=?", (signal_id,))
        return rows[0] if rows else None

    # ---------- backtests ----------
    def save_backtest(self, bt: dict[str, Any]) -> int:
        if "id" not in bt:
            cur = self.db.execute("INSERT INTO backtests(created, status, spec) VALUES (?,?,?)", (bt["created"], bt["status"], json.dumps(bt["spec"])))
            bt["id"] = cur.lastrowid
        self.db.execute("UPDATE backtests SET status=?, result=? WHERE id=?",
                        (bt["status"], json.dumps(bt.get("result")) if bt.get("result") else None, bt["id"]))
        self.db.commit()
        return bt["id"]

    def backtests(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id, created, status, spec FROM backtests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": i, "created": c, "status": s, "spec": json.loads(sp)} for i, c, s, sp in rows]

    def backtest(self, bt_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT id, created, status, spec, result FROM backtests WHERE id=?", (bt_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "created": row[1], "status": row[2], "spec": json.loads(row[3]), "result": json.loads(row[4]) if row[4] else None}

    def fail_interrupted(self, why: str = "interrupted: Ras was restarted while it ran") -> int:
        """At start-up: a backtest, Strategies run or scan still marked running was cut off by a restart. Mark it failed
        so it doesn't block new runs or show 'running' forever."""
        n = 0
        for table in ("backtests", "strategy_runs"):
            n += self.db.execute(f"UPDATE {table} SET status='failed', result=? WHERE status='running'",
                                 (json.dumps({"error": why}),)).rowcount
        for key in ("edge_scan", "strategy_scan"):
            s = self.get_setting(key, None)
            if s and s.get("status") == "running":
                s.pop("current", None)
                self.set_setting(key, {**s, "status": "failed", "error": why})
                n += 1
        self.db.commit()
        return n

    # ---------- strategy lab runs (Strategies tab) ----------
    def save_strategy_run(self, run: dict[str, Any]) -> int:
        if "id" not in run:
            cur = self.db.execute("INSERT INTO strategy_runs(created, status, spec) VALUES (?,?,?)",
                                  (run["created"], run["status"], json.dumps(run["spec"])))
            run["id"] = cur.lastrowid
        self.db.execute("UPDATE strategy_runs SET status=?, result=? WHERE id=?",
                        (run["status"], json.dumps(run.get("result")) if run.get("result") else None, run["id"]))
        self.db.commit()
        return run["id"]

    def strategy_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id, created, status, spec FROM strategy_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": i, "created": c, "status": s, "spec": json.loads(sp)} for i, c, s, sp in rows]

    def strategy_run(self, run_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT id, created, status, spec, result FROM strategy_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "created": row[1], "status": row[2], "spec": json.loads(row[3]), "result": json.loads(row[4]) if row[4] else None}

    def add_briefing(self, b: dict[str, Any]) -> dict[str, Any]:
        cur = self.db.execute("INSERT INTO briefings(kind, day, created, data) VALUES (?,?,?,?)",
                              (b["kind"], b["day"], b["created"], json.dumps(b)))
        self.db.commit()
        return {**b, "id": cur.lastrowid}

    def briefings(self, limit: int = 10, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id, data FROM briefings WHERE ? IS NULL OR kind = ? ORDER BY created DESC LIMIT ?",
                               (kind, kind, limit)).fetchall()
        return [{**json.loads(d), "id": i} for i, d in rows]
