"""실적 발표일과 실적 전후 주가 반응.

실적 주기(분기마다 1월·4월·7월·11월 등)로 오르내리는 종목은
월별 계절성보다 '실적 발표 전후 흐름'으로 보는 편이 더 정확하다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import _close

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/earnings")


def load_earnings_dates(ticker: str, refresh: bool = False, cache_dir: Path = DEFAULT_CACHE_DIR) -> pd.DatetimeIndex:
    """야후에서 과거·예정 실적 발표일을 받아 캐시한다 (실패하면 빈 목록)."""
    path = Path(cache_dir) / f"{ticker.upper()}.json"
    if path.exists() and not refresh:
        return pd.DatetimeIndex(json.loads(path.read_text()))
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=60)
        dates = [] if df is None else sorted({d.strftime("%Y-%m-%d") for d in df.index.tz_localize(None)})
    except Exception as e:  # 실적일이 없는 종목(ETF 등)도 있음
        log.warning("%s: 실적 발표일을 받지 못함 (%s)", ticker, e)
        dates = []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dates))
    return pd.DatetimeIndex(dates)


def earnings_reactions(
    prices: pd.DataFrame | pd.Series,
    dates: pd.DatetimeIndex,
    before: int = 20,
    after: int = 20,
) -> pd.DataFrame:
    """실적 발표마다 '발표 전 20일 흐름', '발표 반응(전일→다음날)', '발표 후 20일 흐름'.

    장전/장후 발표를 모두 포함하도록 반응은 발표 전날 종가 → 발표 다음날 종가로 잰다.
    """
    close = _close(prices)
    rows = []
    for d in pd.DatetimeIndex(dates).sort_values():
        i = close.index.searchsorted(d)
        if i - before < 0 or i + 1 + after >= len(close):
            continue
        c = close.to_numpy()
        rows.append(
            {
                "date": d,
                "before": c[i - 1] / c[i - before - 1] - 1,
                "reaction": c[i + 1] / c[i - 1] - 1,
                "after": c[i + 1 + after] / c[i + 1] - 1,
            }
        )
    return pd.DataFrame(rows)


def swing_earnings_distance(swings: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.Series:
    """각 고점·저점이 가장 가까운 실적 발표일에서 며칠 떨어져 있나 (+ 는 실적 전, - 는 실적 후)."""
    if swings.empty or len(dates) == 0:
        return pd.Series(np.nan, index=swings.index)
    dates = pd.DatetimeIndex(dates).sort_values()
    out = []
    for d in swings["date"]:
        diff = (dates - d).days
        out.append(int(diff[np.argmin(np.abs(diff))]))
    return pd.Series(out, index=swings.index, name="days_to_earnings")


def next_earnings(dates: pd.DatetimeIndex, today: pd.Timestamp | None = None) -> pd.Timestamp | None:
    today = today or pd.Timestamp.today().normalize()
    future = pd.DatetimeIndex(dates)[pd.DatetimeIndex(dates) >= today]
    return future.min() if len(future) else None
