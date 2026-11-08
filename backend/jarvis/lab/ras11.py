"""Ras 1.1: strategies found by researching the web, each with its source and how strong that source is, and each judged
only by Ras's own walk-forward test on Capital.com prices with costs. What a source claims is shown as a claim, never as
a result. Nothing here opens a signal or a paper trade."""
from typing import Any

FX = ["EUR_USD", "GBP_USD", "USD_JPY"]
ALL = ["EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD", "XAG_USD"]

STRATEGIES: dict[str, dict[str, Any]] = {
    "trend_following": {
        "tf": "DAY", "days": 5000, "markets": ALL, "evidence": "strong",
        "claim": "Best-documented rule found. AQR's century-long study reports a net Sharpe of 0.76, but for a diversified "
                 "portfolio of many markets. A secondary summary puts gold alone near 0.4, with metals weaker since 2010.",
        "sources": [["Hurst, Ooi, Pedersen: A Century of Evidence on Trend-Following", "https://www.TrendFollowing.com/whitepaper/Century_Evidence_Trend_Following.pdf"],
                    ["Summary of Moskowitz, Ooi, Pedersen 2012 (secondary)", "https://qgtm-docs.pages.dev/research/gold_silver/paper_moskowitz_tsmom"]],
    },
    "donchian_breakout": {
        "tf": "DAY", "days": 5000, "markets": ALL, "evidence": "strong",
        "claim": "A CME and Gresham study of over 100 futures markets, 2000 to 2019, found no sign that crowding had decayed "
                 "trend following. It is not a test of this exact rule or of these markets alone.",
        "sources": [["Babbedge and Kerson: Trends Not Dead (CME)", "https://www.cmegroup.com/education/files/trends-not-dead-babbedge-kerson-2019.pdf"]],
    },
    "fix_reversal": {
        "tf": "HOUR", "days": 1000, "markets": FX, "evidence": "strong",
        "claim": "Published in the Journal of Finance on about 21 years of 5-minute data: the dollar rises before fixes and "
                 "falls after. The authors say it can be exploited after costs at institutional spreads. A retail CFD spread "
                 "may be too wide, and hourly bars are coarser than their data.",
        "sources": [["Krohn, Mueller, Whelan 2024, Journal of Finance", "https://econpapers.repec.org/RePEc:bla:jfinan:v:79:y:2024:i:1:p:541-578"],
                    ["Working paper (Bank of Canada)", "https://www.bankofcanada.ca/wp-content/uploads/2021/10/swp2021-48.pdf"]],
    },
    "gold_asia_drift": {
        "tf": "HOUR", "days": 1000, "markets": ["XAU_USD"], "evidence": "medium",
        "claim": "A master's thesis reports gold rising in eastern hours and falling later, with Sharpe up to 1.61 before costs.",
        "sources": [["Gold Price Dynamics Around the Clock (Copenhagen Business School)", "https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/"]],
    },
    "gold_london_fade": {
        "tf": "HOUR", "days": 1000, "markets": ["XAU_USD"], "evidence": "medium",
        "claim": "A master's thesis finds returns around the London fixes among the weakest of the day. Gold's strong rise in "
                 "recent years works against a short-only rule.",
        "sources": [["University of Zurich thesis", "https://www.merlin.uzh.ch/publication/show/11299"]],
    },
    "cta_momentum": {
        "tf": "DAY", "days": 5000, "markets": ALL, "evidence": "weak",
        "claim": "A Reddit trader claims 59.9% over about two years at a CTA, with a 13.3% worst month. Unverified. Reddit "
                 "blocked the full post, so this is Ras's own fixed reading of its summary, not the trader's exact method.",
        "sources": [["r/Forex: I profitably traded FX for a CTA", "https://www.reddit.com/r/Forex/comments/f09ujh/i_profitably_traded_fx_for_a_cta_here_is_my/"]],
    },
    "vwap_ema9": {
        "tf": "MINUTE_3", "days": 180, "markets": ["XAU_USD", "EUR_USD", "GBP_USD", "USD_JPY"], "evidence": "weak", "options": True,
        "claim": "A Roboquant Instagram reel: a trader says he made $179,000 in a month buying options on this cross, and shows a "
                 "backtest of +381.9%, 396 trades, 56.6% wins, profit factor 1.92 and a 24.2% worst drop. No market, stop or exit is "
                 "shown, and that backtest is titled 'Daily Anchored VWAP Signal Strategy', so it may not be this exact rule. It was "
                 "traded on US stock options; here it is tested on gold and forex with tick volume for VWAP, on about 6 months of "
                 "1-minute Capital.com history built into 3-minute bars.",
        "sources": [["VWAP explained (Investopedia)", "https://www.investopedia.com/terms/v/vwap.asp"]],
    },
}

LEFT_OUT = [
    {"name": "Gold bot with 96% winners", "why": "A single backtest from its seller's post. A win rate that high usually means small wins and rare large losses. Nothing to verify.",
     "url": "https://www.reddit.com/r/Forex/comments/1s79hjo/rebuilt_my_gold_ea_from_the_ground_up_9607/"},
    {"name": "Gold bot with no losing months", "why": "Sales post with client screenshots. Not independently checked.",
     "url": "https://www.reddit.com/r/Forexstrategy/comments/1qoyk1t/gold_trading_bot_client_results/"},
    {"name": "Martingale and grid bots", "why": "They add to losers, so the record looks smooth until the account is lost. One 18-month test reports a grid bot giving back nine good months.",
     "url": "https://www.reddit.com/r/Trading/comments/1s824cz/i_ran_6_mt5_eas_on_eurusd_for_18_months_and/"},
    {"name": "Currency carry and momentum portfolios", "why": "Research-backed, but they rank many currencies and carry needs interest-rate history. Ras has three pairs. One study also finds the returns faded after publication.",
     "url": "https://repository.adu.ac.ae/items/49b11e04-d4e2-4397-96aa-dac0c017647b"},
    {"name": "Gold-silver ratio reversion", "why": "No rigorous backtest found, and one formal test could not tell the ratio from a random walk.",
     "url": "https://jonathankinlay.com/category/metals"},
    {"name": "Gold futures rule with Sharpe 2.9", "why": "Far above the rest of the research on gold trend following. Too good to take without an independent replication.",
     "url": "https://arxiv.org/html/2511.08571v1"},
    {"name": "Century of industry trends (18.2% a year)", "why": "Stock sectors, not forex or metals, and a later replication says it is hard to adapt to modern markets.",
     "url": "https://arxiv.org/abs/2412.14361v1"},
]


def latest(runs: list[dict[str, Any]], key: str, tf: str, markets: list[str] | None = None) -> dict[str, Any] | None:
    """The newest finished run of this rule on its own timeframe (runs come newest first), on exactly `markets` if given."""
    for r in runs:
        res = r.get("result") or {}
        if r.get("status") == "done" and r["spec"]["tf"] == tf and key in r["spec"]["strategies"] \
                and (markets is None or sorted(r["spec"]["symbols"]) == sorted(markets)):
            for s in res.get("strategies", []):
                if s["key"] == key:
                    return {"run": r["id"], "created": r["created"], "range": res.get("range"), "symbols": r["spec"]["symbols"], "days": r["spec"]["days"],
                            **{k: s.get(k) for k in ("verdict", "summary", "positive_folds", "checks", "fresh", "account", "cost_stress", "random_entries", "by_symbol", "options")},
                            "folds": len(s.get("folds", []))}
    return None


def page(runs: list[dict[str, Any]]) -> dict[str, Any]:
    from jarvis.lab.rules import CATALOG
    rows = [{"key": k, "name": CATALOG[k]["name"], "rules": CATALOG[k]["rules"], **s, "test": latest(runs, k, s["tf"])} for k, s in STRATEGIES.items()]
    rows.sort(key=lambda r: -((r["test"] or {}).get("summary") or {}).get("expectancy", -99) if r["test"] else 99)
    return {"strategies": rows, "left_out": LEFT_OUT}
