import { useEffect, useState } from "react";
import { API, type Stats } from "../ws";
import { GAIN, LOSS } from "./charts";
import { META } from "./InstrumentTile";
import { Panel } from "./Panels";

type Row = { mode: string; symbol: string; live: Stats; verdict: string; range: [number, number] | null; alarms?: string[];
  feasibility?: { signals: number; taken: number; skipped: number; signal_r: number | null } | null;
  backtest: { id: number; created: number; expectancy: number | null; trades: number; win_rate: number | null } | null };
type FT = { rows: Row[]; total: Stats; target: number; min_trades: number; verdict: string; since: number;
  range: [number, number] | null; trials: number; notes: string[]; news_diffs: number;
  parity: { at: number; checked: number; matched: number; no_data: number; rows: { id: number; symbol: string; mode: string; result: string }[] } | null;
  ledger: Record<string, { trades: number; pnl: number }>; news_trial: Record<string, Stats> };

const CHIP: Record<string, string> = {
  "on track": "border-hud-green/50 text-hud-green", "below backtest": "border-hud-amber/50 text-hud-amber",
  "can't tell yet": "border-hud-line text-slate-300",
  losing: "border-hud-red/50 text-hud-red", collecting: "border-hud-line text-slate-400",
};
const r2 = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`);

/** Forward test: is the live paper record holding up against the backtest, per instrument and mode? */
export function ForwardTestPanel() {
  const [ft, setFt] = useState<FT>();
  useEffect(() => {
    const load = () => fetch(`${API}/api/forward-test`).then((r) => r.json()).then(setFt).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  if (!ft) return <Panel title="FORWARD TEST"><p className="text-sm text-slate-500">Loading…</p></Panel>;
  const n = ft.total.trades, done = Math.min(1, n / ft.target);
  return (
    <Panel title="FORWARD TEST · LIVE PAPER VS BACKTEST" right={<span className={`rounded border px-2 py-0.5 text-xs ${CHIP[ft.verdict]}`}>{ft.verdict}</span>}>
      <div className="flex flex-col gap-3 text-sm">
        <div>
          <div className="mb-1 flex justify-between text-xs text-slate-400">
            <span>{n} of {ft.target} trades{ft.since ? ` since ${new Date(ft.since).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}` : ""}</span>
            <span>win {ft.total.win_rate ?? "—"}% · avg {r2(ft.total.expectancy)}{ft.range ? ` (noise ${r2(ft.range[0])} to ${r2(ft.range[1])})` : ""} · net {r2(ft.total.total_r)}</span>
          </div>
          <div className="h-2 rounded bg-slate-800/60"><div className="h-full rounded bg-hud-accent" style={{ width: `${done * 100}%` }} /></div>
        </div>
        {ft.rows.length === 0 ? <p className="text-slate-500">No signals since this evaluation began.</p> : (
          <table className="w-full text-left tabular-nums">
            <thead className="text-xs text-slate-500"><tr><th>Instrument</th><th>Mode</th><th className="text-right">Live n</th><th className="text-right" title="Signals the $100 account took / skipped, and the signals' own result at target risk before costs">Taken · skipped · signal avg</th>
              <th className="text-right">Win</th><th className="text-right">Live avg</th><th className="text-right">Backtest avg</th><th className="text-right">Verdict</th></tr></thead>
            <tbody>{ft.rows.map((r) => (
              <tr key={`${r.mode}:${r.symbol}`} className="border-t border-hud-line/60">
                <td className="text-slate-200">{META[r.symbol]?.name ?? r.symbol}</td><td className="text-xs text-slate-400">{r.mode}</td>
                <td className="text-right">{r.live.trades}{r.live.trades < ft.min_trades && <span className="text-xs text-slate-500">/{ft.min_trades}</span>}</td>
                <td className="text-right text-xs text-slate-400">{r.feasibility ? `${r.feasibility.taken} · ${r.feasibility.skipped} · ${r.feasibility.signal_r === null ? "—" : r2(r.feasibility.signal_r)}` : "—"}</td>
                <td className="text-right">{r.live.win_rate ?? "—"}%</td>
                <td className="text-right" style={{ color: (r.live.expectancy ?? 0) >= 0 ? GAIN : LOSS }} title={r.range ? `noise range ${r2(r.range[0])} to ${r2(r.range[1])}` : "too few trades for a range"}>{r2(r.live.expectancy)}</td>
                <td className="text-right text-slate-300" title={r.backtest ? `run #${r.backtest.id}, ${r.backtest.trades} out-of-sample trades` : "no backtest for this instrument + mode"}>
                  {r.backtest ? `${r2(r.backtest.expectancy)} (n=${r.backtest.trades})` : "none"}</td>
                <td className="text-right"><span className={`rounded border px-2 py-0.5 text-xs ${CHIP[r.verdict]}`}>{r.verdict}</span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {ft.rows.flatMap((r) => (r.alarms ?? []).map((a) => <p key={r.mode + r.symbol + a} className="text-xs text-hud-amber">{META[r.symbol]?.name ?? r.symbol} {r.mode}: {a}</p>))}
        <p className="text-xs text-slate-500">
          Live = net results of real paper fills, including spread and overnight financing, since the settings were frozen. Noise range = the average +/- 2 standard errors.
          From {ft.min_trades} trades: <b>losing</b> = the whole range is below zero; <b>below backtest</b> = the whole range is under its backtest; <b>on track</b> = the whole range is above zero;
          otherwise <b>can't tell yet</b>, which is the normal answer for a long time. {ft.trials} variants have been tested so far.
        </p>
        {Object.keys(ft.ledger).length > 0 && <p className="text-xs text-slate-400">By desk: {Object.entries(ft.ledger).map(([m, d]) => `${m} ${d.trades} trades, $${d.pnl.toFixed(2)}`).join(" · ")}</p>}
        <p className="text-xs text-slate-500">News vote on trial, not acted on: it would have changed {ft.news_diffs} decisions.{" "}
          {Object.entries(ft.news_trial).map(([k, s]) => `${k}: ${s.trades} signals, avg ${r2(s.expectancy)}`).join(" · ")}</p>
        {ft.notes.map((n) => <p key={n} className="text-xs text-slate-500">{n}</p>)}
        {ft.parity && <p className="text-xs text-slate-500" title={ft.parity.rows.filter((r) => r.result !== "match").map((r) => `#${r.id} ${r.symbol} ${r.mode}: ${r.result}`).join("\n")}>
          Replay check: {ft.parity.matched} of {ft.parity.checked} recent live signals reproduced by the backtester{ft.parity.no_data ? `, ${ft.parity.no_data} without history` : ""}. Hover for the rest.</p>}
      </div>
    </Panel>
  );
}
