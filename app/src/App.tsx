import { useEffect, useState } from "react";
import { BacktestPage } from "./components/Backtest";
import { StrategiesPage } from "./components/Strategies";
import { SectionGuard } from "./components/SectionGuard";
import { MobileNav, Sidebar, TAB_NAME, botState, type TabKey } from "./components/Shell";
import { BrainPage } from "./components/Brain";
import { BooksPage, Ras11Page } from "./components/Ras11";
import { RasLivePage } from "./components/RasLive";
import { Test101Page } from "./components/Test101";
import { AnalysisOverview } from "./components/Analysis";
import { JournalPage } from "./components/Journal";
import { PerformancePage } from "./components/Performance";
import { INSTRUMENTS, InstrumentTile } from "./components/InstrumentTile";
import { BriefingPanel, CalendarPanel, NewsPanel } from "./components/Panels";
import { PositionsPanel } from "./components/Positions";
import { Room } from "./components/Room";
import { SessionClock } from "./components/SessionClock";
import { VoicePanel } from "./components/VoicePanel";
import { Dashboard, MacroPanel } from "./components/Dashboard";
import { Toasts } from "./components/Toasts";
import { HaltBanner, KillButton, SettingsPage } from "./components/Safety";
import { API, PHONE, saveServer, useFeed, type Status } from "./ws";

const TZ = "Asia/Bahrain";

function Pill({ label, s }: { label: string; s?: Status | { state: string; detail?: string } }) {
  const state = s?.state ?? "unknown";
  const tone = state === "live" ? "text-hud-green border-hud-green/40"
    : state === "no_credentials" || state === "reconnecting" ? "text-hud-amber border-hud-amber/40"
    : "text-hud-red border-hud-red/40";
  return (
    <span title={s?.detail} className={`rounded border px-2 py-0.5 text-xs uppercase ${tone}`}>
      {label}: {state.replace("_", " ")}
    </span>
  );
}

/** First run on a phone, or "Change address": where the Mac running Ras can be reached. */
function Connect() {
  const [address, setAddress] = useState(API);
  return (
    <form className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-3 p-6"
      onSubmit={(e) => { e.preventDefault(); if (address.trim()) saveServer(address); }}>
      <h1 className="text-xl font-bold tracking-[0.4em] text-hud-accent">R · A · S</h1>
      <p className="text-sm text-slate-400">Ras runs on your Mac. Enter the Mac's Tailscale address to connect this phone.</p>
      <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="your-mac.your-tailnet.ts.net"
        autoCapitalize="none" autoCorrect="off" spellCheck={false} inputMode="url" aria-label="Mac address"
        className="rounded border border-hud-line bg-transparent px-3 py-2 text-base text-slate-200" />
      <button className="rounded bg-hud-accent/15 px-3 py-2 text-hud-accent">Connect</button>
    </form>
  );
}

type View = { kind: "home" } | { kind: "room"; symbol: string } | { kind: "performance" } | { kind: "journal" } | { kind: "backtest" } | { kind: "strategies" } | { kind: "ras11" } | { kind: "books" } | { kind: "test101" } | { kind: "brain" } | { kind: "live" } | { kind: "settings" };

export default function App() {
  const [changing, setChanging] = useState(false);
  return PHONE && (!API || changing) ? <Connect /> : <Station onChangeAddress={() => setChanging(true)} />;
}

function Station({ onChangeAddress }: { onChangeAddress: () => void }) {
  const { prices, status, online, calls, signals, calendar, news, briefing, yields, paper, modes, bt, sbt, learning, review, haltState, macro } = useFeed();
  const [view, setView] = useState<View>(() => {
    // a theme switch reloads the window from Settings: land back there
    let back = false;
    try { back = sessionStorage.getItem("ras.view") === "settings"; }  catch { /* storage blocked */ }
    return { kind: back ? "settings" : "home" };
  });
  const [now, setNow] = useState(new Date());
  useEffect(() => { try { sessionStorage.removeItem("ras.view"); } catch { /* storage blocked */ } }, []);
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  const home = () => setView({ kind: "home" });
  const tab: TabKey = view.kind === "room" ? "home" : view.kind;
  const pick = (k: TabKey) => { setView({ kind: k } as View); window.scrollTo({ top: 0 }); };
  const bot = { online, capital: status.capital, halt: haltState, paper };
  const bs = botState(bot);

  return (
    <div className="flex min-h-screen">
      <Sidebar active={tab} onPick={pick} bot={bot}
        footer={<>
          <KillButton />
          <div className="flex flex-wrap gap-1"><Pill label="engine" s={{ state: online ? "live" : "offline" }} /><Pill label="capital.com" s={status.capital} /></div>
        </>} />
      <main className="flex min-w-0 flex-1 flex-col gap-4 p-4 md:px-6">
      <header className="sticky top-0 z-40 -mx-4 flex flex-col gap-2 border-b border-hud-line bg-hud-bg/85 px-4 py-2.5 backdrop-blur md:-mx-6 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-3">
            <span className="text-base font-bold tracking-[0.35em] text-hud-accent md:hidden">RAS</span>
            <span className={`h-2 w-2 shrink-0 rounded-full md:hidden ${bs.tone === "green" ? "bg-hud-green" : bs.tone === "amber" ? "bg-hud-amber" : "bg-hud-red"}`} title={bs.label} />
            <div className="hidden min-w-0 text-sm md:block"><span className="text-slate-500">{TAB_NAME[tab][1]} /</span> <span className="font-semibold text-slate-100">{view.kind === "room" ? `${view.symbol} room` : TAB_NAME[tab][0]}</span></div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!PHONE && <span className="hidden lg:inline-flex"><SessionClock /></span>}
            {status.ras?.state === "error" && <Pill label="ras" s={status.ras} />}
            {status.calendar?.state === "error" && <Pill label="news calendar" s={status.calendar} />}
            {!PHONE && yields.DGS2 && (
              <span className="hidden text-xs tabular-nums text-slate-400 xl:inline" title={`FRED, as of ${yields.DGS2.date}`}>
                US2Y {yields.DGS2.value.toFixed(2)} · 10Y {yields.DGS10?.value.toFixed(2)} · real {yields.DFII10?.value.toFixed(2)}
              </span>
            )}
            <span className="tabular-nums text-sm text-slate-300">
              {now.toLocaleTimeString("en-GB", { timeZone: TZ })} <span className="text-slate-500">Bahrain</span>
            </span>
            <span className="md:hidden"><KillButton /></span>
          </div>
        </div>
        <MobileNav active={tab} onPick={pick} />
      </header>

      <HaltBanner h={haltState} />
      {PHONE && !online && (
        <div className="flex items-center justify-between gap-3 rounded border border-hud-amber/40 bg-hud-amber/5 px-3 py-2 text-sm text-hud-amber">
          Can't reach your Mac. Check that it's awake and Tailscale is on.
          <button className="shrink-0 underline" onClick={onChangeAddress}>Change address</button>
        </div>
      )}
      {status.capital?.state === "no_credentials" && (
        <div className="rounded border border-hud-amber/40 bg-hud-amber/5 px-3 py-2 text-sm text-hud-amber">
          Capital.com credentials missing — in <code>backend/</code> run <code>uv run python -m jarvis.keys</code>, then restart.
        </div>
      )}

      <SectionGuard key={view.kind === "room" ? `room:${view.symbol}` : view.kind} name={view.kind}>
      {view.kind === "settings" ? <SettingsPage halt={haltState} />
        : view.kind === "performance" ? <PerformancePage learning={learning} />
        : view.kind === "journal" ? <JournalPage review={review} />
        : view.kind === "backtest" ? <BacktestPage bt={bt} />
        : view.kind === "strategies" ? <StrategiesPage sbt={sbt} />
        : view.kind === "ras11" ? <Ras11Page sbt={sbt} />
        : view.kind === "books" ? <BooksPage sbt={sbt} />
        : view.kind === "test101" ? <Test101Page />
        : view.kind === "brain" ? <BrainPage />
        : view.kind === "live" ? <RasLivePage prices={prices} paper={paper} halted={haltState.active} />
        : view.kind === "room" ? (
        <Room symbol={view.symbol} price={prices[view.symbol]} calls={calls} signals={signals} modes={modes} calendar={calendar} onBack={home} />
      ) : (
        <Dashboard panels={{
          tiles: (
            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {INSTRUMENTS.map((s) => (
                <InstrumentTile key={s} symbol={s} price={prices[s]} call={calls[`${s}:intraday`]} scalp={calls[`${s}:scalp`]} trend={calls[`${s}:trend`]}
                  mode={modes[s]} onOpen={() => setView({ kind: "room", symbol: s })} />
              ))}
            </section>
          ),
          news: <NewsPanel headlines={news.headlines} view={news.view} />,
          positions: <PositionsPanel paper={paper} />,
          briefing: <BriefingPanel briefing={briefing} />,
          calendar: <CalendarPanel events={calendar} />,
          macro: <MacroPanel m={macro} />,
          analysis: <AnalysisOverview symbols={INSTRUMENTS} onOpen={(s) => setView({ kind: "room", symbol: s })} />,
        }} />
      )}
      </SectionGuard>
      <SectionGuard name="voice"><VoicePanel bodyOnStage={view.kind === "live"} /></SectionGuard>
      <Toasts />
      </main>
    </div>
  );
}
