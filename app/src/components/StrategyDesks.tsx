import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, LineSeries, LineStyle, createChart, createSeriesMarkers, type LogicalRange, type UTCTimestamp } from "lightweight-charts";
import { API, type Stats } from "../ws";
import { css } from "../theme";
import { candleStyle, chartOptions } from "./CryptoChart";
import { INSTRUMENTS, META } from "./InstrumentTile";
import { Panel } from "./Panels";

type Mark = { time: number; call: "BUY" | "SELL"; status: string; entry: number; stop: number; target: number | null; kind: string; tag: string; live: boolean };
type Ev = { run: number; verdict: string; summary: Stats; this_market?: Stats | null };
type Live = {
  key: string; name: string; group: string; rules: string; symbol: string; tf: string; params: string; evidence: Ev | null; basis: string | null;
  decision: { call: "BUY" | "SELL" | "HOLD"; why: string; entry?: number | null; stop?: number | null; target?: number | null; kind?: string; status?: string };
  candles: { time: number; open: number; high: number; low: number; close: number }[];
  lines: Record<string, [number, number][]>; marks: Mark[]; bar_time: number;
};
type Info = { key: string; name: string; group: string; tf: string };

const TF_NAME: Record<string, string> = { MINUTE_3: "3m", MINUTE_15: "15m", HOUR: "1h", HOUR_4: "4h", DAY: "1D" };
// timeframes each rule can use on live candles (ICT needs intraday sessions; trend following is a daily rule)
const TFS = (key: string) => key === "trend_following" ? ["DAY"] : key === "ict_sweep_fvg" ? ["MINUTE_15", "HOUR"] : ["MINUTE_15", "HOUR", "HOUR_4", "DAY"];
const LINE_VARS = ["--chart-y1", "--chart-y2", "--chart-y3"];
const TONE: Record<string, string> = { BUY: "border-hud-green/60 text-hud-green", SELL: "border-hud-red/60 text-hud-red", HOLD: "border-hud-line text-slate-400" };
const r2 = (x?: number | null) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`);

function useSaved<T>(k: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => { try { const s = localStorage.getItem(k); return s ? (JSON.parse(s) as T) : init; } catch { return init; } });
  return [v, (x: T) => { setV(x); try { localStorage.setItem(k, JSON.stringify(x)); } catch { /* storage blocked */ } }];
}

/** The strategy's own chart: candles, the lines it reads, a marker for every setup, and the live setup's levels.
 *  Redrawn on each refresh (200 bars, cheap); the zoom is kept unless the market or timeframe changes. */
function DeskChart({ d, digits }: { d: Live; digits: number }) {
  const el = useRef<HTMLDivElement>(null);
  const view = useRef<{ key: string; range: LogicalRange | null }>({ key: "", range: null });
  useEffect(() => {
    const c = createChart(el.current!, { ...chartOptions(), height: 260 });
    const s = c.addSeries(CandlestickSeries, { ...candleStyle(), priceFormat: { type: "price", precision: digits, minMove: 10 ** -digits } });
    s.setData(d.candles.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
    Object.entries(d.lines).forEach(([name, pts], i) => {
      const l = c.addSeries(LineSeries, { color: css(LINE_VARS[i % LINE_VARS.length]), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: name });
      l.setData(pts.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    });
    const up = css("--chart-up"), down = css("--chart-down");
    createSeriesMarkers(s, d.marks.map((m) => ({
      time: m.time as UTCTimestamp, position: m.call === "BUY" ? "belowBar" : "aboveBar", shape: m.call === "BUY" ? "arrowUp" : "arrowDown",
      color: m.call === "BUY" ? up : down, text: m.live ? m.call : "",
    })));
    const dc = d.decision;
    if (dc.call !== "HOLD") {
      const lv: [string, number | null | undefined, string, LineStyle][] = [
        [dc.kind === "limit" ? "limit" : "entry", dc.entry, css("--chart-ref"), LineStyle.Dashed],
        ["stop", dc.stop, down, LineStyle.Solid], ["target", dc.target, up, LineStyle.Solid]];
      lv.forEach(([title, price, color, lineStyle]) => price != null && s.createPriceLine({ title, price, color, lineStyle, lineWidth: 1 }));
    }
    const key = `${d.symbol}:${d.tf}`;
    if (view.current.key === key && view.current.range) c.timeScale().setVisibleLogicalRange(view.current.range);
    else c.timeScale().fitContent();
    return () => { view.current = { key, range: c.timeScale().getVisibleLogicalRange() }; c.remove(); };
  }, [d, digits]);
  return <div ref={el} style={{ height: 260 }} />;
}

/** One strategy, one market: it watches the live chart and makes its own call. */
function Desk({ info }: { info: Info }) {
  const [sym, setSym] = useSaved(`ras.desk.${info.key}.sym`, "XAU_USD");
  const [tf, setTf] = useSaved(`ras.desk.${info.key}.tf`, TFS(info.key).includes(info.tf) ? info.tf : TFS(info.key)[0]);
  const [d, setD] = useState<Live>();
  const [err, setErr] = useState("");
  useEffect(() => {
    let live = true;
    const load = () => fetch(`${API}/api/strategy-live/${info.key}?symbol=${sym}&tf=${tf}`)
      .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail ?? r.statusText); return j as Live; })
      .then((j) => { if (live) { setD(j); setErr(""); } }).catch((e) => live && setErr(e.message));
    load();
    const t = setInterval(load, 30_000);
    return () => { live = false; clearInterval(t); };
  }, [info.key, sym, tf]);
  const digits = META[sym]?.digits ?? 2;
  const f = (x?: number | null) => (x == null ? "—" : x.toFixed(digits));
  const sel = "rounded border border-hud-line bg-hud-bg px-1.5 py-0.5 text-xs text-slate-200";
  const dc = d?.decision;
  const mine = d?.evidence?.this_market;
  const failed = d && (!d.evidence || (mine?.expectancy ?? d.evidence.summary.expectancy ?? 0) <= 0);

  return (
    <Panel title={`${info.name.toUpperCase()} · ${info.group}`} right={
      <span className="flex gap-1">
        <select className={sel} value={sym} onChange={(e) => setSym(e.target.value)}>
          {INSTRUMENTS.map((s) => <option key={s} value={s}>{META[s].name}</option>)}
        </select>
        <select className={sel} value={tf} onChange={(e) => setTf(e.target.value)}>
          {TFS(info.key).map((t) => <option key={t} value={t}>{TF_NAME[t]}</option>)}
        </select>
      </span>}>
      {err ? <p className="py-8 text-center text-sm text-slate-500">{err === "no candles yet" ? "Waiting for live candles…" : err}</p>
        : !d || !dc ? <p className="py-8 text-center text-sm text-slate-500">Loading…</p> : (
        <div className="flex flex-col gap-2">
          <DeskChart d={d} digits={digits} />
          <div className={`flex flex-wrap items-center gap-3 rounded border px-3 py-2 ${TONE[dc.call]}`}>
            <span className="text-lg font-semibold tracking-widest">{dc.call}</span>
            {dc.call !== "HOLD" && (
              <span className="text-xs tabular-nums text-slate-300">
                {dc.entry != null && <>{dc.kind === "limit" ? "limit" : "entry"} {f(dc.entry)} · </>}
                {dc.stop != null && <>stop <span className="text-hud-red">{f(dc.stop)}</span> · </>}
                target {dc.target == null ? (info.key === "trend_following" ? "until the trend flips" : "exit rule") : <span className="text-hud-green">{f(dc.target)}</span>}
              </span>
            )}
            <span className="w-full text-xs text-slate-400">{dc.why}</span>
          </div>
          <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-500">
            <span title={d.params}>Tested on {META[sym].name} {TF_NAME[tf]}: {mine ? `${r2(mine.expectancy)} over ${mine.trades} trades` : "not run yet"}
              {d.evidence && <> · overall {r2(d.evidence.summary.expectancy)} ({d.evidence.verdict}, run #{d.evidence.run})</>}</span>
            <span>last candle {new Date(d.bar_time * 1000).toLocaleString("en-GB", { timeZone: "Asia/Bahrain", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
          </div>
          {failed && dc.call !== "HOLD" && (
            <p className="text-xs text-hud-amber">This rule hasn't shown a profit in testing on this market and timeframe — treat its call as information, not a trade.</p>
          )}
        </div>
      )}
    </Panel>
  );
}

/** A live desk per strategy: each picks its own market and timeframe and makes its own BUY / SELL / HOLD. */
export function StrategyDesks() {
  const [list, setList] = useState<Info[]>([]);
  useEffect(() => { fetch(`${API}/api/strategies`).then((r) => r.json()).then((c: { strategies: Info[] }) => setList(c.strategies)).catch(() => {}); }, []);
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm tracking-[0.3em] text-hud-accent">LIVE DESKS · EACH STRATEGY ON ITS OWN</h3>
      <p className="text-xs text-slate-500">
        Each desk runs one strategy on the live Capital.com candles of the market you pick and calls BUY or SELL while it
        has a live setup, HOLD otherwise. Desks only show their calls — they don't open trades. Arrows mark every setup the
        rule made on the chart; lines show what it reads; entry, stop and target are drawn for a live setup.
      </p>
      <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">{list.map((s) => <Desk key={s.key} info={s} />)}</div>
    </div>
  );
}
