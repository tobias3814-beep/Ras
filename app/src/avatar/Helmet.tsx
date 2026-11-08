import { useEffect, useRef, useState } from "react";
import shell from "./shell.svg?raw";
import faces from "./faces.json";
import type { AvatarProps } from "./types";

export type Face = keyof typeof faces;
const BODY = shell.replace(/^[\s\S]*?<svg[^>]*>/, "").replace(/<!-- FACE -->[\s\S]*$/, "");
const GREEN = "#a6f52b";
const IDLE_GLANCES: Face[] = ["joy", "wink", "content", "square", "bars"];
const SLEEPY_AFTER_MS = 5 * 60_000;
const rand = (a: number, b: number) => a + Math.random() * (b - a);

/** What Ras is saying sets the mood while it speaks. */
export function mood(text = ""): Face | null {
  if (/can't|cannot|failed|error|stopped out|hit (its|the) stop|minus|\bloss|lost\b|freeze/i.test(text)) return "wince";
  if (/\btarget\b|\bplus\b|profit|banked|well done/i.test(text)) return "joy";
  return null;
}

/** The expression for a moment: fixed for alerts and listening, cycling while thinking / speaking, relaxed when idle. */
export function useFace({ state, tone, reply }: Pick<AvatarProps, "state" | "tone" | "reply">): Face {
  const [face, setFace] = useState<Face>("signature");
  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    const cycle = (list: Face[], ms: number) => {
      let i = 0;
      setFace(list[0]);
      timers.push(setInterval(() => setFace(list[++i % list.length]), ms));
    };
    const talking = state === "speaking" || state === "alert";
    if (tone === "breach") setFace("xx");
    else if (tone === "warning") setFace("angry");
    else if (state === "listening") setFace("surprised");
    else if (state === "thinking") cycle(["skeptical", "calm"], 1600);
    else if (talking) {
      const m = mood(reply);
      if (m) setFace(m); else cycle(["square", "bars", "square", "signature"], 1400);
    } else {
      setFace("signature");
      const since = Date.now();
      const glance = () => timers.push(setTimeout(() => {
        const sleepy = Date.now() - since > SLEEPY_AFTER_MS;
        setFace(sleepy ? "sleepy" : IDLE_GLANCES[Math.floor(Math.random() * IDLE_GLANCES.length)]);
        if (!sleepy) timers.push(setTimeout(() => setFace("signature"), 1600));
        glance();
      }, rand(8000, 18000)));
      glance();
    }
    return () => timers.forEach(clearTimeout);
  }, [state, tone, reply]);
  return face;
}

/** Ras: the helmet floats, leans in to listen, sways while thinking and pulses with the voice; its eyes blink and
 *  change expression, and turn amber on a warning and red on a risk breach. */
export function HelmetAvatar({ state, tone, level, reply }: AvatarProps) {
  const face = useFace({ state, tone, reply });
  const root = useRef<SVGSVGElement>(null);
  const eyes = useRef<SVGGElement>(null);
  const color = tone === "breach" ? "var(--color-hud-red)" : tone === "warning" ? "var(--color-hud-amber)" : GREEN;
  useEffect(() => {
    let raf = 0, smooth = 0, nextBlink = performance.now() + rand(2000, 5000);
    const tick = (ms: number) => {
      const t = ms / 1000;
      smooth += (level.current - smooth) * 0.3;
      const talking = state === "speaking" || state === "alert";
      const scale = talking ? 1 + smooth * 0.05 : state === "listening" ? 1.05 : 1 + Math.sin(t * 2) * 0.01;
      const rot = state === "thinking" ? Math.sin(t * 1.5) * 4 : tone === "breach" ? Math.sin(t * 18) * 2.5 : 0;
      const y = state === "alert" ? -Math.abs(Math.sin(t * 6)) * 6 : Math.sin(t * 1.3) * 2;
      if (root.current) root.current.style.transform = `translateY(${y}px) scale(${scale}) rotate(${rot}deg)`;
      if (ms > nextBlink + 130) nextBlink = ms + rand(2500, 6000);
      const blink = ms > nextBlink ? 0.08 : 1;
      if (eyes.current) eyes.current.setAttribute("transform", `translate(256 232) scale(${1 + smooth * 0.14} ${blink * (1 + smooth * 0.22)}) translate(-256 -232)`);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state, tone, level]);
  return (
    <div className="grid h-full place-items-center">
      <svg ref={root} viewBox="0 0 512 512" className="h-40 w-40 select-none" role="img" aria-label={`Ras: ${face}`} data-face={face}
        style={{ filter: "drop-shadow(0 3px 6px rgba(0,0,0,0.35))" }}>
        <g dangerouslySetInnerHTML={{ __html: BODY }} />
        <g fill="none" stroke={color} strokeWidth="13" strokeLinecap="round" strokeLinejoin="round" filter="url(#ras-neon)" style={{ color }}>
          <g ref={eyes}><g key={face} className="ras-face" dangerouslySetInnerHTML={{ __html: faces[face] }} /></g>
          <path d="M240 292 L256 276 L272 292" strokeWidth="7" />
        </g>
      </svg>
    </div>
  );
}
