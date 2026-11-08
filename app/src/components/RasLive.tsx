import { memo, useEffect, useRef, useState, type PointerEvent as PE } from "react";
import { useFace, type Face } from "../avatar/Helmet";
import { HIP_X, RasBody, SHIN, THIGH, type Parts } from "../avatar/RasBody";
import type { AvatarState, AvatarTone } from "../avatar";
import { API, onEvent, type PaperState, type Price } from "../ws";
import { INSTRUMENTS, META, weekendClosed } from "./InstrumentTile";

/** Ras Live: Ras's whole body living in a room. It walks between its desk, window and charging dock on its own, and
 *  reacts to you (look, poke, pat, drag, hold to talk), to its voice (listening, thinking, speaking) and to real events
 *  (a paper trade closing, a new signal, a risk breach). The room's sky follows the time in Bahrain. */

const W = 1200, H = 700, FLOOR = 618, SCALE = 0.72, STAND = 303;
const SPOT = { desk: 262, window: 640, dock: 948 };
const TZ = "Asia/Bahrain";
type Act = "idle" | "walk" | "desk" | "window" | "dock" | "pet" | "stretch" | "dance" | "wave" | "cheer" | "slump" | "hop" | "pat" | "cross" | "held" | "fall" | "land" | "listen" | "think" | "speak" | "alarm";
type Pose = { hip: number; lean: number; head: number; headY: number; lookX: number; lookY: number; sL: number; eL: number; sR: number; eR: number; fLx: number; fLy: number; fRx: number; fRy: number; air: number; tail: number; k: number };

const FACE: Partial<Record<Act, Face>> = {
  desk: "calm", window: "content", dock: "sleepy", pet: "joy", stretch: "content", dance: "joy", wave: "joy", cheer: "joy", slump: "wince",
  hop: "surprised", pat: "joy", cross: "angry", held: "surprised", fall: "surprised", land: "wince", alarm: "xx",
};
const CAPTION: Record<Act, string> = {
  idle: "Hanging around", walk: "On the move", desk: "Checking the markets", window: "Looking out of the window", dock: "Asleep on the charger", pet: "Playing with the trilobite",
  stretch: "Stretching", dance: "Dancing", wave: "Saying hello", cheer: "A paper trade won", slump: "A paper trade lost", hop: "That made Ras jump", pat: "Enjoying the head pat",
  cross: "Enough poking", held: "You picked Ras up", fall: "Falling", land: "Oof", listen: "Listening to you", think: "Thinking", speak: "Talking", alarm: "Risk breach",
};
const rand = (a: number, b: number) => a + Math.random() * (b - a);
const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));

function pose(act: Act, t: number, level: number, dir: number, phi: number): Pose {
  const s = Math.sin(t * 1.6);
  const p: Pose = { hip: STAND + s * 2.5, lean: Math.sin(t * .7) * .012, head: Math.sin(t * .5) * .03, headY: 0, lookX: 0, lookY: 0, sL: .38 + s * .02, eL: .22, sR: .38 + s * .02, eR: .22, fLx: -50, fLy: 0, fRx: 50, fRy: 0, air: 0, tail: .5, k: 9 };
  switch (act) {
    case "walk": {
      const c = Math.cos(phi), n = Math.sin(phi);
      return { ...p, hip: STAND + Math.abs(n) * 6, lean: dir * .05, head: dir * .09, lookX: dir * 13, sL: .3 + dir * c * -.28, eL: .3, sR: .3 + dir * c * .28, eR: .3, fLx: -46 + dir * 38 * c, fLy: Math.max(0, n) * 34, fRx: 46 - dir * 38 * c, fRy: Math.max(0, -n) * 34, tail: 1, k: 32 };
    }
    case "desk":
      return { ...p, hip: 186, lean: -.08, head: -.2, lookX: -15, lookY: -4, sL: .95, eL: .25 + Math.sin(t * 13) * .05, sR: .4, eR: -.75 + Math.sin(t * 11) * .05, fLx: -72, fRx: 72, tail: .3 };
    case "window":
      return { ...p, head: .12 + Math.sin(t * .4) * .08, lookX: 8 * Math.sin(t * .3), lookY: -10, sL: .18, eL: .1, sR: .18, eR: .1, tail: .9 };
    case "dock":
      return { ...p, hip: 148 + s * 2, lean: .03, head: .2, headY: 12, lookY: 8, sL: .32, eL: .5, sR: .32, eR: .5, fLx: -92, fRx: 92, tail: .12, k: 4 };
    case "pet":
      return { ...p, hip: 205, lean: .1, head: .22, lookX: 14, lookY: 10, sL: .3, eL: .2, sR: 1.2 + Math.sin(t * 7) * .12, eR: -1.5, fLx: -72, fRx: 72, tail: 1.3 };
    case "stretch":
      return { ...p, hip: STAND + 8, head: Math.sin(t * 1.2) * .08, lookY: -8, sL: 2.75, eL: .2, sR: 2.75, eR: .2, fLx: -46, fRx: 46, tail: .8 };
    case "dance": {
      const b = Math.sin(t * 7);
      return { ...p, hip: STAND - 18 + b * 14, lean: Math.sin(t * 3.5) * .13, head: Math.sin(t * 3.5) * -.14, sL: 1.5 + b * .6, eL: .9, sR: 1.5 - b * .6, eR: .9, fLx: -60, fRx: 60, fLy: Math.max(0, b) * 16, fRy: Math.max(0, -b) * 16, tail: 2, k: 18 };
    }
    case "wave":
      return { ...p, head: -.08, lookX: 4, sR: 2.5, eR: .45 + Math.sin(t * 11) * .42, tail: 1.5, k: 14 };
    case "cheer": {
      const b = Math.abs(Math.sin(t * 6.5));
      return { ...p, hip: STAND - 10 + (1 - b) * -18, air: b * 58, sL: 2.65, eL: .3 + b * .2, sR: 2.65, eR: .3 + b * .2, fLx: -34, fRx: 34, fLy: b * 8, fRy: b * 8, tail: 2.2, k: 20 };
    }
    case "slump":
      return { ...p, hip: STAND - 28, lean: .06, head: .32, headY: 14, lookY: 10, sL: .08, eL: .04, sR: .08, eR: .04, fLx: -44, fRx: 44, tail: .06, k: 5 };
    case "hop": {
      const b = Math.abs(Math.sin(t * 8));
      return { ...p, air: b * 36, sL: 1.55, eL: .55, sR: 1.55, eR: .55, fLy: 6, fRy: 6, tail: 2.4, k: 24 };
    }
    case "pat":
      return { ...p, hip: STAND - 16 + s * 3, head: Math.sin(t * 3) * .14, headY: 5, sL: .25, eL: .15, sR: .25, eR: .15, tail: 1.8 };
    case "cross":
      return { ...p, head: Math.sin(t * 2) * .05, sL: .4, eL: -2.1, sR: .4, eR: -2.1, fRy: Math.max(0, Math.sin(t * 9)) * 12, tail: 1.6, k: 14 };
    case "held":
      return { ...p, hip: 300, lean: Math.sin(t * 2.2) * .04, sL: 2.1, eL: .3, sR: 2.1, eR: .3, fLx: -22 + Math.sin(t * 3) * 14, fRx: 22 - Math.sin(t * 3.4) * 14, fLy: 14, fRy: 14, tail: 2, k: 12 };
    case "fall":
      return { ...p, hip: 300, sL: 2.5, eL: .1, sR: 2.5, eR: .1, fLx: -60, fRx: 60, fLy: 8, fRy: 8, tail: 2.5, k: 16 };
    case "land":
      return { ...p, hip: 228, lean: .03, head: .1, headY: 8, sL: 1.4, eL: .4, sR: 1.4, eR: .4, fLx: -70, fRx: 70, k: 22 };
    case "listen":
      return { ...p, lean: .03, head: -.14, lookX: 8, sR: 1.15, eR: 1.75, sL: .3, tail: .9, k: 11 };
    case "think":
      return { ...p, lean: -.02, head: Math.sin(t * 1.3) * .07 - .06, lookX: -9, lookY: -8, sR: .35, eR: 2.55, sL: .25, eL: 1.25, tail: .5 };
    case "speak": {
      const g = level * 1.3;
      return { ...p, head: Math.sin(t * 3.1) * (.04 + g * .1), headY: -g * 5, sL: .55 + Math.sin(t * 3.7) * (.1 + g * .45), eL: .75, sR: .55 + Math.cos(t * 3.1) * (.1 + g * .45), eR: .75, tail: 1, k: 13 };
    }
    case "alarm": {
      const b = Math.sin(t * 22);
      return { ...p, hip: STAND - 14, lean: b * .025, head: b * .05, sL: 2.5 + b * .1, eL: .5, sR: 2.5 - b * .1, eR: .5, tail: 3, k: 20 };
    }
    default:
      return p;
  }
}

/** Two-bone leg: thigh and shin angles (SVG rotation, clockwise from straight down) reaching a foot at (dx, dy) from the hip. */
function legIk(dx: number, dy: number, bend: 1 | -1): [number, number] {
  const d = clamp(Math.hypot(dx, dy), SHIN - THIGH + 1, SHIN + THIGH - .5);
  const base = Math.atan2(-dx, dy);
  const a = Math.acos(clamp((THIGH * THIGH + d * d - SHIN * SHIN) / (2 * THIGH * d), -1, 1));
  const knee = Math.acos(clamp((THIGH * THIGH + SHIN * SHIN - d * d) / (2 * THIGH * SHIN), -1, 1));
  return [base + bend * a, -bend * (Math.PI - knee)];
}
const deg = (r: number) => (r * 180) / Math.PI;

function sky(ms: number) {
  const hour = Number(new Date(ms).toLocaleString("en-GB", { timeZone: TZ, hour: "2-digit", hour12: false })) % 24;
  if (hour >= 7 && hour < 17) return { name: "day", hour, top: "#8fd0ea", low: "#dff3e6", wall: "#e9ecdc", wall2: "#d8dcc8", floor: "#bcc4a6", light: 0 };
  if (hour >= 5 && hour < 19) return { name: "dusk", hour, top: "#54508a", low: "#f4a968", wall: "#a9a68c", wall2: "#8f8d76", floor: "#6f745c", light: .35 };
  return { name: "night", hour, top: "#0b1a2c", low: "#21414a", wall: "#27362c", wall2: "#1f2c24", floor: "#141d17", light: .8 };
}

/** The room behind Ras. Clicking a piece of it sends Ras there. */
const Room = memo(function Room({ sk }: { sk: ReturnType<typeof sky> }) {
  const night = sk.name === "night";
  const INK = "#1d2118";
  const sunX = 500 + 280 * clamp((sk.hour - 6) / 13, 0, 1), stars: [number, number, number][] = [[505, 120, 5], [565, 215, 3.5], [622, 100, 4], [700, 170, 6], [752, 110, 3], [772, 255, 4], [528, 278, 3]];
  return (
    <g>
      <defs>
        <linearGradient id="live-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={sk.top} /><stop offset="1" stopColor={sk.low} /></linearGradient>
        <radialGradient id="live-lamp" cx=".5" cy="0" r="1"><stop offset="0" stopColor="#fff3b0" stopOpacity={.2 + sk.light * .28} /><stop offset="1" stopColor="#fff3b0" stopOpacity="0" /></radialGradient>
        <radialGradient id="live-dock" cx=".5" cy=".5" r=".5"><stop offset="0" stopColor="#a6f52b" stopOpacity=".5" /><stop offset="1" stopColor="#a6f52b" stopOpacity="0" /></radialGradient>
      </defs>
      <rect width={W} height={FLOOR - 42} fill={sk.wall} />
      <path d="M0 150 H470 M810 150 H1200" stroke={sk.wall2} strokeWidth="5" />
      <rect y={FLOOR - 42} width={W} height={H - FLOOR + 42} fill={sk.floor} />
      <rect y={FLOOR - 50} width={W} height="10" fill={sk.wall2} stroke={INK} strokeWidth="2" />
      {/* window */}
      <g data-go="window" style={{ cursor: "pointer" }}>
        <path d="M480 400 V150 Q480 70 640 70 Q800 70 800 150 V400Z" fill="url(#live-sky)" stroke={INK} strokeWidth="8" />
        {night ? <><circle cx={740} cy={135} r="28" fill="#f2f0d0" /><circle cx={752} cy={127} r="24" fill={sk.top} />
          {stars.map(([x, y, r], i) => <circle key={i} className="live-twinkle" style={{ animationDelay: `${i * .7}s` }} cx={x} cy={y} r={r / 1.6} fill="#e8ffc0" />)}</>
          : <circle cx={sunX} cy={140 + Math.abs(sk.hour - 12) * 15} r="30" fill={sk.name === "day" ? "#fff2a8" : "#ffc27a"} />}
        <path d="M484 400 V330 Q540 280 610 320 Q680 356 740 300 Q770 280 796 300 V400Z" fill={night ? "#0f2a1f" : sk.name === "dusk" ? "#3d5a35" : "#6fa84a"} />
        <path d="M484 400 V364 Q580 340 650 368 Q730 392 796 354 V400Z" fill={night ? "#0a1e16" : sk.name === "dusk" ? "#2c4227" : "#528a35"} />
        <path d="M640 72 V400 M480 235 H800" stroke={INK} strokeWidth="5" />
        <rect x="462" y="398" width="356" height="16" rx="4" fill={sk.wall2} stroke={INK} strokeWidth="4" />
      </g>
      {/* wall stars, from the character sheet */}
      <path d="M86 92 L110 30 L126 90 L190 94 L136 128 L156 190 L108 150 L54 188 L76 128 L24 92Z" fill="#5b9a33" stroke={INK} strokeWidth="3" transform="translate(30 25) scale(.62)" />
      <path d="M1084 60 l9 25 27 1 -21 16 8 26 -23 -15 -22 15 8 -26 -21 -16 27 -1Z" fill="none" stroke="#7aa15c" strokeWidth="5" />
      {/* lamp */}
      <path d="M390 0 V60" stroke={INK} strokeWidth="4" />
      <path d="M355 96 Q358 60 390 60 Q422 60 425 96Z" fill="#5b9a33" stroke={INK} strokeWidth="4" />
      <path d="M355 96 L60 600 H720 L425 96Z" fill="url(#live-lamp)" pointerEvents="none" />
      {/* shelf and plant */}
      <rect x="990" y="240" width="170" height="11" rx="3" fill="#a78a5f" stroke={INK} strokeWidth="3" />
      <path d="M1014 238 h44 l-6 -34 h-32Z" fill="#ecede2" stroke={INK} strokeWidth="3" />
      <path d="M1036 204 Q1016 170 992 170 M1036 204 Q1034 160 1048 142 M1036 204 Q1056 180 1080 176" fill="none" stroke="#5b9a33" strokeWidth="7" strokeLinecap="round" />
      <rect x="1094" y="196" width="46" height="42" rx="3" fill="#ecede2" stroke={INK} strokeWidth="3" />
      <path d="M1106 228 l9 -12 8 7 9 -16" fill="none" stroke="#5b9a33" strokeWidth="3.5" strokeLinecap="round" />
      {/* rug */}
      <ellipse cx="640" cy={FLOOR + 30} rx="230" ry="34" fill="#5b9a33" opacity=".55" /><ellipse cx="640" cy={FLOOR + 30} rx="186" ry="23" fill="none" stroke="#a6f52b" strokeWidth="3" opacity=".6" />
      {/* charging dock */}
      <g data-go="dock" style={{ cursor: "pointer" }}>
        <ellipse cx={SPOT.dock} cy={FLOOR - 4} rx="150" ry="52" fill="url(#live-dock)" className="live-pulse" />
        <path d="M1110 360 H1060 Q1040 360 1040 400 V560" fill="none" stroke={INK} strokeWidth="6" />
        <rect x="1100" y="340" width="40" height="40" rx="6" fill="#ecede2" stroke={INK} strokeWidth="3" /><path d="M1122 346 l-10 16 h9 l-3 13 10 -17 h-9Z" fill="#5b9a33" />
        <path d={`M${SPOT.dock - 112} ${FLOOR + 2} Q${SPOT.dock} ${FLOOR - 48} ${SPOT.dock + 112} ${FLOOR + 2} L${SPOT.dock + 100} ${FLOOR + 22} Q${SPOT.dock} ${FLOOR + 40} ${SPOT.dock - 100} ${FLOOR + 22}Z`} fill="#ecede2" stroke={INK} strokeWidth="4" />
        <ellipse cx={SPOT.dock} cy={FLOOR - 6} rx="76" ry="13" fill="#14170f" stroke="#a6f52b" strokeWidth="4" />
      </g>
      {/* desk, drawn before the monitor text */}
      <g data-go="desk" style={{ cursor: "pointer" }}>
        <rect x="20" y="444" width="330" height="17" rx="4" fill="#a78a5f" stroke={INK} strokeWidth="4" />
        <path d="M40 461 L34 590 M330 461 L336 590" stroke={INK} strokeWidth="9" />
        <rect x="40" y="248" width="250" height="172" rx="12" fill="#14170f" stroke={INK} strokeWidth="5" />
        <path d="M152 420 L146 444 H184 L178 420Z" fill="#ecede2" stroke={INK} strokeWidth="3" />
        <rect x="130" y="432" width="118" height="12" rx="3" fill="#c9cbbd" stroke={INK} strokeWidth="3" />
      </g>
    </g>
  );
});

function Monitor({ prices, paper }: { prices: Record<string, Price>; paper?: PaperState }) {
  const acct = paper?.accounts?.fx, open = paper?.open?.length ?? 0, closed = weekendClosed();
  return (
    <g pointerEvents="none" fontFamily="var(--font-mono)" fontSize="15">
      {INSTRUMENTS.slice(0, 6).map((s, i) => {
        const p = prices[s];
        return <text key={s} x="54" y={274 + i * 20} fill="#a6f52b">{META[s].name.padEnd(8, " ")}<tspan x="150" fill="#ecede2">{p ? ((p.bid + p.ask) / 2).toFixed(META[s].digits) : "…"}</tspan></text>;
      })}
      <text x="54" y="402" fill={closed ? "#ffb547" : "#a6f52b"}>{closed ? "Markets closed" : acct ? `$${acct.equity.toFixed(2)} · ${open} open` : `${open} open`}</text>
    </g>
  );
}

function Stool() {
  return <g pointerEvents="none"><ellipse cx={SPOT.desk} cy={FLOOR - 120} rx="52" ry="11" fill="#5b9a33" stroke="#1d2118" strokeWidth="4" /><path d={`M${SPOT.desk - 30} ${FLOOR - 112} L${SPOT.desk - 44} ${FLOOR + 2} M${SPOT.desk + 30} ${FLOOR - 112} L${SPOT.desk + 44} ${FLOOR + 2}`} stroke="#1d2118" strokeWidth="7" /></g>;
}

function Trilobite({ at }: { at: React.RefObject<SVGGElement | null> }) {
  return (
    <g ref={at} data-go="pet" style={{ cursor: "pointer" }}>
      <ellipse cy="3" rx="42" ry="7" fill="#000" opacity=".16" />
      <path className="live-legs" d="M-26 -3 l-6 8 M-16 -2 l-5 9 M-6 -2 l-3 10 M6 -2 l3 10 M16 -2 l5 9 M26 -3 l6 8" stroke="#3f7023" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M-40 -4 Q-34 -24 -6 -25 Q24 -25 40 -8 Q24 2 -6 1 Q-30 1 -40 -4Z" fill="#b9dd8c" stroke="#3f7023" strokeWidth="3" />
      <path d="M-22 -22 Q-26 -10 -22 0 M-10 -24 Q-14 -11 -10 1 M2 -25 Q-2 -12 2 1 M14 -22 Q10 -11 14 0 M25 -17 Q22 -9 25 -3" fill="none" stroke="#3f7023" strokeWidth="2.4" />
      <circle cx="31" cy="-10" r="2.2" fill="#1d2118" /><path d="M40 -8 Q50 -14 54 -22" fill="none" stroke="#3f7023" strokeWidth="2.4" strokeLinecap="round" />
    </g>
  );
}

export function RasLivePage({ prices, paper, halted }: { prices: Record<string, Price>; paper?: PaperState; halted: boolean }) {
  const parts = useRef<Parts>({});
  const svg = useRef<SVGSVGElement>(null);
  const shadow = useRef<SVGEllipseElement>(null);
  const tri = useRef<SVGGElement>(null);
  const bubble = useRef<HTMLDivElement>(null);
  const zzz = useRef<SVGGElement>(null);
  const [voice, setVoice] = useState<{ state: AvatarState; tone: AvatarTone; reply: string; heard: string }>({ state: "idle", tone: "normal", reply: "", heard: "" });
  const level = useRef(0);
  const [act, setAct] = useState<Act>("wave");
  const [log, setLog] = useState("Ras is awake.");
  const [sk, setSk] = useState(() => sky(Date.now()));
  const [narrow, setNarrow] = useState(() => matchMedia("(max-width: 700px)").matches);
  const baseFace = useFace({ state: voice.state, tone: voice.tone, reply: voice.reply });

  // everything the animation loop reads lives here, so the loop never restarts
  const m = useRef({
    x: SPOT.window, goal: SPOT.window, dir: 1, phi: 0, act: "wave" as Act, until: 0, next: null as Act | null, routineUntil: 0, air: 0, vy: 0,
    px: 0, py: 0, pointerAt: 0, lastTouch: 0, pokes: [] as number[], triX: 780, triDir: 1, triNext: 0, press: null as null | { x: number; y: number; part: string; timer: number; talking: boolean; drag: boolean },
    voice: "idle" as AvatarState, tone: "normal" as AvatarTone, closed: false, up: false,
    cur: pose("idle", 0, 0, 1, 0), wanted: "idle" as Act, shown: "wave" as Act,
  });

  /** Do `a` for `sec` seconds, or until something more important comes along. */
  const react = (a: Act, sec: number) => { const s = m.current; s.act = a; s.until = performance.now() / 1000 + sec; s.next = null; };
  const goTo = (x: number, then: Act, sec = rand(8, 16)) => {
    const s = m.current;
    s.goal = clamp(x, 90, W - 90); s.next = then; s.routineUntil = performance.now() / 1000 + sec + Math.abs(s.goal - s.x) / 140; s.until = 0; s.act = "walk";
  };
  const spotFor = (a: string, x: number) => a === "desk" ? SPOT.desk : a === "window" ? SPOT.window : a === "dock" ? SPOT.dock : a === "pet" ? clamp(m.current.triX - 130, 100, W - 100) : x;

  useEffect(() => {
    m.current.voice = voice.state; m.current.tone = voice.tone;
    if (voice.state !== "idle") m.current.lastTouch = performance.now();
  }, [voice.state, voice.tone]);
  useEffect(() => { m.current.closed = weekendClosed(); const t = setInterval(() => { setSk(sky(Date.now())); m.current.closed = weekendClosed(); }, 60_000); return () => clearInterval(t); }, []);
  useEffect(() => { const q = matchMedia("(max-width: 700px)"); const f = () => setNarrow(q.matches); q.addEventListener("change", f); return () => q.removeEventListener("change", f); }, []);
  useEffect(() => { m.current.up = !!paper?.accounts?.fx && paper.accounts.fx.equity >= paper.accounts.fx.start; }, [paper?.accounts?.fx?.equity]);

  // real events: voice, signals, closing paper trades
  useEffect(() => onEvent((e) => {
    if (e.type === "voice") {
      level.current = e.level;
      setVoice((o) => (o.state === e.state && o.tone === e.tone && o.reply === e.reply && o.heard === e.heard ? o : { state: e.state, tone: e.tone, reply: e.reply, heard: e.heard }));
    } else if (e.type === "signal" && (e as { call?: string }).call !== "HOLD") {
      setLog(`New signal on ${META[e.symbol]?.name ?? e.symbol}, so Ras went to its desk.`);
      goTo(SPOT.desk, "desk", 10);
    }
  }), []);
  const seen = useRef<string | null>(null);
  const newest = paper?.recent?.[0];
  useEffect(() => {
    if (!newest) return;
    const id = `${newest.signal_id}:${newest.closed}`;
    if (seen.current !== null && seen.current !== id && newest.pnl != null) {
      const name = META[newest.symbol]?.name ?? newest.symbol;
      setLog(`Paper trade on ${name} ${newest.pnl >= 0 ? "won" : "lost"} $${Math.abs(newest.pnl).toFixed(2)}.`);
      react(newest.pnl >= 0 ? "cheer" : "slump", newest.pnl >= 0 ? 4 : 5);
    }
    seen.current = id;
  }, [newest?.signal_id, newest?.closed]);

  useEffect(() => { // first hello
    m.current.until = performance.now() / 1000 + 2.6;
    m.current.lastTouch = performance.now();
    let raf = 0, last = performance.now(), blinkAt = last + 2500;
    const tick = (ms: number) => {
      const s = m.current, t = ms / 1000, dt = Math.min(.12, (ms - last) / 1000);
      last = ms;
      // --- what is Ras doing? held, then breach, then voice, then a reaction, then its own routine
      let a: Act;
      const held = s.press?.drag;
      if (held) a = "held";
      else if (s.air > 0 && s.vy !== 0) a = "fall";
      else if (s.tone === "breach") a = "alarm";
      else if (s.voice === "listening") a = "listen";
      else if (s.voice === "thinking") a = "think";
      else if (s.voice === "speaking" || s.voice === "alert") a = "speak";
      else if (t < s.until) a = s.act;
      else if (Math.abs(s.goal - s.x) > 3) a = "walk";
      else if (s.next) { s.act = s.next; s.next = null; a = s.act; }
      else if (t < s.routineUntil && s.act !== "walk" && s.act !== "idle") a = s.act;
      else {
        // pick what to do next; sleepy after 4 quiet minutes
        const quiet = (ms - s.lastTouch) / 1000;
        if (quiet > 240) goTo(SPOT.dock, "dock", 90);
        else {
          const r = Math.random();
          if (r < (s.closed ? .12 : .34)) goTo(SPOT.desk, "desk");
          else if (r < .56) goTo(SPOT.window, "window");
          else if (r < .74) goTo(spotFor("pet", s.x), "pet", 7);
          else if (r < .84) { s.act = "stretch"; s.routineUntil = t + 3.5; }
          else if (r < .92 && s.up) { s.act = "dance"; s.routineUntil = t + 5; }
          else { s.act = "idle"; s.goal = clamp(s.x + rand(-180, 180), 120, W - 120); s.routineUntil = t + rand(3, 7); }
        }
        a = "idle";
      }
      // --- move
      if (a === "walk") {
        const d = s.goal - s.x; s.dir = Math.sign(d) || s.dir;
        const v = 145; s.x += s.dir * Math.min(Math.abs(d), v * dt); s.phi += dt * v * Math.PI / 96;
      }
      if (a === "fall") {
        s.vy -= 1500 * dt; s.air += s.vy * dt;
        if (s.air <= 0) { s.air = 0; s.vy = 0; s.goal = s.x; react("land", .8); setLog("Ras landed. Gently, please."); }
      }
      // --- pose, eased toward the target
      const target = pose(a, t, level.current, s.dir, s.phi);
      const ease = 1 - Math.exp(-dt * target.k);
      const cur = s.cur as Record<keyof Pose, number>;
      (Object.keys(target) as (keyof Pose)[]).forEach((k) => { cur[k] += (target[k] - cur[k]) * ease; });
      // look at the pointer when it has moved recently and Ras isn't busy
      const free = !["walk", "desk", "dock", "window", "alarm", "think"].includes(a) && ms - s.pointerAt < 3500;
      const lx = free ? clamp((s.px - s.x) / 28, -15, 15) : cur.lookX, ly = free ? clamp((s.py - (FLOOR - 400)) / 34, -9, 9) : cur.lookY;
      // --- put it on the bones
      const P = parts.current, air = a === "held" || a === "fall" ? s.air : cur.air;
      P.root?.setAttribute("transform", `translate(${s.x.toFixed(1)} ${(FLOOR - air).toFixed(1)}) scale(${SCALE})`);
      const hip = cur.hip;
      P.torso?.setAttribute("transform", `translate(0 ${(-hip).toFixed(1)}) rotate(${deg(cur.lean).toFixed(2)})`);
      for (const [side, x, fx, fy, bend] of [["L", -HIP_X, cur.fLx, cur.fLy, 1], ["R", HIP_X, cur.fRx, cur.fRy, -1]] as const) {
        const [a1, a2] = legIk(fx - x, hip - fy, bend);
        P[`leg${side}`]?.setAttribute("transform", `translate(${x} ${-hip})`);
        P[`thigh${side}`]?.setAttribute("transform", `rotate(${deg(a1).toFixed(2)})`);
        P[`shin${side}`]?.setAttribute("transform", `rotate(${deg(a2).toFixed(2)})`);
      }
      P.armL?.setAttribute("transform", `rotate(${deg(cur.sL).toFixed(2)})`);
      P.foreL?.setAttribute("transform", `rotate(${deg(cur.eL).toFixed(2)})`);
      P.armR?.setAttribute("transform", `rotate(${deg(-cur.sR).toFixed(2)})`);
      P.foreR?.setAttribute("transform", `rotate(${deg(-cur.eR).toFixed(2)})`);
      P.head?.setAttribute("transform", `translate(0 ${cur.headY.toFixed(1)}) rotate(${deg(cur.head).toFixed(2)} 0 -30)`);
      // the eyes ride on the head's screen, shifted a little in the look direction
      // (the same chain as the head's own transforms: head turn about its base, then neck, then torso lean)
      const hx = 36 * Math.sin(cur.head) + lx * .9, hy = -30 - 36 * Math.cos(cur.head) + cur.headY - 150 + ly * .7;
      const fx = hx * Math.cos(cur.lean) - hy * Math.sin(cur.lean), fy = hx * Math.sin(cur.lean) + hy * Math.cos(cur.lean) - hip;
      const hr = cur.lean + cur.head;
      P.faceAt?.setAttribute("transform", `translate(${fx.toFixed(1)} ${fy.toFixed(1)}) rotate(${deg(hr).toFixed(2)})`);
      if (ms > blinkAt + 130) blinkAt = ms + rand(2300, 6000);
      const blink = ms > blinkAt ? .08 : 1, lv = level.current;
      P.eyes?.setAttribute("transform", `translate(256 232) scale(${1 + lv * .14} ${blink * (1 + lv * .24)}) translate(-256 -232)`);
      P.antenna?.setAttribute("transform", `rotate(${(Math.sin(t * 2.3) * 5 + (a === "walk" ? Math.sin(s.phi * 2) * 9 : 0)).toFixed(1)} 8 -134)`);
      const perk = a === "listen" || a === "hop" ? 12 : a === "slump" ? -18 : a === "dock" ? -12 : 0, flick = Math.sin(t * .9) > .985 ? 10 : 0;
      P.earL?.setAttribute("transform", `rotate(${-perk + flick} -44 -122)`);
      P.earR?.setAttribute("transform", `rotate(${-perk} -44 -122)`);
      P.core?.setAttribute("opacity", (a === "dock" ? .35 + Math.sin(t * 1.5) * .3 : .85 + lv * .15 + Math.sin(t * 3) * .1).toFixed(2));
      // tail: a chain that hangs from the hip, swings, and curls at the tip
      const pts: string[] = []; let tx = 18, ty = -hip + 12; const seg = 25;
      for (let i = 0; i < 15; i++) {
        pts.push(`${tx.toFixed(1)} ${ty.toFixed(1)}`);
        const u = i / 14, ang = .35 + u * 1.15 * (1 - u * .2) + Math.sin(t * 2.1 - i * .45) * .13 * cur.tail;
        tx += Math.sin(ang) * seg * .94; ty = Math.min(ty + Math.cos(ang) * seg, -6);
      }
      const d = `M${pts.join(" L")}`;
      P.tailInk?.setAttribute("d", d); P.tailWhite?.setAttribute("d", d); P.tailTip?.setAttribute("d", `M${pts.slice(-4).join(" L")}`);
      shadow.current?.setAttribute("cx", s.x.toFixed(1));
      shadow.current?.setAttribute("rx", (120 - Math.min(80, air * .25)).toFixed(0));
      shadow.current?.setAttribute("opacity", (.28 - Math.min(.18, air * .0006)).toFixed(2));
      // --- the trilobite wanders, and stays still while it's being petted
      if (a !== "pet") {
        if (t > s.triNext) { s.triDir = Math.random() < .5 ? -1 : 1; s.triNext = t + rand(4, 11); }
        s.triX += s.triDir * dt * (t % 8 < 5 ? 16 : 0);
        if (s.triX < 420 || s.triX > 860) { s.triDir = s.triX < 420 ? 1 : -1; s.triX = clamp(s.triX, 420, 860); }
      }
      tri.current?.setAttribute("transform", `translate(${s.triX.toFixed(1)} ${FLOOR + 36}) scale(${s.triDir} 1)`);
      zzz.current?.setAttribute("transform", `translate(${s.x + 52} ${FLOOR - 340})`);
      if (zzz.current) zzz.current.style.display = a === "dock" ? "" : "none";
      // --- phone camera follows Ras; speech bubble follows too
      if (svg.current) {
        if (matchMedia("(max-width: 700px)").matches) svg.current.setAttribute("viewBox", `${clamp(s.x - 330, 0, W - 660).toFixed(0)} 0 660 ${H}`);
        else svg.current.setAttribute("viewBox", `0 0 ${W} ${H}`);
        const ctm = svg.current.getScreenCTM(), box = svg.current.parentElement?.getBoundingClientRect();
        if (ctm && box && bubble.current) {
          const headTop = FLOOR - air - (hip + 300) * SCALE;
          const px = ctm.a * s.x + ctm.e - box.left, py = ctm.d * headTop + ctm.f - box.top;
          const half = bubble.current.offsetWidth / 2;
          bubble.current.style.left = `${clamp(px, half + 6, box.width - half - 6)}px`;
          bubble.current.style.top = `${Math.max(bubble.current.offsetHeight + 6, py)}px`;
        }
      }
      if (a !== s.shown) { s.shown = a; setAct(a); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const toWorld = (e: { clientX: number; clientY: number }) => {
    const c = svg.current?.getScreenCTM();
    return c ? { x: (e.clientX - c.e) / c.a, y: (e.clientY - c.f) / c.d } : { x: 0, y: 0 };
  };
  const ptt = (down: boolean) => fetch(`${API}/api/voice/ptt`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ down }) }).catch(() => {});

  const onDown = (e: PE<SVGSVGElement>) => {
    const s = m.current, w = toWorld(e), el = e.target as Element;
    if (!el.closest("[data-ras]")) return;
    e.preventDefault();
    svg.current?.setPointerCapture(e.pointerId);
    const part = el.closest("[data-part]")?.getAttribute("data-part") ?? "body";
    const press = { x: w.x, y: w.y, part, talking: false, drag: false, timer: 0 };
    press.timer = window.setTimeout(() => { // held still: talk, same as holding the helmet
      if (s.press === press && !press.drag) { press.talking = true; ptt(true); setLog("Listening while you hold Ras."); }
    }, 380);
    s.press = press; s.lastTouch = performance.now();
  };
  const onMove = (e: PE<SVGSVGElement>) => {
    const s = m.current, w = toWorld(e);
    s.px = w.x; s.py = w.y; s.pointerAt = performance.now();
    const pr = s.press;
    if (!pr) return;
    if (!pr.drag && !pr.talking && Math.hypot(w.x - pr.x, w.y - pr.y) > 10) { pr.drag = true; clearTimeout(pr.timer); setLog("You picked Ras up."); }
    if (pr.drag) {
      s.x = clamp(w.x, 70, W - 70); s.goal = s.x;
      s.air = clamp(FLOOR - w.y - STAND * SCALE, 0, 300);
    }
  };
  const onUp = (e: PE<SVGSVGElement>) => {
    const s = m.current, pr = s.press;
    if (!pr) { // a click on the room
      const go = (e.target as Element).closest("[data-go]")?.getAttribute("data-go");
      s.lastTouch = performance.now();
      if (go) { const a = go as Act; goTo(spotFor(go, s.x), a); setLog(`You sent Ras to the ${go === "pet" ? "trilobite" : go}.`); }
      else if (toWorld(e).y > FLOOR - 40) goTo(toWorld(e).x, "idle", 3);
      return;
    }
    clearTimeout(pr.timer);
    s.press = null;
    if (pr.talking) { ptt(false); return; }
    if (pr.drag) { if (s.air > 0) s.vy = -1; else { s.goal = s.x; react("land", .5); } return; }
    // a poke: where you touched matters, and too many in a row annoy it
    const now = performance.now();
    s.pokes = [...s.pokes.filter((x) => now - x < 6000), now];
    if (s.act === "dock") { setLog("You woke Ras up."); react("stretch", 2.5); return; }
    if (s.pokes.length >= 5) { s.pokes = []; react("cross", 3); setLog("Ras has had enough poking."); return; }
    if (pr.part === "head") { react("pat", 2.2); setLog("Ras likes that."); }
    else if (pr.part === "tail") { react("hop", 1.5); setLog("Not the tail!"); }
    else { react(s.pokes.length > 2 ? "wave" : "hop", 1.3); setLog(s.pokes.length > 2 ? "Ras waves at you." : "Ras jumped."); }
  };
  const command = (a: Act) => {
    const s = m.current;
    s.lastTouch = performance.now();
    if (a === "desk" || a === "window" || a === "dock" || a === "pet") goTo(spotFor(a, s.x), a);
    else react(a, a === "dance" ? 6 : 2.5);
  };

  const breach = voice.tone === "breach";
  const shown: Face = breach ? baseFace : voice.tone === "warning" ? "angry" : voice.state !== "idle" ? (baseFace === "signature" ? "bars" : baseFace) : FACE[act] ?? (baseFace === "signature" ? "bars" : baseFace);
  const eye = breach ? "var(--color-hud-red)" : voice.tone === "warning" || halted ? "var(--color-hud-amber)" : "#a6f52b";
  const speaking = (voice.state === "speaking" || voice.state === "alert") && !!voice.reply;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg tracking-[0.3em] text-hud-accent">RAS LIVE</h2>
        <p className="text-sm text-slate-300">{CAPTION[act]}<span className="text-slate-500"> · {log}</span></p>
      </div>
      <div className="relative mx-auto w-full overflow-hidden rounded-xl border border-hud-line" style={{ maxWidth: narrow ? undefined : "calc((100vh - 200px) * 12 / 7)" }}>
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block w-full select-none" style={{ aspectRatio: narrow ? "660 / 700" : "12 / 7", touchAction: "manipulation" }}
          onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
          <Room sk={sk} />
          <Monitor prices={prices} paper={paper} />
          <Stool />
          <ellipse ref={shadow} cy={FLOOR + 2} ry="20" fill="#000" pointerEvents="none" />
          <Trilobite at={tri} />
          <RasBody parts={parts} face={shown} color={eye} />
          <g ref={zzz} pointerEvents="none" fill="#a6f52b" fontFamily="var(--font-mono)" fontWeight="700" style={{ display: "none" }}>
            <text className="live-z" fontSize="30">Z</text><text className="live-z" style={{ animationDelay: "1s" }} x="24" y="-30" fontSize="22">z</text><text className="live-z" style={{ animationDelay: "2s" }} x="44" y="-54" fontSize="16">z</text>
          </g>
          {(voice.tone !== "normal" || halted) && <rect className="live-alarm" width={W} height={H} fill={breach ? "#ff2040" : "#ffb547"} pointerEvents="none" />}
        </svg>
        <div ref={bubble} aria-live="polite" className={`pointer-events-none absolute z-10 max-w-[min(26rem,85%)] -translate-x-1/2 -translate-y-full rounded-xl border-2 border-[#1d2118] bg-[#f6f7ec] px-3 py-2 text-sm text-[#1d2118] shadow-md ${speaking || voice.state === "thinking" || voice.state === "listening" ? "" : "invisible"}`}>
          {voice.state === "listening" ? "I'm listening…" : voice.state === "thinking" ? "Hmm…" : voice.reply}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1 text-xs">
        <span className="mr-1 text-slate-500">Ask Ras to</span>
        {([["desk", "Go to desk"], ["window", "Look outside"], ["pet", "Play with trilobite"], ["dance", "Dance"], ["wave", "Wave"], ["dock", "Nap"]] as [Act, string][]).map(([a, label]) => (
          <button key={a} onClick={() => command(a)} className="rounded border border-hud-line px-2 py-1 text-slate-300 hover:border-hud-accent hover:text-hud-accent">{label}</button>
        ))}
        <span className="ml-1 text-slate-500">Tap to poke or pat · drag to pick up · hold to talk</span>
      </div>
    </div>
  );
}
