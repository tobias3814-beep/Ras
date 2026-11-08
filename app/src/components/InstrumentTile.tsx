import { useRef } from "react";
import type { Decision, Price } from "../ws";
import { CallBadge } from "./SignalCard";

export const META: Record<string, { name: string; digits: number; pip: number }> = {
  GBP_USD: { name: "GBP/USD", digits: 5, pip: 0.0001 },
  EUR_USD: { name: "EUR/USD", digits: 5, pip: 0.0001 },
  USD_JPY: { name: "USD/JPY", digits: 3, pip: 0.01 },
  XAU_USD: { name: "XAU/USD", digits: 2, pip: 0.1 },
  XAG_USD: { name: "XAG/USD", digits: 3, pip: 0.01 },
  WTI_USD: { name: "WTI/USD", digits: 2, pip: 0.01 },
};
export const INSTRUMENTS = Object.keys(META);

/** FX, metals and oil stop trading from Friday ~21:00 to Sunday ~21:00 UTC (22:00 in northern winter). */
export const weekendClosed = (d = new Date()) => {
  const day = d.getUTCDay(), h = d.getUTCHours();
  return day === 6 || (day === 5 && h >= 21) || (day === 0 && h < 21);
};

type TileProps = { symbol: string; price?: Price; call?: Decision; scalp?: Decision; trend?: Decision; mode?: { intraday: boolean; scalp: boolean; trend?: boolean }; onOpen: () => void };

export function InstrumentTile({ symbol, price, call, scalp, trend, mode, onOpen }: TileProps) {
  const m = META[symbol];
  const prev = useRef<number | undefined>(undefined);
  const mid = price && (price.bid + price.ask) / 2;
  const dir = mid === undefined || prev.current === undefined ? 0 : Math.sign(mid - prev.current);
  if (mid !== undefined) prev.current = mid;
  const color = dir > 0 ? "text-hud-green" : dir < 0 ? "text-hud-red" : "text-hud-accent";

  return (
    <button onClick={onOpen} className="rounded-lg border border-hud-line bg-hud-panel/80 p-4 text-left shadow-[0_1px_8px_rgba(76,29,149,0.06)] transition hover:border-hud-accent/60">
      <div className="flex items-baseline justify-between">
        <span className="text-sm tracking-widest text-hud-accent">{m.name}</span>
        <span className="flex flex-col items-end gap-1 text-xs text-slate-400">
          <span>Intraday: {mode && !mode.intraday ? "off" : call ? <CallBadge call={call.call} conf={call.confidence} /> : "—"}</span>
          <span>Scalp: {mode && !mode.scalp ? "off" : scalp ? <CallBadge call={scalp.call} conf={scalp.confidence} scalp /> : "—"}</span>
          <span>Trend: {!mode?.trend ? "off" : trend ? <CallBadge call={trend.call} conf={trend.confidence} /> : "—"}</span>
        </span>
      </div>
      <div className={`mt-3 text-3xl font-semibold tabular-nums ${color}`}>
        {mid !== undefined ? mid.toFixed(m.digits) : <span className="text-base font-normal text-slate-500">
          {weekendClosed() ? "Market closed for the weekend" : "Waiting for prices…"}</span>}
      </div>
      <div className="mt-2 flex justify-between text-xs text-slate-400 tabular-nums">
        <span>B {price ? price.bid.toFixed(m.digits) : "—"}</span>
        <span>A {price ? price.ask.toFixed(m.digits) : "—"}</span>
        <span>SPR {price ? ((price.ask - price.bid) / m.pip).toFixed(1) : "—"}</span>
      </div>
    </button>
  );
}
