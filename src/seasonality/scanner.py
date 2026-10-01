"""여러 종목을 스캔해서 계절성이 강한 (종목, 월) 조합을 순위로 뽑는다."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import numpy as np
import pandas as pd

from .analysis import find_patterns

log = logging.getLogger(__name__)


def benjamini_hochberg(p: pd.Series) -> pd.Series:
    """다중검정 보정 q값 (Benjamini-Hochberg).

    종목 100개 × 12개월 = 1200번을 검정하면 p<0.05 인 '가짜 패턴'이 약 60개는 우연히 나온다.
    q값은 그 거짓 발견 비율을 통제한 값이다.
    """
    valid = p.dropna()
    m = len(valid)
    if m == 0:
        return pd.Series(np.nan, index=p.index)
    order = valid.sort_values()
    ranks = np.arange(1, m + 1)
    q = order.to_numpy() * m / ranks
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = pd.Series(np.minimum(q, 1.0), index=order.index)
    return out.reindex(p.index)


def scan(
    prices: Mapping[str, pd.DataFrame],
    bench_prices: pd.DataFrame | None = None,
    lookback_years: int | None = None,
    recent_years: int = 5,
    min_years: int = 8,
    alpha: float = 0.05,
    min_hit_rate: float = 0.7,
    min_dollar_volume: float = 0.0,
) -> pd.DataFrame:
    """모든 종목의 12개월 지표를 한 표로 모은다. significant 행이 위로 오도록 정렬."""
    frames = []
    for ticker, df in prices.items():
        if df is None or df.empty:
            continue
        if min_dollar_volume and "Volume" in df:
            recent = df.tail(60)
            if (recent["Close"] * recent["Volume"]).median() < min_dollar_volume:
                continue
        try:
            s = find_patterns(
                df,
                bench_prices=bench_prices,
                lookback_years=lookback_years,
                recent_years=recent_years,
                min_years=min_years,
                alpha=alpha,
                min_hit_rate=min_hit_rate,
            )
        except Exception as e:  # 한 종목 실패가 전체 스캔을 멈추지 않도록
            log.warning("%s: 분석 실패 (%s)", ticker, e)
            continue
        s = s.reset_index()
        s.insert(0, "ticker", ticker)
        frames.append(s)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    eligible = out["n"] >= min_years
    out["q"] = np.nan
    out.loc[eligible, "q"] = benjamini_hochberg(out.loc[eligible, "p"])
    out = out.sort_values(["significant", "score"], ascending=[False, False])
    return out.reset_index(drop=True)


def top_patterns(result: pd.DataFrame, top: int = 20, use_q: bool = False, alpha: float = 0.05) -> pd.DataFrame:
    """유의미한 패턴만 골라 보기 좋게 정리."""
    if result.empty:
        return result
    df = result[result["significant"]]
    if use_q:
        df = df[df["q"] < alpha]
    cols = ["ticker", "month_name", "direction", "mean", "diff", "hit_rate", "n", "p", "q", "recent_mean", "score"]
    return df[cols].head(top).reset_index(drop=True)
