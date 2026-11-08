import { useEffect, useState, type ReactNode } from "react";
import { API } from "../ws";
import { GAIN, LOSS, LineChart } from "./charts";

type Streak = { current: number; current_win: boolean; best_win: number; worst_loss: number };
type Day = { pnl: number; trades: number; wins: number; pct: number };
type Dash = {
  trades: number; wins: number; losses: number; win_rate: number | null; avg_win: number; avg_loss: number; ratio: number | null;
  profit_factor: number | null; net: number; gain_pct: number; start: number; balance: number; equity: number; highest: number;
  max_dd_pct: number; returns: Record<"daily" | "weekly" | "monthly" | "annual", number | null>; trade_streak: Streak; day_streak: Streak;
  days: Record<string, Day>; per_day: number[]; curve: { time: number; value: number }[]; score: { axes: Record<string, number>; total: number };
};

const money = (x: number, sign = false) => `${sign ? (x >= 0 ? "+" : "−") : x < 0 ? "−" : ""}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (x: number) => `${x >= 0 ? "+" : ""}${x.toFixed(2)}%`;
const tone = (x: number) => ({ color: x >= 0 ? GAIN : LOSS });
const ACCENT = "var(--color-hud-accent)";

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-hud-line bg-hud-panel/80 p-4 ${className}`}>{children}</div>;
}
function Big({ value, label }: { value: string; label: string }) {
  return <div><div className="text-2xl font-bold tabular-nums text-slate-100">{value}</div><div className="text-sm font-semibold text-slate-300">{label}</div></div>;
}

/** Trades per active day, last 30. */
function Spark({ data }: { data: number[] }) {
  if (data.length < 2) return <div className="h-12" />;
  const max = Math.max(...data), W = 300, H = 48;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * W},${H - 4 - (v / max) * (H - 8)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 h-12 w-full" preserveAspectRatio="none" aria-label="Trades per day">
      <polygon points={`0,${H} ${pts} ${W},${H}`} fill={ACCENT} opacity={0.12} />
      <polyline points={pts} fill="none" stroke={ACCENT} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Half-donut: wins (gain colour) then losses. */
function Gauge({ wins, losses }: { wins: number; losses: number }) {
  const w = wins + losses ? (wins / (wins + losses)) * 100 : 0;
  const arc = "M 10 50 A 40 40 0 0 1 90 50";
  return (
    <svg viewBox="0 0 100 56" className="mx-auto mt-2 h-16" aria-label={`${wins} wins, ${losses} losses`}>
      <path d={arc} fill="none" stroke={LOSS} strokeWidth={12} pathLength={100} />
      <path d={arc} fill="none" stroke={GAIN} strokeWidth={12} pathLength={100} strokeDasharray={`${w} 100`} />
    </svg>
  );
}

function Streaks({ label, s }: { label: string; s: Streak }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="flex items-center gap-3">
        <span className="text-4xl font-bold tabular-nums" style={{ color: s.current_win ? GAIN : LOSS }} title={s.current_win ? "current winning run" : "current losing run"}>
          {s.current}
        </span>
        <span className="text-xs text-slate-400">{s.current_win ? (s.current === 1 ? "win" : "wins") : s.current === 1 ? "loss" : "losses"}<br />in a row</span>
        <div className="flex flex-col gap-1 text-xs font-bold tabular-nums text-white">
          <span className="rounded px-2 py-0.5 text-center" style={{ background: GAIN }} title="longest winning run">{s.best_win}</span>
          <span className="rounded px-2 py-0.5 text-center" style={{ background: LOSS }} title="longest losing run">{s.worst_loss}</span>
        </div>
      </div>
      <span className="text-sm text-slate-400">{label}</span>
    </div>
  );
}

/** Ras score: five 0-100 axes on a pentagon, the average in the middle. */
function Radar({ axes, total }: { axes: Record<string, number>; total: number }) {
  const names = Object.keys(axes), C = 150, R = 80;
  const at = (i: number, r: number) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / names.length;
    return [C + Math.cos(a) * r, C + Math.sin(a) * r];
  };
  const ring = (f: number) => names.map((_, i) => at(i, R * f).join(",")).join(" ");
  const shape = names.map((n, i) => at(i, (R * Math.max(4, axes[n])) / 100).join(",")).join(" ");
  return (
    <svg viewBox="0 20 300 260" className="mx-auto h-full max-h-72 w-full" aria-label={`Ras score ${total}`}>
      {[0.25, 0.5, 0.75, 1].map((f) => <polygon key={f} points={ring(f)} fill="none" stroke="var(--color-hud-line)" />)}
      {names.map((_, i) => { const [x, y] = at(i, R); return <line key={i} x1={C} y1={C} x2={x} y2={y} stroke="var(--color-hud-line)" />; })}
      <polygon points={shape} fill={ACCENT} fillOpacity={0.18} stroke={ACCENT} strokeWidth={2} />
      {names.map((n, i) => { const [x, y] = at(i, (R * Math.max(4, axes[n])) / 100); return <circle key={n} cx={x} cy={y} r={3} fill={ACCENT} />; })}
      {names.map((n, i) => {
        const [x, y] = at(i, R + 20);
        return <text key={n} x={x} y={y} textAnchor="middle" dominantBaseline="middle" fontSize={10} fill="var(--color-slate-300)">
          {n} <tspan fill="var(--color-slate-500)">{axes[n]}</tspan></text>;
      })}
      <text x={C} y={C} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={700} fill="var(--color-slate-100)">{total}</text>
    </svg>
  );
}

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function Cell({ d }: { d?: Day }) {
  return d ? (
    <div className="pt-2 text-center leading-tight tabular-nums">
      <div className="text-xs font-semibold xl:text-sm" style={tone(d.pnl)}>{money(d.pnl)}</div>
      <div className="text-[10px] text-slate-400">{pct(d.pct)} · {d.trades}t</div>
    </div>
  ) : null;
}

/** Month calendar of daily P&L with a weekly total column (Bahrain days). */
function Calendar({ days }: { days: Record<string, Day> }) {
  const [month, setMonth] = useState(() => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), 1); });
  const first = new Date(month.getFullYear(), month.getMonth(), 1 - month.getDay());
  const weeks = Array.from({ length: 6 }, (_, w) => Array.from({ length: 7 }, (_, i) => new Date(first.getFullYear(), first.getMonth(), first.getDate() + w * 7 + i)))
    .filter((wk) => wk.some((d) => d.getMonth() === month.getMonth()));
  const inMonth = Object.entries(days).filter(([k]) => k.startsWith(iso(month).slice(0, 7))).map(([, v]) => v);
  const sum = (ds: Day[], k: "pnl" | "trades" | "wins" | "pct") => ds.reduce((a, d) => a + d[k], 0);
  const shift = (n: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + n, 1));
  const nav = "rounded px-2 text-lg text-slate-400 hover:text-hud-accent";
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button className={nav} onClick={() => shift(-1)} aria-label="Previous month">‹</button>
          <span className="w-40 text-center text-lg text-slate-100">{month.toLocaleDateString("en-GB", { month: "long", year: "numeric" })}</span>
          <button className={nav} onClick={() => shift(1)} aria-label="Next month">›</button>
        </div>
        <div className="flex gap-5 rounded-lg bg-hud-accent/10 px-4 py-1.5 text-center text-sm tabular-nums">
          {([["Trades", String(sum(inMonth, "trades"))], ["Wins", String(sum(inMonth, "wins"))],
             ["Profits", money(sum(inMonth, "pnl"))], ["Percent", pct(sum(inMonth, "pct"))]] as const).map(([k, v]) => (
            <div key={k}><div className="font-semibold text-hud-accent">{k}</div><div className="text-slate-100">{v}</div></div>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-8 gap-1.5 text-sm">
        {[...DOW, "Total"].map((d) => <div key={d} className="text-center font-semibold text-slate-300">{d}</div>)}
        {weeks.map((wk) => {
          const ds = wk.map((d) => days[iso(d)]).filter(Boolean) as Day[];
          const tot = sum(ds, "pnl");
          return [
            ...wk.map((d) => {
              const x = days[iso(d)], out = d.getMonth() !== month.getMonth();
              return (
                <div key={iso(d)} className={`relative grid h-16 place-items-center rounded-md border border-hud-line ${out ? "opacity-40" : ""}`}
                  style={x ? { borderTop: `3px solid ${x.pnl >= 0 ? GAIN : LOSS}`, background: `color-mix(in srgb, ${x.pnl >= 0 ? GAIN : LOSS} 8%, transparent)` } : undefined}>
                  <span className={x ? "absolute left-1.5 top-0.5 text-[10px] text-slate-500" : "text-slate-500"}>{d.getDate()}</span>
                  <Cell d={x} />
                </div>
              );
            }),
            <div key={`t${iso(wk[0])}`} className="grid h-16 place-items-center rounded-md border-2"
              style={{ borderColor: ds.length ? (tot >= 0 ? GAIN : LOSS) : "var(--color-hud-line)" }}>
              {ds.length ? <Cell d={{ pnl: tot, trades: sum(ds, "trades"), wins: 0, pct: sum(ds, "pct") }} /> : <span className="text-slate-500">—</span>}
            </div>,
          ];
        })}
      </div>
    </Card>
  );
}

/** Journal dashboard over the Signal Station paper account: every paper trade Ras takes, in money. */
export function JournalDashboard() {
  const [d, setD] = useState<Dash>();
  const [unit, setUnit] = useState<"%" | "$">("%");
  useEffect(() => {
    const load = () => fetch(`${API}/api/journal/dashboard`).then((r) => r.json()).then(setD).catch(() => {});
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);
  if (!d) return <p className="text-sm text-slate-500">Loading the journal dashboard…</p>;
  const split = d.avg_win + d.avg_loss ? (d.avg_win / (d.avg_win + d.avg_loss)) * 100 : 50;
  const ret = (k: keyof Dash["returns"]) => { const r = d.returns[k]; return r == null ? "—" : unit === "%" ? pct(r) : money(d.balance * r / 100, true); };
  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg tracking-[0.3em] text-hud-accent">JOURNAL DASHBOARD <span className="text-xs tracking-normal text-slate-500">· paper account, every trade Ras takes</span></h2>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="flex flex-col gap-4">
          <Card><Big value={String(d.trades)} label="Trade count" /><Spark data={d.per_day} /></Card>
          <div className="grid grid-cols-2 gap-4">
            <Card><Big value={d.win_rate == null ? "—" : `${d.win_rate.toFixed(2)}%`} label="Win rate" /><Gauge wins={d.wins} losses={d.losses} /></Card>
            <Card>
              <Big value={d.ratio == null ? "—" : d.ratio.toFixed(2)} label="Avg win / avg loss" />
              <div className="mt-5 flex h-4 overflow-hidden rounded-full" title={`avg win ${money(d.avg_win)} · avg loss ${money(d.avg_loss)}`}>
                <div style={{ width: `${split}%`, background: GAIN }} /><div className="flex-1" style={{ background: LOSS }} />
              </div>
              <div className="mt-1 flex justify-between text-xs tabular-nums text-slate-400"><span>{money(d.avg_win)}</span><span>{money(d.avg_loss)}</span></div>
            </Card>
          </div>
          <Card>
            <div className="mb-3 text-lg font-semibold text-slate-100">Win streak</div>
            <div className="flex justify-around"><Streaks label="Days" s={d.day_streak} /><Streaks label="Trades" s={d.trade_streak} /></div>
          </Card>
        </div>
        <Card className="flex flex-col">
          <div className="text-lg font-semibold text-slate-100">Ras score</div>
          <div className="text-xs text-slate-500">win rate, payoff, profit factor, drawdown control and green-day consistency, each 0–100</div>
          <div className="min-h-64 flex-1"><Radar axes={d.score.axes} total={d.score.total} /></div>
          {d.trades < 20 && <p className="text-xs text-slate-500">Only {d.trades} trades so far: the score settles after a few dozen.</p>}
        </Card>
        <Card className="flex flex-col">
          <Big value={money(d.balance)} label="Balance" />
          <div className="mt-2 flex-1"><LineChart points={d.curve} unit="USD" height={300} /></div>
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="flex flex-col gap-4 text-sm">
          <div>
            <div className="mb-2 font-semibold text-slate-100">Performance</div>
            <div className="flex justify-between border-l-4 pl-3" style={{ borderColor: ACCENT }}>
              <div><div className="font-semibold text-slate-300">Gain %</div><div className="text-lg tabular-nums" style={tone(d.gain_pct)}>{pct(d.gain_pct)}</div></div>
              <div className="text-right"><div className="font-semibold text-slate-300">Net P&L</div><div className="text-lg font-bold tabular-nums" style={tone(d.net)}>{money(d.net, true)}</div></div>
            </div>
          </div>
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold text-slate-100">Period returns <span className="text-xs font-normal text-slate-500">(average so far, compounded)</span></span>
              <span className="flex overflow-hidden rounded border border-hud-line">
                {(["%", "$"] as const).map((u) => <button key={u} onClick={() => setUnit(u)} aria-pressed={unit === u}
                  className={`px-3 py-0.5 ${unit === u ? "bg-hud-accent/15 text-hud-accent" : "text-slate-400"}`}>{u}</button>)}
              </span>
            </div>
            <div className="grid grid-cols-4 divide-x divide-hud-line text-center">
              {([["Daily", "daily"], ["Weekly", "weekly"], ["Monthly", "monthly"], ["Annualised", "annual"]] as const).map(([l, k]) => (
                <div key={k}><div className="text-slate-300">{l}</div><div className="tabular-nums" style={tone(d.returns[k] ?? 0)} title={d.returns[k] == null ? "not enough history yet" : undefined}>{ret(k)}</div></div>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-2 font-semibold text-slate-100">Risk</div>
            <div className="divide-y divide-hud-line/60 rounded border border-hud-line px-3">
              {([["Max balance drawdown", `${d.max_dd_pct.toFixed(2)}%`], ["Current equity", money(d.equity)], ["Current balance", money(d.balance)],
                 ["Highest balance", money(d.highest)], ["Starting balance", money(d.start)]] as const).map(([k, v]) => (
                <div key={k} className="flex justify-between py-1.5"><span className="text-slate-300">{k}</span><span className="tabular-nums text-slate-100">{v}</span></div>
              ))}
            </div>
          </div>
        </Card>
        <div className="lg:col-span-2"><Calendar days={d.days} /></div>
      </div>
    </div>
  );
}
