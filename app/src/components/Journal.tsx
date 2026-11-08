import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, LineStyle, createChart, type UTCTimestamp } from "lightweight-charts";
import { API, type Briefing, type Position, type Signal } from "../ws";
import { candleStyle, chartOptions } from "./CryptoChart";
import { css } from "../theme";
import { JournalDashboard } from "./JournalDashboard";
import { Panel } from "./Panels";
import { SignalCard, fmtTime } from "./SignalCard";

type Entry = Signal & { journal: { notes: string; snapshot: number[][] | null; trade_log?: string }; paper?: Position };

/** Tiny Markdown subset for reviews: ## headings, - bullets, paragraphs. */
export function Md({ text }: { text: string }) {
  return (
    <div className="space-y-1 text-sm leading-relaxed text-slate-200">
      {text.split("\n").map((l, i) => l.startsWith("## ") ? <h3 key={i} className="mt-3 text-xs tracking-widest text-hud-accent">{l.slice(3).toUpperCase()}</h3>
        : l.startsWith("- ") ? <p key={i} className="pl-3">• {l.slice(2)}</p>
        : l.trim() ? <p key={i} className={l.startsWith("_") ? "text-xs italic text-slate-500" : ""}>{l.replace(/^_|_$/g, "")}</p> : null)}
    </div>
  );
}

function Snapshot({ e }: { e: Entry }) {
  const el = useRef<HTMLDivElement>(null);
  const digits = e.digits ?? 5;
  useEffect(() => {
    if (!e.journal.snapshot) return;
    const chart = createChart(el.current!, { ...chartOptions(), height: 240 });
    const s = chart.addSeries(CandlestickSeries, { ...candleStyle(), priceFormat: { type: "price", precision: digits, minMove: 10 ** -digits } });
    s.setData(e.journal.snapshot.map(([time, open, high, low, close]) => ({ time: time as UTCTimestamp, open, high, low, close })));
    if (e.levels) {
      ([["ENTRY", e.levels.entry, css("--color-hud-accent")], ["SL", e.levels.stop, css("--color-hud-red")], ["TP1", e.levels.tp1, css("--color-hud-green")]] as const)
        .forEach(([title, price, color]) => s.createPriceLine({ title, price, color, lineWidth: 1, lineStyle: LineStyle.Dashed }));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [e, digits]);
  return e.journal.snapshot ? <div ref={el} style={{ height: 240 }} /> : <p className="text-sm text-slate-500">No chart snapshot for this signal.</p>;
}

export function JournalPage({ review }: { review?: Briefing & { source: string } }) {
  const [mode, setMode] = useState("");
  const [rows, setRows] = useState<Entry[]>([]);
  const [sel, setSel] = useState<number>();
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = () => fetch(`${API}/api/journal?limit=150${mode ? `&mode=${mode}` : ""}`).then((r) => r.json()).then(setRows);
  useEffect(() => { load(); }, [mode]);
  const cur = rows.find((r) => r.id === sel);
  useEffect(() => { setNote(cur?.journal.notes ?? ""); setSaved(false); }, [sel]);
  const save = () => fetch(`${API}/api/journal/${sel}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notes: note }) })
    .then(() => { setSaved(true); load(); });
  const digits = (e: Entry) => e.digits ?? (e.symbol.includes("JPY") ? 3 : e.symbol.startsWith("X") || e.symbol.startsWith("WTI") ? 2 : 5);

  return (
    <div className="flex flex-col gap-4">
    <JournalDashboard />
    <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <h2 className="mr-4 text-lg tracking-[0.3em] text-hud-accent">JOURNAL</h2>
          <select className="rounded border border-hud-line bg-hud-bg px-2 py-1 text-sm" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">All</option><option value="intraday">Intraday</option><option value="scalp">Scalp</option>
          </select>
        </div>
        <Panel title={`SIGNALS (${rows.length})`}>
          {rows.length === 0 ? <p className="text-sm text-slate-500">Every signal is journaled automatically.</p> : (
            <div className="max-h-[560px] overflow-y-auto">
              <table className="w-full text-left text-sm tabular-nums">
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} onClick={() => setSel(r.id)} className={`cursor-pointer border-t border-hud-line/60 hover:bg-hud-accent/5 ${sel === r.id ? "bg-hud-accent/10" : ""}`}>
                      <td className="text-xs text-slate-500">{fmtTime(r.created)}</td>
                      <td className="text-slate-200">{r.display ?? r.symbol.replace("_", "/")}</td>
                      <td className="text-xs text-slate-400">{r.mode}</td>
                      <td className={r.call === "BUY" ? "text-hud-green" : "text-hud-red"}>{r.call}</td>
                      <td className="text-xs">{r.status.replace("_", " ")}</td>
                      <td className="text-right">{r.result_r ?? "—"}</td>
                      <td className="text-xs" title={r.journal.notes ? "has your notes" : r.journal.trade_log ? "trade logged" : ""}>{r.journal.notes ? "📝" : r.journal.trade_log ? "✓" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <ReviewPanel review={review} busy={busy} onGenerate={() => { setBusy(true); fetch(`${API}/api/reviews`, { method: "POST" }).finally(() => setBusy(false)); }} />
      </div>
      <div className="flex flex-col gap-4">
        {cur ? (
          <>
            <Panel title="CHART AT SIGNAL TIME"><Snapshot e={cur} /></Panel>
            <SignalCard s={cur} digits={digits(cur)} title={`#${cur.id} · ${cur.mode.toUpperCase()}`} detailed />
            {cur.journal.trade_log && (
              <Panel title="TRADE LOG · WRITTEN BY RAS">
                {cur.journal.trade_log.split("\n").map((l, i) => <p key={i} className="text-sm text-slate-200">{l}</p>)}
              </Panel>
            )}
            {cur.paper && (
              <Panel title="PAPER TRADE">
                <p className="text-sm tabular-nums text-slate-300">
                  {cur.paper.side > 0 ? "Long" : "Short"} {cur.paper.units.toPrecision(4)} @ {cur.paper.entry.toPrecision(6)}
                  {cur.paper.closed ? ` → ${cur.paper.exit?.toPrecision(6)} (${cur.paper.reason}) · P&L ${(cur.paper.pnl ?? 0).toFixed(2)} · fees ${cur.paper.fees.toFixed(2)}` : " · open"}
                </p>
              </Panel>
            )}
            <Panel title="MY NOTES">
              <textarea value={note} onChange={(e) => { setNote(e.target.value); setSaved(false); }} rows={4} maxLength={5000}
                placeholder="Did you take it? What did you see? What would you do differently?"
                className="w-full rounded border border-hud-line bg-hud-panel p-2 text-sm text-slate-200" />
              <button onClick={save} className="mt-2 self-start rounded border border-hud-line px-3 py-1 text-xs text-hud-accent hover:border-hud-accent">{saved ? "Saved ✓" : "Save note"}</button>
            </Panel>
          </>
        ) : <p className="text-sm text-slate-500">Select a signal to see its snapshot, votes, outcome and notes.</p>}
      </div>
    </div>
    </div>
  );
}

function ReviewPanel({ review, busy, onGenerate }: { review?: Briefing & { source: string }; busy: boolean; onGenerate: () => void }) {
  return (
    <Panel title="WEEKLY REVIEW" right={
      <button disabled={busy} onClick={onGenerate} className="rounded border border-hud-line px-2 py-0.5 text-hud-accent hover:border-hud-accent disabled:opacity-40">
        {busy ? "Writing…" : "Generate now"}
      </button>
    }>
      {review ? (
        <>
          <div className="text-xs text-slate-500">{fmtTime(review.created)} · {review.source === "claude" ? "written by Claude" : "template (no Anthropic key)"}</div>
          <Md text={review.text} />
        </>
      ) : <p className="text-sm text-slate-500">Generated every Saturday 10:00 Bahrain, or on demand.</p>}
    </Panel>
  );
}
