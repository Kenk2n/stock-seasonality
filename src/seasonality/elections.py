"""미국 선거 주기와 주가.

중간선거일 = 11월 첫 월요일 다음 화요일 (짝수 해 중 대선이 아닌 해).
선거일에 증시가 쉬던 해(1984년 이전 일부)가 있으므로 '선거일 당일 또는 직전 거래일 종가'를 기준(0일)으로 쓴다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def election_day(year: int) -> pd.Timestamp:
    """11월 첫 월요일 다음 화요일."""
    d = pd.Timestamp(year=year, month=11, day=1)
    first_monday = d + pd.Timedelta(days=(0 - d.weekday()) % 7)
    return first_monday + pd.Timedelta(days=1)


def midterm_years(start: int = 1950, end: int = 2026) -> list[int]:
    return [y for y in range(start, end + 1) if y % 4 == 2]


def base_index(close: pd.Series, day: pd.Timestamp) -> int:
    """선거일 당일(장이 열렸으면) 또는 직전 거래일 위치."""
    return int(close.index.searchsorted(day, side="right") - 1)


WINDOWS = {"선거 6개월 전→선거일": (-126, 0), "3개월 전→선거일": (-63, 0), "1개월 전→선거일": (-21, 0),
           "선거일→1개월 후": (0, 21), "선거일→3개월 후": (0, 63), "선거일→6개월 후": (0, 126),
           "선거일→1년 후": (0, 252)}


def window_returns(close: pd.Series, years: list[int], windows: dict = WINDOWS) -> pd.DataFrame:
    """선거마다 구간 수익률. 아직 오지 않은 구간은 NaN."""
    rows = []
    for y in years:
        i = base_index(close, election_day(y))
        if i < 130:
            continue
        row = {"year": y, "base_date": close.index[i]}
        for name, (a, b) in windows.items():
            ia, ib = i + a, i + b
            row[name] = close.iloc[ib] / close.iloc[ia] - 1 if 0 <= ia and ib < len(close) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("year")


def drawdown_stats(close: pd.Series, years: list[int]) -> pd.DataFrame:
    """중간선거 해: 1월 1일~선거일 사이 최대 낙폭, 그 해 저점 날짜, 저점에서 1년 뒤 상승률."""
    rows = []
    for y in years:
        e = election_day(y)
        s = close[(close.index >= f"{y}-01-01") & (close.index <= e)]
        if len(s) < 100:
            continue
        dd = s / s.cummax() - 1
        low_d = s.idxmin()
        i = close.index.get_loc(low_d)
        rows.append({
            "year": y,
            "max_drawdown": float(dd.min()),
            "low_date": low_d,
            "low_to_1y": close.iloc[i + 252] / close.iloc[i] - 1 if i + 252 < len(close) else np.nan,
            "ytd_to_election": s.iloc[-1] / s.iloc[0] - 1,
        })
    return pd.DataFrame(rows).set_index("year")


def realized_vol(close: pd.Series, years: list[int], span: int = 63) -> pd.DataFrame:
    """선거 전 3개월 vs 후 3개월 일간 변동성(연율화)."""
    r = np.log(close).diff()
    rows = []
    for y in years:
        i = base_index(close, election_day(y))
        if i - span < 1 or i + span >= len(close):
            continue
        rows.append({"year": y,
                     "vol_before": float(r.iloc[i - span + 1 : i + 1].std() * np.sqrt(252)),
                     "vol_after": float(r.iloc[i + 1 : i + span + 1].std() * np.sqrt(252))})
    return pd.DataFrame(rows).set_index("year")


def vix_around(vix: pd.Series, years: list[int], span: int = 21) -> pd.DataFrame:
    rows = []
    for y in years:
        i = base_index(vix, election_day(y))
        if i - span < 0 or i + span >= len(vix):
            continue
        rows.append({"year": y, "vix_before": float(vix.iloc[i - span : i + 1].mean()),
                     "vix_on": float(vix.iloc[i]), "vix_after": float(vix.iloc[i + 1 : i + span + 1].mean())})
    return pd.DataFrame(rows).set_index("year")


def paths(close: pd.Series, years: list[int], pre: int = 252, post: int = 252, anchor: int = 0) -> pd.DataFrame:
    """선거일(0일) 기준 -pre ~ +post 거래일 경로, anchor 일 = 100 으로 맞춤. 열: 연도."""
    out = {}
    for y in years:
        i = base_index(close, election_day(y))
        if i - pre < 0:
            continue
        seg = close.iloc[i - pre : min(i + post + 1, len(close))]
        idx = np.arange(-pre, -pre + len(seg))
        s = pd.Series(seg.to_numpy(), index=idx)
        if anchor not in s.index:
            continue
        out[y] = s / s[anchor] * 100
    return pd.DataFrame(out).reindex(range(-pre, post + 1))


def baseline(close: pd.Series, windows: dict = WINDOWS, start: int = 1950, end: int = 2025,
             exclude: list[int] | None = None) -> pd.DataFrame:
    """비교 기준: 중간선거가 아닌 해의 '같은 날짜(11월 첫 화요일)' 기준 같은 구간 수익률."""
    yrs = [y for y in range(start, end + 1) if y not in set(exclude or [])]
    return window_returns(close, yrs, windows)


def trading_days_until(close: pd.Series, day: pd.Timestamp) -> int:
    """마지막 데이터 날짜에서 day 까지 남은 평일 수 (미국 공휴일은 무시한 근사)."""
    last = close.index[-1]
    return int(len(pd.bdate_range(last + pd.Timedelta(days=1), day)))
