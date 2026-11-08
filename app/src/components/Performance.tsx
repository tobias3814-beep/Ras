import { useEffect, useState } from "react";
import { API, type Learning, type Report } from "../ws";
import { LineChart, ReportView } from "./charts";
import { ForwardTestPanel } from "./ForwardTest";
import { Panel } from "./Panels";

type Perf = Report & { paper_equity: Record<string, { time: number; equity: number }[]> };

export function PerformancePage({ learning }: { learning?: Learning }) {
  const [mode, setMode] = useState("");
  const [days, setDays] = useState(30);
  const [rep, setRep] = useState<Perf>();
  useEffect(() => {
    const load = () => fetch(`${API}/api/performance?days=${days}${mode ? `&mode=${mode}` : ""}`).then((r) => r.json()).then(setRep);
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [mode, days]);
  const sel = "rounded border border-hud-line bg-hud-bg px-2 py-1 text-sm text-slate-200";
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-4 text-lg tracking-[0.3em] text-hud-accent">PERFORMANCE</h2>
        <select className={sel} value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="">All modes</option><option value="intraday">Intraday</option><option value="scalp">Scalp</option>
        </select>
        <select className={sel} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
        <span className="text-xs text-slate-500">Live signal outcomes in R (1R = the signal's initial risk)</span>
      </div>
      <ForwardTestPanel />
      {rep ? <ReportView rep={rep} /> : <p className="text-slate-500">Loading…</p>}
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="PAPER EQUITY">
          <LineChart points={(rep?.paper_equity.fx ?? []).map((p) => ({ time: p.time, value: p.equity }))} unit="USD" height={220} />
        </Panel>
        <LearningPanel learning={learning} />
      </div>
    </div>
  );
}

function LearningPanel({ learning }: { learning?: Learning }) {
  const act = (a: string) => fetch(`${API}/api/learning/${a}`, { method: "POST" });
  const btn = "rounded border border-hud-line px-2 py-0.5 text-xs text-hud-accent hover:border-hud-accent disabled:opacity-40";
  if (!learning) return <Panel title="AGENT WEIGHTS"><p className="text-sm text-slate-500">Loading…</p></Panel>;
  return (
    <Panel title="SELF-LEARNING" right={
      <span className="flex gap-2">
        <button className={btn} disabled={learning.frozen} onClick={() => act("update")}>Update now</button>
        <button className={btn} onClick={() => act(learning.frozen ? "unfreeze" : "freeze")}>{learning.frozen ? "Unfreeze" : "Freeze"}</button>
        <button className={btn} onClick={() => act("reset")}>Reset</button>
      </span>
    }>
      <table className="w-full text-left text-sm tabular-nums">
        <thead className="text-xs text-slate-500"><tr><th>Agent</th><th className="text-right">Weight</th><th className="text-right">Default</th><th className="text-right">Accuracy</th><th className="text-right">Graded</th></tr></thead>
        <tbody>
          {Object.entries(learning.weights).map(([a, w]) => {
            const s = learning.agents[a];
            return (
              <tr key={a} className="border-t border-hud-line/60">
                <td className="text-slate-200">{a}{a === "technical" && <span className="text-xs text-slate-500"> (times the trade, not learned)</span>}</td>
                <td className="text-right">{w.toFixed(3)}</td><td className="text-right text-slate-500">{learning.defaults[a]}</td>
                <td className="text-right">{s ? `${s.accuracy}%` : "—"}</td>
                <td className="text-right">{s?.graded ?? 0}{(s?.graded ?? 0) < learning.min_samples && <span className="text-xs text-slate-500"> /{learning.min_samples}</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-slate-500">
        {learning.frozen ? "Frozen — nothing changes." : "Learns after every closed trade: weights move at most ±0.05 per trade, bounded 0.1–1.5, only after enough graded votes."}
      </p>
      {Object.keys(learning.strategies).length > 0 && (
        <div className="mt-3">
          <div className="text-xs tracking-widest text-slate-400">SCALP STRATEGIES · LEARNED SCORE ADJUSTMENT</div>
          <div className="mt-1 flex flex-wrap gap-2 text-xs tabular-nums">
            {Object.entries(learning.strategies).map(([k, v]) => (
              <span key={k} className={`rounded border px-2 py-0.5 ${v < 0 ? "border-hud-red/40 text-hud-red" : v > 0 ? "border-hud-green/40 text-hud-green" : "border-hud-line text-slate-400"}`}>{k} {v > 0 ? "+" : ""}{v}</span>
            ))}
          </div>
        </div>
      )}
      <div className="mt-2 max-h-32 overflow-y-auto text-xs text-slate-400">
        {learning.log.length === 0 ? "No changes yet." : learning.log.map((l, i) => (
          <div key={i}>{new Date(l.ts).toLocaleDateString("en-GB")} · {l.agent} {l.old.toFixed(3)} → {l.new.toFixed(3)} · {l.reason}</div>
        ))}
      </div>
    </Panel>
  );
}
