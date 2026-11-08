# JARVIS — Local Trading Intelligence Station

Human-in-the-loop only: JARVIS analyses and recommends; it never places trades on its own.

## Run
- **macOS:** double-click `jarvis.command` (or `./jarvis.command`)
- **Windows:** double-click `jarvis.bat`

Needs: Node 20+, [uv](https://docs.astral.sh/uv/), Rust (rustup). First launch compiles the shell (~1–2 min).

## Credentials (OS keychain, never files)
Capital.com → Settings → API integrations → generate key (demo account, 2FA on). Then:
```
cd backend && uv run python -m jarvis.keys
```
If the Capital.com pill shows a login error, quit JARVIS and run `uv run python -m jarvis.capital_check` (in `backend/`):
it tries your stored key on Demo and on Live, explains each answer, and switches JARVIS to the one that works.

The same prompt optionally stores an **Anthropic API key** (news analysis with Claude Haiku; without it, keyword rules).
Restart the app. Crypto (Binance public data) and all macro/news feeds need no key.

## Layout
- `config.yaml` — instruments (symbol → Capital.com epic), crypto pair, port
- `backend/jarvis/` — FastAPI (`main.py`), event bus (`bus.py`), feeds (`feeds/capital.py`, `feeds/crypto.py`)
- `app/` — React + Vite + Tailwind HUD; `app/src-tauri/` desktop shell; `app/scripts/dev.mjs` starts backend + UI together

## Tests
```
cd backend && uv run pytest
```

## Signal Station (Phase 2)
Every closed 15M candle, per instrument (intraday mode):
1. **Technical agent** (`station/agents.py`) — 4H bias (EMA50/200), 1H structure (EMA21/50 + slope, ADX), 15M pullback-to-EMA21 trigger. Builds entry zone, stop (1.5×ATR 1H), TP1/TP2 (2R/3R), invalidation.
2. **Risk Officer** — vetoes on stale data/closed market, missing quote, spread > max, net R:R < 1.5, chop (1H ADX < 18), 15M volatility spike.
3. **Commander** (`station/commander.py`) — weighted vote → BUY/SELL if confidence ≥ 60, no conflict, no veto; otherwise HOLD with the reasons and what would change it.
4. **Store** (`station/store.py`, `data/jarvis.sqlite`) — BUY/SELL signals tracked live to hit TP1 / hit SL / expired (12h) / invalidated (1H close beyond level, or an opposite call).

Thresholds live under `station:` in `config.yaml`. REST: `GET /api/candles/{symbol}`, `GET /api/signals/{symbol}`.

## Fundamentals, calendar, briefings (Phase 3)
Free, keyless sources: ForexFactory weekly calendar, FRED (US 2Y / 10Y / 10Y real), CFTC COT (weekly), RSS
(FXStreet, CNBC, OilPrice, Google News), Capital.com client sentiment, DXY computed from Capital.com FX pairs.

- **Macro agent** — USD strength (DXY 24h + US 2Y 5d) mapped per instrument; gold/silver add real yields, USD/JPY adds 10Y and an intervention flag.
- **News agent** — Claude Haiku (`news.model`) reads up to 80 recent headlines every ≥15 min → per-instrument bias + risk mood (structured output). Falls back to keyword rules.
- **Sentiment agent** — contrarian at extremes: retail ≥70% / ≤30% long, COT speculators at 3-year 90th/10th percentile.
- **Commander** — technicals must have a setup; fundamentals add/subtract confidence (`support_factor`), strong opposition (`conflict_score`) forces HOLD.
- **Risk Officer** — news blackout 30 min before / 15 min after high-impact events for the instrument's currencies (and crude inventories for WTI).
- **Briefings** — morning (08:00) and end-of-day (23:30) Bahrain time, or on demand from the Briefing panel. Stored in SQLite; spoken in Phase 6.

## Scalping + paper trading (Phase 4)
**Shared framework** (`backend/jarvis/scalp/`): strategy plugins implement `Strategy.evaluate(ScalpInput) -> Setup | None`
and declare the regimes they run in (trend / range / chop, from 5M ADX + efficiency ratio). Setups are combined by
confluence (+10 per agreeing strategy, −15 per opposing), levels are ATR-based (stop ≥ 1×ATR 5M, TP1/TP2 1.8R/2.5R),
then the same commander + a scalp Risk Officer decide.

- **Signal Station scalp mode** (per-instrument switch in each room; both modes can run together): 15M bias / 5M structure /
  1M entry on Capital.com bid/ask candles. Strategies: London ORB, Asian range sweep, session VWAP, EMA 9/21 pullback,
  round numbers, post-news continuation, oil EIA. Fires only at London open, NY open, the overlap (+ Tokyo for USD/JPY);
  vetoes for rollover, 30/20-min news blackout, max spread, spread > 20% of TP1, live spread widening, DXY spike.
- **Crypto desk**: top-30 USDT spot + perpetuals by volume (hourly; stablecoins, tokenised stocks, new listings excluded),
  live bid/ask websockets, scan every minute. Strategies: VWAP, EMA ribbon 9/21/50, liquidity sweep, Bollinger squeeze
  breakout, order-book imbalance, funding/OI divergence; 1H context, BTC correlation filter, fee + slippage cost check.
- **Scalp risk** (separate per division): daily loss limit (R), max per day / session / concurrent, cooldown after 2 losses,
  correlation guard across intraday + scalp + crypto signals (warns at 2 stacked, vetoes above `max_same_exposure`).
- **Cards**: SCALP BUY/SELL in violet, entry countdown (auto-closes if price runs 0.5R past the entry zone), time stop,
  copy-ready levels in pips, and a one-sentence `say` line for the voice phase.
- **Paper trading** (`paper.py`): every BUY/SELL signal opens a simulated position (`paper.auto_follow`), sized at 0.5% risk of
  a small paper account per division (100 USD for FX, metals and oil; set in config.yaml); fills at ask/bid + slippage, crypto taker fees, stop/target exits on live quotes.
  Never sends an order anywhere.

API: `GET /api/candles?symbol=&tf=`, `GET /api/signals?symbol=`, `POST /api/modes/{symbol}`, `GET /api/sessions`, `GET /api/paper`.

## Backtesting, performance, journal, self-learning (Phase 5)
- **History** (`jarvis/history.py`): candles download on demand into `data/history/<source>/<symbol>_<tf>.parquet`
  (Capital.com bid/ask for FX/metals/oil, Binance OHLCV for crypto); DuckDB can query the files directly.
  CLI: `uv run python -m jarvis.history crypto perp:BTC/USDT:USDT 1m 30`.
- **Backtester** (`jarvis/backtest.py`, Backtest tab): replays history bar by bar through the *live* strategy, agent and
  risk code. Fills at the next bar's open on the right side of the spread plus slippage, crypto fees, stop-before-target
  when both touch in one bar, time stops, max hold, scalp limits. Modes: FX scalp (1M bid/ask), intraday technical (15M),
  crypto scalp (1M). **Walk-forward** (anchored): the score threshold is picked in-sample from `backtest.threshold_grid`
  and judged on the next unseen slice; the out-of-sample report is shown first, the in-sample full run second.
  Reports by instrument / strategy / session / hour, plus calibration.
- **Performance** tab: equity curve (R), win rate, profit factor, avg R / expectancy, max drawdown, per instrument /
  strategy / session / mode, best and worst hours (Bahrain), confidence calibration, paper equity per division.
- **Journal** tab: every signal is journaled automatically with a chart snapshot at signal time, agent votes, reasoning,
  outcome and its paper trade; add your own notes. **Weekly review** every Saturday 10:00 (or on demand) by Claude Opus 5.5
  (`review.model`), with a template fallback when no Anthropic key is stored.
- **Self-learning** (`jarvis/learning.py`): once an agent has ≥ 20 graded votes, its weight moves daily by at most ±0.05
  toward its accuracy (bounded 0.1–1.5). Every change is logged; Freeze / Reset in the Performance tab. The technical
  agent times the trade and is not weighted.

## JARVIS voice + avatar (Phase 6)
Everything runs locally except the LLM call (Claude), with Ollama as the offline fallback.
- **Wake word**: say **"Hey Jarvis"** (openWakeWord's pretrained model — a bare "Jarvis" would need a custom-trained model).
  **Push-to-talk**: hold the <kbd>`</kbd> key, or hold the orb. Or type in the JARVIS panel.
- **Speech**: faster-whisper `base.en` (STT) and Kokoro `bm_george` (British TTS, streamed sentence by sentence).
  Models live in `data/models/`. macOS asks once for microphone permission; until it's granted the panel says so
  and text chat still works.
- **Brain** (`voice/assistant.py`): Claude Opus 5.5 (low effort, server-side fallback) → Ollama `qwen3:8b`. It answers
  with live data through tools — calls and agent votes, why something is HOLD, performance, positions, calendar, news,
  risk status, quiet mode, briefings. It never trades; mute/unmute work without any LLM.
- **Alerts** (`voice/announce.py`): priorities 1 crypto & outcomes · 2 intraday & briefings · 3 FX scalps · 4 risk
  warnings (overtrading, cooldown, daily loss limit, stacked exposure) · 5 replies. "Speak:" in the panel sets the
  threshold; **Mute 20m / 1h** lets only risk warnings through.
- **Avatar** (`app/src/avatar/`): holographic orb (Three.js) reacting to voice level; idle / listening / thinking /
  speaking, cyan normal, amber warning, red risk breach. Swappable via `AVATARS` (e.g. a VRM character later).
- Tests: `uv run pytest` (fast); `JARVIS_VOICE_TESTS=1 uv run pytest -k round_trip` runs the real TTS → wake word → STT loop.
- espeak-ng (used by Kokoro) can't read paths with spaces, so if the project lives under one, its 19 MB data folder
  is copied once to `~/.jarvis/espeak-ng-data`.

## Execution, kill switch, Telegram (Phase 7)
- **Approval-based crypto execution** (`jarvis/execution.py`): USDT perpetuals on **Binance Demo Trading only** (live is not
  selectable in this build). Off by default; turn it on in **Settings** by typing `ENABLE DEMO EXECUTION` (this also checks
  the key works). Each perp signal's crypto room then shows **APPROVE / MODIFY / REJECT** while its entry window is open.
  APPROVE re-checks everything (kill switch, window, price hasn't run > 0.25R, stop/target sides, size ≥ exchange minimum),
  sizes at 0.5% of free USDT (≤ 500 USDT notional, 3x isolated), then sends a market entry + reduce-only stop-loss +
  take-profit (+ optional trailing stop). If the stop can't be placed, the position is closed immediately. A monitor
  cancels the leftover leg when a position closes and records P&L. Spot signals stay paper-only.
- **Keys**: `uv run python -m jarvis.keys` — Binance **demo** API key/secret (trade-only, withdrawals disabled), Telegram bot
  token + chat id. All in the OS keychain.
- **KILL SWITCH**: red button in the header (one confirmation) or "Hey Jarvis, kill switch" → "confirm" within 15 s. Pauses
  every new signal in all divisions, closes all paper positions, cancels every open futures order and closes every futures
  position on the execution account. Stays active across restarts until you press **Resume**.
- **Telegram**: the same alerts JARVIS speaks, priority ≥ `telegram.min_priority` (3: FX scalps + risk), plus every
  execution and kill-switch event. "Send test message" in Settings.

## Installable app (Phase 8)
**Build on a Mac:** `./build-mac.sh` → `app/src-tauri/target/release/bundle/dmg/JARVIS_0.1.0_aarch64.dmg`
(and `bundle/macos/JARVIS.app`). **Build on Windows:** `powershell -ExecutionPolicy Bypass -File .\build-windows.ps1`
→ `bundle\nsis\JARVIS_0.1.0_x64-setup.exe` and `bundle\msi\JARVIS_0.1.0_x64_en-US.msi`. Each OS builds its own installer.

How the installed app works:
- The window starts the bundled backend (PyInstaller, `backend/dist/jarvis-backend`) and stops it when you quit.
- Your data lives in the per-user app folder, not in the app: macOS `~/Library/Application Support/com.jarvis.station/`,
  Windows `%APPDATA%\com.jarvis.station\` — `config.yaml` (copied from the default on first run; edit it in Settings),
  `data/` (signals, journal, history, models), `logs/jarvis.log` + `logs/backend-console.log`.
- Voice models (~480 MB) download on first launch into that folder; the JARVIS panel shows progress. To skip the download,
  copy an existing `data/models` folder there before the first launch.
- Keys stay in the OS keychain (service `jarvis`) — the same ones the dev setup uses. On macOS the first launch asks
  once per stored key whether "jarvis-backend" may read it: choose **Always Allow**. The app keeps running while it asks.
- Unsigned build: macOS may say it's from an unidentified developer (right-click → Open the first time); Windows
  SmartScreen may need "More info → Run anyway". Code signing needs your own Apple / Windows certificates.
- `jarvis-backend --selftest` (inside the app's `Resources/backend` folder) checks the bundled speech engines.
- Don't run the dev launcher and the installed app at the same time: both use port 18765.

Also new in Phase 8: draggable / resizable dashboard panels (layout saved; "Reset layout"), DXY + US-yields mini-charts,
crypto heatmap, funding-rates panel, on-screen alerts with an optional chime, microphone/speaker selection and a
validated `config.yaml` editor in Settings, rotating log files, and the app icon.
