import { useEffect, useState } from "react";
import { API, type Briefing, type CalEvent, type Headline, type NewsView } from "../ws";
import { fmtTime } from "./SignalCard";

const WATCHED = new Set(["USD", "EUR", "GBP", "JPY"]);
const IMPACT: Record<string, string> = { High: "text-hud-red", Medium: "text-hud-amber", Low: "text-slate-500" };

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

export function Panel({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col rounded-xl border border-hud-line bg-hud-panel/90 p-4 shadow-[0_1px_0_rgba(255,255,255,0.03)_inset,0_8px_24px_-12px_rgba(0,0,0,0.5)]">
      <div className="mb-3 flex items-center justify-between gap-2 text-[11px] font-semibold uppercase tracking-widest text-slate-400">
        <span className="flex items-center gap-2"><span className="h-3 w-0.5 rounded-full bg-hud-accent" aria-hidden />{title}</span>{right}
      </div>
      {children}
    </section>
  );
}

const countdown = (ms: number) => {
  const m = Math.round(Math.abs(ms) / 60000), h = Math.floor(m / 60);
  const s = h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
  return ms >= 0 ? `in ${s}` : `${s} ago`;
};

export function CalendarPanel({ events }: { events: CalEvent[] }) {
  const now = useNow();
  const rows = events.filter((e) => (WATCHED.has(e.country) || /crude oil/i.test(e.title)) && e.impact !== "Low" && e.impact !== "Holiday"
    && e.time > now - 20 * 60_000 && e.time < now + 36 * 3_600_000);
  return (
    <Panel title="ECONOMIC CALENDAR · 36H">
      {rows.length === 0 ? <p className="text-sm text-slate-500">{events.length ? "Nothing medium/high-impact ahead." : "Loading calendar…"}</p> : (
        <ul className="max-h-80 space-y-1 overflow-y-auto text-sm">
          {rows.map((e) => {
            const dt = e.time - now;
            const blackout = e.impact === "High" && dt < 30 * 60_000 && dt > -15 * 60_000;
            return (
              <li key={e.title + e.time} className="flex items-baseline gap-2 tabular-nums">
                <span className="w-24 shrink-0 text-xs text-slate-500">{fmtTime(e.time)}</span>
                <span className={`w-10 shrink-0 font-semibold ${IMPACT[e.impact]}`}>{e.country}</span>
                <span className="flex-1 truncate text-slate-200" title={`forecast ${e.forecast || "—"} · previous ${e.previous || "—"}`}>{e.title}</span>
                {blackout && <span className="rounded bg-hud-red/20 px-1 text-xs text-hud-red">BLACKOUT</span>}
                <span className="w-20 shrink-0 text-right text-xs text-slate-400">{countdown(dt)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

const MOOD = { risk_on: "text-hud-green", risk_off: "text-hud-red", neutral: "text-slate-300" };

export function NewsPanel({ headlines, view }: { headlines: Headline[]; view?: NewsView }) {
  const now = useNow(60_000);
  return (
    <Panel title="NEWS" right={view && (
      <span>mood <b className={MOOD[view.risk_sentiment]}>{view.risk_sentiment.replace("_", "-")}</b> · {view.source}</span>
    )}>
      {view?.geopolitical_alert && <p className="mb-2 rounded border border-hud-amber/40 px-2 py-1 text-sm text-hud-amber">{view.geopolitical_alert}</p>}
      {headlines.length === 0 ? <p className="text-sm text-slate-500">Loading headlines…</p> : (
        <ul className="max-h-80 space-y-1 overflow-y-auto text-sm">
          {headlines.map((h) => (
            <li key={h.id} className="flex gap-2">
              <span className="w-16 shrink-0 text-xs text-slate-500">{countdown(h.time - now).replace(" ago", "")}</span>
              <span className="w-20 shrink-0 truncate text-xs text-hud-accent/70">{h.source}</span>
              <span className="text-slate-200">{h.title}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function BriefingPanel({ briefing }: { briefing?: Briefing }) {
  const [busy, setBusy] = useState(false);
  const make = (kind: string) => { setBusy(true); fetch(`${API}/api/briefings/${kind}`, { method: "POST" }).finally(() => setBusy(false)); };
  const btn = "rounded border border-hud-line px-2 py-0.5 text-hud-accent hover:border-hud-accent disabled:opacity-40";
  return (
    <Panel title="BRIEFING" right={
      <span className="flex gap-2">
        <button disabled={busy} className={btn} onClick={() => make("morning")}>Morning</button>
        <button disabled={busy} className={btn} onClick={() => make("eod")}>End of day</button>
      </span>
    }>
      {briefing ? (
        <>
          <div className="text-xs text-slate-500">{briefing.title} · {fmtTime(briefing.created)}</div>
          <p className="mt-1 text-sm leading-relaxed text-slate-200">{briefing.text}</p>
        </>
      ) : <p className="text-sm text-slate-500">No briefing yet — scheduled at your configured times, or generate one now.</p>}
    </Panel>
  );
}
