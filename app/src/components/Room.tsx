import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, LineStyle, createChart, type IPriceLine, type ISeriesApi } from "lightweight-charts";
import { API, type CalEvent, type Candle, type Decision, type Modes, type Price, type Signal } from "../ws";
import { CalendarPanel } from "./Panels";
import { candleStyle, chartOptions, toBar } from "./CryptoChart";
import { css } from "../theme";
import { META, InstrumentTile } from "./InstrumentTile";
import { SignalCard, fmtTime } from "./SignalCard";
import { AnalysisPanel, TradingViewChart } from "./Analysis";

type Props = {
  symbol: string; price?: Price; calls: Record<string, Decision>; signals: Record<string, Signal>;
  modes: Modes; calendar: CalEvent[]; onBack: () => void;
};

// calendar currencies per room (mirrors config.yaml ccy)
const CCY: Record<string, string[]> = {
  GBP_USD: ["GBP", "USD"], EUR_USD: ["EUR", "USD"], USD_JPY: ["USD", "JPY"], XAU_USD: ["USD"], XAG_USD: ["USD"], WTI_USD: ["USD"],
};

export const fetchHistory = (symbol: string) =>
  fetch(`${API}/api/signals?symbol=${encodeURIComponent(symbol)}`).then((r) => r.json() as Promise<Signal[]>);

function Toggle({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`rounded border px-3 py-1 text-xs ${on ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>
      {label}: {on ? "ON" : "OFF"}
    </button>
  );
}

export function Room({ symbol, price, calls, signals, modes, calendar, onBack }: Props) {
  const m = META[symbol];
  const mode = modes[symbol] ?? { intraday: true, scalp: true };
  const [tf, setTf] = useState<"MINUTE_15" | "MINUTE">("MINUTE_15");
  const [history, setHistory] = useState<Signal[]>([]);
  const [chart, setChart] = useState<"ras" | "tv">(() => { try { return localStorage.getItem("ras.chart") === "tv" ? "tv" : "ras"; } catch { return "ras"; } });
  const [atf, setAtf] = useState("MINUTE_15");
  const pickChart = (c: "ras" | "tv") => { setChart(c); try { localStorage.setItem("ras.chart", c); } catch { /* storage blocked */ } };
  const intra = calls[`${symbol}:intraday`], scalp = calls[`${symbol}:scalp`];
  const openIntra = signals[`${symbol}:intraday`], openScalp = signals[`${symbol}:scalp`];
  const trend = calls[`${symbol}:trend`], openTrend = signals[`${symbol}:trend`];
  useEffect(() => { fetchHistory(symbol).then(setHistory).catch(() => setHistory([])); },
    [symbol, openIntra?.id, openIntra?.status, openScalp?.id, openScalp?.status]);
  const setMode = (k: "intraday" | "scalp" | "trend") =>
    fetch(`${API}/api/modes/${symbol}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [k]: !mode[k] }) });
  const lineSignal = tf === "MINUTE" ? openScalp : openIntra;
  // a just-issued signal IS the current call: show the tracked version (countdown, status)
  const same = (sig: Signal | undefined, call: Decision | undefined) => sig?.status === "active" && sig.created === call?.created;
  const scalpCard = same(openScalp, scalp) ? openScalp : scalp, intraCard = same(openIntra, intra) ? openIntra : intra;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-4">
        <button onClick={onBack} className="rounded border border-hud-line px-3 py-1 text-sm text-hud-accent hover:border-hud-accent">← Station</button>
        <h2 className="text-lg tracking-[0.3em] text-hud-accent">{m.name} ROOM</h2>
        <Toggle on={mode.intraday} label="INTRADAY 4H·1H·15M" onClick={() => setMode("intraday")} />
        <Toggle on={mode.scalp} label="SCALP 15M·5M·1M" onClick={() => setMode("scalp")} />
        <Toggle on={!!mode.trend} label="TREND · DAILY" onClick={() => setMode("trend")} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2 text-xs">
            {(["ras", "tv"] as const).map((c) => (
              <button key={c} onClick={() => pickChart(c)} className={`rounded border px-2 py-0.5 ${chart === c ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>
                {c === "ras" ? "Ras chart" : "TradingView"}
              </button>
            ))}
            {chart === "ras" && <span className="mx-1 border-l border-hud-line" />}
            {chart === "ras" && (["MINUTE_15", "MINUTE"] as const).map((t) => (
              <button key={t} onClick={() => setTf(t)} className={`rounded border px-2 py-0.5 ${tf === t ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>
                {t === "MINUTE" ? "1M (scalp)" : "15M (intraday)"}
              </button>
            ))}
          </div>
          {chart === "tv" ? <TradingViewChart symbol={symbol} tf={atf} /> : (
            <div className="h-[420px] rounded-lg border border-hud-line bg-hud-panel/80 p-2">
              <SignalChart symbol={symbol} tf={tf} signal={lineSignal?.status === "active" ? lineSignal : undefined} digits={m.digits} />
            </div>
          )}
          <AnalysisPanel symbol={symbol} digits={m.digits} tf={atf} setTf={setAtf} />
          <History rows={history} digits={m.digits} />
          <CalendarPanel events={calendar.filter((e) => CCY[symbol].includes(e.country) || (symbol === "WTI_USD" && /crude oil/i.test(e.title)))} />
        </div>
        <div className="flex flex-col gap-4">
          <InstrumentTile symbol={symbol} price={price} call={intra} scalp={scalp} trend={trend} mode={mode} onOpen={() => {}} />
          {openTrend?.status === "active" ? <SignalCard s={openTrend} digits={m.digits} title={`TREND POSITION #${openTrend.id}`} />
            : mode.trend && (trend ? <SignalCard s={trend} digits={m.digits} title="TREND CALL" /> : <p className="text-sm text-slate-500">Trend: waiting for daily candles…</p>)}
          {mode.scalp && (scalpCard ? <SignalCard s={scalpCard} digits={m.digits} pip={m.pip} title="SCALP CALL" /> : <p className="text-sm text-slate-500">Scalp: waiting for 1M candles…</p>)}
          {openScalp?.status === "active" && !same(openScalp, scalp) &&
            <SignalCard s={openScalp} digits={m.digits} pip={m.pip} title={`OPEN SCALP #${openScalp.id}`} />}
          {mode.intraday && (intraCard ? <SignalCard s={intraCard} digits={m.digits} title="INTRADAY CALL" detailed /> : <p className="text-sm text-slate-500">Intraday: waiting for candles…</p>)}
          {openIntra?.status === "active" && !same(openIntra, intra) && <SignalCard s={openIntra} digits={m.digits} title={`OPEN SIGNAL #${openIntra.id}`} />}
        </div>
      </div>
    </div>
  );
}

export function SignalChart({ symbol, tf, signal, digits }: { symbol: string; tf: string; signal?: Signal; digits: number }) {
  const el = useRef<HTMLDivElement>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [msg, setMsg] = useState("loading candles…");

  useEffect(() => {
    const chart = createChart(el.current!, chartOptions());
    series.current = chart.addSeries(CandlestickSeries, { ...candleStyle(), priceFormat: { type: "price", precision: digits, minMove: 10 ** -digits } });
    const load = () => fetch(`${API}/api/candles?symbol=${encodeURIComponent(symbol)}&tf=${tf}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: Candle[]) => { series.current?.setData(c.map(toBar)); setMsg(""); })
      .catch(() => setMsg("no candles yet"));
    load();
    const t = setInterval(load, 30_000);
    return () => { clearInterval(t); chart.remove(); series.current = null; };
  }, [symbol, tf, digits]);

  useEffect(() => {
    const s = series.current;
    if (!s || !signal?.levels) return;
    const lv = signal.levels;
    const rows: [string, number, string, LineStyle][] = [
      ["ENTRY", lv.entry, css("--color-hud-accent"), LineStyle.Solid], ["SL", lv.stop, css("--color-hud-red"), LineStyle.Solid],
      ["TP1", lv.tp1, css("--color-hud-green"), LineStyle.Dashed], ["TP2", lv.tp2, css("--color-hud-green"), LineStyle.Dotted],
    ];
    if (signal.mode === "intraday" && signal.invalidation != null) rows.push(["INVALID", signal.invalidation, css("--color-hud-amber"), LineStyle.Dashed]);
    const lines: IPriceLine[] = rows.map(([title, price, color, lineStyle]) => s.createPriceLine({ title, price, color, lineStyle, lineWidth: 1 }));
    return () => lines.forEach((l) => s.removePriceLine(l));
  }, [signal]);

  return (
    <div className="relative h-full">
      <div ref={el} className="h-full" />
      {msg && <div className="absolute inset-0 grid place-items-center text-sm text-slate-500">{msg}</div>}
    </div>
  );
}

export function History({ rows, digits }: { rows: Signal[]; digits: number }) {
  const f = (x?: number) => (x == null ? "—" : x.toFixed(digits));
  return (
    <div className="rounded-lg border border-hud-line bg-hud-panel/80 p-4">
      <div className="mb-2 text-xs tracking-widest text-slate-400">SIGNAL HISTORY</div>
      {rows.length === 0 ? <p className="text-sm text-slate-500">No signals yet.</p> : (
        <table className="w-full text-left text-sm tabular-nums">
          <thead className="text-xs text-slate-500"><tr><th>Time</th><th>Mode</th><th>Call</th><th>Conf</th><th>Entry</th><th>SL</th><th>TP1</th><th>Status</th><th>R</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-hud-line/60">
                <td>{fmtTime(r.created)}</td><td className="text-xs text-slate-400">{r.mode}</td>
                <td className={r.call === "BUY" ? "text-hud-green" : "text-hud-red"}>{r.call}</td>
                <td>{r.confidence}</td><td>{f(r.levels?.entry)}</td><td>{f(r.levels?.stop)}</td><td>{f(r.levels?.tp1)}</td>
                <td>{r.status.replace("_", " ")}</td><td>{r.result_r ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
