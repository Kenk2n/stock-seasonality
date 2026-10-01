"""박스권 왕복 종목 찾기: 같은 가격 범위 안에서 위아래로 여러 번 오가는 종목.

모든 계산은 주봉 종가의 로그값으로 한다 (일봉 잡음을 줄이고, 2배 상승과 1/2 하락을 같은 크기로 보기 위해).

지표
  band        박스 폭 = 상위 10% 가격 / 하위 10% 가격 (예: 2.0 = 박스 상단이 하단의 2배)
  legs        박스 하단(하위 20%)에서 상단(상위 20%)으로, 또는 그 반대로 건너간 횟수
  trend_ratio 기간 전체 추세(회귀선)로 움직인 폭 / 박스 폭. 0 에 가까울수록 옆으로 기는 박스
  peak_disp   스윙 고점들의 높이 차이(로그 표준편차) / 박스 폭. 작을수록 고점이 일정
  trough_disp 스윙 저점들의 높이 차이 / 박스 폭
  pos         현재가의 박스 안 위치 (0 = 하단, 1 = 상단)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .patterns import find_swings


def range_metrics(close: pd.Series, start=None, end=None) -> dict | None:
    s = close.dropna()
    if start is not None:
        s = s[s.index > pd.Timestamp(start)]
    if end is not None:
        s = s[s.index <= pd.Timestamp(end)]
    w = s.resample("W-FRI").last().dropna()
    if len(w) < 104 or (w <= 0).any():
        return None
    y = np.log(w.to_numpy())
    t = (w.index - w.index[0]).days.to_numpy() / 365.25
    years = t[-1]
    lo, hi = np.quantile(y, [0.1, 0.9])
    width = hi - lo
    if width <= 0:
        return None
    slope, intercept = np.polyfit(t, y, 1)
    trend_ratio = abs(slope * years) / width

    z = (y - lo) / width
    legs, state = 0, None
    for v in z:
        zone = "low" if v < 0.2 else "high" if v > 0.8 else None
        if zone and zone != state:
            if state is not None:
                legs += 1
            state = zone

    # 박스 폭의 절반 이상 되돌린 것만 스윙으로 (일봉으로 고점·저점 위치를 정확히)
    th = float(np.exp(width * 0.5) - 1)
    sw = find_swings(s, min(max(th, 0.08), 0.6))
    hi_p = np.log(sw.loc[sw["kind"] == "고점", "price"].to_numpy())
    lo_p = np.log(sw.loc[sw["kind"] == "저점", "price"].to_numpy())
    peak_disp = float(np.std(hi_p) / width) if len(hi_p) >= 2 else np.nan
    trough_disp = float(np.std(lo_p) / width) if len(lo_p) >= 2 else np.nan

    return {
        "years": float(years),
        "band": float(np.exp(width)),
        "low": float(np.exp(lo)),
        "high": float(np.exp(hi)),
        "legs": legs,
        "legs_per_year": legs / years,
        "trend_ratio": float(trend_ratio),
        "trend_per_year": float(np.exp(slope) - 1),
        "peak_disp": peak_disp,
        "trough_disp": trough_disp,
        "swing_highs": int(len(hi_p)),
        "swing_lows": int(len(lo_p)),
        "pos": float(np.clip(z[-1], -0.5, 1.5)),
        "price": float(w.iloc[-1]),
    }


def range_score(m: dict) -> float:
    """박스 왕복형 점수: 왕복이 잦고 폭이 크면서, 추세가 없고 고점·저점이 일정할수록 높다."""
    if m is None:
        return np.nan
    disp = np.nanmean([m["peak_disp"], m["trough_disp"]])
    disp = 1.0 if not np.isfinite(disp) else disp
    return float(
        m["legs_per_year"] * np.log(m["band"])
        * max(0.0, 1 - m["trend_ratio"] / 0.6)
        * max(0.0, 1 - disp / 0.5)
    )


def scan_rangebound(prices: dict[str, pd.DataFrame], start=None, end=None, min_band: float = 1.5,
                    max_trend_ratio: float = 0.35, min_legs: int = 4) -> pd.DataFrame:
    rows = []
    for t, df in prices.items():
        m = range_metrics(df["Close"], start, end)
        if m is None:
            continue
        rows.append({"ticker": t, "score": range_score(m), **m})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["passes"] = (out["band"] >= min_band) & (out["trend_ratio"] <= max_trend_ratio) & (out["legs"] >= min_legs)
    return out.sort_values(["passes", "score"], ascending=False).reset_index(drop=True)


def box_status(pos: float) -> str:
    """박스 안 위치(0 = 하단, 1 = 상단)를 말로."""
    if pos < -0.25:
        return "하단 이탈"
    if pos < 0:
        return "박스 아래"
    if pos > 1.25:
        return "상단 돌파"
    if pos > 1:
        return "박스 위"
    if pos < 0.25:
        return "박스 하단"
    if pos > 0.75:
        return "박스 상단"
    return "박스 중간"


def _legs_in_box(w: pd.Series, lo: float, hi: float) -> tuple[int, float]:
    z = (np.log(w) - np.log(lo)) / (np.log(hi) - np.log(lo))
    legs, state = 0, None
    for v in z:
        zone = "low" if v < 0.2 else "high" if v > 0.8 else None
        if zone and zone != state:
            if state is not None:
                legs += 1
            state = zone
    return legs, float(((z >= -0.25) & (z <= 1.25)).mean())


def validate_persistence(prices: dict[str, pd.DataFrame], cut: pd.Timestamp, train_years: int = 3,
                         min_legs: int = 4) -> dict:
    """표본 외 검증: cut 이전 train_years 년으로 찾은 박스형이 cut 이후에도 그 박스에서 왕복했나.

    같은 박스 폭(변동성)의 박스형이 아닌 종목(대조군)과 비교한다.
    """
    a = scan_rangebound(prices, cut - pd.DateOffset(years=train_years), cut, min_legs=min_legs)
    box = a[a["passes"]]
    if box.empty:
        return {}
    lo_b, hi_b = box["band"].quantile([0.2, 0.8])
    ctrl = a[(~a["passes"]) & a["band"].between(lo_b, hi_b)]

    def stats(df):
        rows = []
        for r in df.itertuples():
            c = prices[r.ticker]["Close"]
            w = c[c.index > cut].resample("W-FRI").last().dropna()
            if len(w) < 52:
                continue
            legs, inside = _legs_in_box(w, r.low, r.high)
            rows.append({"legs": legs, "inside": inside})
        x = pd.DataFrame(rows)
        return {"n": len(x), "legs": float(x["legs"].mean()), "inside": float(x["inside"].mean())}

    return {"box": stats(box), "control": stats(ctrl), "cut": cut}
