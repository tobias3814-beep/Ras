"""Books: what three trading books teach, turned into fixed rules and tested on gold and forex in Ras's normal
walk-forward test. Replaces the Legends page.

- Alexander Elder, Trading for a Living (the full book): Triple Screen, Elder-ray, Force Index, channel trading, the 2%
  rule. His entries and stops are written precisely enough to test as stated.
- John J. Murphy, Technical Analysis of the Financial Markets (a lesson summary): moving-average crossovers, the
  four-week rule and oscillator failure swings. Parameters are the book's well-known ones; the summary does not give stops.
- Jack D. Schwager, Market Wizards (a summary): no entry rules, but firm money rules from the traders interviewed. They
  are tested on every rule's trades as a $100 account run with and without them.

Nothing here opens a signal or a paper trade."""
from typing import Any

from jarvis.lab import account as lab_account
from jarvis.lab.ras11 import latest

MARKETS = ["XAU_USD", "EUR_USD", "GBP_USD", "USD_JPY"]
ELDER = "Alexander Elder, Trading for a Living"
MURPHY = "John J. Murphy, Technical Analysis of the Financial Markets"

STRATEGIES: dict[str, dict[str, Any]] = {
    "elder_triple_screen": {
        "book": ELDER, "chapter": "43. Triple Screen Trading System", "tf": "DAY", "days": 5000, "markets": MARKETS, "method": "as written",
        "teaches": "Trade in the direction of the weekly trend (the slope of weekly MACD-Histogram), enter on daily pullbacks against "
                   "it found by an oscillator, and get in with a trailing buy-stop above the previous day's high. Elder calls it his "
                   "main system and says it filters out most of the whipsaws that trend-followers and oscillator traders each suffer.",
    },
    "elder_ray": {
        "book": ELDER, "chapter": "41. Elder-ray", "tf": "DAY", "days": 5000, "markets": MARKETS, "method": "as written",
        "teaches": "Bull Power and Bear Power measure how far the day's high and low get from the 13-day EMA, the average consensus of "
                   "value. Buy when the trend is up and Bear Power is negative but rising; never buy when Bear Power is positive.",
    },
    "elder_channel": {
        "book": ELDER, "chapter": "45. Channel Trading Systems", "tf": "DAY", "days": 5000, "markets": MARKETS, "method": "close reading",
        "teaches": "Amateurs buy breakouts; professionals fade them, because most breakouts from a channel are false. In a flat channel "
                   "buy near the bottom and sell near the top. The channel is sized to hold 90-95% of prices.",
    },
    "murphy_triple_ma": {
        "book": MURPHY, "chapter": "Lesson 7: Moving Averages", "tf": "DAY", "days": 5000, "markets": MARKETS, "method": "close reading",
        "teaches": "Moving averages are the trend-follower's main tool: the shorter average times trades, the longer one identifies the "
                   "trend. The triple crossover (4, 9 and 18 days) waits for all three to line up.",
    },
    "murphy_double_ma": {
        "book": MURPHY, "chapter": "Lesson 7: Moving Averages", "tf": "DAY", "days": 5000, "markets": MARKETS, "method": "close reading",
        "teaches": "The double crossover method: a buy when the shorter average crosses above the longer, a sell on the cross below. "
                   "Common pairs are 5 and 20 days, and 10 and 50 days.",
    },
    "four_week_rule": {
        "book": MURPHY, "chapter": "Lesson 7: Moving Averages (the four-week rule)", "tf": "DAY", "days": 5000, "markets": MARKETS,
        "method": "as written",
        "teaches": "Richard Donchian's weekly rule: buy when price exceeds the highs of the last four weeks, sell when it falls below "
                   "the lows of the last four weeks, and reverse. Murphy presents it as a simple alternative to moving averages.",
    },
    "rsi_failure_swing": {
        "book": MURPHY, "chapter": "Lesson 8: Oscillators and Contrary Opinion", "tf": "DAY", "days": 5000, "markets": MARKETS,
        "method": "close reading",
        "teaches": "Oscillators like RSI mark overbought and oversold markets, and their strongest signals are divergences and failure "
                   "swings. The summary warns they give many false signals in trends.",
    },
}

LEFT_OUT = [
    {"name": "Market Wizards: entry methods", "why": "the interviews give no fixed entry rules; their money rules are tested on every card below",
     "url": "https://en.wikipedia.org/wiki/Market_Wizards"},
    {"name": "Elder: Parabolic system (ch. 44)", "why": "a stop-and-reverse trailing method he says only works in trends; covered by the trend rules here",
     "url": "https://en.wikipedia.org/wiki/Parabolic_SAR"},
    {"name": "Elder: crowd psychology, MACD divergences, New High-New Low, consensus and commitment indicators",
     "why": "need stock-market breadth, futures positioning or judgement by eye, which Capital.com CFDs don't supply",
     "url": "https://en.wikipedia.org/wiki/Alexander_Elder"},
    {"name": "Murphy: chart patterns, point and figure, candlesticks, Dow Theory",
     "why": "read by eye; a fixed version of one of them would be Ras's guess, not the book",
     "url": "https://en.wikipedia.org/wiki/Technical_analysis"},
]

# The money rules, each a variant of the $100 account run on the same trades.
MONEY = [
    {"key": "elder_2", "name": "Elder's 2% rule", "who": "Elder", "spec": {"risk_pct": 2.0, "max_risk_pct": 2.0}},
    {"key": "hite_1", "name": "Hite's 1% rule", "who": "Larry Hite (Market Wizards)", "spec": {"risk_pct": 1.0, "max_risk_pct": 1.0}},
    {"key": "cut_size", "name": "1%, cut size in drawdowns", "who": "Paul Tudor Jones, Ed Seykota, Randy McKay (Market Wizards)",
     "spec": {"risk_pct": 1.0, "max_risk_pct": 1.0, "dd_cut": [[10, 0.5], [20, 0.25]]}},
    {"key": "stop_month", "name": "1%, stop for the month after -6%", "who": "Richard Dennis, Michael Marcus (Market Wizards); Elder's 6% rule",
     "spec": {"risk_pct": 1.0, "max_risk_pct": 1.0, "month_stop_pct": 6}},
]


def money(trades: list[dict[str, Any]], base: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """The same trades through the $100 account under each book's money rule."""
    if not trades or not base:
        return None
    out = []
    for m in MONEY:
        res = lab_account.simulate(trades, {**base, **m["spec"]})
        if res:
            out.append({"key": m["key"], "name": m["name"], "who": m["who"], **{k: res[k] for k in ("final", "return_pct", "max_dd_pct", "taken", "paused", "too_big")}})
    return out


def page(runs: list[dict[str, Any]]) -> dict[str, Any]:
    from jarvis.lab.rules import CATALOG
    rows = []
    for k, s in STRATEGIES.items():
        test = latest(runs, k, s["tf"], s["markets"])
        if test is not None:
            test["money"] = _money_of(runs, test["run"], k)
        rows.append({"key": k, "name": CATALOG[k]["name"], "rules": CATALOG[k]["rules"], **s, "trader": s["book"],
                     "evidence": {"as written": "strong", "close reading": "medium"}.get(s["method"], "weak"),
                     "claim": f"{s['teaches']} ({s['book']}, {s['chapter']}.)",
                     "sources": [[s["book"] + " — " + s["chapter"], "https://openlibrary.org/search?q=" + s["book"].split(", ", 1)[1].replace(" ", "+")]], "test": test})
    rows.sort(key=lambda r: -((r["test"] or {}).get("summary") or {}).get("expectancy", -99) if r["test"] else 99)
    return {"strategies": rows, "left_out": LEFT_OUT, "money_rules": [{k: m[k] for k in ("key", "name", "who")} for m in MONEY]}


def _money_of(runs: list[dict[str, Any]], run_id: int, key: str) -> list[dict[str, Any]] | None:
    for r in runs:
        if r.get("id") == run_id:
            for s in (r.get("result") or {}).get("strategies", []):
                if s["key"] == key:
                    return s.get("money")
    return None
