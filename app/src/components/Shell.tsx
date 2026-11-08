/** The app frame: a bot console. Left rail with grouped sections and a live "what is Ras doing" card; on phones the
 *  same sections become a scrolling strip under the top bar. */
import type { HaltState, PaperState, Status } from "../ws";

const P: Record<string, string> = {
  home: "M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10",
  live: "M12 3a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V7a4 4 0 0 1 4-4zM5 21a7 7 0 0 1 14 0",
  performance: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  journal: "M5 4h11l3 3v13H5zM9 9h6M9 13h6M9 17h4",
  backtest: "M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 7v5l3 2",
  strategies: "M4 6h10M4 12h16M4 18h7M17 4v4M14 16v4",
  ras11: "M12 2l3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z",
  books: "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 19V5M9 7h6",
  test101: "M9 3h6M10 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4A1.5 1.5 0 0 0 20 19l-6-10V3",
  brain: "M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a2 2 0 0 0-3-1zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
};

export function Icon({ name, className = "h-4 w-4" }: { name: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={P[name] ?? P.home} />
    </svg>
  );
}

export const GROUPS = [
  { name: "Trade", tabs: [["home", "Station"], ["live", "Ras Live"], ["performance", "Performance"], ["journal", "Journal"]] },
  { name: "Research", tabs: [["backtest", "Backtest"], ["strategies", "Strategies"], ["ras11", "Ras 1.1"], ["books", "Books"], ["test101", "TEST 101"]] },
  { name: "Bot", tabs: [["brain", "Brain"], ["settings", "Settings"]] },
] as const;
export type TabKey = (typeof GROUPS)[number]["tabs"][number][0];
const ALL = GROUPS.flatMap((g) => g.tabs as readonly (readonly [TabKey, string])[]);
export const TAB_NAME = Object.fromEntries(GROUPS.flatMap((g) => g.tabs.map(([k, l]) => [k, [l, g.name]]))) as Record<TabKey, [string, string]>;

type BotProps = { online: boolean; capital?: Status; halt: HaltState; paper?: PaperState };

/** What the bot is doing right now, in one glance. */
export function botState({ online, capital, halt }: BotProps) {
  if (!online) return { tone: "red", label: "Offline", detail: "The Ras engine isn't answering." };
  if (halt.active) return { tone: "red", label: "Halted", detail: "Kill switch on: no new signals." };
  if (capital?.state === "live") return { tone: "green", label: "Watching markets", detail: "Prices live from Capital.com demo." };
  if (capital?.state === "no_credentials") return { tone: "amber", label: "Needs keys", detail: "Capital.com sign-in missing." };
  return { tone: "amber", label: "Connecting", detail: capital?.detail || "Waiting for prices." };
}
const TONE = { green: "bg-hud-green", amber: "bg-hud-amber", red: "bg-hud-red" } as const;

export function BotCard(p: BotProps) {
  const s = botState(p);
  const acct = p.paper && Object.values(p.paper.accounts)[0];
  const pnl = acct ? acct.equity - acct.start : null;
  return (
    <div className="rounded-xl border border-hud-line bg-hud-bg/60 p-3">
      <div className="flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5">
          {s.tone === "green" && <span className={`bot-ping absolute inline-flex h-full w-full rounded-full ${TONE[s.tone]} opacity-60`} />}
          <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${TONE[s.tone as keyof typeof TONE]}`} />
        </span>
        <span className="text-sm font-semibold text-slate-100">{s.label}</span>
      </div>
      <p className="mt-1 text-xs leading-snug text-slate-400">{s.detail}</p>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg bg-hud-panel px-2 py-1.5">
          <div className="text-slate-500">Paper equity</div>
          <div className="tabular-nums text-slate-100">{acct ? `$${acct.equity.toFixed(2)}` : "—"}</div>
        </div>
        <div className="rounded-lg bg-hud-panel px-2 py-1.5">
          <div className="text-slate-500">Open</div>
          <div className="tabular-nums text-slate-100">
            {p.paper ? p.paper.open.length : "—"}
            {pnl != null && <span className={`ml-1 ${pnl >= 0 ? "text-hud-green" : "text-hud-red"}`}>{pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}</span>}
          </div>
        </div>
      </div>
      <p className="mt-2 inline-flex items-center gap-1 rounded-full border border-hud-accent/40 px-2 py-0.5 text-[11px] text-hud-accent">PAPER ONLY · no real orders</p>
    </div>
  );
}

export function Sidebar({ active, onPick, bot, footer }: { active: string; onPick: (k: TabKey) => void; bot: BotProps; footer: React.ReactNode }) {
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col gap-4 overflow-y-auto border-r border-hud-line bg-hud-panel/70 p-4 backdrop-blur md:flex">
      <div className="flex items-center gap-2 px-1">
        <img src="/favicon.png" alt="" className="h-8 w-8 rounded-lg border border-hud-line object-contain p-0.5" />
        <div>
          <div className="text-base font-bold tracking-[0.35em] text-hud-accent">RAS</div>
          <div className="text-[11px] text-slate-500">trading bot · paper</div>
        </div>
      </div>
      <BotCard {...bot} />
      <nav className="flex flex-col gap-4" aria-label="Sections">
        {GROUPS.map((g) => (
          <div key={g.name}>
            <div className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-widest text-slate-500">{g.name}</div>
            <ul className="flex flex-col gap-0.5">
              {g.tabs.map(([k, label]) => {
                const on = active === k;
                return (
                  <li key={k}>
                    <button onClick={() => onPick(k)} aria-current={on ? "page" : undefined}
                      className={`nav-item flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm ${on ? "bg-hud-accent/15 text-hud-accent" : "text-slate-300 hover:bg-hud-accent/5 hover:text-slate-100"}`}>
                      <Icon name={k} className={`h-4 w-4 ${on ? "" : "text-slate-500"}`} />
                      {label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="mt-auto flex flex-col gap-2">{footer}</div>
    </aside>
  );
}

/** Phones and narrow windows: the sections as one scrolling strip. */
export function MobileNav({ active, onPick }: { active: string; onPick: (k: TabKey) => void }) {
  return (
    <nav className="-mx-4 flex gap-1 overflow-x-auto px-4 pb-1 md:hidden" aria-label="Sections">
      {ALL.map(([k, label]) => (
        <button key={k} onClick={() => onPick(k)} aria-current={active === k ? "page" : undefined}
          className={`nav-item flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm ${active === k ? "border-hud-accent/50 bg-hud-accent/15 text-hud-accent" : "border-hud-line text-slate-400"}`}>
          <Icon name={k} className="h-3.5 w-3.5" />{label}
        </button>
      ))}
    </nav>
  );
}
