import { useEffect, useRef, useState } from "react";
import { onEvent } from "../ws";

type Toast = { id: number; text: string; tone: "info" | "good" | "warn" | "bad" };
const TONE = { info: "border-hud-accent/60", good: "border-hud-green/60", warn: "border-hud-amber/70", bad: "border-hud-red" };
export const SOUND_KEY = "jarvis.sound";

export function soundOn(): boolean {
  try { return localStorage.getItem(SOUND_KEY) !== "off"; } catch { return true; }
}

function chime(urgent: boolean) {
  try {
    const ctx = new AudioContext();
    (urgent ? [880, 660, 880] : [660, 990]).forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.14);
      g.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + i * 0.14 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.14 + 0.13);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + i * 0.14);
      o.stop(ctx.currentTime + i * 0.14 + 0.15);
    });
    setTimeout(() => ctx.close(), 1000);
  } catch { /* no audio device */ }
}

const pretty = (s: string) => s.replace(/^(spot|perp):/, "").replace(":USDT", "").replace("_", "/");

/** On-screen alerts (with an optional chime) for new signals, executions and the kill switch. */
export function Toasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const start = useRef(Date.now());
  const seq = useRef(0);
  useEffect(() => onEvent((e) => {
    let t: Omit<Toast, "id"> | null = null, sound = false, urgent = false;
    if (e.type === "signal" && e.status === "active" && e.created > start.current) {
      t = { text: `${e.mode === "intraday" ? "" : "SCALP "}${e.call} ${pretty(e.symbol)} · ${e.confidence} · ${e.mode}`, tone: "info" };
      sound = e.mode !== "crypto";
    } else if (e.type === "execution" && (e.closed ?? e.opened) > start.current && e.status !== "submitting") {
      t = { text: `Order ${e.status}: ${pretty(e.symbol)}${e.reason ? ` — ${e.reason}` : ""}`, tone: e.status === "failed" ? "bad" : "good" };
      sound = true; urgent = e.status === "failed";
    } else if (e.type === "kill" && e.created > start.current) {
      t = { text: "KILL SWITCH engaged — signals paused", tone: "bad" };
      sound = urgent = true;
    }
    if (!t) return;
    const id = ++seq.current;
    setToasts((x) => [...x.slice(-3), { id, ...t! }]);
    setTimeout(() => setToasts((x) => x.filter((y) => y.id !== id)), 8000);
    if (sound && soundOn()) chime(urgent);
  }), []);
  return (
    <div className="pointer-events-none fixed left-1/2 top-4 z-[70] flex w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} className={`pointer-events-auto rounded border bg-hud-bg/95 px-3 py-2 text-sm text-slate-100 shadow-lg ${TONE[t.tone]}`}>{t.text}</div>
      ))}
    </div>
  );
}
