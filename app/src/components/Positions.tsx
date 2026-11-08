import type { PaperState } from "../ws";
import { Panel } from "./Panels";

const money = (x: number, ccy: string) => `${x >= 0 ? "" : "−"}${Math.abs(x).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${ccy}`;
const label = (s: string) => s.replace(/^(spot|perp):/, "").replace(":USDT", "").replace("_", "/");

export function PositionsPanel({ paper: all }: { paper?: PaperState }) {
  // crypto desk is off: Signal Station only
  const paper = all && { ...all, open: all.open.filter((p) => p.mode !== "crypto"), recent: all.recent.filter((p) => p.mode !== "crypto") };
  if (!paper) return <Panel title="PAPER POSITIONS"><p className="text-sm text-slate-500">Loading…</p></Panel>;
  return (
    <Panel title="PAPER POSITIONS · SIMULATED">
      <div className="mb-3 grid grid-cols-2 gap-2 text-sm">
        {Object.entries(paper.accounts).filter(([div]) => div === "fx").map(([div, a]) => (
          <div key={div} className="rounded border border-hud-line p-2">
            <div className="text-xs text-slate-500">{div === "fx" ? "SIGNAL STATION" : "CRYPTO DESK"}</div>
            <div className="tabular-nums text-slate-100">{money(a.equity, a.currency)}</div>
            <div className={`text-xs tabular-nums ${a.equity >= a.start ? "text-hud-green" : "text-hud-red"}`}>
              {((a.equity / a.start - 1) * 100).toFixed(2)}% · balance {money(a.balance, a.currency)}
            </div>
          </div>
        ))}
      </div>
      {paper.open.length === 0 ? <p className="text-sm text-slate-500">No open positions.</p> : (
        <table className="w-full text-left text-sm tabular-nums">
          <thead className="text-xs text-slate-500"><tr><th>Pos</th><th>Mode</th><th className="text-right">Size</th><th className="text-right">Entry</th><th className="text-right">P&L</th><th className="text-right">R</th></tr></thead>
          <tbody>
            {paper.open.map((p) => (
              <tr key={p.signal_id} className="border-t border-hud-line/60">
                <td className={p.side > 0 ? "text-hud-green" : "text-hud-red"}>{p.side > 0 ? "LONG" : "SHORT"} {label(p.symbol)}</td>
                <td className="text-xs text-slate-400">{p.mode}</td>
                <td className="text-right">{p.units.toPrecision(4)}</td>
                <td className="text-right">{p.entry.toPrecision(6)}</td>
                <td className={`text-right ${(p.upnl ?? 0) >= 0 ? "text-hud-green" : "text-hud-red"}`}>{(p.upnl ?? 0).toFixed(2)}</td>
                <td className="text-right">{p.r ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {paper.recent.length > 0 && (
        <div className="mt-3 text-xs text-slate-400">
          Recent: {paper.recent.slice(0, 5).map((p) => `${label(p.symbol)} ${p.reason} ${(p.pnl ?? 0) >= 0 ? "+" : ""}${(p.pnl ?? 0).toFixed(2)}`).join(" · ")}
        </div>
      )}
    </Panel>
  );
}
