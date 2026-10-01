"""계절성 분석.

핵심 아이디어: 월별 수익률을 "연도 × 월" 표로 만들고, 같은 달끼리 모아서
평균·승률·t-검정으로 "그 달에 반복적으로 오르거나 내리는가"를 판단한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

MONTH_NAMES = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"]


def _close(prices: pd.DataFrame | pd.Series) -> pd.Series:
    s = prices["Close"] if isinstance(prices, pd.DataFrame) else prices
    return s.dropna().sort_index()


def monthly_returns(prices: pd.DataFrame | pd.Series) -> pd.Series:
    """월말 종가 기준 월 수익률. 아직 끝나지 않은 마지막 달은 제외한다."""
    close = _close(prices)
    if close.empty:
        return pd.Series(dtype="float64")
    month_end = close.resample("ME").last()
    rets = month_end.pct_change().dropna()

    last_day = close.index[-1]
    last_bday = last_day + pd.offsets.BMonthEnd(0)
    if last_day.normalize() < last_bday.normalize() and len(rets):
        if rets.index[-1].to_period("M") == last_day.to_period("M"):
            rets = rets.iloc[:-1]
    rets.index = rets.index.to_period("M")
    rets.name = "return"
    return rets


def excess_returns(rets: pd.Series, bench_rets: pd.Series) -> pd.Series:
    """지수 대비 초과수익 (종목 월수익률 - 지수 월수익률)."""
    aligned = pd.concat([rets, bench_rets], axis=1, join="inner").dropna()
    out = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    out.name = "excess_return"
    return out


def last_n_years(rets: pd.Series, years: int | None) -> pd.Series:
    """최근 N년치 월 수익률만 남긴다 (None 이면 전체)."""
    if years is None or rets.empty:
        return rets
    return rets.iloc[-12 * years :]


def year_month_table(rets: pd.Series) -> pd.DataFrame:
    """연도(행) × 월(열) 수익률 표."""
    df = pd.DataFrame({"year": rets.index.year, "month": rets.index.month, "r": rets.values})
    table = df.pivot(index="year", columns="month", values="r")
    return table.reindex(columns=range(1, 13))


def month_stats(rets: pd.Series) -> pd.DataFrame:
    """월별 통계.

    t·p 값은 "그 달 수익률 vs 나머지 달 수익률" 비교(Welch t-검정)다.
    0% 와 비교하면 꾸준히 오르는 종목은 모든 달이 '유의미'하게 나오므로,
    계절성은 다른 달과 비교해서 판단해야 한다.
    """
    months = rets.index.month
    rows = []
    for m in range(1, 13):
        x = rets[months == m].to_numpy(dtype="float64")
        rest = rets[months != m].to_numpy(dtype="float64")
        n = len(x)
        if n >= 3 and len(rest) >= 3 and (np.std(x) > 0 or np.std(rest) > 0):
            t, p = stats.ttest_ind(x, rest, equal_var=False)
        else:
            t, p = np.nan, np.nan
        mean = x.mean() if n else np.nan
        rest_mean = rest.mean() if len(rest) else np.nan
        rows.append(
            {
                "month": m,
                "n": n,
                "mean": mean,
                "median": np.median(x) if n else np.nan,
                "std": x.std(ddof=1) if n > 1 else np.nan,
                "win_rate": (x > 0).mean() if n else np.nan,
                "rest_mean": rest_mean,
                "diff": mean - rest_mean,
                "t": float(t),
                "p": float(p),
            }
        )
    return pd.DataFrame(rows).set_index("month")


def find_patterns(
    prices: pd.DataFrame | pd.Series,
    bench_prices: pd.DataFrame | pd.Series | None = None,
    lookback_years: int | None = None,
    recent_years: int = 5,
    min_years: int = 8,
    alpha: float = 0.05,
    min_hit_rate: float = 0.7,
) -> pd.DataFrame:
    """12개월 각각의 계절성 지표와 '유의미한 패턴인지' 판정을 돌려준다.

    판정 기준 (모두 만족해야 significant=True):
      - 표본(연도 수) >= min_years
      - 그 달이 나머지 달과 다르다 (Welch t-검정 p < alpha)
      - 적중률 >= min_hit_rate
        (상승 패턴이면 그 달에 실제로 오른 비율, 하락 패턴이면 실제로 내린 비율)
      - 최근 recent_years 년에도 같은 방향 (패턴이 최근에도 유지)
    """
    rets = monthly_returns(prices)
    if bench_prices is not None:
        rets = excess_returns(rets, monthly_returns(bench_prices))
    long = last_n_years(rets, lookback_years)
    recent = last_n_years(rets, recent_years)

    s = month_stats(long)
    r = month_stats(recent)

    up = s["diff"] >= 0
    s["direction"] = np.where(up, "상승", "하락")
    s["hit_rate"] = np.where(up, s["win_rate"], 1 - s["win_rate"])
    s["recent_mean"] = r["mean"]
    s["recent_diff"] = r["diff"]
    s["consistent"] = np.sign(s["diff"]) == np.sign(s["recent_diff"])
    s["score"] = (s["t"].abs() * s["hit_rate"]).where(s["consistent"], 0.0).fillna(0.0)
    s["significant"] = (
        (s["n"] >= min_years)
        & (s["p"] < alpha)
        & (s["hit_rate"] >= min_hit_rate)
        & s["consistent"]
    )
    s["month_name"] = [MONTH_NAMES[m - 1] for m in s.index]
    return s


def yearly_paths(prices: pd.DataFrame | pd.Series, years: int | None = None) -> pd.DataFrame:
    """연도별 가격 흐름을 연초=100 으로 맞춘 표 (행: 1~366일, 열: 연도).

    각 해의 첫 거래일 종가를 100 으로 놓고, 달력상 날짜(day of year)에 맞춰 정렬한다.
    """
    close = _close(prices)
    out = {}
    for year, s in close.groupby(close.index.year):
        # 연초부터 시작하지 않는 해(상장 첫해)는 비교가 어려우므로 제외
        if s.index[0].dayofyear > 10:
            continue
        doy = s.index.dayofyear
        norm = pd.Series(s.to_numpy() / s.iloc[0] * 100, index=doy)
        norm = norm[~norm.index.duplicated()]
        # 지난 해는 연말(366일)까지 마지막 값을 이어서, 평균 경로 끝이 튀지 않게 함
        end = 366 if year < close.index[-1].year else doy.max()
        out[year] = norm.reindex(range(1, end + 1)).ffill()
    df = pd.DataFrame(out).reindex(range(1, 367))
    if years is not None:
        df = df[df.columns[-years:]]
    return df


def average_path(paths: pd.DataFrame, exclude_current: bool = True) -> pd.Series:
    """완료된 해들의 평균 경로 (올해는 아직 진행 중이라 기본적으로 제외)."""
    cols = list(paths.columns)
    if exclude_current and cols:
        last = paths[cols[-1]]
        if last.last_valid_index() is not None and last.last_valid_index() < 360:
            cols = cols[:-1]
    if not cols:
        return pd.Series(dtype="float64")
    sub = paths[cols]
    # 연말 366일처럼 일부 해에만 있는 날은 평균이 튀므로, 절반 이상의 해에 값이 있는 날만 사용
    enough = sub.notna().sum(axis=1) >= max(1, len(cols) / 2)
    return sub.mean(axis=1).where(enough)
