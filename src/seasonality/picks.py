"""박스 하단 후보 계산 (box_picks.py 와 daily_watch.py 가 같이 쓴다).

박스 = 쿠라 스시형 (swingbox.py): 큰 고점·저점이 같은 구간에서 3번 이상 되풀이 (진폭 1.8배 이상).
후보 = 지금 저점 구간 근처 (박스 위치 pos_min ~ pos_max, 0 = 기준 저점, 1 = 기준 고점),
       인수 발표 등으로 가격이 고정된 종목 제외.
점수(raw, 100점): 박스 품질 35 · 저점 구간 근접 20 · KRUS 닮음 20 · 매집 흔적 15 · 최근 거래량 증가 10
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import accumulation as acc
from . import earnings, patterns
from . import swingbox as sb

WEIGHTS = {"box": 35, "bottom": 20, "krus": 20, "accum": 15, "volume": 10}
REF = "KRUS"
STOP_BELOW_FLOOR = 0.95  # 손절가 = 지난 저점들 중 가장 낮은 저점 × 0.95


def _clip01(x):
    return float(np.clip(x, 0, 1)) if np.isfinite(x) else 0.0


def pinned_tickers(snap: pd.DataFrame) -> set[str]:
    """변동성이 거의 0 이고 52주 고점 근처 = 인수 발표 등으로 주가가 고정된 경우."""
    return set(snap.index[(snap["contraction"] < 0.15) & (snap["pos52"] > 0.9)])


def box_scan(prices: dict[str, pd.DataFrame], end: pd.Timestamp, years=(3, 4, 5), **kw) -> pd.DataFrame:
    """종목마다 3·4·5년 중 가장 뚜렷한 쿠라 스시형 박스 (조건 통과한 것만, index = ticker)."""
    return sb.scan(prices, end, years, **kw)


def plan(r: pd.Series | dict) -> dict:
    """손절가·목표가: 지난 저점들 중 가장 낮은 저점 × 0.95, 지난 고점들 중 가장 낮은 고점."""
    return {"stop": float(r["low_zone"][0]) * STOP_BELOW_FLOOR, "target": float(r["high_zone"][0])}


def box_candidates(prices: dict[str, pd.DataFrame], box: pd.DataFrame, snap: pd.DataFrame, end: pd.Timestamp,
                   pos_min: float = -0.3, pos_max: float = 0.35, ref: str = REF
                   ) -> tuple[pd.DataFrame, dict[str, pd.DatetimeIndex]]:
    """저점 구간 근처 후보 + 점수. (후보 표, 종목별 실적 발표일) 을 돌려준다."""
    if box.empty:
        return pd.DataFrame(), {}
    cand = box[box["pos"].between(pos_min, pos_max) & box.index.isin(snap.index)
               & ~box.index.isin(pinned_tickers(snap))].copy()
    if cand.empty:
        return pd.DataFrame(), {}

    ref_amp = float(box.loc[ref, "amp"]) if ref in box.index else 2.2
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
        price = float(c.iloc[-1])
        fp = earnings.earnings_fingerprint(df, eds[t], start5, end) if len(eds[t]) else None
        e_dist = earnings.fingerprint_distance(ref_fp, fp) if (fp and ref_fp) else np.nan
        vol_ratio = v.tail(20).median() / v.tail(120).median() if v.tail(120).median() > 0 else np.nan
        nxt = earnings.next_earnings(eds[t], end) if len(eds[t]) else None
        pl = plan(r)
        s = {
            "box": np.nan,  # 아래에서 순위로
            "bottom": _clip01(1 - abs(r["pos"] - 0.05) / 0.4),
            "krus": 0.5 * np.exp(-abs(np.log(r["amp"] / ref_amp)) / 0.4) + 0.5 * (np.exp(-e_dist) if np.isfinite(e_dist) else 0.3),
            "accum": _clip01((z_stealth.get(t, 0) + z_cmf.get(t, 0)) / 4 + 0.5),
            "volume": _clip01((vol_ratio - 0.8) / 0.8),
        }
        rows.append({
            "ticker": t, **{f"s_{k}": val for k, val in s.items()}, "box_score": r["score"],
            # 쿠라 스시형 박스
            "years": int(r["box_years"]), "amp": r["amp"], "lm": r["lm"], "hm": r["hm"],
            "low_zone": r["low_zone"], "high_zone": r["high_zone"], "round_trips": r["round_trips"],
            "n_high": r["n_high"], "n_low": r["n_low"], "valid_frac": r["valid_frac"], "inside": r["inside"],
            "drift_high": r["drift_high"], "drift_low": r["drift_low"], "swings": r["swings"],
            "pos": r["pos"], "price": price, "stop": pl["stop"], "target": pl["target"],
            "to_target": pl["target"] / price - 1, "to_stop": pl["stop"] / price - 1,
            # box_picks.py 리포트가 쓰는 이름 (예전 박스 지표와 같은 뜻으로 맞춤)
            "band": r["amp"], "low": r["lm"], "high": r["hm"], "legs": int(round(r["round_trips"] * 2)),
            "trend_per_year": np.nan, "peak_disp": r["spread_high"], "trough_disp": r["spread_low"],
            "to_high": r["hm"] / price - 1, "to_low": price / r["lm"] - 1,
            "rsi": float(patterns.rsi(c).iloc[-1]), "ret1m": float(c.iloc[-1] / c.iloc[-22] - 1),
            "ret3m": float(c.iloc[-1] / c.iloc[-64] - 1), "vol_ratio": vol_ratio,
            "dollar_volume": float(snap.loc[t, "dollar_volume"]), "stealth": float(snap.loc[t, "stealth"]),
            "cmf": float(snap.loc[t, "cmf"]), "earn_dist": e_dist,
            "react_abs": fp["react_abs"] if fp else np.nan, "next_earnings": nxt, "is_ref": t == ref,
        })
    x = pd.DataFrame(rows).set_index("ticker")
    x["s_box"] = x["box_score"].rank(pct=True)
    x["raw"] = sum(WEIGHTS[k] * x[f"s_{k}"] for k in WEIGHTS)
    return x.sort_values("raw", ascending=False), eds
