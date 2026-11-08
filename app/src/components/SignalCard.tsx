import { useEffect, useState } from "react";
import type { Decision, Odds, Signal } from "../ws";

const TONE = { BUY: "text-hud-green border-hud-green/50", SELL: "text-hud-red border-hud-red/50", HOLD: "text-hud-amber border-hud-amber/50" };
const STATUS: Record<Signal["status"], string> = {
  active: "ACTIVE", hit_tp: "HIT TP", hit_sl: "HIT SL", expired: "EXPIRED", invalidated: "INVALIDATED", killed: "KILLED", time_stop: "TIME STOP",
};

export const fmtTime = (ms: number) =>
  new Date(ms).toLocaleString("en-GB", { timeZone: "Asia/Bahrain", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const hhmm = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { timeZone: "Asia/Bahrain", hour: "2-digit", minute: "2-digit" });

export const isScalp = (s: Decision) => s.mode === "scalp" || s.mode === "crypto";

export function CallBadge({ call, conf, scalp }: { call: Decision["call"]; conf: number; scalp?: boolean }) {
  return (
    <span className={`rounded border px-1.5 font-semibold ${TONE[call]} ${scalp && call !== "HOLD" ? "bg-hud-scalp/10" : ""}`}>
      {scalp && call !== "HOLD" ? "SCALP " : ""}{call} {call !== "HOLD" && conf}
    </span>
  );
}

function useNow() {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  return now;
}

function Countdown({ until }: { until: number }) {
  const left = Math.max(0, until - useNow());
  const m = Math.floor(left / 60000), s = Math.floor((left % 60000) / 1000);
  return <span className="tabular-nums">{m}:{String(s).padStart(2, "0")}</span>;
}

/** Large copy-ready levels for placing a manual order. */
function CopyBlock({ s, digits, pip }: { s: Decision; digits: number; pip?: number }) {
  const lv = s.levels!;
  // trend signals have no targets (held until the trend flips): show "—" instead of failing
  const f = (x?: number | null) => (x == null ? "—" : x.toFixed(digits));
  const dist = (x?: number | null) => x == null ? "none" : pip ? `${(Math.abs(x - lv.entry) / pip).toFixed(1)}p` : `${(Math.abs(x - lv.entry) / lv.entry * 100).toFixed(2)}%`;
  const text = `${s.call} ${s.display ?? s.symbol.replace("_", "")} @ ${f(lv.entry)} | SL ${f(lv.stop)} (${dist(lv.stop)}) | TP1 ${f(lv.tp1)} (${dist(lv.tp1)}) | TP2 ${f(lv.tp2)}`;
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-3 rounded border border-hud-scalp/40 bg-hud-bg p-3">
      <div className="grid grid-cols-4 gap-2 font-mono text-lg tabular-nums">
        <div><div className="text-xs text-slate-500">ENTRY</div><div className="text-hud-accent">{f(lv.entry)}</div></div>
        <div><div className="text-xs text-slate-500">STOP · {dist(lv.stop)}</div><div className="text-hud-red">{f(lv.stop)}</div></div>
        <div><div className="text-xs text-slate-500">TP1 · {dist(lv.tp1)}</div><div className="text-hud-green">{f(lv.tp1)}</div></div>
        <div><div className="text-xs text-slate-500">TP2 · {dist(lv.tp2)}</div><div className="text-hud-green">{f(lv.tp2)}</div></div>
      </div>
      <button className="mt-2 rounded border border-hud-line px-2 py-0.5 text-xs text-hud-accent hover:border-hud-accent"
        onClick={() => navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>
        {copied ? "Copied ✓" : "Copy levels"}
      </button>
      {s.say && <p className="mt-2 text-xs italic text-slate-400">“{s.say}”</p>}
    </div>
  );
}

export function SignalCard({ s, digits, title, detailed, pip }: { s: Decision | Signal; digits: number; title: string; detailed?: boolean; pip?: number }) {
  const f = (x?: number | null) => (x == null ? "—" : x.toFixed(digits));
  const sig = "status" in s ? s : undefined;
  const scalp = isScalp(s);
  const wrong = s.call === "BUY" ? "below" : "above";
  const border = scalp && s.call !== "HOLD" ? "border-hud-scalp/60" : TONE[s.call].split(" ")[1];
  return (
    <div className={`rounded-lg border bg-hud-panel/80 p-4 ${border}`}>
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span className="tracking-widest">{title}</span>
        <span>{fmtTime(s.created)}{sig && <> · <b className="text-slate-200">{STATUS[sig.status]}</b>{sig.result_r != null && ` ${sig.result_r > 0 ? "+" : ""}${sig.result_r}R`}</>}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-4">
        <span className={`text-4xl font-bold ${TONE[s.call].split(" ")[0]}`}>{scalp && s.call !== "HOLD" ? "SCALP " : ""}{s.call}</span>
        {s.call !== "HOLD" ? <span className="text-2xl tabular-nums text-slate-200">{s.confidence}%</span>
          : s.lean !== "HOLD" && <span className="text-sm text-slate-400">leaning {s.lean} {s.confidence}</span>}
        {scalp && <span className="text-xs text-slate-500">{[s.session, s.regime].filter(Boolean).join(" · ")}</span>}
      </div>

      {sig && sig.status === "active" && scalp && sig.entry_until && (
        <p className={`mt-2 text-sm ${sig.actionable ? "text-hud-scalp" : "text-slate-500"}`}>
          {sig.actionable ? <>Enter within <Countdown until={sig.entry_until} /></> : <>Entry closed — {sig.entry_note ?? "window passed"} (still tracked)</>}
          {sig.time_stop_at && <> · time stop {hhmm(sig.time_stop_at)} if not +0.5R</>}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        {s.votes.map((v) => (
          <span key={v.agent} className={`rounded border px-2 py-0.5 ${TONE[v.direction]}`}>{v.agent}: {v.direction} {v.confidence}</span>
        ))}
        <span className={`rounded border px-2 py-0.5 ${s.risk.veto ? "border-hud-red/50 text-hud-red" : "border-hud-green/50 text-hud-green"}`}>
          risk officer: {s.risk.veto ? "VETO" : "CLEAR"}
        </span>
      </div>

      {s.levels && scalp && <CopyBlock s={s} digits={digits} pip={pip} />}
      {s.levels && (
        <div className="mt-4 grid grid-cols-3 gap-x-4 gap-y-2 text-sm tabular-nums">
          {!scalp && <>
            <L k="Entry zone" v={`${f(s.levels.entry_low)} – ${f(s.levels.entry_high)}`} wide />
            <L k="Stop" v={f(s.levels.stop)} tone="text-hud-red" />
            <L k="TP1" v={f(s.levels.tp1)} tone="text-hud-green" />
            <L k="TP2" v={f(s.levels.tp2)} tone="text-hud-green" />
          </>}
          <L k="R:R (net)" v={s.rr == null ? "— (no target)" : String(s.rr)} />
          <L k="Hold" v={s.hold_time ?? "—"} />
          <L k="Expires" v={s.expires ? fmtTime(s.expires) : "—"} />
        </div>
      )}
      {s.levels && <OddsBlock odds={s.odds} />}
      {s.invalidation != null && !scalp && (
        <p className="mt-3 text-sm text-hud-amber">Wrong if 1H closes {wrong} {f(s.invalidation)}</p>
      )}

      {(s.warnings ?? []).length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-sm text-hud-amber">{s.warnings.map((w) => <li key={w}>⚠ {w}</li>)}</ul>
      )}
      {s.hold_why.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-sm text-hud-amber">{s.hold_why.map((r) => <li key={r}>{r}</li>)}</ul>
      )}
      <ul className="mt-3 list-disc pl-5 text-sm text-slate-300">{s.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      {detailed && (
        <div className="mt-4 space-y-2 border-t border-hud-line pt-3 text-xs">
          {s.votes.map((v) => (
            <div key={v.agent}>
              <span className={`font-semibold ${TONE[v.direction].split(" ")[0]}`}>{v.agent.toUpperCase()} {v.direction} {v.confidence || ""}</span>
              <ul className="list-disc pl-5 text-slate-400">{v.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
          ))}
        </div>
      )}
      {s.call === "HOLD" && s.watch.length > 0 && (
        <p className="mt-3 text-sm text-slate-400">What would change it: {s.watch.join("; ")}</p>
      )}
    </div>
  );
}

const LEAN = { good: "text-hud-green", bad: "text-hud-red", flat: "text-hud-amber" };
const pctf = (x?: number) => `${((x ?? 0) * 100).toFixed(1)}%`;
const span = (o: Odds) => o.bars && o.bar_s ? `${o.bars} × ${o.bar_s >= 900 ? "15M" : "1M"} bars to expiry` : "";

/** Time Machine odds for a signal: a split bar, the three odds, average R after costs and the verdict line. */
export function OddsBlock({ odds, compact }: { odds?: Odds; compact?: boolean }) {
  if (!odds) return null;
  if (odds.pending) return <p className="mt-3 text-xs text-slate-500">Time Machine: replaying 10,000 sessions…</p>;
  if (odds.error) return <p className="mt-3 text-xs text-slate-500">Time Machine unavailable — {odds.error}</p>;
  const seg = (p: number | undefined, cls: string) => <div className={cls} style={{ width: `${(p ?? 0) * 100}%` }} />;
  return (
    <div className={`mt-3 rounded border border-hud-line bg-hud-bg/60 ${compact ? "p-2" : "p-3"}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-slate-500">
        <span className="tracking-widest">TIME MACHINE</span>
        <span>{odds.replays?.toLocaleString()} replays · seed {odds.seed}{!compact && ` · ${span(odds)} · ${odds.history_days}d history`}</span>
      </div>
      <div className="mt-2 flex h-2 overflow-hidden rounded bg-slate-700" title="TP1 first · still open at expiry · stop first">
        {seg(odds.p_tp1, "bg-hud-green")}{seg(odds.p_neither, "bg-slate-600")}{seg(odds.p_stop, "bg-hud-red")}
      </div>
      <div className="mt-2 grid grid-cols-4 gap-2 text-sm tabular-nums">
        <div><div className="text-xs text-slate-500">TP1 first</div><div className="text-hud-green">{pctf(odds.p_tp1)}</div></div>
        <div><div className="text-xs text-slate-500">Stop first</div><div className="text-hud-red">{pctf(odds.p_stop)}</div></div>
        <div><div className="text-xs text-slate-500">Open at expiry</div><div className="text-slate-300">{pctf(odds.p_neither)}</div></div>
        <div><div className="text-xs text-slate-500">Avg R · costs {odds.cost_r?.toFixed(2)}R</div>
          <div className={(odds.avg_r ?? 0) >= 0 ? "text-hud-green" : "text-hud-red"}>{(odds.avg_r ?? 0) > 0 ? "+" : ""}{odds.avg_r?.toFixed(2)}R</div></div>
      </div>
      <p className={`mt-2 text-sm ${LEAN[odds.lean ?? "flat"]}`}>{odds.verdict}</p>
      {(odds.notes ?? []).map((n) => <p key={n} className="mt-1 text-xs text-slate-400">{n}</p>)}
      {!compact && <p className="mt-1 text-xs text-slate-500">Simulated from this market's past bars at the same time of day — not a prediction.</p>}
    </div>
  );
}

function L({ k, v, tone = "text-slate-200", wide }: { k: string; v: string; tone?: string; wide?: boolean }) {
  return (
    <div className={wide ? "col-span-2" : ""}>
      <div className="text-xs text-slate-500">{k}</div>
      <div className={tone}>{v}</div>
    </div>
  );
}
