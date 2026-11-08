import { useEffect, useState } from "react";

// Mirrors the backend's bus events (backend/jarvis/feeds/*.py)
export type Price = { type: "price"; symbol: string; bid: number; ask: number; ts: number };
export type Status = { type: "status"; feed: string; state: string; detail: string };
export type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };
export type Levels = { entry: number; entry_low: number; entry_high: number; stop: number; tp1: number; tp2: number };
export type Vote = { agent: string; direction: "BUY" | "SELL" | "HOLD"; confidence: number; reasons: string[]; invalidation: number | null; watch: string };
// Time Machine (backend/jarvis/timemachine.py): odds of TP1 first / stop first / still open at expiry, R after costs
export type Odds = { pending?: boolean; error?: string; replays?: number; seed?: number; bars?: number; bar_s?: number;
  p_tp1?: number; p_stop?: number; p_neither?: number; avg_r?: number; r_tp1?: number; cost_r?: number; history_days?: number;
  breakeven?: number | null; share?: number | null; lean?: "good" | "bad" | "flat"; verdict?: string; notes?: string[] };
// "call" = the commander's latest decision (incl. HOLD); "signal" = a stored BUY/SELL with tracked outcome
export type Mode = "intraday" | "scalp" | "crypto";
export type Decision = {
  symbol: string; mode: Mode; priority: number; call: "BUY" | "SELL" | "HOLD"; lean: string; confidence: number; votes: Vote[];
  risk: { veto: boolean; reasons: string[]; warnings: string[] }; reasons: string[]; hold_why: string[]; watch: string[];
  warnings: string[]; created: number; levels?: Levels; invalidation?: number; hold_time?: string; rr?: number; expires?: number;
  // scalp / crypto
  session?: string | null; regime?: string; strategies?: string[]; say?: string; entry_until?: number; time_stop_at?: number;
  actionable?: boolean; entry_note?: string; market?: string; display?: string; digits?: number;
  spread?: number; odds?: Odds;
};
export type Signal = Decision & {
  id: number; status: "active" | "hit_tp" | "hit_sl" | "expired" | "invalidated" | "time_stop" | "killed"; closed_at: number | null; result_r: number | null;
};
export type BoardRow = {
  key: string; symbol: string; base: string; market: string; price: number; digits: number; change_1h: number | null;
  funding: number | null; atr_pct?: number; spread_pct?: number | null; regime?: string; h1_trend?: number; call: Decision["call"];
  score: number; direction?: number; strategies: string[]; rr?: number; filtered?: string; decision?: Decision;
};
export type Position = {
  division: "fx" | "crypto"; signal_id: number; symbol: string; mode: Mode; side: number; units: number; entry: number;
  stop: number; tp: number; risk: number; opened: number; fees: number; closed: number | null; pnl: number | null;
  exit?: number; reason?: string; mark?: number; upnl?: number; r?: number;
};
export type PaperState = {
  accounts: Record<string, { currency: string; balance: number; equity: number; start: number; since?: number }>; open: Position[]; recent: Position[];
};
export type Modes = Record<string, { intraday: boolean; scalp: boolean; trend?: boolean }>;
export type CalEvent = { title: string; country: string; time: number; impact: string; forecast: string; previous: string };
export type Headline = { id: string; title: string; source: string; time: number };
export type NewsView = {
  source: "claude" | "keywords"; updated: number; risk_sentiment: "risk_on" | "risk_off" | "neutral"; geopolitical_alert: string;
  instruments: { symbol: string; bias: string; confidence: number; reason: string; headlines: string[] }[];
};
export type Briefing = { id: number; kind: "morning" | "eod"; title: string; created: number; text: string };
export type Yields = Record<string, { date: string; value: number }>;
export type Stats = {
  trades: number; wins: number; win_rate: number | null; profit_factor: number | null; avg_r: number | null;
  expectancy: number | null; total_r: number; max_dd_r: number; avg_win: number | null; avg_loss: number | null;
};
export type Report = {
  summary: Stats; equity: { time: number; r: number; pnl: number }[]; by_symbol: Record<string, Stats>; by_mode: Record<string, Stats>;
  by_strategy: Record<string, Stats>; by_session: Record<string, Stats>; by_hour: Record<string, Stats>;
  best_hours: string[]; worst_hours: string[]; calibration: (Stats & { bucket: string; expected: number })[];
  agents: Record<string, { graded: number; right: number; with_call: number; accuracy: number }>;
};
export type Learning = {
  weights: Record<string, number>; defaults: Record<string, number>; frozen: boolean; min_samples: number;
  agents: Report["agents"]; strategies: Record<string, number>; log: { ts: number; agent: string; old: number; new: number; reason: string }[];
};
export type BacktestEvent = { id: number; status: "running" | "done" | "failed"; msg: string; progress: number };
export type StrategyEvent = BacktestEvent;
export type Execution = {
  id: number; signal_id: number; symbol: string; side?: string; qty?: number; stop?: number; tp?: number; risk_pct?: number;
  status: "submitting" | "open" | "closed" | "failed" | "rejected"; reason?: string; opened: number; closed?: number;
  pnl?: number | null; entry_px?: number; orders?: Record<string, string>; environment: string; modified?: boolean;
};
export type ExecutionState = {
  enabled: boolean; environment: string; has_keys: boolean; balance: number | null; error: string; confirm_phrase: string;
  risk_pct: number; max_notional_usdt: number; leverage: number; trailing_pct: number | null; open: Execution[]; recent: Execution[];
};
export type HaltState = { active: boolean; since: number; why: string };
export type MacroSeries = { dxy: [number, number][]; yields: Record<string, [number, number][]> };
export type Event =
  | ({ type: "macro_series" } & MacroSeries)
  | { type: "kill"; created: number; source: string; paper_closed: number; exchange: { cancelled: number; closed: string[]; errors: string[] } }
  | ({ type: "execution" } & Execution)
  | ({ type: "execution_state" } & ExecutionState)
  | ({ type: "halt" } & HaltState)
  | { type: "test101"; updated: number }
  | { type: "brain"; built_at: number }
  | { type: "brain_lesson"; text: string }
  | { type: "brain_learn"; events: { time: number; kind: string; text: string; lesson?: string; symbol?: string }[] }
  | { type: "test101_alert"; created: number; kind: "entry" | "exit"; symbol: string }
  | { type: "voice"; state: "idle" | "listening" | "thinking" | "speaking" | "alert"; tone: "normal" | "warning" | "breach";
      level: number; ready: boolean; error: string; heard: string; reply: string; min_priority: number; muted_until: number; wake_word: string }
  | ({ type: "backtest" } & BacktestEvent)
  | ({ type: "strategy_bt" } & StrategyEvent)
  | ({ type: "learning" } & Learning)
  | (Briefing & { type: "review"; source: string })
  | { type: "board"; updated: number; rows: BoardRow[] }
  | ({ type: "paper" } & PaperState)
  | { type: "modes"; modes: Modes }
  | { type: "cprices"; quotes: Record<string, [number, number]> }
  | { type: "calendar"; events: CalEvent[] }
  | { type: "news"; headlines: Headline[]; view: NewsView | Record<string, never> }
  | (Briefing & { type: "briefing" })
  | ({ type: "yields" } & Yields)
  | Price
  | Status
  | (Decision & { type: "call" })
  | (Signal & { type: "signal" })
  | { type: "candles"; symbol: string; tf: string; candles: Candle[] }
  | { type: "candle"; symbol: string; tf: string; candle: Candle };

// on a phone the backend is the Mac's, at the address saved on the Connect screen; on the Mac it runs alongside the app
export const PHONE = /iPhone|iPad|Android/.test(navigator.userAgent);
const SERVER_KEY = "ras.server";
function savedServer(): string {
  try { return localStorage.getItem(SERVER_KEY) ?? ""; } catch { return ""; }
}
export function saveServer(address: string): void {
  const a = address.trim().replace(/\/+$/, "");
  const full = !a || /^https?:\/\//.test(a) ? a : `https://${a}`;
  try { localStorage.setItem(SERVER_KEY, full); } catch { /* storage blocked */ }
  location.reload();
}
export const API = PHONE ? savedServer() : "http://127.0.0.1:18765";
const URL = `${API.replace(/^http/, "ws")}/ws`;
const listeners = new Set<(e: Event) => void>();
let connected = false;
const connListeners = new Set<(c: boolean) => void>();
// the backend sends its snapshot once, on connect: hold it until the UI is listening, or a slow first render loses it
let held: Event[] | null = [];
let releasing = false;
const deliver = (e: Event) => listeners.forEach((l) => l(e));

function connect() {
  const ws = new WebSocket(URL);
  const setConn = (c: boolean) => { connected = c; connListeners.forEach((l) => l(c)); };
  ws.onopen = () => setConn(true);
  ws.onmessage = (m) => { const e = JSON.parse(m.data) as Event; if (held) held.push(e); else deliver(e); };
  ws.onclose = () => { setConn(false); setTimeout(connect, 2000); };
}
if (API) connect();

export function onEvent(fn: (e: Event) => void): () => void {
  listeners.add(fn);
  if (held && !releasing) {
    releasing = true;
    setTimeout(() => { const first = held ?? []; held = null; first.forEach(deliver); }, 0); // after every effect of this render subscribed
  }
  return () => { listeners.delete(fn); };
}

export function useFeed() {
  const [prices, setPrices] = useState<Record<string, Price>>({});
  const [status, setStatus] = useState<Record<string, Status>>({});
  const [calls, setCalls] = useState<Record<string, Decision>>({});
  const [signals, setSignals] = useState<Record<string, Signal>>({});
  const [calendar, setCalendar] = useState<CalEvent[]>([]);
  const [news, setNews] = useState<{ headlines: Headline[]; view?: NewsView }>({ headlines: [] });
  const [briefing, setBriefing] = useState<Briefing>();
  const [yields, setYields] = useState<Yields>({});
  const [board, setBoard] = useState<{ updated: number; rows: BoardRow[] }>({ updated: 0, rows: [] });
  const [paper, setPaper] = useState<PaperState>();
  const [modes, setModes] = useState<Modes>({});
  const [cquotes, setCquotes] = useState<Record<string, [number, number]>>({});
  const [bt, setBt] = useState<BacktestEvent>();
  const [sbt, setSbt] = useState<StrategyEvent>();
  const [learning, setLearning] = useState<Learning>();
  const [review, setReview] = useState<Briefing & { source: string }>();
  const [execs, setExecs] = useState<Record<number, Execution>>({});
  const [execState, setExecState] = useState<ExecutionState>();
  const [haltState, setHalt] = useState<HaltState>({ active: false, since: 0, why: "" });
  const [macro, setMacro] = useState<MacroSeries>({ dxy: [], yields: {} });
  const [online, setOnline] = useState(connected);
  useEffect(() => {
    connListeners.add(setOnline);
    setOnline(connected); // socket may have opened before this effect ran
    const off = onEvent((e) => {
      if (e.type === "price") setPrices((p) => ({ ...p, [e.symbol]: e }));
      if (e.type === "status") setStatus((s) => ({ ...s, [e.feed]: e }));
      // keyed by symbol:mode — intraday and scalp run side by side
      if (e.type === "call") setCalls((c) => ({ ...c, [`${e.symbol}:${e.mode}`]: e }));
      if (e.type === "signal") setSignals((s) => ({ ...s, [`${e.symbol}:${e.mode}`]: e }));
      if (e.type === "board") setBoard(e);
      if (e.type === "paper") setPaper(e);
      if (e.type === "modes") setModes(e.modes);
      if (e.type === "cprices") setCquotes((q) => ({ ...q, ...e.quotes }));
      if (e.type === "backtest") setBt(e);
      if (e.type === "strategy_bt") setSbt(e);
      if (e.type === "learning") setLearning(e);
      if (e.type === "review") setReview(e);
      if (e.type === "execution") setExecs((x) => ({ ...x, [e.signal_id]: e }));
      if (e.type === "execution_state") setExecState(e);
      if (e.type === "halt") setHalt(e);
      if (e.type === "macro_series") setMacro(e);
      if (e.type === "calendar") setCalendar(e.events);
      if (e.type === "news") setNews({ headlines: e.headlines, view: "source" in e.view ? (e.view as NewsView) : undefined });
      if (e.type === "briefing") setBriefing(e);
      if (e.type === "yields") { const { type: _, ...y } = e; setYields(y as Yields); }
    });
    return () => { off(); connListeners.delete(setOnline); };
  }, []);
  return { prices, status, online, calls, signals, calendar, news, briefing, yields, board, paper, modes, cquotes, bt, sbt, learning, review, execs, execState, haltState, macro };
}
