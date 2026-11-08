import { useEffect, useState } from "react";
import { API, type Stats, type StrategyEvent } from "../ws";
import { GAIN, LOSS } from "./charts";
import { META } from "./InstrumentTile";
import { Panel } from "./Panels";

type Test = {
  run: number; created: number; range?: [number, number]; symbols: string[]; days: number; verdict: string; summary: Stats; positive_folds: number; folds: number;
  checks?: { passed: boolean; deflated_sharpe: number | null; overfit_probability: number | null; reality_check_p: number | null } | null;
  account?: { return_pct: number; max_dd_pct: number; taken: number } | null;
  cost_stress?: { x2: number | null; x3: number | null } | null;
  random_entries?: { expectancy: number; top: number; beats: boolean } | null;
  by_symbol?: Record<string, Stats>;
  options?: { trades: number; win_rate: number; avg_pct: number; median_pct: number; without_best_10: number; stake: number; profit: number;
    pricier: Record<string, number>; by_symbol: Record<string, { avg_pct: number; trades: number }>; model: string } | null;
  money?: { key: string; name: string; who: string; final: number; return_pct: number; max_dd_pct: number; taken: number; paused: number }[] | null;
};
type Row = {
  key: string; name: string; rules: string; tf: string; days: number; markets: string[]; evidence: "strong" | "medium" | "weak"; method?: string; trader?: string; book?: string; chapter?: string;
  claim: string; sources: [string, string][]; test: Test | null;
};
type Page = { strategies: Row[]; left_out: { name: string; why: string; url: string }[] };
type Live = { decision: { call: string; why: string } };

const TF_NAME: Record<string, string> = { MINUTE_3: "3 minute", MINUTE_15: "15 minute", HOUR: "1 hour", HOUR_4: "4 hour", DAY: "Daily" };
const EVIDENCE = { strong: "text-hud-green border-hud-green/50", medium: "text-hud-amber border-hud-amber/50", weak: "text-hud-red border-hud-red/50" };
const CHIP: Record<string, string> = {
  "survives checks": "border-hud-green/50 text-hud-green", "passes screen": "border-hud-accent/50 text-hud-accent", marginal: "border-hud-amber/50 text-hud-amber",
  "doesn't work": "border-hud-red/50 text-hud-red", "too few trades": "border-hud-line text-slate-400",
};
const signed = (x: number | null | undefined, d = 2) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(d)}`);
const tone = (x: number | null | undefined) => ({ color: (x ?? 0) >= 0 ? GAIN : LOSS });

function Num({ label, value, style }: { label: string; value: string; style?: React.CSSProperties }) {
  return <div><div className="text-xs text-slate-500">{label}</div><div className="tabular-nums text-slate-200" style={style}>{value}</div></div>;
}

/** What the rule says on the live candles right now. Display only: nothing here trades. */
function Now({ row }: { row: Row }) {
  const [live, setLive] = useState<Record<string, Live | null>>({});
  useEffect(() => {
    let gone = false;
    const load = () => row.markets.forEach((m) => fetch(`${API}/api/strategy-live/${row.key}?symbol=${m}&tf=${row.tf}`)
      .then((r) => (r.ok ? r.json() : null)).then((d) => { if (!gone) setLive((old) => ({ ...old, [m]: d })); }).catch(() => {}));
    load();
    const t = setInterval(load, 60_000);
    return () => { gone = true; clearInterval(t); };
  }, [row.key, row.tf, row.markets.join()]);
  return (
    <div className="flex flex-wrap gap-2 text-xs">
      {row.markets.map((m) => {
        const c = live[m]?.decision.call;
        return (
          <span key={m} title={live[m]?.decision.why} className="rounded border border-hud-line px-2 py-0.5 text-slate-300">
            {META[m]?.name ?? m}{" "}
            <b style={c === "BUY" ? { color: GAIN } : c === "SELL" ? { color: LOSS } : undefined}>{c ?? "no candles"}</b>
          </span>
        );
      })}
    </div>
  );
}

function Card({ row, rank, busy, onTest }: { row: Row; rank: number | null; busy: boolean; onTest: () => void }) {
  const t = row.test;
  const s = t?.summary;
  const [open, setOpen] = useState(false);
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-hud-line bg-hud-panel/60 p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-bold text-slate-100">{rank != null && <span className="mr-2 text-slate-500">#{rank}</span>}{row.name}</h3>
          <p className="text-xs text-slate-500">{TF_NAME[row.tf] ?? row.tf} · {row.markets.map((m) => META[m]?.name ?? m).join(", ")}{row.chapter && ` · ${row.chapter}`}</p>
        </div>
        <div className="flex flex-wrap gap-1 text-xs">
          <span title={row.method ? "How much of the trader's real method is known, not how the rule did" : "How strong the published source is, not how the rule did"}
            className={`rounded border px-2 py-0.5 ${EVIDENCE[row.evidence]}`}>{row.method ? `method ${row.method}` : `${row.evidence} source`}</span>
          <span className={`rounded border px-2 py-0.5 ${t ? CHIP[t.verdict] ?? "border-hud-line text-slate-400" : "border-hud-line text-slate-400"}`}>{t ? t.verdict : "not tested yet"}</span>
        </div>
      </div>
      {t && s ? (
        <div className="grid grid-cols-3 gap-3 border-y border-hud-line py-2 sm:grid-cols-6">
          <Num label="Avg per trade" value={`${signed(s.expectancy)}R`} style={tone(s.expectancy)} />
          <Num label="Trades" value={String(s.trades)} />
          <Num label="Profit factor" value={s.profit_factor == null ? "—" : s.profit_factor.toFixed(2)} />
          <Num label="Folds up" value={`${t.positive_folds} of ${t.folds}`} />
          <Num label="On $100" value={t.account ? `${signed(t.account.return_pct, 1)}%` : "—"} style={tone(t.account?.return_pct)} />
          <Num label="Worst drop" value={t.account ? `${t.account.max_dd_pct.toFixed(1)}%` : "—"} />
        </div>
      ) : <p className="border-y border-hud-line py-2 text-xs text-slate-400">No result yet, so there is nothing to rank it on.</p>}
      {t?.options && (
        <div className="flex flex-col gap-2 border-b border-hud-line pb-2">
          <div className="text-xs tracking-widest text-slate-500">AS OPTIONS · MODELLED, NOT REAL QUOTES</div>
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
            <Num label="Avg per trade" value={`${signed(t.options.avg_pct, 1)}%`} style={tone(t.options.avg_pct)} />
            <Num label="Median" value={`${signed(t.options.median_pct, 1)}%`} style={tone(t.options.median_pct)} />
            <Num label="Winners" value={`${t.options.win_rate.toFixed(0)}%`} />
            <Num label="Without best 10" value={`${signed(t.options.without_best_10, 1)}%`} style={tone(t.options.without_best_10)} />
            <Num label={`$${t.options.stake} a trade`} value={`${t.options.profit >= 0 ? "+" : "−"}$${Math.abs(t.options.profit).toFixed(0)}`} style={tone(t.options.profit)} />
            <Num label="Options 40% dearer" value={`${signed(t.options.pricier["x1.4"], 1)}%`} style={tone(t.options.pricier["x1.4"])} />
          </div>
          <p className="text-xs text-slate-500">
            {Object.entries(t.options.by_symbol).map(([m, v]) => `${META[m]?.name ?? m} ${signed(v.avg_pct, 1)}%`).join(" · ")}. Each trade buys an at-the-money
            call or put expiring that Friday and sells it at the rule's exit. {t.options.model}.
          </p>
        </div>
      )}
      <div><div className="mb-1 text-xs tracking-widest text-slate-500">{row.book ? "WHAT THE BOOK TEACHES" : row.method ? "KNOWN FOR, AND WHAT THIS RULE COPIES" : "THE SOURCE CLAIMS, NOT PROVEN HERE"}</div><p className="text-slate-300">{row.claim}</p></div>
      {t?.money && t.money.length > 0 && (
        <div>
          <div className="mb-1 text-xs tracking-widest text-slate-500">MONEY RULES FROM THE BOOKS · SAME TRADES, $100 ACCOUNT</div>
          <table className="w-full text-xs tabular-nums">
            <thead className="text-slate-500"><tr><th className="text-left font-normal">Rule</th><th className="text-right font-normal">End</th><th className="text-right font-normal">Return</th><th className="text-right font-normal">Worst drop</th></tr></thead>
            <tbody>{t.money.map((m) => (
              <tr key={m.key} className="border-t border-hud-line" title={m.who}>
                <td className="py-0.5 text-slate-300">{m.name}{m.paused > 0 && <span className="text-slate-500"> · {m.paused} skipped while stopped</span>}</td>
                <td className="text-right text-slate-300">${m.final.toFixed(2)}</td>
                <td className="text-right" style={tone(m.return_pct)}>{signed(m.return_pct, 1)}%</td>
                <td className="text-right text-slate-300">{m.max_dd_pct.toFixed(1)}%</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="mt-1 text-xs text-slate-500">Money rules can't turn losing trades into winners; they decide how much a losing streak costs. Hover a rule for who taught it.</p>
        </div>
      )}
      <div><div className="mb-1 text-xs tracking-widest text-slate-500">SAYS NOW · NOT TRADING</div><Now row={row} /></div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button className="text-xs text-slate-400 underline hover:text-slate-200" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Rules and sources"}</button>
        <button disabled={busy} onClick={onTest} className="rounded border border-hud-accent px-3 py-0.5 text-xs text-hud-accent disabled:opacity-40">{t ? "Test again" : "Test it"}</button>
      </div>
      {open && (
        <div className="flex flex-col gap-2 text-xs text-slate-400">
          <p>{row.rules}</p>
          <ul className="flex flex-col gap-1">{row.sources.map(([name, url]) => <li key={url}><a className="underline hover:text-hud-accent" href={url} target="_blank" rel="noreferrer">{name}</a></li>)}</ul>
          {t && <p>Tested {t.days} days of Capital.com prices with spread, slippage and financing, on {new Date(t.created).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}.
            {t.random_entries && ` Random entries on the same trades: ${signed(t.random_entries.expectancy)}R; ${t.random_entries.beats ? "the rule beat them" : "the rule did not beat them"}.`}
            {t.cost_stress?.x2 != null && ` At double costs: ${signed(t.cost_stress.x2)}R.`}
            {t.checks && ` Luck checks ${t.checks.passed ? "passed" : "not passed"}.`}</p>}
        </div>
      )}
    </section>
  );
}

export function Ras11Page({ sbt }: { sbt?: StrategyEvent }) {
  return <ResearchPage sbt={sbt} api="ras11" title="RAS 1.1"
    intro="Forex and metal strategies from the research, ranked by Ras's own test after costs. Published and social media numbers are shown as claims only." />;
}

export function BooksPage({ sbt }: { sbt?: StrategyEvent }) {
  return <ResearchPage sbt={sbt} api="books" title="BOOKS"
    intro="What three trading books teach, as fixed rules tested on gold and forex with Ras's walk-forward test after costs: Elder's Trading for a Living (the full book), Murphy's Technical Analysis of the Financial Markets and Schwager's Market Wizards (both as summaries). Market Wizards has no entry rules, so its money rules are tested on every rule's trades." />;
}

function ResearchPage({ sbt, api, title, intro }: { sbt?: StrategyEvent; api: string; title: string; intro: string }) {
  const [page, setPage] = useState<Page>();
  const [err, setErr] = useState("");
  const load = () => fetch(`${API}/api/${api}`).then((r) => r.json()).then(setPage).catch(() => setErr("Can't reach Ras."));
  useEffect(() => { load(); }, [api]);
  useEffect(() => { if (sbt && sbt.status !== "running") load(); }, [sbt?.status, sbt?.id]);
  const running = sbt?.status === "running";
  const test = (key: string) => {
    setErr("");
    fetch(`${API}/api/${api}/test/${key}`, { method: "POST" }).then(async (r) => { if (!r.ok) setErr((await r.json()).detail?.toString() ?? "That test didn't start."); });
  };
  const rows = page?.strategies ?? [];
  const tested = rows.filter((r) => r.test);
  const proven = tested.filter((r) => r.test!.verdict === "survives checks");

  return (
    <div className="flex flex-col gap-4 pb-72 md:pb-4">
      <div>
        <h2 className="text-lg tracking-[0.3em] text-hud-accent">{title}</h2>
        <p className="mt-1 max-w-3xl text-sm text-slate-400">
          {intro}
          {" "}{!tested.length ? "None tested yet." : proven.length
            ? `${proven.length} of ${tested.length} tested survive every check so far.`
            : `None of the ${tested.length} tested has survived every check yet, so none is proven profitable.`}
        </p>
      </div>
      {running && <p className="text-sm text-hud-accent">{sbt?.msg ?? "Testing"}… {Math.round((sbt?.progress ?? 0) * 100)}%</p>}
      {err && <p className="text-sm text-hud-red">{err}</p>}
      <div className="grid gap-4 xl:grid-cols-2">
        {rows.map((r) => <Card key={r.key} row={r} busy={running} onTest={() => test(r.key)} rank={r.test ? tested.indexOf(r) + 1 : null} />)}
      </div>
      <Panel title="LOOKED AT AND LEFT OUT">
        <ul className="flex flex-col gap-2 text-sm">
          {page?.left_out.map((x) => <li key={x.name}><a className="text-slate-200 underline hover:text-hud-accent" href={x.url} target="_blank" rel="noreferrer">{x.name}</a><span className="text-slate-400"> · {x.why}</span></li>)}
        </ul>
      </Panel>
    </div>
  );
}
