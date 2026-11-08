import { useEffect, useState } from "react";
import type { BoardRow, Execution, ExecutionState, Signal } from "../ws";
import { ExecutionBar } from "./Safety";
import { Panel } from "./Panels";
import { History, SignalChart, fetchHistory } from "./Room";
import { CallBadge, SignalCard } from "./SignalCard";

const pct = (x?: number | null, d = 2) => (x == null ? "—" : `${x.toFixed(d)}%`);

export function OpportunityBoard({ rows, updated, quotes, onOpen }: {
  rows: BoardRow[]; updated: number; quotes: Record<string, [number, number]>; onOpen: (key: string) => void;
}) {
  const [showFiltered, setShowFiltered] = useState(false);
  const shown = rows.filter((r) => showFiltered || !r.filtered);
  return (
    <Panel title="CRYPTO OPPORTUNITY BOARD" right={
      <span className="flex items-center gap-3">
        <label className="flex items-center gap-1"><input type="checkbox" checked={showFiltered} onChange={(e) => setShowFiltered(e.target.checked)} /> filtered ({rows.filter((r) => r.filtered).length})</label>
        <span>{updated ? new Date(updated).toLocaleTimeString("en-GB", { timeZone: "Asia/Bahrain" }) : "scanning…"}</span>
      </span>
    }>
      {shown.length === 0 ? <p className="text-sm text-slate-500">First scan takes ~30s…</p> : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-left text-sm tabular-nums">
            <thead className="sticky top-0 bg-hud-panel text-xs text-slate-500">
              <tr><th>Pair</th><th>Mkt</th><th>Call</th><th>Score</th><th>Strategies</th><th>Regime</th><th className="text-right">Price</th><th className="text-right">1h</th><th className="text-right">ATR5m</th><th className="text-right">Spread</th><th className="text-right">Funding</th></tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const q = quotes[r.key];
                const px = q ? (q[0] + q[1]) / 2 : r.price;
                return (
                  <tr key={r.key} onClick={() => onOpen(r.key)} className={`cursor-pointer border-t border-hud-line/60 hover:bg-hud-accent/5 ${r.filtered ? "opacity-40" : ""}`}>
                    <td className="font-semibold text-slate-200">{r.base}</td>
                    <td className="text-xs text-slate-400">{r.market}</td>
                    <td>{r.filtered ? <span className="text-xs text-slate-500">{r.filtered}</span> : <CallBadge call={r.call} conf={r.score} />}</td>
                    <td>{r.score || "—"}</td>
                    <td className="max-w-44 truncate text-xs text-slate-400">{r.strategies.join(", ") || "—"}</td>
                    <td className="text-xs text-slate-400">{r.regime ?? "—"}</td>
                    <td className="text-right">{px.toFixed(r.digits)}</td>
                    <td className={`text-right ${(r.change_1h ?? 0) >= 0 ? "text-hud-green" : "text-hud-red"}`}>{pct(r.change_1h)}</td>
                    <td className="text-right">{pct(r.atr_pct)}</td>
                    <td className="text-right">{pct(r.spread_pct, 3)}</td>
                    <td className="text-right">{r.funding == null ? "—" : pct(r.funding * 100, 4)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function CryptoRoom({ row, signal, onBack, execs, execState }: {
  row?: BoardRow; signal?: Signal; onBack: () => void; execs: Record<number, Execution>; execState?: ExecutionState;
}) {
  const [history, setHistory] = useState<Signal[]>([]);
  useEffect(() => { if (row) fetchHistory(row.key).then(setHistory).catch(() => setHistory([])); }, [row?.key, signal?.id, signal?.status]);
  if (!row) return <button onClick={onBack} className="text-hud-accent">← Back (pair left the watchlist)</button>;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-4">
        <button onClick={onBack} className="rounded border border-hud-line px-3 py-1 text-sm text-hud-accent hover:border-hud-accent">← Desk</button>
        <h2 className="text-lg tracking-[0.3em] text-hud-accent">{row.base} · {row.market.toUpperCase()}</h2>
        <span className="text-xs text-slate-500">Scalp: 15M bias · 5M structure · 1M entry · 1H context · {execState?.enabled && row.market === "perp" ? "approval-based demo execution" : "paper only"}</span>
      </div>
      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <div className="flex flex-col gap-4">
          <div className="h-[420px] rounded-lg border border-hud-line bg-hud-panel/80 p-2">
            <SignalChart symbol={row.key} tf="1m" signal={signal?.status === "active" ? signal : undefined} digits={row.digits} />
          </div>
          <History rows={history} digits={row.digits} />
        </div>
        <div className="flex flex-col gap-4">
          {signal?.status === "active" && signal.created === row.decision?.created
            ? <SignalCard s={signal} digits={row.digits} title="CURRENT CALL" detailed />
            : row.decision ? <SignalCard s={row.decision} digits={row.digits} title="CURRENT CALL" detailed /> : <p className="text-sm text-slate-500">{row.filtered ?? "No decision yet"}</p>}
          {signal?.status === "active" && signal.created !== row.decision?.created && <SignalCard s={signal} digits={row.digits} title={`OPEN SCALP #${signal.id}`} />}
          {signal && <ExecutionBar sig={signal} exec={execs[signal.id]} state={execState} digits={row.digits} />}
        </div>
      </div>
    </div>
  );
}
