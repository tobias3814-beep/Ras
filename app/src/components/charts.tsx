import { useEffect, useRef, useState } from "react";
import { LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import type { Report, Stats } from "../ws";
import { css } from "../theme";
import { chartOptions } from "./CryptoChart";

// Chart marks come from the theme (reference palette steps: blue/orange, a CVD-safe polarity pair in light and dark).
export const GAIN = "var(--chart-gain)", LOSS = "var(--chart-loss)", REF = "var(--chart-ref)";

const r2 = (x: number | null | undefined, d = 2) => (x == null ? "—" : x.toFixed(d));

export function Kpis({ s }: { s: Stats }) {
  const tiles: [string, string][] = [
    ["Trades", String(s.trades)], ["Win rate", s.win_rate == null ? "—" : `${s.win_rate}%`],
    ["Profit factor", r2(s.profit_factor)], ["Expectancy", s.expectancy == null ? "—" : `${s.expectancy >= 0 ? "+" : ""}${r2(s.expectancy)}R`],
    ["Net", `${s.total_r >= 0 ? "+" : ""}${r2(s.total_r)}R`], ["Max drawdown", `${r2(s.max_dd_r)}R`],
  ];
  return (
    <div className="grid grid-cols-3 gap-2 lg:grid-cols-6">
      {tiles.map(([k, v]) => (
        <div key={k} className="rounded border border-hud-line bg-hud-bg p-3">
          <div className="text-xs text-slate-500">{k}</div>
          <div className="text-2xl tabular-nums text-slate-100">{v}</div>
        </div>
      ))}
    </div>
  );
}

/** Single-series line with a crosshair readout (title names the series, so no legend). */
export function LineChart({ points, unit, height = 260 }: { points: { time: number; value: number }[]; unit: string; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ t: number; v: number } | null>(null);
  useEffect(() => {
    const chart = createChart(el.current!, { ...chartOptions(), height });
    const s = chart.addSeries(LineSeries, { color: css("--chart-gain"), lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    // lightweight-charts needs strictly increasing times
    const seen = new Map<number, number>();
    points.forEach((p) => seen.set(p.time, p.value));
    s.setData([...seen].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as UTCTimestamp, value })));
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      const d = param.seriesData.get(s) as { value: number } | undefined;
      setHover(param.time && d ? { t: param.time as number, v: d.value } : null);
    });
    return () => chart.remove();
  }, [points, height]);
  return (
    <div className="relative">
      <div ref={el} style={{ height }} />
      {hover && (
        <div className="pointer-events-none absolute left-2 top-2 rounded border border-hud-line bg-hud-bg/90 px-2 py-1 text-xs tabular-nums text-slate-200">
          {new Date(hover.t * 1000).toLocaleString("en-GB", { timeZone: "Asia/Bahrain", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
          {" · "}{hover.v.toFixed(2)} {unit}
        </div>
      )}
      {points.length === 0 && <div className="absolute inset-0 grid place-items-center text-sm text-slate-500">No closed trades yet</div>}
    </div>
  );
}

/** Expectancy by hour (Bahrain): bars up = gain, down = loss, from a zero baseline. */
export function HourBars({ byHour }: { byHour: Report["by_hour"] }) {
  const [hover, setHover] = useState<string | null>(null);
  const hours = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));
  const max = Math.max(0.1, ...Object.values(byHour).map((s) => Math.abs(s.expectancy ?? 0)));
  const H = 70;
  return (
    <div>
      <div className="relative flex items-center gap-[2px]" style={{ height: 2 * H + 4 }}>
        <div className="absolute left-0 right-0 border-t border-slate-600" style={{ top: H + 2 }} />
        {hours.map((h) => {
          const s = byHour[h];
          const e = s?.expectancy ?? 0;
          const len = (Math.abs(e) / max) * H;
          return (
            <div key={h} className="relative h-full flex-1" onMouseEnter={() => setHover(h)} onMouseLeave={() => setHover(null)}>
              {s && <div className="absolute left-0 right-0" style={{
                background: e >= 0 ? GAIN : LOSS, height: Math.max(2, len),
                top: e >= 0 ? H + 2 - len : H + 2, borderRadius: e >= 0 ? "4px 4px 0 0" : "0 0 4px 4px",
              }} />}
              {hover === h && s && (
                <div className="absolute bottom-full left-1/2 z-10 mb-1 -translate-x-1/2 whitespace-nowrap rounded border border-hud-line bg-hud-bg px-2 py-1 text-xs tabular-nums text-slate-200">
                  {h}:00 · {s.trades} trades · {e >= 0 ? "+" : ""}{r2(e)}R avg · win {s.win_rate}%
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex text-[10px] text-slate-500">{hours.map((h) => <span key={h} className="flex-1 text-center">{Number(h) % 3 === 0 ? h : ""}</span>)}</div>
    </div>
  );
}

/** Calibration: actual win rate per score bucket (bar) against the bucket's midpoint (gray tick). */
export function Calibration({ rows }: { rows: Report["calibration"] }) {
  if (!rows.length) return <p className="text-sm text-slate-500">No closed trades yet.</p>;
  return (
    <div>
      <div className="mb-2 flex gap-4 text-xs text-slate-400">
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-3 rounded-sm" style={{ background: GAIN }} /> actual win rate</span>
        <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-3" style={{ background: REF }} /> score midpoint</span>
      </div>
      <div className="space-y-1.5">
        {rows.map((c) => (
          <div key={c.bucket} className="flex items-center gap-2 text-xs tabular-nums" title={`${c.trades} trades, win ${c.win_rate}%, ${r2(c.expectancy)}R avg`}>
            <span className="w-12 text-slate-400">{c.bucket}</span>
            <div className="relative h-3 flex-1 rounded-sm bg-slate-800/60">
              <div className="h-full rounded-r" style={{ width: `${c.win_rate ?? 0}%`, background: GAIN }} />
              <div className="absolute top-[-2px] h-[16px] w-0.5" style={{ left: `${c.expected}%`, background: REF }} />
            </div>
            <span className="w-24 text-right text-slate-300">{c.win_rate}% · n={c.trades}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function StatsTable({ title, rows }: { title: string; rows: Record<string, Stats> }) {
  const entries = Object.entries(rows).sort((a, b) => (b[1].expectancy ?? -99) - (a[1].expectancy ?? -99));
  return (
    <div>
      <div className="mb-1 text-xs tracking-widest text-slate-400">{title}</div>
      {entries.length === 0 ? <p className="text-sm text-slate-500">—</p> : (
        <table className="w-full text-left text-sm tabular-nums">
          <thead className="text-xs text-slate-500"><tr><th></th><th className="text-right">n</th><th className="text-right">Win</th><th className="text-right">PF</th><th className="text-right">Avg R</th><th className="text-right">Net R</th></tr></thead>
          <tbody>
            {entries.map(([k, s]) => (
              <tr key={k} className="border-t border-hud-line/60">
                <td className="max-w-40 truncate text-slate-200">{k.replace(/^(spot|perp):/, "").replace(":USDT", "")}</td>
                <td className="text-right">{s.trades}</td><td className="text-right">{s.win_rate == null ? "—" : `${s.win_rate}%`}</td>
                <td className="text-right">{r2(s.profit_factor)}</td>
                <td className="text-right" style={{ color: (s.expectancy ?? 0) >= 0 ? GAIN : LOSS }}>{r2(s.expectancy)}</td>
                <td className="text-right">{r2(s.total_r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function ReportView({ rep }: { rep: Report }) {
  return (
    <div className="flex flex-col gap-4">
      <Kpis s={rep.summary} />
      <div className="rounded-lg border border-hud-line bg-hud-panel/80 p-4">
        <div className="mb-2 text-xs tracking-widest text-slate-400">EQUITY CURVE · CUMULATIVE R</div>
        <LineChart points={rep.equity.map((e) => ({ time: e.time, value: e.r }))} unit="R" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-hud-line bg-hud-panel/80 p-4">
          <div className="mb-2 flex justify-between text-xs tracking-widest text-slate-400">
            <span>EXPECTANCY BY HOUR · BAHRAIN</span>
            <span>best {rep.best_hours.join(", ") || "—"} · worst {rep.worst_hours.join(", ") || "—"}</span>
          </div>
          <HourBars byHour={rep.by_hour} />
        </div>
        <div className="rounded-lg border border-hud-line bg-hud-panel/80 p-4">
          <div className="mb-2 text-xs tracking-widest text-slate-400">CONFIDENCE CALIBRATION</div>
          <Calibration rows={rep.calibration} />
        </div>
      </div>
      <div className="grid gap-4 rounded-lg border border-hud-line bg-hud-panel/80 p-4 lg:grid-cols-2">
        <StatsTable title="BY INSTRUMENT" rows={rep.by_symbol} />
        <StatsTable title="BY STRATEGY" rows={rep.by_strategy} />
        <StatsTable title="BY SESSION" rows={rep.by_session} />
        <StatsTable title="BY MODE" rows={rep.by_mode} />
      </div>
    </div>
  );
}
