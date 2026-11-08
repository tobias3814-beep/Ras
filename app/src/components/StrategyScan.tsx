import { useEffect, useState } from "react";
import { API, type Stats } from "../ws";
import { GAIN, LOSS } from "./charts";
import { INSTRUMENTS, META } from "./InstrumentTile";

type Row = { run: number; symbol: string; tf: string; days: number; key: string; name: string; group: string; summary: Stats; verdict: string;
  positive_folds: number; folds: number; combos_positive: number; combos: number; params: string };
type Scan = { status: string; created?: number; total?: number; runs?: number[]; current?: string; error?: string;
  spec?: { tfs: string[]; folds: number; funding_pct: number; days: Record<string, number> }; rows: Row[]; failed: { symbol: string; tf: string; error?: string }[] };

const TFS: [string, string, string][] = [["MINUTE_15", "15m", "1 year"], ["HOUR", "1h", "2 years"], ["HOUR_4", "4h", "~4 years"], ["DAY", "Daily", "~25 years"]];
const TF_NAME = Object.fromEntries(TFS.map(([k, n]) => [k, n]));
const MARK: Record<string, string> = { "survives checks": "★", "passes screen": "●", promising: "●", marginal: "◐", "doesn't work": "○", "too few trades": "·" };
const SCREENED = ["survives checks", "passes screen", "promising"];
const r2 = (x?: number | null) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}`);
const post = (path: string, body: object) =>
  fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail?.toString() ?? r.statusText); return j; });

/** Edge scan for the Strategies-tab rules: each rule walk-forward tested on each market on its own, per timeframe. */
export function StrategyScan() {
  const [scan, setScan] = useState<Scan>();
  const [tfs, setTfs] = useState<string[]>(["HOUR", "DAY"]);
  const [folds, setFolds] = useState(5);
  const [funding, setFunding] = useState(2);
  const [view, setView] = useState<string>();
  const [msg, setMsg] = useState("");
  const load = () => fetch(`${API}/api/strategy-scan`).then((r) => r.json()).then((s: Scan) => setScan(s)).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (scan?.status !== "running") return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [scan?.status]);

  const running = scan?.status === "running";
  const start = () => { setMsg(""); post("/api/strategy-scan", { tfs, folds, funding_pct: funding }).then(load).catch((e) => setMsg(e.message)); };
  const scanned = TFS.map(([k]) => k).filter((k) => scan?.rows.some((r) => r.tf === k));
  const tf = view && scanned.includes(view) ? view : scanned[0];
  const rows = (scan?.rows ?? []).filter((r) => r.tf === tf);
  const keys = [...new Map(rows.map((r) => [r.key, r])).values()];
  const cell = (key: string, sym: string) => rows.find((r) => r.key === key && r.symbol === sym);
  const input = "w-16 rounded border border-hud-line bg-hud-bg px-2 py-0.5 text-sm text-slate-200";

  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="text-xs text-slate-400">
        Walk-forward tests every Strategies-tab rule (indicators, trend following, ICT, SMC, ICC) on each market on its own:
        each fold picks the rule's settings on that market's earlier data and judges them on the next stretch. One run per
        market and timeframe, saved under Strategies, so the live desks and analysis panels use the results too. Changes nothing.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        {TFS.map(([k, n, span]) => (
          <label key={k} className="flex items-center gap-1" title={`${span} of history`}>
            <input type="checkbox" checked={tfs.includes(k)} onChange={(e) => setTfs(e.target.checked ? [...tfs, k] : tfs.filter((x) => x !== k))} />{n}
          </label>
        ))}
        <label className="flex items-center gap-1">Folds <input type="number" min={2} max={8} className={input} value={folds} onChange={(e) => setFolds(Number(e.target.value))} /></label>
        <label className="flex items-center gap-1" title="Overnight funding on positions held overnight (CFDs)">Funding %/yr <input type="number" min={0} max={20} step={0.5} className={input} value={funding} onChange={(e) => setFunding(Number(e.target.value))} /></label>
        <button disabled={running || !tfs.length} onClick={start} className="rounded border border-hud-accent px-3 py-1 text-hud-accent disabled:opacity-40">
          {running ? "Scanning…" : scan?.rows.length ? "Run a new scan" : "Run strategy scan"}</button>
        <span className="text-xs text-slate-500">{tfs.length * INSTRUMENTS.length} runs; 15m downloads a year of candles, so allow 10–30 minutes.</span>
      </div>
      {running && (
        <div>
          <div className="mb-1 text-xs text-slate-400">{scan.runs?.length ?? 0} of {scan.total} · {scan.current}</div>
          <div className="h-1.5 rounded bg-slate-800/60"><div className="h-full rounded bg-hud-accent" style={{ width: `${((scan.runs?.length ?? 1) - 1) / (scan.total ?? 1) * 100}%` }} /></div>
        </div>
      )}
      {scan?.status === "failed" && <p className="text-xs text-hud-red">Scan stopped: {scan.error}</p>}
      {msg && <p className="text-xs text-hud-red">{msg}</p>}
      {scanned.length > 0 && (
        <>
          <div className="flex items-center gap-2 text-xs">
            {scanned.map((k) => (
              <button key={k} onClick={() => setView(k)} className={`rounded border px-2 py-0.5 ${tf === k ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>{TF_NAME[k]}</button>
            ))}
            <span className="text-slate-500">· {rows[0]?.days.toFixed(0)} days · {scan!.spec?.folds} folds · funding {scan!.spec?.funding_pct}%/yr
              {scan!.created && <> · scanned {new Date(scan!.created).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}</>}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm tabular-nums">
              <thead className="text-xs text-slate-500"><tr><th>Strategy</th>
                {INSTRUMENTS.map((s) => <th key={s} className="text-right">{META[s].name}</th>)}<th className="pl-3">Has an edge on</th></tr></thead>
              <tbody>{keys.map((k) => {
                const wins = INSTRUMENTS.filter((s) => SCREENED.includes(cell(k.key, s)?.verdict ?? ""));
                return (
                  <tr key={k.key} className="border-t border-hud-line/60">
                    <td className="py-1"><span className="text-slate-200">{k.name}</span> <span className="text-xs text-slate-500">{k.group}</span></td>
                    {INSTRUMENTS.map((s) => {
                      const c = cell(k.key, s);
                      if (!c) return <td key={s} className="text-right text-slate-600">—</td>;
                      const e = c.summary.expectancy;
                      return (
                        <td key={s} className="text-right" title={`${c.verdict} · ${c.summary.trades} trades · win ${c.summary.win_rate ?? "—"}% · PF ${c.summary.profit_factor ?? "—"} · net ${r2(c.summary.total_r)}R\nfolds positive ${c.positive_folds}/${c.folds} · settings positive ${c.combos_positive}/${c.combos}\nlast fold's settings: ${c.params || "—"}\nrun #${c.run}`}>
                          <span style={{ color: c.summary.trades ? ((e ?? 0) >= 0 ? GAIN : LOSS) : undefined }} className={SCREENED.includes(c.verdict) ? "font-semibold" : c.verdict === "too few trades" ? "text-slate-500" : ""}>
                            {c.summary.trades ? r2(e) : "—"}</span>
                          <span className="ml-1 text-xs text-slate-500">{MARK[c.verdict] ?? ""}{c.summary.trades}</span>
                        </td>
                      );
                    })}
                    <td className="pl-3 text-xs">{wins.length ? <span className="text-hud-green">{wins.map((s) => META[s].name).join(", ")}</span> : <span className="text-slate-500">none</span>}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          {scan!.failed.length > 0 && <p className="text-xs text-hud-amber">Couldn't test: {scan!.failed.map((f) => `${META[f.symbol]?.name ?? f.symbol} ${TF_NAME[f.tf]} (${f.error || "failed"})`).join(" · ")}</p>}
          <p className="text-xs text-slate-500">
            Each cell: out-of-sample average R per trade, then ★ survives the luck checks · ● passes screen only (30+ trades, ≥ +0.10R, PF ≥ 1.15, half the folds positive; rules with no edge often reach this) ·
            ◐ marginal · ○ doesn't work · · too few trades, and the trade count. Hover a cell for details. A rule that works on one
            market but fails on the rest may just be riding that market's own trend.
          </p>
        </>
      )}
    </div>
  );
}
