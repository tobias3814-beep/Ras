import { useEffect, useMemo, useState } from "react";
import { API, type Stats, type StrategyEvent } from "../ws";
import { GAIN, Kpis, LineChart, LOSS, StatsTable } from "./charts";
import { INSTRUMENTS, META } from "./InstrumentTile";
import { Panel } from "./Panels";
import { StrategyDesks } from "./StrategyDesks";

type Info = { key: string; name: string; group: string; tf: string; rules: string; grid: Record<string, unknown[]> };
type Catalog = { strategies: Info[]; max_days: Record<string, number>; notes: string[] };
type Fold = { fold: number; from: number | null; to: number | null; params: string; in_sample: Stats; out_of_sample: Stats };
type Res = {
  key: string; name: string; group: string; rules: string; summary: Stats; verdict: string; positive_folds: number;
  checks?: { trials: number; deflated_sharpe: number | null; overfit_probability: number | null; reality_check_p: number | null; passed: boolean };
  fresh?: Stats | null; drawdown_range?: { typical: number; bad: number } | null;
  account?: { start: number; final: number; return_pct: number; max_dd_pct: number; taken: number; too_big: number; no_margin: number; limits: number } | null;
  cost_stress?: { x2: number | null; x3: number | null } | null; random_entries?: { expectancy: number; top: number; beats: boolean } | null;
  folds: Fold[]; by_period: Record<string, Stats>; period: "month" | "year"; by_symbol: Record<string, Stats>;
  equity: { time: number; r: number }[]; combos: (Stats & { params: string })[]; combos_positive: number; avg_days_held: number | null;
};
type Spec = { strategies: string[]; symbols: string[]; tf: string; days: number; folds: number; funding_pct: number };
type Result = { spec: Spec; range: [number, number]; bars: Record<string, number>; strategies: Res[]; notes: string[]; error?: string };
type Run = { id: number; created: number; status: string; spec: Spec; result?: Result | null };

const TFS: [string, string, number][] = [["MINUTE_15", "15 minutes", 180], ["HOUR", "1 hour", 730], ["HOUR_4", "4 hours", 1500], ["DAY", "Daily", 9000]];
const TF_NAME: Record<string, string> = { MINUTE_3: "3 minutes", ...Object.fromEntries(TFS.map(([k, n]) => [k, n])) };
const GROUPS = ["Indicators", "Trend following", "ICT", "SMC", "ICC"];
const CHIP: Record<string, string> = {
  "survives checks": "border-hud-green/50 text-hud-green", "passes screen": "border-hud-accent/50 text-hud-accent",
  promising: "border-hud-accent/50 text-hud-accent", marginal: "border-hud-amber/50 text-hud-amber",
  "doesn't work": "border-hud-red/50 text-hud-red", "too few trades": "border-hud-line text-slate-400",
};
const r2 = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}`);
const tone = (x: number | null | undefined) => ({ color: (x ?? 0) >= 0 ? GAIN : LOSS });
const date = (ms: number | null) => (ms == null ? "—" : new Date(ms).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" }));

export function StrategiesPage({ sbt }: { sbt?: StrategyEvent }) {
  const [cat, setCat] = useState<Catalog>();
  const [picked, setPicked] = useState<string[]>([]);
  const [fx, setFx] = useState<string[]>(["XAU_USD", "EUR_USD"]);
  const [tf, setTf] = useState("HOUR");
  const [days, setDays] = useState(730);
  const [folds, setFolds] = useState(4);
  const [funding, setFunding] = useState(2);
  const [runs, setRuns] = useState<Run[]>([]);
  const [shown, setShown] = useState<Run>();
  const [sel, setSel] = useState<string>();
  const [err, setErr] = useState("");

  const loadRuns = () => fetch(`${API}/api/strategies/runs`).then((r) => r.json()).then(setRuns).catch(() => {});
  const open = (id: number) => fetch(`${API}/api/strategies/run/${id}`).then((r) => r.json()).then((r: Run) => { setShown(r); setSel(r.result?.strategies?.[0]?.key); });
  useEffect(() => {
    fetch(`${API}/api/strategies`).then((r) => r.json()).then((c: Catalog) => { setCat(c); setPicked(c.strategies.map((s) => s.key)); }).catch(() => {});
    loadRuns().then(() => {});
  }, []);
  useEffect(() => { if (!shown && runs.length) open(runs.find((r) => r.status === "done")?.id ?? runs[0].id); }, [runs.length]);
  // id 0 = the edge scan finishing (its runs are listed, not opened)
  useEffect(() => { if (sbt && sbt.status !== "running") { loadRuns(); if (sbt.id) open(sbt.id); } }, [sbt?.status, sbt?.id]);

  const start = () => {
    setErr("");
    fetch(`${API}/api/strategies/backtest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategies: picked, symbols: fx, tf, days, folds, funding_pct: funding }),
    }).then(async (r) => { if (!r.ok) setErr((await r.json()).detail?.toString() ?? "failed"); else loadRuns(); });
  };
  const running = sbt?.status === "running";
  const input = "rounded border border-hud-line bg-hud-bg px-2 py-1 text-sm text-slate-200";
  const res = shown?.result;
  const cur = res?.strategies?.find((s) => s.key === sel);
  const maxDays = cat?.max_days[tf] ?? 365;
  const ictOff = (tf === "HOUR_4" || tf === "DAY") && picked.includes("ict_sweep_fvg");
  const byGroup = useMemo(() => GROUPS.map((g) => [g, (cat?.strategies ?? []).filter((s) => s.group === g)] as const), [cat]);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg tracking-[0.3em] text-hud-accent">STRATEGIES</h2>
      <StrategyDesks />
      <h3 className="mt-2 text-sm tracking-[0.3em] text-hud-accent">BACKTESTS · WALK-FORWARD</h3>
      <div className="grid gap-4 xl:grid-cols-[1fr_2.2fr]">
        <Panel title="NEW RUN">
          <div className="flex flex-col gap-3 text-sm">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>Strategies</span>
              <span className="flex gap-2">
                <button className="hover:text-hud-accent" onClick={() => setPicked(cat?.strategies.map((s) => s.key) ?? [])}>all</button>
                <button className="hover:text-hud-accent" onClick={() => setPicked([])}>none</button>
              </span>
            </div>
            {byGroup.map(([g, list]) => list.length > 0 && (
              <div key={g}>
                <div className="mb-1 text-xs tracking-widest text-slate-500">{g.toUpperCase()}</div>
                {list.map((s) => (
                  <label key={s.key} title={s.rules} className="flex items-start gap-2 py-0.5">
                    <input type="checkbox" className="mt-1" checked={picked.includes(s.key)}
                      onChange={(e) => setPicked(e.target.checked ? [...picked, s.key] : picked.filter((k) => k !== s.key))} />
                    <span className="text-slate-200">{s.name} <span className="text-xs text-slate-500">· best on {TF_NAME[s.tf]}</span></span>
                  </label>
                ))}
              </div>
            ))}
            <div className="flex flex-wrap gap-2 border-t border-hud-line pt-2">{INSTRUMENTS.map((s) => (
              <label key={s} className="flex items-center gap-1"><input type="checkbox" checked={fx.includes(s)}
                onChange={(e) => setFx(e.target.checked ? [...fx, s] : fx.filter((x) => x !== s))} />{META[s].name}</label>
            ))}</div>
            <label className="flex items-center gap-2">Timeframe
              <select className={input} value={tf} onChange={(e) => { const t = e.target.value; setTf(t); setDays(TFS.find((x) => x[0] === t)![2]); }}>
                {TFS.map(([k, n]) => <option key={k} value={k}>{n}</option>)}
              </select>
            </label>
            <div className="flex flex-wrap gap-4">
              <label className="flex items-center gap-2">Days <input type="number" min={30} max={maxDays} className={`${input} w-24`} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
              <label className="flex items-center gap-2">Folds <input type="number" min={2} max={8} className={`${input} w-16`} value={folds} onChange={(e) => setFolds(Number(e.target.value))} /></label>
              <label className="flex items-center gap-2" title="Overnight funding charged on the position for the time it's held (CFDs). Check your instrument's fee in Capital.com.">
                Funding %/yr <input type="number" min={0} max={20} step={0.5} className={`${input} w-16`} value={funding} onChange={(e) => setFunding(Number(e.target.value))} /></label>
            </div>
            <p className="text-xs text-slate-500">{TF_NAME[tf]} history goes back up to {maxDays} days here. Longer tests download more candles first.</p>
            {ictOff && <p className="text-xs text-hud-amber">ICT kill zones need 15-minute or 1-hour bars; on {TF_NAME[tf]} it takes no trades.</p>}
            <button disabled={running || !picked.length || !fx.length} onClick={start}
              className="self-start rounded border border-hud-accent px-4 py-1 text-hud-accent disabled:opacity-40">Run {picked.length} strateg{picked.length === 1 ? "y" : "ies"}</button>
            {err && <p className="text-hud-red">{err}</p>}
            {sbt && (sbt.status === "running" || sbt.status === "failed") && (
              <div>
                <div className="h-1.5 rounded bg-slate-800"><div className="h-full rounded bg-hud-accent" style={{ width: `${sbt.progress * 100}%` }} /></div>
                <p className={`mt-1 text-xs ${sbt.status === "failed" ? "text-hud-red" : "text-slate-400"}`}>{sbt.msg}</p>
              </div>
            )}
          </div>
          <div className="mt-4 border-t border-hud-line pt-2 text-xs">
            <div className="mb-1 tracking-widest text-slate-400">PAST RUNS</div>
            {runs.map((r) => (
              <div key={r.id} onClick={() => open(r.id)} className={`cursor-pointer truncate py-0.5 hover:text-hud-accent ${shown?.id === r.id ? "text-hud-accent" : "text-slate-300"}`}>
                #{r.id} {TF_NAME[r.spec.tf]} · {r.spec.days}d · {r.spec.strategies.length} strategies · {r.spec.symbols.map((s) => META[s]?.name ?? s).join(", ")} · {r.status}
              </div>
            ))}
            {!runs.length && <p className="text-slate-500">None yet.</p>}
          </div>
        </Panel>

        <div className="flex flex-col gap-4">
          {!res ? <p className="text-sm text-slate-500">Pick strategies and markets, then run. Results are walk-forward: settings are chosen on earlier data and judged on later data.</p>
            : res.error ? <p className="text-hud-red">Failed: {res.error}</p> : (
            <>
              <Panel title={`LEADERBOARD · OUT-OF-SAMPLE · ${(TF_NAME[res.spec.tf] ?? res.spec.tf).toUpperCase()} · ${date(res.range[0] * 1000)} – ${date(res.range[1] * 1000)}`}>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm tabular-nums">
                    <thead className="text-xs text-slate-500"><tr>
                      <th>Strategy</th><th className="text-right">Trades</th><th className="text-right">Win</th><th className="text-right">Avg R</th>
                      <th className="text-right">PF</th><th className="text-right">Net R</th><th className="text-right">Max DD</th>
                      <th className="text-right" title="Walk-forward folds with a positive average R">Folds +</th>
                      <th className="text-right" title="Parameter settings profitable over the whole period (in-sample)">Settings +</th>
                      <th className="text-right">Avg hold</th><th className="pl-3">Verdict</th>
                    </tr></thead>
                    <tbody>{res.strategies.map((s) => (
                      <tr key={s.key} onClick={() => setSel(s.key)} className={`cursor-pointer border-t border-hud-line/60 hover:bg-hud-accent/5 ${sel === s.key ? "bg-hud-accent/10" : ""}`}>
                        <td className="py-1"><span className="text-slate-100">{s.name}</span> <span className="text-xs text-slate-500">{s.group}</span></td>
                        <td className="text-right">{s.summary.trades}</td>
                        <td className="text-right">{s.summary.win_rate == null ? "—" : `${s.summary.win_rate}%`}</td>
                        <td className="text-right" style={tone(s.summary.expectancy)}>{r2(s.summary.expectancy)}</td>
                        <td className="text-right">{s.summary.profit_factor?.toFixed(2) ?? "—"}</td>
                        <td className="text-right" style={tone(s.summary.total_r)}>{r2(s.summary.total_r)}</td>
                        <td className="text-right">{s.summary.max_dd_r.toFixed(1)}</td>
                        <td className="text-right">{s.positive_folds}/{s.folds.length}</td>
                        <td className="text-right">{s.combos_positive}/{s.combos.length}</td>
                        <td className="text-right text-xs text-slate-400">{s.avg_days_held == null ? "—" : s.avg_days_held >= 1 ? `${s.avg_days_held.toFixed(1)}d` : `${Math.round(s.avg_days_held * 24)}h`}</td>
                        <td className="pl-3"><span title={s.checks ? `Luck checks against ${s.checks.trials} variants tried: deflated Sharpe ${s.checks.deflated_sharpe ?? "—"} (needs 0.95+), overfitting ${s.checks.overfit_probability ?? "—"} (needs 0.10 or less), reality check ${s.checks.reality_check_p ?? "—"} (needs 0.05 or less)${s.fresh ? `. Fresh trades: ${s.fresh.trades}, avg ${s.fresh.expectancy ?? "—"}R` : ""}${s.drawdown_range ? `. Losing run: typically ${s.drawdown_range.typical}R, bad case ${s.drawdown_range.bad}R` : ""}${s.cost_stress ? `. With 2x spread ${s.cost_stress.x2 ?? "—"}R, 3x ${s.cost_stress.x3 ?? "—"}R` : ""}${s.random_entries ? `. Random entries ${s.random_entries.expectancy}R: ${s.random_entries.beats ? "beats them" : "does not beat them"}` : ""}${s.account ? `. On this account: $${s.account.start} to $${s.account.final}, worst drop ${s.account.max_dd_pct}%, ${s.account.taken} taken, ${s.account.too_big + s.account.no_margin + s.account.limits} skipped` : ""}` : "From before the luck checks existed"} className={`rounded border px-1.5 py-0.5 text-xs ${CHIP[s.verdict] ?? ""}`}>{s.verdict}</span></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  {res.spec.symbols.map((s) => META[s]?.name ?? s).join(", ")} · {res.spec.folds} folds · funding {res.spec.funding_pct}%/yr ·
                  "passes screen" = 30+ trades, ≥ +0.10R, PF ≥ 1.15 and half the folds positive, which rules with no edge often reach. "survives checks" also passes three luck checks (hover a verdict). Click a row for details.
                </p>
              </Panel>

              {cur && (
                <>
                  <Panel title={`${cur.name.toUpperCase()} · ${cur.group}`}>
                    <p className="mb-3 text-sm text-slate-300">{cur.rules}</p>
                    <Kpis s={cur.summary} />
                    <div className="mt-3"><LineChart points={cur.equity.map((e) => ({ time: e.time, value: e.r }))} unit="R" height={220} /></div>
                  </Panel>
                  <div className="grid gap-4 2xl:grid-cols-2">
                    <Panel title="WALK-FORWARD FOLDS · SETTINGS PICKED ON EARLIER DATA">
                      <table className="w-full text-left text-sm tabular-nums">
                        <thead className="text-xs text-slate-500"><tr><th>Fold</th><th>Tested on</th><th>Settings picked</th><th className="text-right">Train n / R</th><th className="text-right">Test n / R</th><th className="text-right">PF</th></tr></thead>
                        <tbody>{cur.folds.map((f) => (
                          <tr key={f.fold} className="border-t border-hud-line/60">
                            <td>{f.fold}</td><td className="text-xs text-slate-400">{date(f.from)} – {date(f.to)}</td>
                            <td className="max-w-56 truncate text-xs text-slate-300" title={f.params}>{f.params || "—"}</td>
                            <td className="text-right">{f.in_sample.trades} / {r2(f.in_sample.expectancy)}</td>
                            <td className="text-right" style={tone(f.out_of_sample.expectancy)}>{f.out_of_sample.trades} / {r2(f.out_of_sample.expectancy)}</td>
                            <td className="text-right">{f.out_of_sample.profit_factor?.toFixed(2) ?? "—"}</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </Panel>
                    <Panel title={`OUT-OF-SAMPLE BY ${cur.period.toUpperCase()}`}>
                      <div className="max-h-80 overflow-y-auto">
                        <table className="w-full text-left text-sm tabular-nums">
                          <thead className="text-xs text-slate-500"><tr><th>{cur.period === "month" ? "Month" : "Year"}</th><th className="text-right">Trades</th><th className="text-right">Win</th><th className="text-right">Avg R</th><th className="text-right">Net R</th></tr></thead>
                          <tbody>{Object.entries(cur.by_period).map(([k, s]) => (
                            <tr key={k} className="border-t border-hud-line/60">
                              <td>{k}</td><td className="text-right">{s.trades}</td><td className="text-right">{s.win_rate == null ? "—" : `${s.win_rate}%`}</td>
                              <td className="text-right" style={tone(s.expectancy)}>{r2(s.expectancy)}</td>
                              <td className="text-right" style={tone(s.total_r)}>{r2(s.total_r)}</td>
                            </tr>
                          ))}</tbody>
                        </table>
                      </div>
                      <p className="mt-2 text-xs text-slate-500">
                        {Object.values(cur.by_period).filter((s) => (s.total_r ?? 0) > 0).length} of {Object.keys(cur.by_period).length} {cur.period}s positive
                      </p>
                    </Panel>
                    <Panel title="BY MARKET · OUT-OF-SAMPLE"><StatsTable title="" rows={cur.by_symbol} /></Panel>
                    <Panel title="EVERY SETTING · WHOLE PERIOD (IN-SAMPLE, FOR COMPARISON)">
                      <div className="max-h-80 overflow-y-auto">
                        <table className="w-full text-left text-sm tabular-nums">
                          <thead className="text-xs text-slate-500"><tr><th>Settings</th><th className="text-right">n</th><th className="text-right">Avg R</th><th className="text-right">PF</th></tr></thead>
                          <tbody>{[...cur.combos].sort((a, b) => (b.expectancy ?? -9) - (a.expectancy ?? -9)).map((c) => (
                            <tr key={c.params} className="border-t border-hud-line/60">
                              <td className="max-w-72 truncate text-xs text-slate-300" title={c.params}>{c.params || "—"}</td>
                              <td className="text-right">{c.trades}</td>
                              <td className="text-right" style={tone(c.expectancy)}>{r2(c.expectancy)}</td>
                              <td className="text-right">{c.profit_factor?.toFixed(2) ?? "—"}</td>
                            </tr>
                          ))}</tbody>
                        </table>
                      </div>
                      <p className="mt-2 text-xs text-slate-500">If only a few settings work, a good headline result is more likely luck.</p>
                    </Panel>
                  </div>
                </>
              )}
              <Panel title="ASSUMPTIONS"><ul className="list-disc pl-5 text-xs text-slate-400">{res.notes.map((n) => <li key={n}>{n}</li>)}</ul></Panel>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
