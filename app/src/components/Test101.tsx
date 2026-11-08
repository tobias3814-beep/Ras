import { useEffect, useState } from "react";
import { API, onEvent } from "../ws";
import { GAIN, LineChart, LOSS } from "./charts";
import { Panel } from "./Panels";

type Tm = { target_first: number; stop_first: number; neither: number; green: number; avg_r: number; verdict: string; toward_stop: boolean; replays: number; seed: number; sessions: number };
type Trade = {
  symbol: string; status: "open" | "closed" | "blocked" | "pending"; trigger_time: number; live?: boolean; why: string; shares: number; fill: number | null; exit: number | null;
  exit_time: number | null; reason: string; pnl: number | null; r: number | null; mark: number | null; tags: string[]; date?: string;
  plan: { ok: boolean; entry?: number; stop?: number; target?: number; risk?: number; why?: string };
  tm: Tm | null; sizing: { shares?: number; decided_by?: string; limits?: Record<string, number>; max_loss?: number; cost?: number; rr?: number };
};
type Board = { status: string; or_high?: number; or_low?: number; price?: number; time?: number; last?: number; away_pct?: number; why?: string; tags: string[] };
type Stats = { trades: number; win_rate?: number; avg_r?: number; se_r?: number | null; profit_factor?: number | null; total_usd?: number; targets?: number; stops?: number; max_dd_usd?: number; days?: number };
type Bt = {
  created: number; range: [string, string] | null; universe: string[];
  main: { stats: Stats; verdict: string; curve: { time: number; equity: number }[]; trades: Trade[] };
  variants: Record<string, { stats: Stats; verdict: string }>; split: { first: Stats; second: Stats }; notes: string[];
};
type Snap = {
  config: Record<string, number | string | boolean | string[]>; phase: string; balance: number; start: number; since: string | null; halted: boolean;
  today: { date: string; board: Record<string, Board>; trades: Trade[]; pnl: number; open_pnl: number } | null;
  tags: Record<string, string[]>; fetched: { ok: number; failed: string[]; at: number };
  days: { date: string; pnl: number; trades: number; caught_up: boolean }[];
  forward: { stats: Stats; verdict: string }; backtest: Bt | null; bt_running: boolean;
};
type News = { title: string; url: string; time: string; tags: string[] };

const usd = (x: number | null | undefined) => (x == null ? "—" : `${x < 0 ? "-" : x > 0 ? "+" : ""}$${Math.abs(x).toFixed(2)}`);
const tone = (x: number | null | undefined) => ({ color: (x ?? 0) > 0 ? GAIN : (x ?? 0) < 0 ? LOSS : undefined });
const pct = (x: number | undefined) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const ny = (ts: number | null | undefined) => (ts ? new Date(ts * 1000).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }) : "—");
const r2 = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`);
const VARIANT: Record<string, string> = { no_gate: "Without the time-machine gate", one_r: "Target at 1R", double_costs: "Double slippage", random_entries: "Random entries, same stops" };

const STEPS = [
  ["Scanner", "Morning tags on daily closes; the intraday scan is the entry rule"],
  ["News", "Yahoo headlines per stock, tagged. Shown, not traded on"],
  ["Entry", "First 5-minute close above the 15-minute opening range, 9:45 to 3:30 New York"],
  ["Exit", "Stop at the range midpoint, target 2R, flat by 3:55"],
  ["Time machine", "Replays the rest of the day; skips trades that lean toward the stop"],
  ["Budget", "Whole shares, smallest of four limits"],
  ["Risk", "Max loss, reward:risk, daily loss, trades per day, window, halts"],
  ["Broker", "Paper fills inside Ras only, never a real broker"],
  ["Alerts", "Ras says each paper fill and exit"],
] as const;

function Num({ label, value, style }: { label: string; value: string; style?: React.CSSProperties }) {
  return <div><div className="text-xs text-slate-500">{label}</div><div className="tabular-nums text-slate-200" style={style}>{value}</div></div>;
}

function StatRow({ s }: { s: Stats }) {
  if (!s.trades) return <p className="text-xs text-slate-400">No closed trades yet.</p>;
  return (
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
      <Num label="Trades" value={String(s.trades)} />
      <Num label="Avg per trade" value={`${r2(s.avg_r)} ± ${s.se_r == null ? "—" : (2 * s.se_r).toFixed(2)}`} style={tone(s.avg_r)} />
      <Num label="Won" value={pct(s.win_rate)} />
      <Num label="Profit factor" value={s.profit_factor == null ? "—" : s.profit_factor.toFixed(2)} />
      <Num label="Total" value={usd(s.total_usd)} style={tone(s.total_usd)} />
      <Num label="Worst drop" value={s.max_dd_usd == null ? "—" : `$${s.max_dd_usd.toFixed(2)}`} />
    </div>
  );
}

function TradeCard({ t }: { t: Trade }) {
  const [news, setNews] = useState<News[] | null>(null);
  const taken = t.status === "open" || t.status === "closed";
  const p = t.plan;
  return (
    <div className="rounded border border-hud-line p-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-bold text-slate-100">{t.symbol} <span className="font-normal text-slate-500">· triggered {ny(t.trigger_time)} at ${p.entry?.toFixed(2) ?? "—"}</span></span>
        <span className={`rounded border px-2 py-0.5 text-xs ${taken ? "border-hud-accent/50 text-hud-accent" : "border-hud-line text-slate-400"}`}>
          {t.status === "closed" ? `closed: ${t.reason}` : t.status === "open" ? "open (paper)" : t.status === "pending" ? "filling next bar" : "skipped"}
        </span>
      </div>
      {!taken && t.why && <p className="mt-1 text-xs text-hud-amber">{t.why}</p>}
      {t.live === false && <p className="mt-1 text-xs text-slate-500">Replayed from the bars: TEST 101 wasn't running yet when this triggered, so it isn't in the live record.</p>}
      {p.ok && (
        <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
          <Num label="Stop" value={`$${p.stop?.toFixed(2)}`} />
          <Num label="Target" value={`$${p.target?.toFixed(2)}`} />
          <Num label="Shares" value={taken ? `${t.shares} (${t.sizing.decided_by})` : "—"} />
          <Num label="Fill" value={t.fill ? `$${t.fill.toFixed(2)}` : "—"} />
          <Num label="Max loss" value={taken ? `$${t.sizing.max_loss?.toFixed(2)}` : "—"} />
          <Num label={t.status === "open" ? "Open P&L" : "P&L"} value={taken ? `${usd(t.pnl)} · ${r2(t.r)}` : "—"} style={tone(t.pnl)} />
        </div>
      )}
      {t.tm && (
        <p className="mt-2 text-xs text-slate-400">
          Time machine ({t.tm.replays.toLocaleString()} replays, {t.tm.sessions} sessions, seed {t.tm.seed}): target first {pct(t.tm.target_first)}, stop first {pct(t.tm.stop_first)},
          neither {pct(t.tm.neither)}, average {r2(t.tm.avg_r)}. <span className={t.tm.toward_stop ? "text-hud-amber" : "text-slate-300"}>{t.tm.verdict}.</span>
        </p>
      )}
      {t.tags.length > 0 && <p className="mt-1 text-xs text-slate-500">Morning scan: {t.tags.join(", ").replaceAll("_", " ")}</p>}
      <div className="mt-2">
        {news === null ? <button className="text-xs text-slate-400 underline hover:text-slate-200"
          onClick={() => fetch(`${API}/api/test101/news/${t.symbol}`).then((r) => (r.ok ? r.json() : [])).then(setNews).catch(() => setNews([]))}>News</button>
          : news.length === 0 ? <p className="text-xs text-slate-500">No headlines came back.</p>
          : <ul className="flex flex-col gap-1 text-xs">{news.slice(0, 5).map((n) => (
            <li key={n.url}><a className="text-slate-300 underline hover:text-hud-accent" href={n.url} target="_blank" rel="noreferrer">{n.title}</a>
              {n.tags.length > 0 && <span className="text-hud-amber"> · {n.tags.join(", ")}</span>}</li>))}</ul>}
      </div>
    </div>
  );
}

export function Test101Page() {
  const [s, setS] = useState<Snap>();
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => fetch(`${API}/api/test101`).then((r) => r.json()).then((x: Snap) => { setS(x); setErr(""); }).catch(() => setErr("Can't reach Ras."));
  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, []);
  useEffect(() => onEvent((e) => { if (e.type === "test101" || e.type === "test101_alert") load(); }), []);
  const setCfg = (body: Record<string, unknown>) => {
    setBusy(true);
    fetch(`${API}/api/test101/config`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(async (r) => { if (!r.ok) setErr((await r.json()).detail?.toString() ?? "That change was refused."); }).finally(() => { setBusy(false); load(); });
  };
  const backtest = () => fetch(`${API}/api/test101/backtest`, { method: "POST" })
    .then(async (r) => { if (!r.ok) setErr((await r.json()).detail?.toString() ?? "The backtest didn't start."); load(); });

  const c = s?.config;
  const bt = s?.backtest;
  const today = s?.today;
  const board = Object.entries(today?.board ?? {});
  const trades = today?.trades ?? [];
  const input = "w-20 rounded border border-hud-line bg-hud-bg px-2 py-0.5 text-sm text-slate-200";

  return (
    <div className="flex flex-col gap-4 pb-72 md:pb-4">
      <div>
        <h2 className="text-lg tracking-[0.3em] text-hud-accent">TEST 101</h2>
        <p className="mt-1 max-w-3xl text-sm text-slate-400">
          The day-trade skills as one algorithm: US stocks, long only, opening-range breakouts, out by 3:55 New York. It trades a separate ${s?.start.toLocaleString() ?? "5,000"} paper
          account inside Ras and never sends an order to a broker. Most people who day trade lose money, and this is a test, not a recommendation.
        </p>
      </div>
      {err && <p className="text-sm text-hud-red">{err}</p>}
      {s?.halted && <p className="rounded border border-hud-amber/40 bg-hud-amber/5 px-3 py-2 text-sm text-hud-amber">Ras is halted, so TEST 101 takes no new entries.</p>}

      <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-9">
        {STEPS.map(([name, what], i) => (
          <div key={name} className="rounded border border-hud-line bg-hud-panel/60 p-2" title={what}>
            <div className="text-xs text-slate-500">{i + 1}</div>
            <div className="text-sm text-slate-200">{name}</div>
            <div className="text-xs text-slate-500">{what}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_2fr]">
        <Panel title="PAPER ACCOUNT">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Num label="Balance" value={s ? `$${s.balance.toFixed(2)}` : "—"} />
            <Num label="Since start" value={s ? usd(s.balance - s.start) : "—"} style={tone(s ? s.balance - s.start : 0)} />
            <Num label="Today closed" value={usd(today?.pnl)} style={tone(today?.pnl)} />
            <Num label="Today open" value={usd(today?.open_pnl)} style={tone(today?.open_pnl)} />
          </div>
          <p className="mt-3 text-xs text-slate-400">Now: {s?.phase ?? "…"}. Data: {s ? `${s.fetched.ok} of ${(s.config.universe as string[]).length} stocks from Yahoo (free, can lag)` : "…"}{s?.fetched.failed.length ? `, missing ${s.fetched.failed.join(", ")}` : ""}.</p>
          <div className="mt-3 border-t border-hud-line pt-3">
            <div className="mb-1 text-xs tracking-widest text-slate-500">LIVE RECORD SINCE {s?.since ?? "—"}</div>
            {s && <StatRow s={s.forward.stats} />}
            {s && s.forward.stats.trades > 0 && <p className="mt-1 text-xs text-slate-400">{s.forward.verdict}</p>}
            {s && s.days.some((d) => d.caught_up) && <p className="mt-1 text-xs text-slate-500">Days marked "caught up" ran afterwards from the same bars because the Mac was asleep. They count in the balance, not in the live record.</p>}
          </div>
        </Panel>

        <Panel title={`TODAY${today ? ` · ${today.date}` : ""}`}>
          {!today ? <p className="text-sm text-slate-400">No session yet today. It starts at 9:30 New York.</p> : <>
            <div className="flex flex-col gap-2">{trades.length ? trades.map((t) => <TradeCard key={`${t.symbol}${t.trigger_time}`} t={t} />)
              : <p className="text-sm text-slate-400">No breakouts yet.</p>}</div>
            <details className="mt-3 text-xs">
              <summary className="cursor-pointer text-slate-400">All {board.length} stocks</summary>
              <div className="mt-2 grid grid-cols-2 gap-1 sm:grid-cols-4">
                {board.map(([sym, b]) => (
                  <div key={sym} className="rounded border border-hud-line px-2 py-1">
                    <span className="text-slate-200">{sym}</span> <span className="text-slate-500">{b.status}{b.status === "waiting" && b.away_pct != null ? `, ${b.away_pct.toFixed(2)}% under` : ""}{b.status === "triggered" ? ` ${ny(b.time)}` : ""}</span>
                    {b.tags.length > 0 && <div className="text-slate-500">{b.tags.join(", ").replaceAll("_", " ")}</div>}
                  </div>
                ))}
              </div>
            </details>
          </>}
        </Panel>
      </div>

      <Panel title="BACKTEST · LAST 60 DAYS" right={<button disabled={s?.bt_running} onClick={backtest} className="rounded border border-hud-accent px-3 py-0.5 text-hud-accent disabled:opacity-40">{s?.bt_running ? "Running…" : bt ? "Run again" : "Run"}</button>}>
        {!bt ? <p className="text-sm text-slate-400">Not run yet.</p> : <div className="flex flex-col gap-3 text-sm">
          <p className="text-slate-300">{bt.range?.[0]} to {bt.range?.[1]}, {bt.main.stats.days} days, {bt.universe.length} stocks. Reading: <b>{bt.main.verdict}</b>.</p>
          <StatRow s={bt.main.stats} />
          {bt.main.curve.length > 1 && <LineChart points={bt.main.curve.map((p) => ({ time: p.time, value: p.equity }))} unit="$" height={200} />}
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-500"><th className="py-1">Same days, changed</th><th>Trades</th><th>Avg per trade</th><th>Total</th><th>Reading</th></tr></thead>
            <tbody>{Object.entries(bt.variants).map(([k, v]) => (
              <tr key={k} className="border-t border-hud-line text-slate-300"><td className="py-1">{VARIANT[k] ?? k}</td><td>{v.stats.trades}</td>
                <td style={tone(v.stats.avg_r)}>{r2(v.stats.avg_r)}</td><td style={tone(v.stats.total_usd)}>{usd(v.stats.total_usd)}</td><td>{v.verdict}</td></tr>))}
              <tr className="border-t border-hud-line text-slate-300"><td className="py-1">First half / second half</td><td>{bt.split.first.trades} / {bt.split.second.trades}</td>
                <td>{r2(bt.split.first.avg_r)} / {r2(bt.split.second.avg_r)}</td><td>{usd(bt.split.first.total_usd)} / {usd(bt.split.second.total_usd)}</td><td /></tr>
            </tbody>
          </table>
          <ul className="list-disc pl-4 text-xs text-slate-500">{bt.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>}
      </Panel>

      {c && <Panel title="RULES">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-slate-300">
          <label className="flex items-center gap-2">Max loss a trade $<input key={`l${c.max_loss_per_trade_usd}`} className={input} type="number" min={1} defaultValue={Number(c.max_loss_per_trade_usd)}
            onBlur={(e) => Number(e.target.value) !== c.max_loss_per_trade_usd && setCfg({ max_loss_per_trade_usd: Number(e.target.value) })} /></label>
          <label className="flex items-center gap-2">Daily loss $<input key={`d${c.max_daily_loss_usd}`} className={input} type="number" min={1} defaultValue={Number(c.max_daily_loss_usd)}
            onBlur={(e) => Number(e.target.value) !== c.max_daily_loss_usd && setCfg({ max_daily_loss_usd: Number(e.target.value) })} /></label>
          <label className="flex items-center gap-2">Trades a day<input key={`t${c.max_trades_per_day}`} className={input} type="number" min={1} max={10} defaultValue={Number(c.max_trades_per_day)}
            onBlur={(e) => Number(e.target.value) !== c.max_trades_per_day && setCfg({ max_trades_per_day: Number(e.target.value) })} /></label>
          <label className="flex items-center gap-2">Target R<input key={`r${c.target_r}`} className={input} type="number" min={1} max={5} step={0.5} defaultValue={Number(c.target_r)}
            onBlur={(e) => Number(e.target.value) !== c.target_r && setCfg({ target_r: Number(e.target.value) })} /></label>
          <label className="flex items-center gap-2"><input type="checkbox" disabled={busy} checked={!!c.tm_gate} onChange={(e) => setCfg({ tm_gate: e.target.checked })} />Skip when the time machine leans toward the stop</label>
          <label className="flex items-center gap-2"><input type="checkbox" disabled={busy} checked={!!c.scan_filter} onChange={(e) => setCfg({ scan_filter: e.target.checked })} />Only stocks the morning scan tagged</label>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Fixed from the skills: budget ${Number(c.budget).toLocaleString()}, {String(c.cash_buffer_pct)}% cash kept back, position at most {String(c.max_position_pct)}% of the budget, orders up to ${Number(c.max_order_usd).toLocaleString()},
          reward:risk at least {String(c.min_reward_risk)}, entries {String(c.no_entries_before)} to {String(c.no_entries_after)}, flat by {String(c.flat_by)} New York, slippage {String(c.slippage_pct)}% a fill.
          Stocks: {(c.universe as string[]).join(", ")}. Changes apply from the next bar.
        </p>
      </Panel>}
    </div>
  );
}
