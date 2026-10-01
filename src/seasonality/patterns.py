"""차트 흐름 분석: RSI, 고점·저점(스윙) 찾기, 진입 시점별 수익률, 기간별 요약, 유사 종목 찾기.

월별 통계(analysis.py)가 "몇 월에 오르나"를 본다면, 여기서는
"주로 언제 바닥을 찍고 언제 꼭지를 찍는가"처럼 실제 차트 흐름을 본다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .analysis import MONTH_NAMES, _close, monthly_returns

# 차트 비교용 기간 (거래일 수가 아니라 달력 기준)
WINDOWS = {
    "1개월": pd.DateOffset(months=1),
    "3개월": pd.DateOffset(months=3),
    "1년": pd.DateOffset(years=1),
    "3년": pd.DateOffset(years=3),
    "5년": pd.DateOffset(years=5),
}


# ------------------------------------------------------------------ RSI
def rsi(prices: pd.DataFrame | pd.Series, period: int = 14) -> pd.Series:
    """Wilder 방식 RSI (0~100). 70 이상 과매수, 30 이하 과매도."""
    close = _close(prices)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & avg_gain.notna()] = 100.0
    out.name = "RSI"
    return out


def rsi_by_month(prices: pd.DataFrame | pd.Series, years: int | None = None, period: int = 14) -> pd.DataFrame:
    """월별 평균 RSI, 과매수(>70)·과매도(<30) 비율."""
    r = rsi(prices, period).dropna()
    if years is not None and not r.empty:
        r = r[r.index > r.index[-1] - pd.DateOffset(years=years)]
    g = r.groupby(r.index.month)
    out = pd.DataFrame(
        {
            "rsi_mean": g.mean(),
            "overbought": g.apply(lambda x: (x > 70).mean()),
            "oversold": g.apply(lambda x: (x < 30).mean()),
        }
    ).reindex(range(1, 13))
    out.index.name = "month"
    out["month_name"] = MONTH_NAMES
    return out


# ------------------------------------------------------------------ 고점·저점
def auto_threshold(prices: pd.DataFrame | pd.Series) -> float:
    """스윙 판정 기준(되돌림 %)을 종목 변동성에 맞춘다. 월 변동성의 1.5배, 8~35% 사이."""
    close = _close(prices)
    vol = close.pct_change().tail(252 * 3).std() * np.sqrt(21)
    if not np.isfinite(vol):
        return 0.15
    return float(np.clip(1.5 * vol, 0.08, 0.35))


def find_swings(prices: pd.DataFrame | pd.Series, threshold: float | None = None) -> pd.DataFrame:
    """지그재그 방식으로 의미 있는 고점·저점을 찾는다.

    고점: 그 뒤로 threshold 이상 떨어진 꼭지.  저점: 그 뒤로 threshold 이상 오른 바닥.
    반환: date, price, kind('고점'/'저점'), move(다음 스윙까지 등락률), days(다음 스윙까지 거래일)
    마지막 스윙 이후 아직 확정되지 않은 구간은 포함하지 않는다.
    """
    close = _close(prices)
    if threshold is None:
        threshold = auto_threshold(close)
    if len(close) < 2:
        return pd.DataFrame(columns=["date", "price", "kind", "move", "days"])

    pivots: list[tuple[pd.Timestamp, float, str]] = []
    trend = None
    hi_t = lo_t = close.index[0]
    hi = lo = float(close.iloc[0])
    for t, p in close.items():
        p = float(p)
        if trend is None:
            if p > hi:
                hi, hi_t = p, t
            if p < lo:
                lo, lo_t = p, t
            if p >= lo * (1 + threshold) and lo_t < t:
                pivots.append((lo_t, lo, "저점"))
                trend, hi, hi_t = "up", p, t
            elif p <= hi * (1 - threshold) and hi_t < t:
                pivots.append((hi_t, hi, "고점"))
                trend, lo, lo_t = "down", p, t
        elif trend == "up":
            if p > hi:
                hi, hi_t = p, t
            elif p <= hi * (1 - threshold):
                pivots.append((hi_t, hi, "고점"))
                trend, lo, lo_t = "down", p, t
        else:
            if p < lo:
                lo, lo_t = p, t
            elif p >= lo * (1 + threshold):
                pivots.append((lo_t, lo, "저점"))
                trend, hi, hi_t = "up", p, t

    df = pd.DataFrame(pivots, columns=["date", "price", "kind"])
    if df.empty:
        df["move"] = []
        df["days"] = []
        return df
    # 다음 스윙(마지막은 현재가)까지의 등락
    next_price = list(df["price"].iloc[1:]) + [float(close.iloc[-1])]
    next_date = list(df["date"].iloc[1:]) + [close.index[-1]]
    df["move"] = np.array(next_price) / df["price"].to_numpy() - 1
    pos = close.index.get_indexer(df["date"])
    df["days"] = close.index.get_indexer(pd.DatetimeIndex(next_date)) - pos
    return df


def swing_calendar(swings: pd.DataFrame, since: pd.Timestamp | None = None) -> pd.DataFrame:
    """월별로 고점·저점이 몇 번 나왔는지."""
    s = swings if since is None else swings[swings["date"] >= since]
    months = pd.DatetimeIndex(s["date"]).month
    out = pd.DataFrame(
        {
            "lows": pd.Series(months[s["kind"].to_numpy() == "저점"]).value_counts(),
            "highs": pd.Series(months[s["kind"].to_numpy() == "고점"]).value_counts(),
        }
    ).reindex(range(1, 13)).fillna(0).astype(int)
    out.index.name = "month"
    out["month_name"] = MONTH_NAMES
    return out


# ------------------------------------------------------------------ 진입 시점별 수익률
def forward_returns_by_month(
    prices: pd.DataFrame | pd.Series,
    horizons: dict[str, int] | None = None,
    years: int | None = None,
) -> pd.DataFrame:
    """매달 첫 거래일에 샀다면 N거래일 뒤 수익률이 평균 얼마였나.

    반환: 행 = (기간, 지표[mean/win_rate/n]), 열 = 1~12월
    """
    horizons = horizons or {"1개월 후": 21, "3개월 후": 63}
    close = _close(prices)
    if years is not None and not close.empty:
        start = close.index[-1] - pd.DateOffset(years=years)
    else:
        start = close.index[0]
    firsts = close.groupby(close.index.to_period("M")).head(1)
    firsts = firsts[firsts.index >= start]
    pos = close.index.get_indexer(firsts.index)

    rows = {}
    for label, h in horizons.items():
        ok = pos + h < len(close)
        fwd = pd.Series(
            close.to_numpy()[pos[ok] + h] / firsts.to_numpy()[ok] - 1,
            index=firsts.index[ok],
        )
        g = fwd.groupby(fwd.index.month)
        rows[(label, "mean")] = g.mean()
        rows[(label, "win_rate")] = g.apply(lambda x: (x > 0).mean())
        rows[(label, "n")] = g.size()
    out = pd.DataFrame(rows).T.reindex(columns=range(1, 13))
    return out


# ------------------------------------------------------------------ 기간별 요약
def window_summary(prices: pd.DataFrame | pd.Series, threshold: float | None = None) -> pd.DataFrame:
    """1개월·3개월·1년·3년·5년 기간별 수익률, 최대낙폭, 고점·저점 위치, RSI 요약."""
    close = _close(prices)
    r = rsi(close)
    end = close.index[-1]
    swings = find_swings(close, threshold)
    rows = []
    for label, off in WINDOWS.items():
        s = close[close.index > end - off]
        if len(s) < 2:
            continue
        rr = r.reindex(s.index)
        dd = (s / s.cummax() - 1).min()
        sw = swings[swings["date"] > end - off]
        rows.append(
            {
                "기간": label,
                "수익률": s.iloc[-1] / s.iloc[0] - 1,
                "최대낙폭": dd,
                "최고가 날짜": s.idxmax().date(),
                "최저가 날짜": s.idxmin().date(),
                "현재가 위치": (s.iloc[-1] - s.min()) / (s.max() - s.min()) if s.max() > s.min() else np.nan,
                "RSI 평균": rr.mean(),
                "RSI 최고": rr.max(),
                "RSI 최저": rr.min(),
                "과매수 일수": int((rr > 70).sum()),
                "과매도 일수": int((rr < 30).sum()),
                "스윙 수": len(sw),
            }
        )
    return pd.DataFrame(rows).set_index("기간")


def describe_pattern(prices: pd.DataFrame | pd.Series, years: int = 5, threshold: float | None = None) -> dict:
    """사람이 읽을 요약: 주로 바닥/꼭지 찍는 달, 강한/약한 달, RSI 계절성."""
    close = _close(prices)
    since = close.index[-1] - pd.DateOffset(years=years)
    swings = find_swings(close, threshold)
    cal = swing_calendar(swings, since)
    rets = monthly_returns(close)
    rets = rets[rets.index.to_timestamp() > since]
    by_m = rets.groupby(rets.index.month)
    mean = by_m.mean().reindex(range(1, 13))
    win = by_m.apply(lambda x: (x > 0).mean()).reindex(range(1, 13))
    rsi_m = rsi_by_month(close, years)
    return {
        "threshold": threshold if threshold is not None else auto_threshold(close),
        "low_months": cal["lows"],
        "high_months": cal["highs"],
        "strong_months": mean[mean > 0].sort_values(ascending=False),
        "weak_months": mean[mean < 0].sort_values(),
        "win_rate": win,
        "mean": mean,
        "rsi_mean": rsi_m["rsi_mean"],
        "swings": swings[swings["date"] >= since],
    }


# ------------------------------------------------------------------ 유사 종목 찾기
def _period_returns(prices, freq: str, bench=None) -> pd.Series:
    close = _close(prices)
    if freq == "M":
        r = monthly_returns(close)
        r.index = r.index.to_timestamp(how="end").normalize()
    else:
        r = close.resample("W-FRI").last().pct_change().dropna()
    if bench is not None:
        rb = _period_returns(bench, freq)
        j = pd.concat([r, rb], axis=1, join="inner").dropna()
        r = j.iloc[:, 0] - j.iloc[:, 1]
    return r


def _key(index: pd.DatetimeIndex, freq: str) -> np.ndarray:
    if freq == "M":
        return index.month.to_numpy()
    return np.minimum(index.isocalendar().week.to_numpy(), 52)


def seasonal_profile(prices: pd.DataFrame | pd.Series, years: int = 5, freq: str = "M",
                     bench: pd.DataFrame | pd.Series | None = None) -> pd.Series | None:
    """계절성 지문: 최근 N년의 월별(또는 주별) 평균 수익률에서 전체 평균을 뺀 값.

    전체 평균을 빼므로 '꾸준히 오르는 종목'과 '계절성 있는 종목'이 구분된다.
    bench 를 주면 지수 대비 초과수익으로 계산한다. 표본이 부족하면 None.
    """
    close = _close(prices)
    if close.empty:
        return None
    since = close.index[-1] - pd.DateOffset(years=years)
    if close.index[0] > since + pd.DateOffset(months=2):
        return None  # N년치 데이터가 없음
    r = _period_returns(close, freq, bench)
    r = r[r.index > since]
    n = 12 if freq == "M" else 52
    if len(r) < n:
        return None
    prof = r.groupby(_key(r.index, freq)).mean().reindex(range(1, n + 1))
    if prof.isna().any():
        return None
    if freq != "M":
        # 주별은 잡음이 크므로 앞뒤 1주와 평균 (연말-연초는 이어서)
        arr = prof.to_numpy()
        prof = pd.Series((np.roll(arr, 1) + arr + np.roll(arr, -1)) / 3, index=prof.index)
    return prof - prof.mean()


def yearly_consistency(prices, ref_profile: pd.Series, years: int = 5, bench=None) -> float:
    """최근 N년 중 그 해의 월별 흐름이 기준 지문과 같은 방향(상관 > 0)이었던 해의 비율."""
    close = _close(prices)
    since = close.index[-1] - pd.DateOffset(years=years)
    r = _period_returns(close, "M", bench)
    r = r[r.index > since]
    hits, total = 0, 0
    for _, g in r.groupby(r.index.year):
        if len(g) < 6:
            continue
        v = g.to_numpy() - g.mean()
        ref = ref_profile.reindex(g.index.month).to_numpy()
        if np.std(v) == 0 or np.std(ref) == 0:
            continue
        total += 1
        hits += np.corrcoef(v, ref)[0, 1] > 0
    return hits / total if total else np.nan


def find_similar(
    reference: pd.DataFrame | pd.Series | list,
    candidates: dict[str, pd.DataFrame],
    years: int = 5,
    min_dollar_volume: float = 1e6,
    min_price: float = 3.0,
    bench: pd.DataFrame | None = None,
    min_amplitude_ratio: float = 0.5,
) -> pd.DataFrame:
    """기준 종목(들)과 계절성 지문이 비슷한 종목 순위.

    reference: 기준 종목 가격 하나, 또는 여러 개의 리스트(여러 개면 지문을 평균)
    similarity: 월별 지문 상관계수와 주별 지문 상관계수의 평균 (-1 ~ 1)
    consistency: 최근 N년 중 기준 패턴과 같은 방향으로 움직인 해의 비율
    bench: 주면 지수 대비 초과수익 기준으로 비교 (시장 전체 흐름을 뺀 종목 고유 패턴)
    min_amplitude_ratio: 계절 진폭(월별 지문의 표준편차)이 기준 종목의 이 비율 미만이면 제외.
        방향은 같아도 출렁임이 미미한 종목(채권형 펀드 등)은 '비슷한 차트'가 아니므로.
    """
    refs = reference if isinstance(reference, list) else [reference]
    ref_m = [seasonal_profile(r, years, "M", bench) for r in refs]
    ref_w = [seasonal_profile(r, years, "W", bench) for r in refs]
    if any(p is None for p in ref_m + ref_w):
        raise ValueError(f"기준 종목에 최근 {years}년 데이터가 부족합니다")
    ref_m = pd.concat(ref_m, axis=1).mean(axis=1)
    ref_w = pd.concat(ref_w, axis=1).mean(axis=1)
    min_amp = float(ref_m.std()) * min_amplitude_ratio

    rows = []
    for t, df in candidates.items():
        if df is None or len(df) < 252:
            continue
        recent = df.tail(60)
        if recent["Close"].iloc[-1] < min_price:
            continue
        if "Volume" in df and (recent["Close"] * recent["Volume"]).median() < min_dollar_volume:
            continue
        pm = seasonal_profile(df, years, "M", bench)
        pw = seasonal_profile(df, years, "W", bench)
        if pm is None or pw is None or pm.std() < min_amp:
            continue
        cm = float(np.corrcoef(ref_m, pm)[0, 1])
        cw = float(np.corrcoef(ref_w, pw)[0, 1])
        rows.append(
            {
                "ticker": t,
                "similarity": (cm + cw) / 2,
                "corr_monthly": cm,
                "corr_weekly": cw,
                "amplitude": float(pm.std()),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values("similarity", ascending=False).reset_index(drop=True)
    # 해마다 일관성은 계산이 무거우므로 상위 200개만
    top = out.head(200).index
    out["consistency"] = np.nan
    out.loc[top, "consistency"] = [
        yearly_consistency(candidates[t], ref_m, years, bench) for t in out.loc[top, "ticker"]
    ]
    return out


def cycle_stats(swings: pd.DataFrame) -> dict:
    """저점→고점(상승 구간), 고점→저점(하락 구간)의 기간·등락률 중앙값.

    마지막 스윙 이후는 아직 진행 중인 구간이라 제외한다.
    """
    done = swings.iloc[:-1]
    up = done[done["kind"] == "저점"]
    down = done[done["kind"] == "고점"]
    return {
        "up_days": float(up["days"].median()) if len(up) else np.nan,
        "up_move": float(up["move"].median()) if len(up) else np.nan,
        "down_days": float(down["days"].median()) if len(down) else np.nan,
        "down_move": float(down["move"].median()) if len(down) else np.nan,
        "n_up": len(up),
        "n_down": len(down),
    }
