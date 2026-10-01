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


# ------------------------------------------------------------------ 실적 주기 지문
EVENT_PRE, EVENT_POST = 40, 40  # 실적 전후로 볼 거래일


def _event_index(close: pd.Series, dates: pd.DatetimeIndex, pre: int, post: int) -> list[int]:
    """실적일이 들어간 거래일 위치 (앞뒤 구간이 데이터 안에 있는 것만)."""
    out = []
    for d in pd.DatetimeIndex(dates).sort_values().unique():
        i = close.index.searchsorted(d)
        if i - pre - 1 >= 0 and i + post < len(close):
            out.append(int(i))
    return out


def event_profile(prices, dates: pd.DatetimeIndex, pre: int = EVENT_PRE, post: int = EVENT_POST) -> pd.Series | None:
    """실적 발표 전후 평균 주가 흐름 (발표 전날 = 0, 로그 수익률). 인덱스: -pre ~ +post 거래일."""
    close = _close(prices)
    idx = _event_index(close, dates, pre, post)
    if len(idx) < 4:
        return None
    logc = np.log(close.to_numpy())
    paths = np.array([logc[i - pre : i + post + 1] - logc[i - 1] for i in idx])
    return pd.Series(paths.mean(axis=0), index=range(-pre, post + 1))


def earnings_fingerprint(prices, dates: pd.DatetimeIndex, start=None, end=None, swing_threshold: float | None = None) -> dict | None:
    """실적 주기 지문: 이 종목이 얼마나 '실적 때 크게 움직이는 종목'인지.

    start~end 기간의 실적만 사용 (검증용으로 기간을 나눌 수 있게).
    """
    from .patterns import auto_threshold, find_swings

    close = _close(prices)
    if start is not None:
        close = close[close.index > pd.Timestamp(start) - pd.Timedelta(days=90)]
    if end is not None:
        close = close[close.index <= pd.Timestamp(end) + pd.Timedelta(days=90)]
    dates = pd.DatetimeIndex(dates)
    sel = dates
    if start is not None:
        sel = sel[sel > pd.Timestamp(start)]
    if end is not None:
        sel = sel[sel <= pd.Timestamp(end)]
    if len(close) < 250:
        return None
    reac = earnings_reactions(close, sel)
    if len(reac) < 6:
        return None

    r = np.log(close).diff().dropna()
    # 실적 전날~다음날(3거래일) 구간
    win = np.zeros(len(r), dtype=bool)
    for i in _event_index(close, sel, 1, 1):
        win[max(i - 2, 0) : i + 1] = True  # r 은 close 보다 한 칸 짧음
    in_period = np.ones(len(r), dtype=bool)
    if start is not None:
        in_period &= r.index > pd.Timestamp(start)
    if end is not None:
        in_period &= r.index <= pd.Timestamp(end)
    rv = r.to_numpy() ** 2
    var_share = rv[win & in_period].sum() / rv[in_period].sum()
    n_days = in_period.sum()
    # 실적 3일이 '평범한 3일'보다 몇 배 출렁이나
    normal_2d = np.abs(close.pct_change(2)).to_numpy()[1:][~win & in_period]
    react_ratio = reac["reaction"].abs().median() / np.nanmedian(normal_2d)

    th = swing_threshold if swing_threshold is not None else auto_threshold(close)
    sw = find_swings(close, th)
    if start is not None:
        sw = sw[sw["date"] > pd.Timestamp(start)]
    if end is not None:
        sw = sw[sw["date"] <= pd.Timestamp(end)]
    dist = swing_earnings_distance(sw, dates) if len(sw) else pd.Series(dtype=float)

    return {
        "events": len(reac),
        "react_abs": float(reac["reaction"].abs().median()),
        "react_ratio": float(react_ratio),
        "var_share": float(var_share),
        "var_share_expected": float(3 * len(reac) / max(n_days, 1)),  # 실적이 특별하지 않다면 기대되는 비율
        "runup_mean": float(reac["before"].mean()),
        "runup_hit": float((reac["before"] > 0).mean()),
        "post_mean": float(reac["after"].mean()),
        "swing_align": float((dist.abs() <= 15).mean()) if len(dist) else np.nan,
        "swings": len(sw),
        "threshold": float(th),
    }


# 비교에 쓰는 항목과 '눈에 띄게 다르다'고 볼 차이(척도)
FP_COLS = ["react_ratio", "var_share", "swing_align", "runup_hit", "threshold"]
FP_SCALE = {"react_ratio": 0.35, "var_share": 0.08, "swing_align": 0.15, "runup_hit": 0.20, "threshold": 0.30}
FP_LOG = {"react_ratio", "threshold"}  # 비율·크기는 로그로 비교


def _fp_vec(fp: dict) -> np.ndarray:
    return np.array([np.log(max(fp[c], 1e-6)) if c in FP_LOG else fp[c] for c in FP_COLS], dtype="float64")


def fingerprint_distance(a: dict, b: dict) -> float:
    """두 실적 지문의 거리 (0 = 같음, 1 ≈ 평균적으로 한 척도만큼 다름)."""
    scale = np.array([FP_SCALE[c] for c in FP_COLS])
    d = (_fp_vec(a) - _fp_vec(b)) / scale
    d = d[np.isfinite(d)]
    return float(np.sqrt(np.mean(d**2))) if len(d) else np.nan


def find_earnings_lookalikes(
    ref_prices,
    ref_dates: pd.DatetimeIndex,
    candidates: dict[str, tuple],
    start=None,
    end=None,
) -> pd.DataFrame:
    """기준 종목과 '실적 때 움직이는 방식'이 비슷한 종목 순위.

    candidates: {종목: (가격 DataFrame, 실적일 DatetimeIndex)}
    """
    ref = earnings_fingerprint(ref_prices, ref_dates, start, end)
    if ref is None:
        raise ValueError("기준 종목의 실적 데이터가 부족합니다")
    rows = []
    for t, (p, d) in candidates.items():
        if p is None or len(d) == 0:
            continue
        try:
            fp = earnings_fingerprint(p, d, start, end)
        except Exception as e:  # 한 종목 실패로 전체가 멈추지 않게
            log.warning("%s: 지문 계산 실패 (%s)", t, e)
            continue
        if fp is None:
            continue
        rows.append({"ticker": t, "distance": fingerprint_distance(ref, fp), **fp})
    out = pd.DataFrame(rows)
    out.attrs["reference"] = ref
    if out.empty:
        return out
    return out.sort_values("distance").reset_index(drop=True)
