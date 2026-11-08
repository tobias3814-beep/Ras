import { useState } from "react";
import { API, type Execution, type ExecutionState, type HaltState, type Signal } from "../ws";
import { Panel } from "./Panels";
import { OddsBlock, fmtTime } from "./SignalCard";
import { AudioSettings, ConfigEditor, PaperAccount, ThemePicker } from "./SettingsExtras";

const post = (path: string, body?: object) =>
  fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) })
    .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail?.toString() ?? r.statusText); return j; });

/** Always-visible KILL SWITCH. One confirmation, then: pause signals, close paper, cancel + flatten the futures account. */
export function KillButton() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const fire = () => {
    setBusy(true);
    post("/api/kill", { confirm: true })
      .then((k) => setResult(`${k.exchange.cancelled} orders cancelled · ${k.exchange.closed.length} positions closed · ${k.paper_closed} paper closed${k.exchange.errors.length ? ` · errors: ${k.exchange.errors.join("; ")}` : ""}`))
      .catch((e) => setResult(`Failed: ${e.message}`))
      .finally(() => setBusy(false));
  };
  return (
    <>
      <button onClick={() => { setOpen(true); setResult(""); }}
        className="rounded border border-hud-red bg-hud-red/15 px-3 py-1 text-xs font-bold tracking-widest text-hud-red hover:bg-hud-red/30">
        KILL SWITCH
      </button>
      {open && (
        <div className="fixed inset-0 z-[60] grid place-items-center bg-black/50" onClick={() => !busy && setOpen(false)}>
          <div className="w-[28rem] rounded-lg border border-hud-red bg-hud-bg p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold tracking-widest text-hud-red">KILL SWITCH</h3>
            {result ? <p className="mt-3 text-sm text-slate-200">{result}</p> : (
              <ul className="mt-3 list-disc pl-5 text-sm text-slate-300">
                <li>Pause all new signals (all divisions) until you resume</li>
                <li>Close every paper position</li>
                <li>Cancel <b>every</b> open futures order and close <b>every</b> futures position on the execution account</li>
              </ul>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setOpen(false)} disabled={busy} className="rounded border border-hud-line px-3 py-1 text-sm text-slate-300">{result ? "Close" : "Cancel"}</button>
              {!result && <button onClick={fire} disabled={busy} className="rounded bg-hud-red px-4 py-1 text-sm font-bold text-black">{busy ? "Working…" : "Confirm — kill"}</button>}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export function HaltBanner({ h }: { h: HaltState }) {
  if (!h.active) return null;
  return (
    <div className="flex items-center justify-between rounded border border-hud-red/60 bg-hud-red/10 px-3 py-2 text-sm text-hud-red">
      <span>KILL SWITCH ACTIVE since {fmtTime(h.since)} — new signals are paused ({h.why}).</span>
      <button onClick={() => post("/api/resume")} className="rounded border border-hud-red px-3 py-0.5 text-xs hover:bg-hud-red/20">Resume signals</button>
    </div>
  );
}

/** APPROVE / MODIFY / REJECT for a crypto perp signal. Only shown while execution is enabled. */
export function ExecutionBar({ sig, exec, state, digits }: { sig: Signal; exec?: Execution; state?: ExecutionState; digits: number }) {
  const [mod, setMod] = useState(false);
  const [stop, setStop] = useState(String(sig.levels?.stop ?? ""));
  const [tp, setTp] = useState(String(sig.levels?.tp1 ?? ""));
  const [risk, setRisk] = useState(String(state?.risk_pct ?? 0.5));
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const perp = sig.symbol.startsWith("perp:");
  if (exec) {
    const tone = exec.status === "failed" ? "text-hud-red" : exec.status === "rejected" ? "text-slate-500" : "text-hud-green";
    return (
      <Panel title={`EXECUTION · ${exec.environment.toUpperCase()}`}>
        <p className={`text-sm ${tone}`}>{exec.status.toUpperCase()}{exec.reason ? ` — ${exec.reason}` : ""}</p>
        {exec.qty != null && <p className="mt-1 text-xs tabular-nums text-slate-400">
          {exec.side} {exec.qty} @ {exec.entry_px?.toFixed(digits) ?? "—"} · SL {exec.stop?.toFixed(digits)} · TP {exec.tp?.toFixed(digits)}
          {exec.pnl != null && ` · P&L ${exec.pnl.toFixed(2)} USDT`}</p>}
      </Panel>
    );
  }
  if (!perp || sig.status !== "active") return null;
  if (!state?.enabled) return <p className="text-xs text-slate-500">Paper only — enable demo execution in Settings to approve real (demo) orders.</p>;
  if (!sig.actionable) return <p className="text-xs text-slate-500">Entry window closed — this one stays paper.</p>;
  const act = (path: string, body?: object) => { setBusy(true); setErr(""); post(path, body).catch((e) => setErr(e.message)).finally(() => setBusy(false)); };
  const input = "w-24 rounded border border-hud-line bg-hud-panel px-1 py-0.5 text-sm tabular-nums text-slate-200";
  return (
    <Panel title={`EXECUTE ON BINANCE ${state.environment.toUpperCase()}`}>
      <OddsBlock odds={sig.odds} compact />
      <div className="mt-3 flex flex-wrap gap-2">
        <button disabled={busy} onClick={() => act(`/api/execution/approve/${sig.id}`, mod ? { stop: +stop, tp: +tp, risk_pct: +risk } : {})}
          className="rounded bg-hud-green px-4 py-1 text-sm font-bold text-black disabled:opacity-40">{mod ? "APPROVE MODIFIED" : "APPROVE"}</button>
        <button disabled={busy} onClick={() => setMod(!mod)} className="rounded border border-hud-amber px-3 py-1 text-sm text-hud-amber">MODIFY</button>
        <button disabled={busy} onClick={() => act(`/api/execution/reject/${sig.id}`)} className="rounded border border-hud-red px-3 py-1 text-sm text-hud-red">REJECT</button>
      </div>
      {mod && (
        <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
          <label>Stop <input className={input} value={stop} onChange={(e) => setStop(e.target.value)} /></label>
          <label>Target <input className={input} value={tp} onChange={(e) => setTp(e.target.value)} /></label>
          <label>Risk % <input className={input} value={risk} onChange={(e) => setRisk(e.target.value)} /></label>
        </div>
      )}
      <p className="mt-2 text-xs text-slate-500">
        Market entry + reduce-only stop-loss and take-profit{state.trailing_pct ? ` + ${state.trailing_pct}% trailing stop` : ""} ·
        {` ${state.risk_pct}% risk · max ${state.max_notional_usdt} USDT · ${state.leverage}x isolated`}
      </p>
      {err && <p className="mt-1 text-sm text-hud-red">{err}</p>}
    </Panel>
  );
}

export function SettingsPage({ halt }: { halt: HaltState }) {
  const [tg, setTg] = useState("");
  const btn = "rounded border border-hud-line px-3 py-1 text-sm text-hud-accent hover:border-hud-accent";
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <h2 className="text-lg tracking-[0.3em] text-hud-accent">SETTINGS</h2>
      <ThemePicker />
      <PaperAccount />
      <Panel title="KILL SWITCH">
        <p className={`text-sm ${halt.active ? "text-hud-red" : "text-slate-300"}`}>
          {halt.active ? `ACTIVE since ${fmtTime(halt.since)} — new signals paused.` : "Off. The red button in the header (or saying “Hey Jarvis, kill switch”, then “confirm”) pauses all signals, and closes paper positions."}
        </p>
        {halt.active && <button className={`${btn} mt-2`} onClick={() => post("/api/resume")}>Resume signals</button>}
      </Panel>
      <Panel title="TELEGRAM ALERTS">
        <p className="text-sm text-slate-300">Scalps, risk warnings and the kill switch go to your phone once a bot token and chat id are in the keychain (<code>uv run python -m jarvis.keys</code>).</p>
        <button className={`${btn} mt-2 self-start`} onClick={() => post("/api/telegram/test").then(() => setTg("Sent ✓")).catch((e) => setTg(e.message))}>Send test message</button>
        {tg && <p className="mt-1 text-xs text-slate-400">{tg}</p>}
      </Panel>
      <AudioSettings />
      <ConfigEditor />
    </div>
  );
}
