import { useEffect, useRef, useState } from "react";
import { API, type Stats } from "../ws";
import { currentTheme } from "../theme";
import { GAIN, LOSS } from "./charts";
import { Panel } from "./Panels";

// TradingView's feeds for Ras's instruments (Capital.com's own feed isn't listed there; these track it closely)
const TV_SYMBOL: Record<string, string> = {
  GBP_USD: "OANDA:GBPUSD", EUR_USD: "OANDA:EURUSD", USD_JPY: "OANDA:USDJPY",
  XAU_USD: "OANDA:XAUUSD", XAG_USD: "OANDA:XAGUSD", WTI_USD: "TVC:USOIL",
};
const TV_INTERVAL: Record<string, string> = { MINUTE_15: "15", HOUR: "60", HOUR_4: "240" };
type TV = { widget: new (o: Record<string, unknown>) => unknown };
declare global { interface Window { TradingView?: TV } }

let tvScript: Promise<void> | null = null;
const loadTv = () => (tvScript ??= new Promise<void>((ok, fail) => {
  const s = document.createElement("script");
  s.src = "https://s3.tradingview.com/tv.js";
  s.async = true;
  s.onload = () => ok();
  s.onerror = () => { tvScript = null; fail(new Error("TradingView didn't load")); };
  document.head.appendChild(s);
}));

/** TradingView's own chart (their data, tools and indicators) for this market. */
export function TradingViewChart({ symbol, tf }: { symbol: string; tf: string }) {
  const id = useRef(`tv_${Math.random().toString(36).slice(2)}`).current;
  const [err, setErr] = useState("");
  useEffect(() => {
    let live = true;
    loadTv().then(() => {
      if (!live || !window.TradingView) return;
      document.getElementById(id)!.innerHTML = "";
      new window.TradingView.widget({
        container_id: id, symbol: TV_SYMBOL[symbol], interval: TV_INTERVAL[tf] ?? "15", autosize: true,
        timezone: "Asia/Bahrain", theme: currentTheme().startsWith("light") ? "light" : "dark", style: "1", locale: "en",
        allow_symbol_change: true, withdateranges: true, hide_side_toolbar: false, save_image: false,
      });
    }).catch((e) => setErr(e.message));
    return () => { live = false; };
  }, [symbol, tf, id]);
  return (
    <div className="relative h-[480px] rounded-lg border border-hud-line bg-hud-panel/80 p-1">
      <div id={id} className="h-full w-full" />
      {err && <div className="absolute inset-0 grid place-items-center text-sm text-hud-red">{err} — check the internet connection.</div>}
    </div>
  );
}

type Ev = { run: number; verdict: string; summary: Stats; this_market?: Stats | null; markets: string[]; days: number; params: string };
type Setup = { call: "BUY" | "SELL"; entry: number; stop: number; target: number | null; bars_ago: number; status: string; live: boolean; kind: string; tag: string };
type Rule = { key: string; name: string; group: string; params: string; evidence: Ev | null; trusted: boolean; basis: string | null; setup: Setup | null };
type Result = { symbol: string; tf: string; bar_time: number; price: number | null; trend: string | null; rules: Rule[];
  decision: { call: "BUY" | "SELL" | "NO TRADE"; why: string; rules: string[]; entry?: number; stop?: number; target?: number | null; kind?: string; status?: string } };

const r2 = (x?: number | null) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`);
const TFS: [string, string][] = [["MINUTE_15", "15m"], ["HOUR", "1h"], ["HOUR_4", "4h"]];
const callTone = (c?: string) => (c === "BUY" ? "text-hud-green border-hud-green/50" : c === "SELL" ? "text-hud-red border-hud-red/50" : "text-slate-400 border-hud-line");

/** Short-term read: every Strategies-tab rule on the live candles, and one decision from the rules that have a
 *  positive tested record. Analysis only — it doesn't place or follow trades. */
export function AnalysisPanel({ symbol, digits, tf, setTf }: { symbol: string; digits: number; tf: string; setTf: (t: string) => void }) {
  const [res, setRes] = useState<Result>();
  const [err, setErr] = useState("");
  useEffect(() => {
    let live = true;
    const load = () => fetch(`${API}/api/analysis/${symbol}?tf=${tf}`)
      .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail ?? r.statusText); return j as Result; })
      .then((j) => { if (live) { setRes(j); setErr(""); } }).catch((e) => live && setErr(e.message));
    load();
    const t = setInterval(load, 30_000);
    return () => { live = false; clearInterval(t); };
  }, [symbol, tf]);
  const f = (x?: number | null) => (x == null ? "—" : x.toFixed(digits));
  const d = res?.decision;

  return (
    <Panel title="SHORT-TERM ANALYSIS · STRATEGIES ON LIVE CANDLES" right={
      <span className="flex gap-1">{TFS.map(([k, n]) => (
        <button key={k} onClick={() => setTf(k)} className={`rounded border px-2 py-0.5 ${tf === k ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>{n}</button>
      ))}</span>}>
      {err ? <p className="text-sm text-slate-500">{err === "no candles yet" ? "Waiting for candles…" : err}</p> : !res || !d ? <p className="text-sm text-slate-500">Analysing…</p> : (
        <div className="flex flex-col gap-3">
          <div className={`rounded border p-3 ${callTone(d.call)}`}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-xl font-semibold tracking-widest">{d.call}</span>
              <span className="text-xs text-slate-400">daily trend {res.trend ?? "—"} · last candle {new Date(res.bar_time * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Bahrain", hour: "2-digit", minute: "2-digit" })}</span>
            </div>
            {d.call !== "NO TRADE" && (
              <div className="mt-2 grid grid-cols-3 gap-2 text-sm tabular-nums text-slate-200">
                <div><div className="text-xs text-slate-500">{d.kind === "limit" ? "Limit entry" : "Entry"}</div>{f(d.entry)}</div>
                <div><div className="text-xs text-slate-500">Stop</div><span className="text-hud-red">{f(d.stop)}</span></div>
                <div><div className="text-xs text-slate-500">Target</div><span className="text-hud-green">{d.target == null ? "exit rule" : f(d.target)}</span></div>
              </div>
            )}
            <p className="mt-2 text-xs text-slate-300">{d.why}</p>
          </div>
          <table className="w-full text-left text-xs tabular-nums">
            <thead className="text-slate-500"><tr><th>Rule</th><th>Now</th><th className="text-right">Tested here</th><th className="text-right">Tested overall</th><th className="pl-2">Counts?</th></tr></thead>
            <tbody>{res.rules.map((r) => (
              <tr key={r.key} className="border-t border-hud-line/60 align-top" title={r.params}>
                <td className="py-1 pr-2"><span className="text-slate-200">{r.name}</span> <span className="text-slate-500">{r.group}</span></td>
                <td className="pr-2">{r.setup ? (
                  <span className={r.setup.live ? "" : "text-slate-500"}>
                    <span className={r.setup.call === "BUY" ? "text-hud-green" : "text-hud-red"}>{r.setup.call}</span> {r.setup.status}
                    {r.setup.live && <> · {r.setup.kind === "limit" ? "limit " : ""}{f(r.setup.entry)} / SL {f(r.setup.stop)}{r.setup.target != null && <> / TP {f(r.setup.target)}</>}</>}
                    <span className="text-slate-500"> · {r.setup.bars_ago} bars ago</span>
                  </span>) : <span className="text-slate-500">no setup</span>}</td>
                <td className="text-right" style={{ color: (r.evidence?.this_market?.expectancy ?? 0) >= 0 ? GAIN : LOSS }}>
                  {r.evidence?.this_market ? `${r2(r.evidence.this_market.expectancy)} · ${r.evidence.this_market.trades}` : "—"}</td>
                <td className="text-right text-slate-400">{r.evidence ? `${r2(r.evidence.summary.expectancy)} · ${r.evidence.verdict}` : "not run"}</td>
                <td className={`pl-2 ${r.trusted ? "text-hud-green" : "text-slate-500"}`}>{r.basis ?? "no"}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="text-xs text-slate-500">
            Analysis only: nothing here opens a trade. A rule counts when its walk-forward test passed, or when it made
            ≥ +0.10R over 100+ tested trades on this market ("this market only" — weaker evidence). Decisions also have to
            go with the daily trend. Run more tests in the Strategies tab to change what counts. "Tested here" = average R · trades.
          </p>
        </div>
      )}
    </Panel>
  );
}

const NAMES: Record<string, string> = { GBP_USD: "GBP/USD", EUR_USD: "EUR/USD", USD_JPY: "USD/JPY", XAU_USD: "XAU/USD", XAG_USD: "XAG/USD", WTI_USD: "WTI/USD" };

/** Station overview: the short-term decision for every market on one timeframe. Click a row to open its room. */
export function AnalysisOverview({ symbols, onOpen }: { symbols: string[]; onOpen: (s: string) => void }) {
  const [tf, setTf] = useState(() => { try { return localStorage.getItem("ras.analysis.tf") ?? "HOUR"; } catch { return "HOUR"; } });
  const [rows, setRows] = useState<Record<string, Result | string>>({});
  const pick = (t: string) => { setTf(t); try { localStorage.setItem("ras.analysis.tf", t); } catch { /* storage blocked */ } };
  useEffect(() => {
    let live = true;
    const load = () => Promise.all(symbols.map((s) => fetch(`${API}/api/analysis/${s}?tf=${tf}`)
      .then(async (r) => { const j = await r.json(); return [s, r.ok ? (j as Result) : String(j.detail ?? r.statusText)] as const; })
      .catch((e) => [s, String(e.message)] as const)))
      .then((all) => { if (live) setRows(Object.fromEntries(all)); });
    load();
    const t = setInterval(load, 30_000);
    return () => { live = false; clearInterval(t); };
  }, [symbols.join(), tf]);

  return (
    <Panel title="SHORT-TERM ANALYSIS · ALL MARKETS" right={
      <span className="no-drag flex gap-1">{TFS.map(([k, n]) => (
        <button key={k} onClick={() => pick(k)} className={`rounded border px-2 py-0.5 ${tf === k ? "border-hud-accent text-hud-accent" : "border-hud-line text-slate-500"}`}>{n}</button>
      ))}</span>}>
      <table className="w-full text-left text-xs tabular-nums">
        <thead className="whitespace-nowrap text-slate-500"><tr><th className="pr-2">Market</th><th className="pr-2">Daily trend</th><th className="pr-2">Decision</th><th className="pr-2">Rules that count</th><th>Why</th></tr></thead>
        <tbody>{symbols.map((s) => {
          const r = rows[s];
          const res = typeof r === "object" ? r : undefined;
          const counted = res?.rules.filter((x) => x.trusted) ?? [];
          return (
            <tr key={s} onClick={() => onOpen(s)} className="cursor-pointer border-t border-hud-line/60 align-top hover:bg-hud-accent/5">
              <td className="whitespace-nowrap py-1 pr-2 text-slate-200">{NAMES[s] ?? s}</td>
              <td className={`pr-2 ${res?.trend === "BUY" ? "text-hud-green" : res?.trend === "SELL" ? "text-hud-red" : "text-slate-500"}`}>{res?.trend ?? "—"}</td>
              <td className="whitespace-nowrap pr-2"><span className={`rounded border px-1.5 py-0.5 ${callTone(res?.decision.call)}`}>{res ? res.decision.call : r ? "—" : "…"}</span></td>
              <td className="pr-2 text-slate-300">{counted.length ? counted.map((x) => (
                <span key={x.key} className="mr-2 whitespace-nowrap">{x.name}{x.setup?.live &&
                  <span className={x.setup.call === "BUY" ? "text-hud-green" : "text-hud-red"}> {x.setup.call}</span>}</span>)) : <span className="text-slate-500">none</span>}</td>
              <td className="w-full max-w-0 truncate text-slate-400" title={res?.decision.why}>{res ? res.decision.why : r === "no candles yet" ? "Waiting for candles…" : typeof r === "string" ? r : ""}</td>
            </tr>
          );
        })}</tbody>
      </table>
      <p className="mt-2 text-xs text-slate-500">Analysis only — nothing here opens a trade. Click a market for every rule's setup and its tested record.</p>
    </Panel>
  );
}
