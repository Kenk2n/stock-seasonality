"""박스 하단 후보 계산 (box_picks.py 와 daily_watch.py 가 같이 쓴다).

조건: 3·4·5년 중 가장 뚜렷한 박스(폭 ≥ 1.5배, 횡단 ≥ min_legs, 추세 ≤ 박스 폭 35%),
      현재가가 박스 안 위치 pos_min ~ pos_max, 인수 발표 등으로 가격이 고정된 종목 제외.
점수(raw, 100점): 박스 품질 35 · 하단 근접 20 · KRUS 닮음 20 · 매집 흔적 15 · 최근 거래량 증가 10
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import accumulation as acc
from . import earnings, patterns
from . import rangebound as rb

WEIGHTS = {"box": 35, "bottom": 20, "krus": 20, "accum": 15, "volume": 10}
REF = "KRUS"


def _clip01(x):
    return float(np.clip(x, 0, 1)) if np.isfinite(x) else 0.0


def pinned_tickers(snap: pd.DataFrame) -> set[str]:
    """변동성이 거의 0 이고 52주 고점 근처 = 인수 발표 등으로 주가가 고정된 경우."""
    return set(snap.index[(snap["contraction"] < 0.15) & (snap["pos52"] > 0.9)])


def box_scan(prices: dict[str, pd.DataFrame], end: pd.Timestamp, years=(3, 4, 5), min_legs: int = 4) -> pd.DataFrame:
    """종목마다 3·4·5년 중 점수 높은 박스 (박스 조건 통과한 것만)."""
    frames = [rb.scan_rangebound(prices, end - pd.DateOffset(years=y), end, 1.5, 0.35, min_legs).assign(years=y)
              for y in years]
    box = pd.concat(frames).sort_values(["passes", "score"], ascending=False).drop_duplicates("ticker")
    return box[box["passes"]].set_index("ticker")


def box_candidates(prices: dict[str, pd.DataFrame], box: pd.DataFrame, snap: pd.DataFrame, end: pd.Timestamp,
                   pos_min: float = -0.2, pos_max: float = 0.3, ref: str = REF
                   ) -> tuple[pd.DataFrame, dict[str, pd.DatetimeIndex]]:
    """박스 하단 근처 후보 + 점수. (후보 표, 종목별 실적 발표일) 을 돌려준다."""
    cand = box[box["pos"].between(pos_min, pos_max) & box.index.isin(snap.index)
               & ~box.index.isin(pinned_tickers(snap))].copy()
    cand = cand[cand.index != ref]
    if cand.empty:
        return pd.DataFrame(), {}

    ref_band = float(box.loc[ref, "band"]) if ref in box.index else 2.0
    start5 = end - pd.DateOffset(years=5)
    ref_fp = None
    if ref in prices:
        ref_fp = earnings.earnings_fingerprint(prices[ref], earnings.load_earnings_dates(ref), start5, end)
    with ThreadPoolExecutor(8) as ex:
        eds = dict(zip(cand.index, ex.map(earnings.load_earnings_dates, cand.index)))

    z_stealth, z_cmf = acc._z(snap["stealth"]), acc._z(snap["cmf"])
    rows = []
    for t, r in cand.iterrows():
        df = prices[t]
        c, v = df["Close"], df["Volume"]
        fp = earnings.earnings_fingerprint(df, eds[t], start5, end) if len(eds[t]) else None
        e_dist = earnings.fingerprint_distance(ref_fp, fp) if (fp and ref_fp) else np.nan
        vol_ratio = v.tail(20).median() / v.tail(120).median() if v.tail(120).median() > 0 else np.nan
        nxt = earnings.next_earnings(eds[t], end) if len(eds[t]) else None
        s = {
            "box": np.nan,  # 아래에서 순위로
            "bottom": _clip01(1 - abs(r["pos"] - 0.1) / 0.4),
            "krus": 0.5 * np.exp(-abs(np.log(r["band"] / ref_band)) / 0.4) + 0.5 * (np.exp(-e_dist) if np.isfinite(e_dist) else 0.3),
            "accum": _clip01((z_stealth.get(t, 0) + z_cmf.get(t, 0)) / 4 + 0.5),
            "volume": _clip01((vol_ratio - 0.8) / 0.8),
        }
        rows.append({
            "ticker": t, **{f"s_{k}": val for k, val in s.items()}, "box_score": r["score"],
            "years": int(r["years"]), "band": r["band"], "low": r["low"], "high": r["high"], "legs": int(r["legs"]),
            "trend_per_year": r["trend_per_year"], "peak_disp": r["peak_disp"], "trough_disp": r["trough_disp"],
            "pos": r["pos"], "price": float(c.iloc[-1]), "to_high": r["high"] / float(c.iloc[-1]) - 1,
            "to_low": float(c.iloc[-1]) / r["low"] - 1,
            "rsi": float(patterns.rsi(c).iloc[-1]), "ret1m": float(c.iloc[-1] / c.iloc[-22] - 1),
            "ret3m": float(c.iloc[-1] / c.iloc[-64] - 1), "vol_ratio": vol_ratio,
            "dollar_volume": float(snap.loc[t, "dollar_volume"]), "stealth": float(snap.loc[t, "stealth"]),
            "cmf": float(snap.loc[t, "cmf"]), "earn_dist": e_dist,
            "react_abs": fp["react_abs"] if fp else np.nan, "next_earnings": nxt,
        })
    x = pd.DataFrame(rows).set_index("ticker")
    x["s_box"] = x["box_score"].rank(pct=True)
    x["raw"] = sum(WEIGHTS[k] * x[f"s_{k}"] for k in WEIGHTS)
    return x.sort_values("raw", ascending=False), eds
