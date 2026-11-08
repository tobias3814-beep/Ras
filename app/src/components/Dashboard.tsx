import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactGridLayout, { useContainerWidth, verticalCompactor, type Layout } from "react-grid-layout";
import { LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import type { BoardRow, MacroSeries } from "../ws";
import { css } from "../theme";
import { GAIN, LOSS, LineChart } from "./charts";
import { chartOptions } from "./CryptoChart";
import { Panel } from "./Panels";

const KEY = "jarvis.layout.v1";
export const DEFAULT_LAYOUT: Layout = [
  { i: "tiles", x: 0, y: 0, w: 12, h: 9, minH: 5 },
  { i: "briefing", x: 0, y: 9, w: 4, h: 9, minH: 4 },
  { i: "positions", x: 4, y: 9, w: 4, h: 9, minH: 5 },
  { i: "calendar", x: 8, y: 9, w: 4, h: 9, minH: 5 },
  { i: "macro", x: 0, y: 18, w: 5, h: 12, minH: 8 },
  { i: "news", x: 5, y: 18, w: 7, h: 12, minH: 5 },
  { i: "analysis", x: 0, y: 30, w: 12, h: 10, minH: 6 },
];

function load(): Layout {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null") as Layout | null;
    // keep saved positions, add any panel introduced since
    if (saved) return [...saved.filter((s) => DEFAULT_LAYOUT.some((d) => d.i === s.i)), ...DEFAULT_LAYOUT.filter((d) => !saved.some((s) => s.i === d.i))];
  } catch { /* storage blocked: defaults */ }
  return DEFAULT_LAYOUT;
}

/** Draggable, resizable home dashboard; drag from any empty part of a panel, resize from its corner. Layout is saved. */
export function Dashboard({ panels }: { panels: Record<string, ReactNode> }) {
  const { width, containerRef, mounted } = useContainerWidth();
  const [layout, setLayout] = useState<Layout>(load);
  const save = (l: Layout) => { setLayout(l); try { localStorage.setItem(KEY, JSON.stringify(l)); } catch { /* ignore */ } };
  // a phone has no room to arrange panels: one column, in the default order
  if (mounted && width < 768) {
    return <div ref={containerRef} className="flex min-w-0 flex-col gap-4">{DEFAULT_LAYOUT.map((d) => <div key={d.i} className="min-w-0">{panels[d.i]}</div>)}</div>;
  }
  return (
    <div ref={containerRef}>
      <div className="mb-1 flex justify-end">
        <button onClick={() => save(DEFAULT_LAYOUT)} className="text-xs text-slate-500 hover:text-hud-accent">Reset layout</button>
      </div>
      {mounted && (
        <ReactGridLayout width={width} layout={layout} onLayoutChange={save} compactor={verticalCompactor}
          gridConfig={{ cols: 12, rowHeight: 30, margin: [16, 16] }}
          dragConfig={{ enabled: true, cancel: "button, input, select, textarea, a, canvas, table, label, .no-drag" }}>
          {layout.map((l) => <div key={l.i} className="h-full overflow-auto [&>*]:min-h-full">{panels[l.i]}</div>)}
        </ReactGridLayout>
      )}
    </div>
  );
}

// ---------- macro: DXY (its own axis) + US yields (one % axis, three series) ----------
// yields palette: reference palette steps 1-3 for the theme (validated all-pairs in light and dark)
const YIELD = [["DGS2", "US 2Y", "--chart-y1"], ["DGS10", "US 10Y", "--chart-y2"], ["DFII10", "Real 10Y", "--chart-y3"]] as const;

function YieldsChart({ data }: { data: MacroSeries["yields"] }) {
  const el = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<string>("");
  useEffect(() => {
    const opts = chartOptions();
    const chart = createChart(el.current!, { ...opts, height: 170, timeScale: { ...opts.timeScale, timeVisible: false } });
    const series = YIELD.map(([id, , color]) => {
      const s = chart.addSeries(LineSeries, { color: css(color), lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: id === "DFII10" ? "real" : id.slice(3) + "Y" });
      s.setData((data[id] ?? []).map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
      return s;
    });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((p) => {
      if (!p.time) return setHover("");
      setHover(series.map((s, i) => { const d = p.seriesData.get(s) as { value: number } | undefined; return d ? `${YIELD[i][1]} ${d.value.toFixed(2)}%` : ""; }).filter(Boolean).join(" · "));
    });
    return () => chart.remove();
  }, [data]);
  return (
    <div className="relative">
      <div className="mb-1 flex gap-3 text-xs text-slate-400">
        {YIELD.map(([id, label, color]) => <span key={id} className="flex items-center gap-1"><span className="inline-block h-0.5 w-3" style={{ background: `var(${color})` }} />{label}</span>)}
      </div>
      <div ref={el} style={{ height: 170 }} />
      {hover && <div className="pointer-events-none absolute left-2 top-6 rounded border border-hud-line bg-hud-bg/90 px-2 py-0.5 text-xs tabular-nums text-slate-200">{hover}</div>}
    </div>
  );
}

export function MacroPanel({ m }: { m: MacroSeries }) {
  return (
    <Panel title="DXY · US YIELDS">
      <div className="text-xs text-slate-500">DXY (computed from FX crosses, 1H)</div>
      {m.dxy.length ? <LineChart points={m.dxy.map(([time, value]) => ({ time, value }))} unit="" height={140} />
        : <p className="py-6 text-center text-sm text-slate-500">Needs Capital.com prices</p>}
      <div className="mt-3 text-xs text-slate-500">US Treasury yields (FRED, daily, %)</div>
      {Object.keys(m.yields).length ? <YieldsChart data={m.yields} /> : <p className="text-sm text-slate-500">Loading…</p>}
    </Panel>
  );
}

// ---------- crypto heatmap: 1h % change, diverging blue (up) / orange (down) around a neutral gray ----------
const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
// neutral midpoint + poles from the theme (reference palette neutral gray: light #f0efec, dark #383835)
const mix = (t: number) => {
  const pole = hex(css(t >= 0 ? "--chart-gain" : "--chart-loss")), mid = hex(css("--chart-neutral")), k = Math.min(1, Math.abs(t));
  return `rgb(${mid.map((n, i) => Math.round(n + (pole[i] - n) * k)).join(",")})`;
};

export function HeatmapPanel({ rows }: { rows: BoardRow[] }) {
  const perps = rows.filter((r) => r.market === "perp" && r.change_1h != null);
  const max = Math.max(1, ...perps.map((r) => Math.abs(r.change_1h ?? 0)));
  return (
    <Panel title="CRYPTO HEATMAP · 1H CHANGE">
      {perps.length === 0 ? <p className="text-sm text-slate-500">Waiting for the first scan…</p> : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(72px,1fr))] gap-[2px]">
          {perps.map((r) => (
            <div key={r.key} title={`${r.base} perp · ${r.change_1h! >= 0 ? "+" : ""}${r.change_1h}% 1h · ${r.call}`}
              className="rounded px-1.5 py-2 text-center" style={{ background: mix(r.change_1h! / max) }}>
              <div className={`text-xs font-semibold ${Math.abs(r.change_1h! / max) > 0.55 ? "text-white" : "text-slate-200"}`}>{r.base}</div>
              <div className={`text-[11px] tabular-nums ${Math.abs(r.change_1h! / max) > 0.55 ? "text-white/90" : "text-slate-400"}`}>{r.change_1h! >= 0 ? "+" : ""}{r.change_1h!.toFixed(2)}%</div>
            </div>
          ))}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2 text-[10px] text-slate-500">
        <span>−{max.toFixed(1)}%</span>
        <div className="h-1.5 flex-1 rounded" style={{ background: `linear-gradient(90deg, ${mix(-1)}, ${mix(0)}, ${mix(1)})` }} />
        <span>+{max.toFixed(1)}%</span>
      </div>
    </Panel>
  );
}

// ---------- funding: bars from a zero line, positive = longs pay ----------
export function FundingPanel({ rows }: { rows: BoardRow[] }) {
  const perps = rows.filter((r) => r.market === "perp" && r.funding != null).sort((a, b) => Math.abs(b.funding!) - Math.abs(a.funding!)).slice(0, 15);
  const max = Math.max(0.0001, ...perps.map((r) => Math.abs(r.funding!)));
  return (
    <Panel title="FUNDING RATES · PER 8H">
      {perps.length === 0 ? <p className="text-sm text-slate-500">Waiting for the first scan…</p> : (
        <div className="space-y-1">
          {perps.map((r) => {
            const f = r.funding!, w = (Math.abs(f) / max) * 50;
            return (
              <div key={r.key} className="flex items-center gap-2 text-xs tabular-nums" title={`${r.base}: ${(f * 100).toFixed(4)}% — ${f > 0 ? "longs pay shorts" : "shorts pay longs"}`}>
                <span className="w-14 truncate text-slate-300">{r.base}</span>
                <div className="relative h-2.5 flex-1">
                  <div className="absolute inset-y-0 left-1/2 w-px bg-slate-600" />
                  <div className="absolute inset-y-0" style={{ background: f >= 0 ? GAIN : LOSS, width: `${w}%`, left: f >= 0 ? "50%" : `${50 - w}%`, borderRadius: f >= 0 ? "0 4px 4px 0" : "4px 0 0 4px" }} />
                </div>
                <span className="w-16 text-right text-slate-300">{(f * 100).toFixed(4)}%</span>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
