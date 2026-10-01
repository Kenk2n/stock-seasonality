"""네트워크 없이 테스트·데모할 때 쓰는 가짜 주가 생성기."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_prices(
    start: str = "2000-01-03",
    end: str = "2025-12-31",
    monthly_effect: dict[int, float] | None = None,
    daily_vol: float = 0.01,
    drift: float = 0.0003,
    seed: int = 0,
) -> pd.DataFrame:
    """영업일 일봉을 만든다. monthly_effect={11: 0.04} 이면 11월에 평균 +4% 를 심는다."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, end)
    r = rng.normal(drift, daily_vol, len(idx))
    if monthly_effect:
        months = idx.month.to_numpy()
        for m, eff in monthly_effect.items():
            mask = months == m
            # 해당 월의 영업일 수(약 21일)에 나눠서 더함
            r[mask] += eff / 21
    close = 100 * np.exp(np.cumsum(r))
    vol = rng.integers(1_000_000, 5_000_000, len(idx)).astype("float64")
    return pd.DataFrame({"Close": close, "Volume": vol}, index=pd.DatetimeIndex(idx, name="Date"))


def demo_universe(seed: int = 42) -> dict[str, pd.DataFrame]:
    """계절성이 있는 종목 몇 개와 없는 종목 여러 개로 구성된 데모 데이터."""
    data = {
        "DEMO_NOV": make_prices(monthly_effect={11: 0.05}, seed=seed),
        "DEMO_SEP": make_prices(monthly_effect={9: -0.05}, seed=seed + 1),
        "DEMO_APR": make_prices(monthly_effect={4: 0.04, 10: -0.03}, seed=seed + 2),
    }
    for i in range(10):
        data[f"NOISE{i}"] = make_prices(seed=seed + 100 + i)
    return data
