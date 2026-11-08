import { useEffect, useState } from "react";
import { API, onEvent } from "../ws";
import { GAIN, LOSS } from "./charts";
import { META } from "./InstrumentTile";
import { Panel } from "./Panels";
import { BrainMapSection } from "./BrainMap";

type Lesson = {
  id: string; text: string; status: "active" | "watching" | "rejected"; off: boolean; saves_r: number | null;
  find: { trades: number; avg_r: number; win_rate: number; base_r: number };
  check: { trades: number; avg_r: number | null; rest_r: number | null };
};
type Model = {
  earned: boolean; why: string; auc?: number | null; z?: number; skill?: number; trained_on?: number; judged_on?: number; skip_below?: number;
  buckets?: { predicted: number; actual: number; avg_r: number; trades: number }[]; skipped_r?: number | null; kept_r?: number | null; skipped?: number;
  top?: { condition: string; weight: number }[];
};
type Mistake = { time: number; source: string; symbol: string; mode: string; r: number; note: string; lessons: string[] };
type Skip = { time: number; symbol: string; mode: string; call: string; why: string };
type State = {
  built_at: number; settings: { enabled: boolean; use_model: boolean; off: string[] }; skips: Skip[];
  memory: { total: number; live: number; backtest: number; avg_r: number | null; win_rate: number | null };
  lessons: Lesson[]; model: Model; recent_mistakes: Mistake[];
};

const STATUS = { active: "border-hud-green/50 text-hud-green", watching: "border-hud-amber/50 text-hud-amber", rejected: "border-hud-line text-slate-500" };
const STATUS_NAME = { active: "acting", watching: "waiting for more trades", rejected: "rejected: didn't hold" };
const r2 = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`);
const tone = (x: number | null | undefined) => ({ color: (x ?? 0) >= 0 ? GAIN : LOSS });
const when = (ms: number) => new Date(ms).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

function Switch({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`rounded border px-3 py-0.5 text-xs ${on ? "border-hud-green/60 text-hud-green" : "border-hud-line text-slate-400"}`}>
      {label}: {on ? "on" : "off"}
    </button>
  );
}

export function BrainPage() {
  const [s, setS] = useState<State>();
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => fetch(`${API}/api/brain`).then((r) => r.json()).then(setS).catch(() => setErr("Can't reach Ras."));
  useEffect(() => { load(); return onEvent((e) => { if (e.type === "brain" || e.type === "brain_lesson") load(); }); }, []);
  const post = (path: string, body?: object) => fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined })
    .then((r) => r.json()).then(setS).catch(() => setErr("That didn't go through."));
  const rebuild = () => { setBusy(true); post("/api/brain/rebuild").finally(() => setBusy(false)); };

  if (!s) return <p className="text-sm text-slate-400">{err || "Loading the brain…"}</p>;
  const active = s.lessons.filter((x) => x.status === "active");
  const m = s.model;
  return (
    <div className="flex flex-col gap-4 pb-72 md:pb-4">
      <div>
        <h2 className="text-lg tracking-[0.3em] text-hud-accent">BRAIN</h2>
        <p className="mt-1 max-w-3xl text-sm text-slate-400">
          Ras remembers every trade and looks for mistakes it keeps repeating. A lesson is found on older trades and only acts if it still
          holds on newer trades it never saw. The win-chance model only gets a say if it beats plain luck on newer trades. It acts on paper
          trading only: a skipped signal is written to the journal with the reason. It cannot make a rule with no edge profitable; it can stop
          repeat mistakes.
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Switch on={s.settings.enabled} label="Brain" onClick={() => post("/api/brain/settings", { enabled: !s.settings.enabled })} />
          <Switch on={s.settings.use_model} label="Win-chance model" onClick={() => post("/api/brain/settings", { use_model: !s.settings.use_model })} />
          <button disabled={busy} onClick={rebuild} className="rounded border border-hud-accent px-3 py-0.5 text-xs text-hud-accent disabled:opacity-40">
            {busy ? "Relearning…" : "Relearn now"}
          </button>
          <span className="text-xs text-slate-500">{s.built_at ? `Last learnt ${when(s.built_at)} · relearns every hour` : "Not built yet"}</span>
        </div>
        {err && <p className="mt-1 text-sm text-hud-red">{err}</p>}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Panel title="MEMORY">
          <p className="text-2xl tabular-nums text-slate-100">{s.memory.total.toLocaleString()}</p>
          <p className="text-xs text-slate-400">{s.memory.live} live paper trades · {s.memory.backtest.toLocaleString()} from Backtest-tab replays of Ras's own signals</p>
          <p className="mt-2 text-sm text-slate-300">Average <b style={tone(s.memory.avg_r)}>{r2(s.memory.avg_r)}</b> a trade · {s.memory.win_rate ?? "—"}% winners</p>
        </Panel>
        <Panel title="LESSONS">
          <p className="text-2xl tabular-nums text-slate-100">{active.length} <span className="text-sm text-slate-400">acting</span></p>
          <p className="text-xs text-slate-400">{s.lessons.filter((x) => x.status === "watching").length} waiting for more trades · {s.lessons.filter((x) => x.status === "rejected").length} rejected because they didn't hold</p>
        </Panel>
        <Panel title="WIN-CHANCE MODEL">
          <p className={`text-2xl ${m.earned ? "text-hud-green" : "text-slate-400"}`}>{m.earned ? "Has a say" : "No say yet"}</p>
          <p className="text-xs text-slate-400">{m.why.charAt(0).toUpperCase() + m.why.slice(1)}.{m.auc != null && ` Ranking score ${m.auc.toFixed(2)} (0.5 is a coin flip), on ${m.judged_on} newer trades.`}</p>
        </Panel>
      </div>

      <BrainMapSection />

      <Panel title="LESSONS FROM REPEATED MISTAKES">
        {!s.lessons.length ? <p className="text-sm text-slate-400">No pattern has lost clearly and often enough to be a lesson yet.</p> : (
          <ul className="flex flex-col gap-2 text-sm">
            {s.lessons.map((x) => (
              <li key={x.id} className="flex flex-col gap-1 rounded border border-hud-line p-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className={x.status === "rejected" ? "text-slate-500" : "text-slate-200"}>{x.text}</p>
                  <p className="text-xs text-slate-500">
                    Older trades: {r2(x.find.avg_r)} a trade on {x.find.trades} ({x.find.win_rate}% won; all trades {r2(x.find.base_r)}) · newer trades:{" "}
                    {x.check.trades ? `${r2(x.check.avg_r)} on ${x.check.trades} (the rest ${r2(x.check.rest_r)})` : "none yet"}
                    {x.saves_r != null && x.saves_r > 0 && ` · would have saved ${x.saves_r.toFixed(1)}R on the newer trades`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2 text-xs">
                  <span className={`rounded border px-2 py-0.5 ${STATUS[x.status]}`}>{STATUS_NAME[x.status]}</span>
                  {x.status === "active" && <Switch on={!x.off} label="Use" onClick={() => post(`/api/brain/lesson?lesson=${encodeURIComponent(x.id)}&on=${x.off}`)} />}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {m.buckets && m.buckets.length > 0 && (
        <Panel title="DOES ITS WIN CHANCE COME TRUE? · NEWER TRADES IT DIDN'T TRAIN ON">
          <table className="w-full text-sm tabular-nums">
            <thead className="text-xs text-slate-500"><tr><th className="text-left">Predicted</th><th className="text-left">Actually won</th><th className="text-left">Avg per trade</th><th className="text-left">Trades</th></tr></thead>
            <tbody>{m.buckets.map((b, i) => (
              <tr key={i} className="border-t border-hud-line"><td>{b.predicted}%</td><td>{b.actual}%</td><td style={tone(b.avg_r)}>{r2(b.avg_r)}</td><td>{b.trades}</td></tr>
            ))}</tbody>
          </table>
          <p className="mt-2 text-xs text-slate-500">If the model were useful, the actual win rate and the average would rise from top to bottom.
            {m.skipped_r != null && ` The setups it rated lowest made ${r2(m.skipped_r)} a trade, the rest ${r2(m.kept_r)}.`}</p>
        </Panel>
      )}

      <div className="grid gap-4">
        <Panel title="PAPER TRADES THE BRAIN SKIPPED">
          {!s.skips.length ? <p className="text-sm text-slate-400">None yet.</p> : (
            <ul className="flex flex-col gap-2 text-sm">{s.skips.map((k, i) => (
              <li key={i}><span className="text-slate-500">{when(k.time)}</span> <b className="text-slate-200">{k.call} {META[k.symbol]?.name ?? k.symbol}</b> <span className="text-slate-500">{k.mode}</span><p className="text-xs text-slate-400">{k.why}</p></li>
            ))}</ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
