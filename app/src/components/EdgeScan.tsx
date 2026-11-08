import { useEffect, useState } from "react";
import { API, type BacktestEvent } from "../ws";
import { GAIN, LOSS } from "./charts";
import { META } from "./InstrumentTile";
import { Panel } from "./Panels";
import { StrategyScan } from "./StrategyScan";

type S = { trades: number; expectancy: number | null; profit_factor: number | null; total_r: number; win_rate: number | null };
type Filt = { trend: boolean; chop: boolean; hours: number[] };
type Row = { backtest_id: number; symbol: string; mode: "intraday" | "scalp"; days: number; full: S; partial: S | null;
  fraction: number; best_exit: "full" | "partial"; verdict: string; best: S; best_filter: string; filter: Filt;
  variants?: Record<string, { stats: S; skip_hours: number[] }> };
type Scan = { status: string; created?: number; total?: number; runs?: number[]; current?: string; error?: string; rows: Row[];
  failed?: { backtest_id: number; spec: { symbols: string[]; mode: string }; error?: string }[];
  exits_now?: number; recommend_partial?: boolean; modes?: Record<string, { intraday: boolean; scalp: boolean }>;
  filters?: Record<string, Filt> };
const FILTER_NAMES: Record<string, string> = { none: "no filter", trend: "trend", chop: "chop", hours: "worst hours", all: "all three" };
const sameFilt = (a?: Filt, b?: Filt) => !!a === !!b && (!a || (a.trend === b!.trend && a.chop === b!.chop && a.hours.join() === b!.hours.join()));
const OFF: Filt = { trend: false, chop: false, hours: [] };
type Choice = "leave" | "on" | "off";

const CHIP: Record<string, string> = {
  "keep on": "border-hud-green/50 text-hud-green", "switch off": "border-hud-red/50 text-hud-red",
  marginal: "border-hud-amber/50 text-hud-amber", "too few trades": "border-hud-line text-slate-400",
};
const r2 = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`);
const post = (path: string, body: object) =>
  fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail?.toString() ?? r.statusText); return j; });

/** Edge scan: walk-forward every instrument in both modes, both exit styles; you choose what to apply. */
export function EdgeScan({ bt }: { bt?: BacktestEvent }) {
  const [scan, setScan] = useState<Scan>();
  const [days, setDays] = useState({ intraday_days: 120, scalp_days: 30, folds: 4 });
  const [choice, setChoice] = useState<Record<string, Choice>>({});
  const [useFilt, setUseFilt] = useState<Record<string, boolean>>({});
  const [partial, setPartial] = useState(false);
  const [msg, setMsg] = useState("");
  const [kind, setKind] = useState<"modes" | "strategies">(() => { try { return localStorage.getItem("ras.edgescan") === "strategies" ? "strategies" : "modes"; } catch { return "modes"; } });
  const pickKind = (k: "modes" | "strategies") => { setKind(k); try { localStorage.setItem("ras.edgescan", k); } catch { /* storage blocked */ } };
  const load = () => fetch(`${API}/api/edge-scan`).then((r) => r.json()).then((s: Scan) => {
    setScan(s);
    setChoice(Object.fromEntries(s.rows.map((r) => [`${r.symbol}:${r.mode}`,
      r.verdict === "keep on" ? "on" : r.verdict === "switch off" ? "off" : "leave"])));
    setPartial(!!s.recommend_partial);
    setUseFilt(Object.fromEntries(s.rows.map((r) => [`${r.symbol}:${r.mode}`, r.best_filter !== "none"])));
  }).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => {  // poll while a scan runs; one final load when it ends
    if (scan?.status !== "running") return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [scan?.status]);

  const start = () => { setMsg(""); post("/api/edge-scan", days).then(load).catch((e) => setMsg(e.message)); };
  const changes = (scan?.rows ?? []).filter((r) => {
    const c = choice[`${r.symbol}:${r.mode}`], now = scan?.modes?.[r.symbol]?.[r.mode];
    return c !== "leave" && (c === "on") !== now;
  });
  const exitChange = (scan?.exits_now ?? 0) > 0 !== partial;
  const want = (r: Row) => (useFilt[`${r.symbol}:${r.mode}`] ? r.filter : OFF);
  const filtChanges = (scan?.rows ?? []).filter((r) => !sameFilt(scan?.filters?.[`${r.symbol}:${r.mode}`] ?? OFF, want(r)));
  const nChanges = changes.length + filtChanges.length + (exitChange ? 1 : 0);
  const apply = async () => {
    try {
      for (const r of changes) await post(`/api/modes/${r.symbol}`, { [r.mode]: choice[`${r.symbol}:${r.mode}`] === "on" });
      for (const r of filtChanges) await post(`/api/filters/${r.symbol}/${r.mode}`, want(r));
      if (exitChange) await post("/api/exits", { partial: partial ? scan?.rows[0]?.fraction || 0.5 : 0 });
      setMsg(`Applied ${nChanges} change(s). The forward test will show whether they hold up live.`);
      load();
    } catch (e) { setMsg((e as Error).message); }
  };
  const running = scan?.status === "running";
  const input = "w-16 rounded border border-hud-line bg-hud-bg px-2 py-0.5 text-sm text-slate-200";
  const sel = "rounded border border-hud-line bg-hud-bg px-1 py-0.5 text-xs text-slate-200";
  const frac = Math.round((scan?.rows[0]?.fraction ?? 0.5) * 100);

  return (
    <Panel title="EDGE SCAN · WHERE DOES RAS ACTUALLY HAVE AN EDGE?" right={
      <span className="flex gap-1 normal-case tracking-normal">{([["modes", "Ras modes"], ["strategies", "Strategies"]] as const).map(([k, n]) => (
        <button key={k} onClick={() => pickKind(k)} className={`rounded border px-2 py-0.5 ${kind === k ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>{n}</button>
      ))}</span>}>
      {kind === "strategies" ? <StrategyScan /> : (
      <div className="flex flex-col gap-3 text-sm">
        <p className="text-xs text-slate-400">
          Backtests every instrument in intraday and scalp mode (walk-forward, out-of-sample only) with both exit styles
          (close everything at TP1, or bank {frac}% at TP1, stop to entry, rest to TP2) and the optional filters: trade only with the
          higher-timeframe trend (daily for intraday, 1H for scalps), skip a choppy 1H market, skip hours that lost in-sample.
          A filter is suggested only if it beats no filter by 0.05R+ with 20+ trades. Hover a filter cell for every variant. Nothing changes until you apply it.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1">Intraday days <input type="number" min={30} max={180} className={input} value={days.intraday_days}
            onChange={(e) => setDays({ ...days, intraday_days: Number(e.target.value) })} /></label>
          <label className="flex items-center gap-1">Scalp days <input type="number" min={7} max={180} className={input} value={days.scalp_days}
            onChange={(e) => setDays({ ...days, scalp_days: Number(e.target.value) })} /></label>
          <label className="flex items-center gap-1">Folds <input type="number" min={2} max={6} className={input} value={days.folds}
            onChange={(e) => setDays({ ...days, folds: Number(e.target.value) })} /></label>
          <button disabled={running} onClick={start} className="rounded border border-hud-accent px-3 py-1 text-hud-accent disabled:opacity-40">
            {running ? "Scanning…" : scan?.rows.length ? "Run a new scan" : "Run edge scan"}</button>
          <span className="text-xs text-slate-500">12 runs; scalps download 1-minute history, so allow 10–20 minutes.</span>
        </div>
        {running && (
          <div>
            <div className="mb-1 text-xs text-slate-400">{scan.runs?.length ?? 0} of {scan.total} · {scan.current} {bt?.status === "running" ? `· ${bt.msg}` : ""}</div>
            <div className="h-1.5 rounded bg-slate-800/60">
              <div className="h-full rounded bg-hud-accent" style={{ width: `${(((scan.runs?.length ?? 1) - 1 + (bt?.status === "running" ? bt.progress : 0)) / (scan.total ?? 12)) * 100}%` }} />
            </div>
          </div>
        )}
        {scan?.status === "failed" && <p className="text-xs text-hud-red">Scan stopped: {scan.error}</p>}
        {(scan?.rows.length ?? 0) > 0 && (
          <>
            <table className="w-full text-left tabular-nums">
              <thead className="text-xs text-slate-500"><tr><th>Instrument</th><th>Mode</th><th className="text-right">Trades</th>
                <th className="text-right">Exit at TP1</th><th className="text-right">Partial + run</th><th className="text-right">Best filter</th><th className="text-right">Verdict</th>
                <th className="text-right">Now</th><th className="text-right">Set to</th></tr></thead>
              <tbody>{scan!.rows.map((r) => {
                const k = `${r.symbol}:${r.mode}`, on = scan!.modes?.[r.symbol]?.[r.mode];
                const cell = (s: S | null, best: boolean) => (
                  <td className={`text-right ${best ? "font-semibold" : "text-slate-400"}`} style={best ? { color: (s?.expectancy ?? 0) >= 0 ? GAIN : LOSS } : undefined}
                    title={s ? `${s.trades} trades · win ${s.win_rate ?? "—"}% · PF ${s.profit_factor ?? "—"} · net ${r2(s.total_r)}` : ""}>{r2(s?.expectancy)}</td>);
                return (
                  <tr key={k} className="border-t border-hud-line/60">
                    <td className="text-slate-200">{META[r.symbol]?.name ?? r.symbol}</td>
                    <td className="text-xs text-slate-400">{r.mode} · {r.days}d</td>
                    <td className="text-right">{(r.best_exit === "partial" ? r.partial : r.full)?.trades}</td>
                    {cell(r.full, r.best_exit === "full")}{cell(r.partial, r.best_exit === "partial")}
                    <td className="text-right text-xs" title={Object.entries(r.variants ?? {}).map(([n, v]) =>
                      `${FILTER_NAMES[n]}: ${r2(v.stats.expectancy)} × ${v.stats.trades}${v.skip_hours.length ? ` (skips ${v.skip_hours.map((h) => `${h}:00`).join(", ")})` : ""}`).join("\n")}>
                      {r.best_filter === "none" ? <span className="text-slate-500">none helps</span> : (
                        <label className="inline-flex items-center gap-1">
                          <input type="checkbox" checked={!!useFilt[k]} onChange={(e) => setUseFilt({ ...useFilt, [k]: e.target.checked })} />
                          <span style={{ color: (r.best.expectancy ?? 0) >= 0 ? GAIN : LOSS }}>{FILTER_NAMES[r.best_filter]} {r2(r.best.expectancy)} × {r.best.trades}</span>
                        </label>
                      )}
                    </td>
                    <td className="text-right"><span className={`rounded border px-2 py-0.5 text-xs ${CHIP[r.verdict]}`}>{r.verdict}</span></td>
                    <td className="text-right text-xs">{on ? "on" : "off"}{scan!.filters?.[k] && !sameFilt(scan!.filters[k], OFF) ? " · filtered" : ""}</td>
                    <td className="text-right">
                      <select className={sel} value={choice[k]} onChange={(e) => setChoice({ ...choice, [k]: e.target.value as Choice })}>
                        <option value="leave">leave</option><option value="on">on</option><option value="off">off</option>
                      </select>
                    </td>
                  </tr>
                );
              })}</tbody>
            </table>
            {(scan!.failed?.length ?? 0) > 0 && <p className="text-xs text-hud-amber">Couldn't test: {scan!.failed!.map((f) => `${f.spec.symbols[0]} ${f.spec.mode} (${f.error ?? "failed"})`).join(" · ")}</p>}
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={partial} onChange={(e) => setPartial(e.target.checked)} />
              Use partial exits (bank {frac}% at TP1, stop to entry, rest to TP2)
              <span className="text-xs text-slate-500">— now {(scan!.exits_now ?? 0) > 0 ? "on" : "off"}; the scan {scan!.recommend_partial ? "recommends on" : "recommends off"}</span>
            </label>
            <div className="flex items-center gap-3">
              <button disabled={!nChanges} onClick={apply}
                className="rounded border border-hud-accent px-3 py-1 text-hud-accent disabled:opacity-40">
                Apply {nChanges} change(s)</button>
              <span className="text-xs text-slate-500">“keep on” = 30+ trades, +0.10R or better, profit factor 1.15+. Judged out-of-sample; past results don't guarantee future ones.</span>
            </div>
          </>
        )}
        {msg && <p className="text-xs text-hud-accent">{msg}</p>}
      </div>
      )}
    </Panel>
  );
}
