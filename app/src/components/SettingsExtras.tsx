import { useEffect, useState } from "react";
import { THEMES, currentTheme, setTheme } from "../theme";
import { API } from "../ws";
import { Panel } from "./Panels";
import { SOUND_KEY, soundOn } from "./Toasts";

const req = (path: string, method = "GET", body?: object) =>
  fetch(`${API}${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined })
    .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail?.toString() ?? r.statusText); return j; });

type Device = { index: number; name: string; inputs: number; outputs: number };

export function AudioSettings() {
  const [devs, setDevs] = useState<Device[]>([]);
  const [sel, setSel] = useState<{ input?: string | null; output?: string | null }>({});
  const [msg, setMsg] = useState("");
  const [sound, setSound] = useState(soundOn());
  useEffect(() => { req("/api/voice/devices").then((d) => { setDevs(d.devices); setSel(d.selected ?? {}); }).catch(() => {}); }, []);
  const save = (next: typeof sel) => { setSel(next); req("/api/voice/devices", "PUT", next).then((r) => setMsg(r.note)).catch((e) => setMsg(e.message)); };
  const select = "rounded border border-hud-line bg-hud-bg px-2 py-1 text-sm text-slate-200";
  return (
    <Panel title="AUDIO">
      <div className="flex flex-wrap gap-4 text-sm text-slate-300">
        <label className="flex items-center gap-2">Microphone
          <select className={select} value={sel.input ?? ""} onChange={(e) => save({ ...sel, input: e.target.value || null })}>
            <option value="">System default</option>
            {devs.filter((d) => d.inputs > 0).map((d) => <option key={d.index} value={d.name}>{d.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">Speaker
          <select className={select} value={sel.output ?? ""} onChange={(e) => save({ ...sel, output: e.target.value || null })}>
            <option value="">System default</option>
            {devs.filter((d) => d.outputs > 0).map((d) => <option key={d.index} value={d.name}>{d.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={sound} onChange={(e) => { setSound(e.target.checked); try { localStorage.setItem(SOUND_KEY, e.target.checked ? "on" : "off"); } catch { /* ignore */ } }} />
          Alert chime with on-screen alerts
        </label>
      </div>
      {msg && <p className="mt-1 text-xs text-slate-400">{msg}</p>}
    </Panel>
  );
}

export function ConfigEditor() {
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const [msg, setMsg] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => { req("/api/config").then((c) => { setText(c.text); setPath(c.path); }).catch((e) => setMsg(e.message)); }, []);
  const save = () => req("/api/config", "PUT", { text }).then((r) => { setMsg(`Saved — ${r.note}. Previous version kept as config.yaml.bak.`); setDirty(false); })
    .catch((e) => setMsg(e.message));
  return (
    <Panel title="CONFIGURATION · config.yaml">
      <p className="mb-2 text-xs text-slate-500">{path} — thresholds, risk limits, sessions, feeds. Checked before saving; applies after a restart.</p>
      <textarea value={text} onChange={(e) => { setText(e.target.value); setDirty(true); }} spellCheck={false}
        className="h-96 w-full rounded border border-hud-line bg-hud-panel p-2 font-mono text-xs leading-relaxed text-slate-200" />
      <div className="mt-2 flex items-center gap-3">
        <button disabled={!dirty} onClick={save} className="rounded border border-hud-line px-3 py-1 text-sm text-hud-accent hover:border-hud-accent disabled:opacity-40">Save</button>
        {msg && <span className="text-xs text-slate-400">{msg}</span>}
      </div>
    </Panel>
  );
}

export function ThemePicker() {
  const now = currentTheme();
  return (
    <Panel title="THEME">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {THEMES.map((th) => (
          <button key={th.id} onClick={() => th.id !== now && setTheme(th.id)} aria-pressed={th.id === now}
            className={`flex items-center gap-3 rounded-lg border p-2 text-left text-sm ${th.id === now ? "border-hud-accent ring-1 ring-hud-accent" : "border-hud-line hover:border-hud-accent"}`}>
            <span className="relative h-9 w-12 shrink-0 overflow-hidden rounded" style={{ background: th.swatch[0] }}>
              <span className="absolute bottom-1 left-1 h-2 w-7 rounded-sm" style={{ background: th.swatch[1] }} />
            </span>
            <span className="text-slate-200">{th.name}{th.id === now && <span className="block text-xs text-hud-accent">current</span>}</span>
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">Switching redraws the window once; your choice is remembered.</p>
    </Panel>
  );
}

type Rules = Record<string, { min_size: number | null; step: number | null; margin_pct: number | null }>;
type Account = { balance: number; equity: number; start: number; since: number };

/** Signal Station paper account: start fresh with a realistic balance; shows the broker rules sizing follows. */
export function PaperAccount() {
  const [acct, setAcct] = useState<Account>();
  const [rules, setRules] = useState<Rules>({});
  const [amount, setAmount] = useState(100);
  const [armed, setArmed] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => req("/api/paper").then((p) => { setAcct(p.accounts.fx); setRules(p.rules ?? {}); }).catch(() => {});
  useEffect(() => { load(); }, []);
  const reset = () => {
    if (!armed) return setArmed(true);  // second click confirms: open paper trades are closed at market
    setArmed(false);
    req("/api/paper/reset", "POST", { balance: amount }).then(() => { setMsg(`New $${amount} account started.`); load(); }).catch((e) => setMsg(e.message));
  };
  const btn = "rounded border px-3 py-1 text-sm";
  return (
    <Panel title="PAPER ACCOUNT · SIGNAL STATION">
      <div className="flex flex-col gap-2 text-sm text-slate-300">
        {acct && <p>Started with <b className="text-slate-100">${acct.start.toLocaleString()}</b>
          {acct.since ? ` on ${new Date(acct.since).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}` : ""} ·
          balance <b className="text-slate-100">${acct.balance.toFixed(2)}</b> · equity ${acct.equity.toFixed(2)}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1">Start fresh with $
            <input type="number" min={10} step={10} value={amount} onChange={(e) => { setAmount(Number(e.target.value)); setArmed(false); }}
              className="w-24 rounded border border-hud-line bg-hud-panel px-2 py-1 text-slate-200" />
          </label>
          <button onClick={reset} disabled={amount < 10}
            className={`${btn} ${armed ? "border-hud-amber text-hud-amber" : "border-hud-line text-hud-accent hover:border-hud-accent"}`}>
            {armed ? "Click again: close open paper trades and start" : "Start fresh account"}
          </button>
          {armed && <button onClick={() => setArmed(false)} className="text-xs text-slate-500 hover:text-slate-300">cancel</button>}
        </div>
        <p className="text-xs text-slate-500">Old trades stay in the journal but stop counting. The forward test restarts from the same day.
          Sizes follow Capital.com's rules below; a signal whose minimum size would risk more than 2% is skipped and the reason journaled.</p>
        {Object.keys(rules).length > 0 && (
          <table className="mt-1 w-full max-w-md text-left text-xs tabular-nums">
            <thead className="text-slate-500"><tr><th>Instrument</th><th className="text-right">Min size</th><th className="text-right">Step</th><th className="text-right">Margin</th></tr></thead>
            <tbody>{Object.entries(rules).map(([s, r]) => (
              <tr key={s} className="border-t border-hud-line/60"><td>{s.replace("_", "/")}</td><td className="text-right">{r.min_size ?? "—"}</td>
                <td className="text-right">{r.step ?? "—"}</td><td className="text-right">{r.margin_pct != null ? `${r.margin_pct}%` : "—"}</td></tr>
            ))}</tbody>
          </table>
        )}
        {msg && <p className="text-xs text-hud-green">{msg}</p>}
      </div>
    </Panel>
  );
}
