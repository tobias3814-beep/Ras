/** The brain map: a holographic picture of what Ras remembers and has learnt.
 *  Core = Ras · inner ring = trading styles · middle ring = markets, each with a cloud of its losing trades (every mistake
 *  in memory, red) · outer ring = lessons (bright when acting) · outermost orbit = every strategy Ras has tested.
 *  When Ras learns something, the core sends out a shockwave and what changed glows. */
import { useEffect, useMemo, useRef, useState } from "react";
import { API, onEvent } from "../ws";
import { css } from "../theme";
import { META } from "./InstrumentTile";
import { Panel } from "./Panels";

type Lesson = { id: string; text: string; status: "active" | "watching" | "rejected"; off: boolean; when: Record<string, string>;
  find: { trades: number; avg_r: number }; check: { trades: number; avg_r: number | null } };
type Mistake = { time: number; symbol: string; mode: string; r: number; source: string; note: string; strategy: string; lessons: number[] };
type Event = { time: number; kind: string; text: string; lesson?: string; symbol?: string; r?: number; why?: string };
type Studied = { key: string; name: string; group: string; verdict: string | null; avg_r: number | null; trades: number | null; time: number };
type Graph = { built_at: number; memory: number; wins: number; lessons: Lesson[]; mistakes: Mistake[]; log: Event[]; studied: Studied[];
  model: { earned: boolean; auc: number | null } };

type Node = { kind: "core" | "mode" | "market" | "lesson" | "studied" | "mistake"; key: string; x: number; y: number; r: number; i?: number; label?: string; a?: number };
type Filter = { kind: "all" } | { kind: "market"; mode: string; symbol: string } | { kind: "lesson"; i: number };

const name = (s: string) => META[s]?.name ?? s.replace("_", "/");
const when = (ms: number) => new Date(ms).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const hexA = (hex: string, a: number) => {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h.slice(0, 6), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
const hash = (s: string) => { let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return (h >>> 0) / 4294967296; };
const GLOW_MS = 9000;
const REDUCED = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function layout(g: Graph, w: number, h: number) {
  const cx = w / 2, cy = h / 2, u = Math.min(w, h) / 2 - 20;
  const nodes: Node[] = [{ kind: "core", key: "core", x: cx, y: cy, r: 22 }];
  const modes = [...new Set([...g.mistakes.map((m) => m.mode), ...g.lessons.map((l) => l.when.mode)])].sort();
  const at = (a: number, r: number) => [cx + Math.cos(a) * r, cy + Math.sin(a) * r] as const;
  const modeAngle: Record<string, number> = {}, marketAt: Record<string, Node> = {};
  const span = (Math.PI * 2) / Math.max(modes.length, 1);
  const edges: [Node, Node][] = [];
  modes.forEach((m, i) => {
    const a = -Math.PI / 2 + i * span;
    modeAngle[m] = a;
    const [x, y] = at(a, u * 0.27);
    const mn: Node = { kind: "mode", key: `mode:${m}`, x, y, r: 9, label: m, a };
    nodes.push(mn); edges.push([nodes[0], mn]);
    const syms = [...new Set(g.mistakes.filter((x) => x.mode === m).map((x) => x.symbol))].sort();
    syms.forEach((s, j) => {
      const aa = a + (syms.length > 1 ? (j / (syms.length - 1) - 0.5) * span * 0.78 : 0);
      const [mx, my] = at(aa, u * 0.58);
      const k: Node = { kind: "market", key: `market:${m}:${s}`, x: mx, y: my, r: 6, label: name(s), a: aa };
      nodes.push(k); edges.push([mn, k]); marketAt[`${m}:${s}`] = k;
    });
  });
  // every mistake: a golden-angle cloud around its market, bigger dots for bigger losses
  const groups: Record<string, number[]> = {};
  g.mistakes.forEach((m, i) => (groups[`${m.mode}:${m.symbol}`] ||= []).push(i));
  const dots: Node[] = [];
  for (const [k, idx] of Object.entries(groups)) {
    const c = marketAt[k]; if (!c) continue;
    const R = Math.min(u * 0.1, 6 + Math.sqrt(idx.length) * 1.7);
    idx.forEach((mi, n) => {
      const rr = R * Math.sqrt((n + 0.5) / idx.length), aa = n * 2.39996 + hash(k) * 6;
      dots.push({ kind: "mistake", key: `m:${mi}`, i: mi, x: c.x + Math.cos(aa) * rr, y: c.y + Math.sin(aa) * rr, r: 1 + Math.min(Math.abs(g.mistakes[mi].r), 2) * 0.8 });
    });
  }
  // lessons on the outer ring, next to the market or style they are about
  const used: Record<string, number> = {};
  const lessonEdges: [Node, Node][] = [];
  g.lessons.forEach((l, i) => {
    const anchor = (l.when.symbol && marketAt[`${l.when.mode}:${l.when.symbol}`]) || nodes.find((n) => n.key === `mode:${l.when.mode}`) || nodes[0];
    const base = anchor.a ?? -Math.PI / 2;
    const n = (used[anchor.key] = (used[anchor.key] ?? -1) + 1);
    const a = base + (n % 2 ? 1 : -1) * Math.ceil(n / 2) * 0.17;
    const [x, y] = at(a, u * 0.84);
    const ln: Node = { kind: "lesson", key: `lesson:${l.id}`, i, x, y, r: l.status === "active" ? 7 : 5, a };
    nodes.push(ln); lessonEdges.push([anchor, ln]);
  });
  // the outer orbit: every strategy tested
  g.studied.forEach((s, i) => {
    const a = -Math.PI / 2 + (i + 0.5) * ((Math.PI * 2) / Math.max(g.studied.length, 1));
    const [x, y] = at(a, u * 0.99);
    nodes.push({ kind: "studied", key: `studied:${s.key}`, i, x, y, r: 4, a, label: s.name });
  });
  return { nodes, dots, edges, lessonEdges, cx, cy, u };
}

export function BrainMapSection() {
  const [g, setG] = useState<Graph>();
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const glow = useRef<Record<string, number>>({});
  const [banner, setBanner] = useState<{ text: string; kind: string; at: number }>();

  const load = () => fetch(`${API}/api/brain/graph`).then((r) => r.json()).then((d: Graph) => { setG(d); setErr(""); return d; })
    .catch(() => { setErr("Can't reach Ras."); return undefined; });
  const light = (events: Event[], d?: Graph) => {
    const now = performance.now();
    glow.current.core = now;
    for (const e of events) {
      if (e.lesson) glow.current[`lesson:${e.lesson}`] = now;
      if (e.symbol && d) for (const m of new Set(d.mistakes.map((x) => x.mode))) glow.current[`market:${m}:${e.symbol}`] = now;
    }
    const top = events.find((e) => e.kind === "learnt") ?? events[0];
    if (top) setBanner({ text: top.text, kind: top.kind, at: Date.now() });
  };

  useEffect(() => {
    load().then((d) => {
      if (!d) return;
      const recent = d.log.filter((e) => e.time > Date.now() - 15 * 60_000);
      if (recent.length) light(recent, d);
      else glow.current.core = performance.now();
    });
    return onEvent((e) => {
      if (e.type === "brain_learn") load().then((d) => light((e as unknown as { events: Event[] }).events, d));
      else if (e.type === "brain") load().then(() => { glow.current.core = performance.now(); });
      else if (e.type === "strategy_bt" && (e as { status?: string }).status === "done")
        load().then((d) => {
          const s = d?.studied.slice().sort((a, b) => b.time - a.time)[0];
          if (s) { glow.current[`studied:${s.key}`] = performance.now(); light([{ time: Date.now(), kind: "studied", text: `Studied ${s.name}: ${s.verdict ?? "tested"}` }]); }
        });
    });
  }, []);
  useEffect(() => { if (!banner) return; const t = setTimeout(() => setBanner(undefined), 7000); return () => clearTimeout(t); }, [banner]);

  if (!g) return <Panel title="BRAIN MAP"><p className="text-sm text-slate-400">{err || "Waking the brain map…"}</p></Panel>;
  return (
    <>
      <Panel title="BRAIN MAP · EVERYTHING IT REMEMBERS AND HAS LEARNT" right={<span className="normal-case tracking-normal text-slate-500">hover to read · click to filter</span>}>
        <Map g={g} glow={glow} banner={banner} onPick={setFilter} filter={filter} />
      </Panel>
      <div className="grid gap-4 xl:grid-cols-2">
        <LearningLog g={g} />
        <EveryMistake g={g} filter={filter} setFilter={setFilter} />
      </div>
    </>
  );
}

function Map({ g, glow, banner, onPick, filter }: { g: Graph; glow: React.RefObject<Record<string, number>>; banner?: { text: string; kind: string };
  onPick: (f: Filter) => void; filter: Filter }) {
  const wrap = useRef<HTMLDivElement>(null);
  const cv = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [hover, setHover] = useState<Node>();
  const hoverRef = useRef<Node | undefined>(undefined);
  hoverRef.current = hover;
  const filterRef = useRef(filter);
  filterRef.current = filter;

  useEffect(() => {
    const el = wrap.current; if (!el) return;
    const ro = new ResizeObserver(() => { const w = el.clientWidth; setSize({ w, h: Math.round(Math.max(380, Math.min(660, w * 0.62))) }); });
    ro.observe(el); return () => ro.disconnect();
  }, []);
  const L = useMemo(() => layout(g, size.w, size.h), [g, size]);

  useEffect(() => {
    const c = cv.current; if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const ctx = c.getContext("2d")!;
    const col = { acc: css("--color-hud-accent") || "#4cc2ff", red: css("--color-hud-red") || "#f87171", amb: css("--color-hud-amber") || "#fbbf24",
      grn: css("--color-hud-green") || "#34d399", dim: css("--color-slate-500") || "#8391a7", ink: css("--color-slate-100") || "#f1f5f9" };
    let raf = 0;
    const draw = (now: number) => {
      const t = REDUCED ? 0 : now / 1000;
      const gl = (k: string) => { const s = glow.current?.[k]; return s ? Math.max(0, 1 - (now - s) / GLOW_MS) : 0; };
      const hv = hoverRef.current, f = filterRef.current;
      const hiLesson = hv?.kind === "lesson" ? hv.i : f.kind === "lesson" ? f.i : undefined;
      const hiMarket = hv?.kind === "market" ? hv.key : f.kind === "market" ? `market:${f.mode}:${f.symbol}` : undefined;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.w, size.h);
      const { cx, cy, u } = L;
      // HUD rings and spokes
      ctx.lineWidth = 1;
      for (const [rr, sp, dash] of [[0.27, 0.05, [2, 6]], [0.58, -0.03, [10, 8]], [0.84, 0.02, [1, 5]], [0.99, -0.015, [24, 10, 2, 10]]] as const) {
        ctx.save(); ctx.translate(cx, cy); ctx.rotate(t * sp);
        ctx.setLineDash(dash as unknown as number[]); ctx.strokeStyle = hexA(col.acc, 0.16);
        ctx.beginPath(); ctx.arc(0, 0, u * rr, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
      }
      ctx.setLineDash([]);
      ctx.strokeStyle = hexA(col.acc, 0.05);
      for (let k = 0; k < 24; k++) { const a = (k / 24) * Math.PI * 2; ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * u * 0.12, cy + Math.sin(a) * u * 0.12); ctx.lineTo(cx + Math.cos(a) * u, cy + Math.sin(a) * u); ctx.stroke(); }
      // a radar sweep
      if (!REDUCED) {
        const a = (t * 0.6) % (Math.PI * 2);
        const grad = ctx.createConicGradient ? ctx.createConicGradient(a - 0.5, cx, cy) : null;
        if (grad) { grad.addColorStop(0, hexA(col.acc, 0)); grad.addColorStop(0.08, hexA(col.acc, 0.07)); grad.addColorStop(0.081, hexA(col.acc, 0)); ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(cx, cy, u, 0, Math.PI * 2); ctx.fill(); }
      }
      // wiring with travelling sparks
      for (const [a, b] of L.edges) {
        ctx.strokeStyle = hexA(col.acc, 0.28); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        if (!REDUCED) { const p = (t * 0.35 + hash(b.key)) % 1; ctx.fillStyle = hexA(col.acc, 0.9); ctx.beginPath(); ctx.arc(a.x + (b.x - a.x) * p, a.y + (b.y - a.y) * p, 1.6, 0, Math.PI * 2); ctx.fill(); }
      }
      for (const [a, b] of L.lessonEdges) {
        const l = g.lessons[b.i!]; const on = l.status === "active";
        const glw = gl(b.key), hi = hiLesson === b.i;
        ctx.strokeStyle = hexA(on ? col.acc : l.status === "watching" ? col.amb : col.dim, hi ? 0.9 : on ? 0.35 + glw * 0.6 : 0.14);
        ctx.lineWidth = hi ? 1.6 : 1;
        const mx = (a.x + b.x) / 2 + (b.y - a.y) * 0.12, my = (a.y + b.y) / 2 - (b.x - a.x) * 0.12;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(mx, my, b.x, b.y); ctx.stroke();
      }
      // every mistake
      ctx.globalCompositeOperation = "lighter";
      for (const d of L.dots) {
        const m = g.mistakes[d.i!];
        const covered = m.lessons.length > 0;
        const lit = (hiLesson !== undefined && m.lessons.includes(hiLesson)) || hiMarket === `market:${m.mode}:${m.symbol}`;
        const live = m.source === "live";
        const j = REDUCED ? 0 : Math.sin(t * 1.3 + d.i!) * 0.6;
        ctx.fillStyle = hexA(lit ? col.amb : col.red, lit ? 0.95 : live ? 0.85 : covered ? 0.55 : 0.32);
        ctx.beginPath(); ctx.arc(d.x + j, d.y - j, lit ? d.r + 0.8 : d.r, 0, Math.PI * 2); ctx.fill();
        if (live) { ctx.strokeStyle = hexA(col.red, 0.8); ctx.beginPath(); ctx.arc(d.x + j, d.y - j, d.r + 2.2, 0, Math.PI * 2); ctx.stroke(); }
      }
      ctx.globalCompositeOperation = "source-over";
      // nodes
      for (const n of L.nodes) {
        const glw = gl(n.key);
        if (n.kind === "core") {
          const cg = gl("core"), pulse = REDUCED ? 0.5 : 0.5 + Math.sin(t * 2) * 0.5;
          if (cg > 0) { // shockwave
            const p = 1 - cg;
            ctx.strokeStyle = hexA(col.acc, cg * 0.8); ctx.lineWidth = 2;
            ctx.beginPath(); ctx.arc(cx, cy, 20 + p * u, 0, Math.PI * 2); ctx.stroke();
            ctx.beginPath(); ctx.arc(cx, cy, 20 + Math.max(0, p - 0.15) * u, 0, Math.PI * 2); ctx.stroke();
          }
          const rg = ctx.createRadialGradient(cx, cy, 2, cx, cy, 60 + cg * 50);
          rg.addColorStop(0, hexA(col.acc, 0.45 + cg * 0.5)); rg.addColorStop(1, hexA(col.acc, 0));
          ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, 60 + cg * 50, 0, Math.PI * 2); ctx.fill();
          for (const [rr, rot, w] of [[26, t * 0.8, 1.5], [33, -t * 0.5, 1]] as const) {
            ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot); ctx.strokeStyle = hexA(col.acc, 0.85); ctx.lineWidth = w;
            ctx.beginPath(); for (let k = 0; k <= 6; k++) { const a = (k / 6) * Math.PI * 2; const px = Math.cos(a) * rr, py = Math.sin(a) * rr; if (k) ctx.lineTo(px, py); else ctx.moveTo(px, py); }
            ctx.stroke(); ctx.restore();
          }
          ctx.shadowColor = col.acc; ctx.shadowBlur = 18 + pulse * 10 + cg * 30;
          ctx.fillStyle = hexA(col.acc, 0.95); ctx.beginPath(); ctx.arc(cx, cy, 9 + pulse * 2, 0, Math.PI * 2); ctx.fill();
          ctx.shadowBlur = 0;
          ctx.fillStyle = col.ink; ctx.font = "600 11px 'JetBrains Mono', ui-monospace, Menlo, monospace"; ctx.textAlign = "center"; ctx.fillText("RAS", cx, cy + 50);
          continue;
        }
        let fill = col.acc, a = 0.9, blur = 0;
        if (n.kind === "market") { const hi = hiMarket === n.key; fill = hi ? col.amb : col.acc; blur = 8 + glw * 30; a = 1; }
        if (n.kind === "mode") blur = 12;
        if (n.kind === "lesson") {
          const l = g.lessons[n.i!];
          fill = l.status === "active" ? (l.off ? col.dim : col.acc) : l.status === "watching" ? col.amb : col.dim;
          a = l.status === "rejected" ? 0.45 : 1; blur = l.status === "active" ? 14 : 0;
          if (hiLesson === n.i) blur = 24;
        }
        if (n.kind === "studied") { const s = g.studied[n.i!]; fill = s.verdict === "works" ? col.grn : s.verdict === "doesn't work" ? col.red : col.dim; a = 0.85; }
        blur += glw * 40;
        ctx.shadowColor = fill; ctx.shadowBlur = blur;
        ctx.fillStyle = hexA(fill, a);
        ctx.beginPath();
        if (n.kind === "studied") { ctx.moveTo(n.x, n.y - n.r); ctx.lineTo(n.x + n.r, n.y); ctx.lineTo(n.x, n.y + n.r); ctx.lineTo(n.x - n.r, n.y); ctx.closePath(); }
        else ctx.arc(n.x, n.y, n.r + glw * 4, 0, Math.PI * 2);
        ctx.fill();
        if (glw > 0) { ctx.strokeStyle = hexA(fill, glw); ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 6 + (1 - glw) * 16, 0, Math.PI * 2); ctx.stroke(); }
        ctx.shadowBlur = 0;
        if (n.kind === "mode" || n.kind === "market") {
          ctx.fillStyle = n.kind === "mode" ? col.ink : hexA(col.ink, 0.75);
          ctx.font = `${n.kind === "mode" ? "600 12px" : "11px"} Inter, -apple-system, system-ui, sans-serif`; ctx.textAlign = "center";
          const out = n.kind === "market" ? 1.0 : 0;
          const lx = n.x + Math.cos(n.a ?? 0) * 18 * out, ly = n.y + (n.kind === "mode" ? -16 : Math.sin(n.a ?? 0) * 18 * out - 10);
          ctx.fillText(n.kind === "mode" ? n.label!.toUpperCase() : n.label!, lx, ly);
        }
      }
      if (!REDUCED || Object.values(glow.current ?? {}).some((s) => now - s < GLOW_MS)) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [L, size, g, glow]);

  const pick = (e: React.MouseEvent) => {
    const r = cv.current!.getBoundingClientRect(); const x = e.clientX - r.left, y = e.clientY - r.top;
    let best: Node | undefined, bd = 12;
    for (const n of [...L.nodes, ...L.dots]) { const d = Math.hypot(n.x - x, n.y - y) - (n.kind === "mistake" ? 2 : n.r); if (d < bd) { bd = d; best = n; } }
    return best;
  };
  const tip = hover && tipText(hover, g);
  const acting = g.lessons.filter((l) => l.status === "active").length;
  return (
    <div ref={wrap} className="relative w-full overflow-hidden rounded-lg" style={{ height: size.h, background: "radial-gradient(ellipse at center, var(--glow) 0%, transparent 70%)" }}>
      <canvas ref={cv} style={{ width: size.w, height: size.h }} className="block"
        onMouseMove={(e) => setHover(pick(e))} onMouseLeave={() => setHover(undefined)}
        onClick={(e) => { const n = pick(e); if (n?.kind === "lesson") onPick({ kind: "lesson", i: n.i! }); else if (n?.kind === "market") { const [, m, s] = n.key.split(":"); onPick({ kind: "market", mode: m, symbol: s }); } else if (n?.kind === "core") onPick({ kind: "all" }); }}
        role="img" aria-label={`Brain map: ${g.memory} trades remembered, ${g.mistakes.length} mistakes, ${acting} lessons acting, ${g.studied.length} strategies tested`} />
      <div className="pointer-events-none absolute left-3 top-3 hidden flex-col gap-0.5 sm:flex text-[11px] uppercase tracking-widest text-slate-400">
        <span>Memory <b className="tabular-nums text-slate-100">{g.memory.toLocaleString()}</b></span>
        <span>Mistakes <b className="tabular-nums text-hud-red">{g.mistakes.length.toLocaleString()}</b></span>
        <span>Lessons acting <b className="tabular-nums text-hud-accent">{acting}</b> / {g.lessons.length}</span>
        <span>Strategies tested <b className="tabular-nums text-slate-100">{g.studied.length}</b></span>
        <span>Win model <b className={g.model.earned ? "text-hud-green" : "text-slate-500"}>{g.model.earned ? "has a say" : "no say"}</b></span>
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-hud-red/70" />mistake (ringed = live paper)</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-hud-accent" />lesson acting</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-hud-amber" />waiting</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-slate-500" />rejected</span>
        <span><i className="mr-1 inline-block h-2 w-2 rotate-45 bg-slate-500" />strategy tested</span>
      </div>
      {banner && (
        <div className="brain-banner pointer-events-none absolute bottom-10 left-1/2 w-max max-w-[min(32rem,90%)] -translate-x-1/2 rounded-lg border border-hud-accent/60 bg-hud-bg/85 px-4 py-2 text-center shadow-[0_0_30px_-4px_var(--color-hud-accent)] backdrop-blur">
          <div className="text-[10px] font-semibold uppercase tracking-[0.3em] text-hud-accent">{banner.kind === "mistake" ? "Mistake remembered" : banner.kind === "studied" ? "Strategy studied" : "New learning"}</div>
          <div className="text-sm text-slate-100">{banner.text}</div>
        </div>
      )}
      {tip && hover && (
        <div className="pointer-events-none absolute z-10 max-w-xs rounded-lg border border-hud-line bg-hud-bg/95 px-3 py-2 text-xs text-slate-300 shadow-xl backdrop-blur"
          style={{ left: Math.min(hover.x + 14, size.w - 260), top: Math.min(hover.y + 14, size.h - 110) }}>
          <div className="mb-0.5 font-semibold text-slate-100">{tip[0]}</div>{tip.slice(1).map((s, i) => <div key={i}>{s}</div>)}
        </div>
      )}
    </div>
  );
}

function tipText(n: Node, g: Graph): string[] | undefined {
  if (n.kind === "core") return ["Ras", `${g.memory.toLocaleString()} trades remembered, ${g.wins.toLocaleString()} won.`, "Click to show every mistake."];
  if (n.kind === "mode") { const ms = g.mistakes.filter((m) => m.mode === n.label); return [`${n.label} trading`, `${ms.length} mistakes remembered`]; }
  if (n.kind === "market") {
    const [, mode, sym] = n.key.split(":"); const ms = g.mistakes.filter((m) => m.mode === mode && m.symbol === sym);
    return [`${name(sym)} · ${mode}`, `${ms.length} losing trades, ${ms.reduce((a, m) => a + m.r, 0).toFixed(1)}R in all`, "Click to list them."];
  }
  if (n.kind === "lesson") {
    const l = g.lessons[n.i!]; const st = { active: l.off ? "acting, switched off by you" : "acting on paper trades", watching: "waiting for more trades", rejected: "rejected: it didn't hold" }[l.status];
    return [l.text, st, `Older trades ${l.find.avg_r.toFixed(2)}R a trade on ${l.find.trades}`, l.check.trades ? `Newer trades ${l.check.avg_r?.toFixed(2)}R on ${l.check.trades}` : "No newer trades yet", "Click to see the mistakes it covers."];
  }
  if (n.kind === "studied") { const s = g.studied[n.i!]; return [s.name, `${s.group} · ${s.verdict ?? "tested"}`, s.avg_r != null ? `${s.avg_r >= 0 ? "+" : ""}${s.avg_r.toFixed(2)}R a trade on ${s.trades} trades` : "", `Tested ${when(s.time)}`].filter(Boolean); }
  if (n.kind === "mistake") { const m = g.mistakes[n.i!]; return [`${m.r.toFixed(2)}R · ${name(m.symbol)} ${m.mode}`, m.note, `${when(m.time)} · ${m.source === "live" ? "live paper trade" : "replayed signal"}`, m.lessons.length ? `Covered by: ${m.lessons.map((i) => g.lessons[i].text).join(" ")}` : "No lesson covers this one yet."]; }
}

const KIND: Record<string, [string, string]> = {
  learnt: ["Learnt", "text-hud-accent border-hud-accent/50"], noticed: ["Noticed", "text-hud-amber border-hud-amber/50"],
  rejected: ["Rejected", "text-slate-400 border-hud-line"], dropped: ["Dropped", "text-hud-red border-hud-red/50"],
  mistake: ["Mistake", "text-hud-red border-hud-red/50"], model: ["Model", "text-hud-green border-hud-green/50"],
  awake: ["Woke up", "text-hud-accent border-hud-accent/50"], studied: ["Studied", "text-slate-200 border-hud-line"],
};

function LearningLog({ g }: { g: Graph }) {
  const [n, setN] = useState(40);
  const all = useMemo(() => [...g.log, ...g.studied.map((s): Event => ({ time: s.time, kind: "studied",
    text: `Tested ${s.name}${s.group ? ` (${s.group})` : ""}: ${s.verdict ?? "done"}${s.avg_r != null ? `, ${s.avg_r >= 0 ? "+" : ""}${s.avg_r.toFixed(2)}R a trade on ${s.trades}` : ""}.` }))]
    .sort((a, b) => b.time - a.time), [g]);
  return (
    <Panel title={`LEARNING LOG · ${all.length} ENTRIES`}>
      <ol className="relative flex max-h-[34rem] flex-col gap-3 overflow-y-auto border-l border-hud-line pl-4 pr-1">
        {all.slice(0, n).map((e, i) => {
          const [label, tone] = KIND[e.kind] ?? [e.kind, "text-slate-400 border-hud-line"];
          return (
            <li key={i} className="relative">
              <span className={`absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 bg-hud-panel ${tone}`} />
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className={`rounded border px-1.5 py-px text-[10px] uppercase tracking-wider ${tone}`}>{label}</span>
                <span className="tabular-nums text-slate-500">{when(e.time)}</span>
              </div>
              <p className="mt-0.5 text-sm text-slate-200">{e.text}</p>
              {e.why && <p className="text-xs text-slate-500">Why: {e.why}.</p>}
            </li>
          );
        })}
      </ol>
      {all.length > n && <button onClick={() => setN(n + 60)} className="mt-2 self-start rounded border border-hud-line px-3 py-1 text-xs text-hud-accent hover:border-hud-accent">Show older</button>}
    </Panel>
  );
}

function EveryMistake({ g, filter, setFilter }: { g: Graph; filter: Filter; setFilter: (f: Filter) => void }) {
  const [n, setN] = useState(60);
  const [src, setSrc] = useState<"all" | "live" | "backtest">("all");
  useEffect(() => setN(60), [filter, src]);
  const rows = g.mistakes.filter((m) => (src === "all" || m.source === src)
    && (filter.kind === "all" || (filter.kind === "market" ? m.mode === filter.mode && m.symbol === filter.symbol : m.lessons.includes(filter.i))));
  const total = rows.reduce((a, m) => a + m.r, 0);
  const chip = (on: boolean) => `rounded-full border px-2.5 py-0.5 text-xs ${on ? "border-hud-accent/60 bg-hud-accent/15 text-hud-accent" : "border-hud-line text-slate-400 hover:text-slate-200"}`;
  return (
    <Panel title={`EVERY MISTAKE · ${rows.length.toLocaleString()} LOSING TRADES · ${total.toFixed(1)}R`}>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {(["all", "live", "backtest"] as const).map((s) => <button key={s} className={chip(src === s)} onClick={() => setSrc(s)}>{s === "all" ? "All" : s === "live" ? "Live paper" : "Replays"}</button>)}
        {filter.kind !== "all" && (
          <button className={chip(true)} onClick={() => setFilter({ kind: "all" })}>
            {filter.kind === "market" ? `${name(filter.symbol)} · ${filter.mode}` : `Lesson: ${g.lessons[filter.i]?.text.slice(0, 40)}…`} ✕
          </button>
        )}
      </div>
      <ul className="flex max-h-[34rem] flex-col divide-y divide-hud-line overflow-y-auto pr-1 text-sm">
        {rows.slice(0, n).map((m, i) => (
          <li key={i} className="py-1.5">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-slate-200">{m.note}</span>
              <span className="shrink-0 tabular-nums text-hud-red">{m.r.toFixed(2)}R</span>
            </div>
            <div className="text-xs text-slate-500">
              {when(m.time)} · {m.source === "live" ? <span className="text-hud-amber">live paper</span> : "replayed signal"}
              {m.lessons.length > 0 && <span className="text-hud-accent"> · lesson now: {m.lessons.map((x) => g.lessons[x].text).join(" ")}</span>}
            </div>
          </li>
        ))}
        {!rows.length && <li className="py-2 text-slate-400">No mistakes here.</li>}
      </ul>
      {rows.length > n && <button onClick={() => setN(n + 100)} className="mt-2 self-start rounded border border-hud-line px-3 py-1 text-xs text-hud-accent hover:border-hud-accent">Show {Math.min(100, rows.length - n)} more</button>}
    </Panel>
  );
}
