import { useEffect, useState } from "react";
import { API, type BacktestEvent, type Report, type Stats } from "../ws";
import { ReportView } from "./charts";
import { INSTRUMENTS, META } from "./InstrumentTile";
import { EdgeScan } from "./EdgeScan";
import { Panel } from "./Panels";

type Result = {
  spec: { symbols: string[]; mode: string; days: number; folds: number }; range: [number, number]; default_threshold: number;
  candidates: Record<string, number>; notes: string[]; baseline: Report; out_of_sample: Report;
  folds: { fold: number; train: [number, number]; test: [number, number]; threshold: number; in_sample: Stats; out_of_sample: Stats }[];
  error?: string; exits?: { fraction: number; used: number; full: Stats; partial: Stats };
};
type Run = { id: number; created: number; status: string; spec: Result["spec"]; result?: Result | null };

export function BacktestPage({ bt }: { bt?: BacktestEvent }) {
  const [mode, setMode] = useState<"scalp" | "intraday">("scalp");
  const [fx, setFx] = useState<string[]>(["XAU_USD"]);
  const [days, setDays] = useState(7);
  const [folds, setFolds] = useState(3);
  const [runs, setRuns] = useState<Run[]>([]);
  const [shown, setShown] = useState<Run>();
  const [err, setErr] = useState("");
  const [view, setView] = useState<"oos" | "baseline">("oos");

  const loadRuns = () => fetch(`${API}/api/backtests`).then((r) => r.json()).then(setRuns);
  const open = (id: number) => fetch(`${API}/api/backtest/${id}`).then((r) => r.json()).then(setShown);
  useEffect(() => { loadRuns(); }, []);
  useEffect(() => { if (bt && bt.status !== "running") { loadRuns(); open(bt.id); } }, [bt?.status, bt?.id]);

  const start = () => {
    setErr("");
    fetch(`${API}/api/backtest`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbols: fx, mode, days, folds }) })
      .then(async (r) => { if (!r.ok) setErr((await r.json()).detail?.toString() ?? "failed"); else loadRuns(); });
  };
  const running = bt?.status === "running";
  const input = "rounded border border-hud-line bg-hud-bg px-2 py-1 text-sm text-slate-200";
  const res = shown?.result;

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg tracking-[0.3em] text-hud-accent">BACKTEST · WALK-FORWARD</h2>
      <EdgeScan bt={bt} />
      <div className="grid gap-4 xl:grid-cols-[1fr_2fr]">
        <Panel title="NEW RUN">
          <div className="flex flex-col gap-3 text-sm">
            <label className="flex items-center gap-2">Mode
              <select className={input} value={mode} onChange={(e) => { const m = e.target.value as typeof mode; setMode(m); setDays(m === "intraday" ? 60 : 7); }}>
                <option value="scalp">FX/metals/oil scalp (1M bid/ask)</option><option value="intraday">Intraday technical (15M)</option>
              </select>
            </label>
            <div className="flex flex-wrap gap-2">{INSTRUMENTS.map((s) => (
                <label key={s} className="flex items-center gap-1"><input type="checkbox" checked={fx.includes(s)}
                  onChange={(e) => setFx(e.target.checked ? [...fx, s] : fx.filter((x) => x !== s))} />{META[s].name}</label>
              ))}</div>
            <div className="flex gap-4">
              <label className="flex items-center gap-2">Days <input type="number" min={1} max={180} className={`${input} w-20`} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
              <label className="flex items-center gap-2">Folds <input type="number" min={1} max={6} className={`${input} w-16`} value={folds} onChange={(e) => setFolds(Number(e.target.value))} /></label>
            </div>
            <button disabled={running} onClick={start} className="self-start rounded border border-hud-accent px-4 py-1 text-hud-accent disabled:opacity-40">Run backtest</button>
            {err && <p className="text-hud-red">{err}</p>}
            {bt && (bt.status === "running" || bt.status === "failed") && (
              <div>
                <div className="h-1.5 rounded bg-slate-800"><div className="h-full rounded bg-hud-accent" style={{ width: `${bt.progress * 100}%` }} /></div>
                <p className={`mt-1 text-xs ${bt.status === "failed" ? "text-hud-red" : "text-slate-400"}`}>{bt.msg}</p>
              </div>
            )}
            <p className="text-xs text-slate-500">History downloads on demand into data/history (Parquet). Needs Capital.com credentials.</p>
          </div>
          <div className="mt-4 border-t border-hud-line pt-2 text-xs">
            <div className="mb-1 tracking-widest text-slate-400">PAST RUNS</div>
            {runs.map((r) => (
              <div key={r.id} onClick={() => open(r.id)} className={`cursor-pointer truncate py-0.5 hover:text-hud-accent ${shown?.id === r.id ? "text-hud-accent" : "text-slate-300"}`}>
                #{r.id} {r.spec.mode} · {r.spec.days}d · {r.spec.symbols.map((s) => s.replace(/^(spot|perp):/, "").replace(":USDT", "")).join(", ")} · {r.status}
              </div>
            ))}
          </div>
        </Panel>
        <div className="flex flex-col gap-4">
          {!res ? <p className="text-sm text-slate-500">Run a backtest or pick a past run.</p> : res.error ? <p className="text-hud-red">Failed: {res.error}</p> : (
            <>
              <Panel title="WALK-FORWARD FOLDS · THRESHOLD CHOSEN IN-SAMPLE, JUDGED OUT-OF-SAMPLE">
                <table className="w-full text-left text-sm tabular-nums">
                  <thead className="text-xs text-slate-500"><tr><th>Fold</th><th>Train → Test</th><th className="text-right">Threshold</th><th className="text-right">IS n / avg R</th><th className="text-right">OOS n / avg R</th><th className="text-right">OOS win</th></tr></thead>
                  <tbody>{res.folds.map((f) => (
                    <tr key={f.fold} className="border-t border-hud-line/60">
                      <td>{f.fold}</td><td className="text-xs text-slate-400">0–{Math.round(f.train[1] * 100)}% → {Math.round(f.test[0] * 100)}–{Math.round(f.test[1] * 100)}%</td>
                      <td className="text-right">{f.threshold}</td>
                      <td className="text-right">{f.in_sample.trades} / {f.in_sample.expectancy?.toFixed(2) ?? "—"}</td>
                      <td className="text-right">{f.out_of_sample.trades} / {f.out_of_sample.expectancy?.toFixed(2) ?? "—"}</td>
                      <td className="text-right">{f.out_of_sample.win_rate == null ? "—" : `${f.out_of_sample.win_rate}%`}</td>
                    </tr>
                  ))}</tbody>
                </table>
                <p className="mt-2 text-xs text-slate-500">
                  {new Date(res.range[0] * 1000).toLocaleDateString("en-GB")} – {new Date(res.range[1] * 1000).toLocaleDateString("en-GB")} ·
                  candidates {Object.values(res.candidates).reduce((a, b) => a + b, 0)} · configured threshold {res.default_threshold}
                </p>
              </Panel>
              <div className="flex gap-2 text-xs">
                {(["oos", "baseline"] as const).map((v) => (
                  <button key={v} onClick={() => setView(v)} className={`rounded border px-2 py-0.5 ${view === v ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>
                    {v === "oos" ? "Out-of-sample (honest)" : `Full period at ${res.default_threshold} (in-sample)`}
                  </button>
                ))}
              </div>
              {res.exits && (
                <Panel title="EXIT STYLE · OUT-OF-SAMPLE, SAME TRADES">
                  <p className="text-sm tabular-nums text-slate-300">
                    Close all at TP1: <b>{res.exits.full.expectancy ?? "—"}R</b> avg, net {res.exits.full.total_r}R ·
                    Bank {Math.round(res.exits.fraction * 100)}% at TP1 + run the rest: <b>{res.exits.partial.expectancy ?? "—"}R</b> avg, net {res.exits.partial.total_r}R
                    <span className="text-xs text-slate-500"> (report below uses {res.exits.used ? "partial" : "TP1"} exits)</span>
                  </p>
                </Panel>
              )}
              <ReportView rep={view === "oos" ? res.out_of_sample : res.baseline} />
              <Panel title="ASSUMPTIONS"><ul className="list-disc pl-5 text-xs text-slate-400">{res.notes.map((n) => <li key={n}>{n}</li>)}</ul></Panel>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
