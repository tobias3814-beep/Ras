import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import { css } from "../theme";
import { onEvent, type Candle } from "../ws";

export const toBar = (c: Candle) => ({ ...c, time: c.time as UTCTimestamp });
// lightweight-charts renders UTC; show everything in the user's time zone instead
const bh = (t: unknown, o: Intl.DateTimeFormatOptions) =>
  new Date((t as number) * 1000).toLocaleString("en-GB", { timeZone: "Asia/Bahrain", ...o });
/** Chart chrome in the active theme's colours (read when a chart is created). */
export const chartOptions = () => ({
  autoSize: true,
  layout: { background: { color: "transparent" }, textColor: css("--chart-text") },
  grid: { vertLines: { color: css("--chart-grid") }, horzLines: { color: css("--chart-grid") } },
  timeScale: {
    timeVisible: true, borderColor: css("--chart-border"),
    // tick type < 3 = year / month / day boundary
    tickMarkFormatter: (t: unknown, type: number) => bh(t, type < 3 ? { day: "2-digit", month: "short" } : { hour: "2-digit", minute: "2-digit" }),
  },
  localization: { timeFormatter: (t: unknown) => bh(t, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) },
  rightPriceScale: { borderColor: css("--chart-border") },
});
export const candleStyle = () => {
  const up = css("--chart-up"), down = css("--chart-down");
  return { upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down };
};

export function CryptoChart() {
  const el = useRef<HTMLDivElement>(null);
  const [label, setLabel] = useState("waiting for data…");
  const [last, setLast] = useState<number>();

  useEffect(() => {
    const chart = createChart(el.current!, chartOptions());
    const series = chart.addSeries(CandlestickSeries, candleStyle());
    const off = onEvent((e) => {
      if (e.type === "candles") {
        series.setData(e.candles.map(toBar));
        setLabel(`${e.symbol} · ${e.tf}`);
      } else if (e.type === "candle") {
        series.update(toBar(e.candle));
        setLast(e.candle.close);
      }
    });
    return () => { off(); chart.remove(); };
  }, []);

  return (
    <div className="flex h-full flex-col rounded-lg border border-hud-line bg-hud-panel/80 p-4">
      <div className="mb-2 flex justify-between text-sm">
        <span className="tracking-widest text-hud-accent">{label}</span>
        <span className="tabular-nums text-slate-300">{last?.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
      </div>
      <div ref={el} className="min-h-0 flex-1" />
    </div>
  );
}
