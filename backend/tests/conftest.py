import numpy as np
import pandas as pd
import pytest
import yaml

from jarvis.config import ROOT

NOW = 1_800_000_000  # fixed "now" (UTC seconds), aligned to 4H


def frame(n: int, tf_s: int, drift: float | np.ndarray, noise: float = 0.2, seed: int = 1,
          start: float = 100.0) -> pd.DataFrame:
    """Synthetic closed candles ending just before NOW."""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(drift + noise * rng.standard_normal(n))
    open_ = np.r_[start, close[:-1]]
    wick = np.abs(noise * rng.standard_normal(n)) * 0.5
    t = NOW - tf_s * np.arange(n, 0, -1)
    return pd.DataFrame({"time": t, "open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close, "volume": 100})


def frames(drift: float, seed: int = 1) -> dict[str, pd.DataFrame]:
    """Trend on every timeframe; the 15M ends with a pullback then a bounce (the entry trigger)."""
    m15_drift = np.r_[np.full(290, drift), np.full(6, -3 * drift), np.full(4, 3 * drift)]
    return {"HOUR_4": frame(300, 14400, drift * 4, 0.4, seed), "HOUR": frame(300, 3600, drift * 2, 0.25, seed + 1),
            "MINUTE_15": frame(300, 900, m15_drift, 0.05, seed + 2)}


@pytest.fixture
def cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())
