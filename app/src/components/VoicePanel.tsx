import { useEffect, useRef, useState } from "react";
import { Avatar, type AvatarState, type AvatarTone } from "../avatar";
import { API, onEvent, PHONE } from "../ws";

type VoiceState = {
  state: AvatarState; tone: AvatarTone; level: number; ready: boolean; error: string; heard: string; reply: string;
  min_priority: number; muted_until: number; wake_word: string;
};
const LEVELS: [number, string][] = [[1, "Everything"], [2, "Signal Station"], [3, "Scalps only"], [4, "Risk only"]];
const post = (path: string, body?: object) =>
  fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

/** Floating Ras panel: avatar, push-to-talk (hold ` or hold the orb), text chat, quiet controls. On Ras Live the full body
 *  is the avatar, so the helmet stage steps aside and the controls stay. */
export function VoicePanel({ bodyOnStage = false }: { bodyOnStage?: boolean }) {
  const [v, setV] = useState<VoiceState>();
  const level = useRef(0);
  const [open, setOpen] = useState(!PHONE);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => onEvent((e) => {
    if (e.type !== "voice") return;
    level.current = e.level;  // 20 Hz while speaking: goes to the avatar via ref, no re-render
    setV((old) => (old && old.state === e.state && old.tone === e.tone && old.heard === e.heard && old.reply === e.reply
      && old.ready === e.ready && old.min_priority === e.min_priority && old.muted_until === e.muted_until ? old : e));
  }), []);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 15_000); return () => clearInterval(t); }, []);

  // push-to-talk on the backtick key, unless typing
  useEffect(() => {
    const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName ?? "");
    const down = (e: KeyboardEvent) => { if (e.key === "`" && !e.repeat && !typing()) { e.preventDefault(); post("/api/voice/ptt", { down: true }); } };
    const up = (e: KeyboardEvent) => { if (e.key === "`" && !typing()) post("/api/voice/ptt", { down: false }); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, []);

  const ask = () => {
    if (!text.trim()) return;
    setBusy(true);
    post("/api/voice/ask", { text }).finally(() => { setBusy(false); setText(""); });
  };
  const muted = (v?.muted_until ?? 0) > now;
  const btn = "rounded border border-hud-line px-2 py-0.5 text-xs text-hud-accent hover:border-hud-accent";
  const status = !v ? "connecting…" : v.state === "listening" ? "listening…" : v.state === "thinking" ? "thinking…"
    : v.state === "speaking" || v.state === "alert" ? "speaking" : v.ready ? `say “${v.wake_word}” or hold \` / Ras` : v.error || "loading voice models…";

  if (PHONE && !open) {  // closed on a phone: just the helmet, so the panel doesn't cover the screen
    return (
      <button aria-label="Open Ras" onClick={() => setOpen(true)}
        className="fixed bottom-[calc(1rem+env(safe-area-inset-bottom))] right-4 z-50 h-16 w-16 overflow-hidden rounded-full border border-hud-line bg-hud-bg/90 shadow-[0_4px_24px_rgba(76,29,149,0.15)] backdrop-blur">
        <img src="/favicon.png" alt="" className="h-full w-full object-contain p-1" />
      </button>
    );
  }

  return (
    <div className={`${bodyOnStage ? "w-full" : "fixed bottom-[calc(1rem+env(safe-area-inset-bottom))] right-4 z-50 w-80 max-w-[calc(100vw-2rem)]"} rounded-xl border border-hud-line bg-hud-bg/90 shadow-[0_4px_24px_rgba(76,29,149,0.15)] backdrop-blur`}>
      {/* the stage: Ras reacts here; hold it to talk */}
      {!bodyOnStage && <div className="h-44 w-full cursor-pointer select-none rounded-t-xl bg-gradient-to-b from-hud-bg to-slate-800" title="Hold to talk"
        onPointerDown={() => post("/api/voice/ptt", { down: true })} onPointerUp={() => post("/api/voice/ptt", { down: false })}
        onPointerLeave={(e) => e.buttons && post("/api/voice/ptt", { down: false })}>
        <Avatar state={v?.state ?? "idle"} tone={v?.tone ?? "normal"} level={level} reply={v?.reply} />
      </div>}
      <div className={`flex items-center gap-2 p-2 ${bodyOnStage ? "" : "border-t border-hud-line"}`}>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between">
            <span className="text-sm font-bold tracking-[0.3em] text-hud-accent">RAS</span>
            <button className="text-xs text-slate-500 hover:text-slate-300" onClick={() => setOpen(!open)}>{open ? "–" : "+"}</button>
          </div>
          <p className={`text-xs ${v?.error ? "text-hud-amber" : "text-slate-400"}`}>{status}</p>
          {muted && <p className="text-xs text-hud-amber">muted until {new Date(v!.muted_until).toLocaleTimeString("en-GB", { timeZone: "Asia/Bahrain", hour: "2-digit", minute: "2-digit" })}</p>}
        </div>
      </div>
      {open && (
        <div className="border-t border-hud-line p-2 text-sm">
          {v?.heard && <p className="text-xs text-slate-500">You: <span className="text-slate-300">{v.heard}</span></p>}
          {v?.reply && <p className="mt-1 text-slate-200">{v.reply}</p>}
          <div className="mt-2 flex gap-1">
            <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} maxLength={500}
              placeholder="Ask Ras…" className="min-w-0 flex-1 rounded border border-hud-line bg-hud-panel px-2 py-1 text-sm text-slate-200" />
            <button disabled={busy} onClick={ask} className={btn}>{busy ? "…" : "Ask"}</button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1">
            {muted ? <button className={btn} onClick={() => post("/api/voice/quiet", { unmute: true })}>Unmute</button> : <>
              <button className={btn} onClick={() => post("/api/voice/quiet", { minutes: 20 })}>Mute 20m</button>
              <button className={btn} onClick={() => post("/api/voice/quiet", { minutes: 60 })}>1h</button>
            </>}
            <button className={btn} onClick={() => post("/api/voice/stop")}>Stop</button>
            <select value={v?.min_priority ?? 2} onChange={(e) => post("/api/voice/quiet", { min_priority: Number(e.target.value) })}
              className="rounded border border-hud-line bg-hud-bg px-1 py-0.5 text-xs text-slate-300" title="Which alerts are spoken">
              {LEVELS.map(([p, l]) => <option key={p} value={p}>Speak: {l}</option>)}
            </select>
          </div>
        </div>
      )}
    </div>
  );
}
