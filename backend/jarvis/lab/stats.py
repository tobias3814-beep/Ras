"""Could this result be luck, given how many variants were tried? Selection alone manufactures good backtests.
- Deflated Sharpe (Bailey & Lopez de Prado 2014): chance the true Sharpe beats the best you'd expect from that many tries.
- Probability of backtest overfitting (Bailey et al. 2017): how often the in-sample winner lands in the bottom half on the
  rest of the data, across many splits.
- Reality check (White 2000): chance the best variant's average would be this high if every variant really earned zero,
  by stationary bootstrap of the same data.
Variants overlap, so counting each one as a separate trial makes the deflated Sharpe strict: that errs the safe way."""
from itertools import combinations
from statistics import NormalDist
from typing import Any

import numpy as np

BUCKETS = 40  # time buckets each variant's results are summed into
PBO_BLOCKS = 10
GAMMA = 0.5772156649015329
_N = NormalDist()


def sharpe(rs: list[float]) -> float | None:
    """Per trade, not annualised."""
    if len(rs) < 2 or (sd := float(np.std(rs, ddof=1))) == 0:
        return None
    return float(np.mean(rs)) / sd


def expected_max_sharpe(trials: int, variance: float) -> float:
    if trials < 2 or variance <= 0:
        return 0.0
    return variance ** 0.5 * ((1 - GAMMA) * _N.inv_cdf(1 - 1 / trials) + GAMMA * _N.inv_cdf(1 - 1 / (trials * np.e)))


def deflated_sharpe(rs: list[float], trials: int, variance: float) -> float | None:
    sr = sharpe(rs)
    if sr is None:
        return None
    x = np.asarray(rs, float)
    z = (x - x.mean()) / x.std()
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return None
    return _N.cdf((sr - expected_max_sharpe(trials, variance)) * (len(rs) - 1) ** 0.5 / denom ** 0.5)


def pbo(grid: np.ndarray, blocks: int = PBO_BLOCKS) -> float | None:
    """grid: time buckets x variants. Share of half-and-half splits whose in-sample best is below the median out of sample."""
    if grid.shape[1] < 2 or grid.shape[0] < blocks or not grid.any():
        return None
    parts = np.array([b.sum(axis=0) for b in np.array_split(grid, blocks)])
    bad = total = 0
    for chosen in combinations(range(blocks), blocks // 2):
        rest = [b for b in range(blocks) if b not in chosen]
        best = int(np.argmax(parts[list(chosen)].sum(axis=0)))
        oos = parts[rest].sum(axis=0)
        bad += (oos < oos[best]).sum() + 0.5 * ((oos == oos[best]).sum() - 1) < (grid.shape[1] - 1) / 2
        total += 1
    return float(bad / total)


def reality_check(grid: np.ndarray, boots: int = 1000, block: int = 4, seed: int = 7) -> float | None:
    if not grid.any():
        return None
    rng, n = np.random.default_rng(seed), grid.shape[0]
    means = grid.mean(axis=0)
    observed = float(means.max())
    if observed <= 0:
        return 1.0
    centred = grid - means
    idx = np.empty((boots, n), int)
    idx[:, 0] = rng.integers(0, n, boots)
    restart, jump = rng.random((boots, n)) < 1 / block, rng.integers(0, n, (boots, n))
    for t in range(1, n):
        idx[:, t] = np.where(restart[:, t], jump[:, t], (idx[:, t - 1] + 1) % n)
    return float((centred[idx].mean(axis=1).max(axis=1) >= observed).mean())


def drawdown_range(rs: list[float], boots: int = 500, block: int = 10, seed: int = 7) -> dict[str, float] | None:
    """The worst losing run is one sample. Resampling blocks of the same trades (a stationary bootstrap, since simple
    resampling understates it) gives the typical worst run and a bad case (95th percentile), in R."""
    if len(rs) < 20:
        return None
    x, n = np.asarray(rs, float), len(rs)
    rng = np.random.default_rng(seed)
    idx = np.empty((boots, n), int)
    idx[:, 0] = rng.integers(0, n, boots)
    restart, jump = rng.random((boots, n)) < 1 / block, rng.integers(0, n, (boots, n))
    for t in range(1, n):
        idx[:, t] = np.where(restart[:, t], jump[:, t], (idx[:, t - 1] + 1) % n)
    eq = x[idx].cumsum(axis=1)
    dd = (np.maximum.accumulate(np.maximum(eq, 0), axis=1) - eq).max(axis=1)
    return {"typical": round(float(np.median(dd)), 2), "bad": round(float(np.quantile(dd, 0.95)), 2)}


def checks(oos: list[float], variants: list[list[float]], grid: np.ndarray, trials: int) -> dict[str, Any]:
    srs = [s for s in map(sharpe, variants) if s is not None]
    variance = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    d, p, rc = deflated_sharpe(oos, trials, variance), pbo(grid), reality_check(grid)
    return {"trials": trials, "deflated_sharpe": None if d is None else round(d, 3), "overfit_probability": None if p is None else round(p, 3),
            "reality_check_p": None if rc is None else round(rc, 3),
            "passed": d is not None and d >= 0.95 and (p is None or p <= 0.1) and rc is not None and rc <= 0.05}
