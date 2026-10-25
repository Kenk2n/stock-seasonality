"""매수 타이밍 점수 (0~100). 높을수록 '지금이 들어갈 만한 자리'에 가깝다는 뜻일 뿐, 수익을 보장하지 않는다.

박스 하단형 (box_timing) — 쿠라 스시형 박스 (swingbox.py)
  저점 구간 근접 25 · 손익비 20 · RSI 반등 15 · MACD 전환 15 · 볼린저 하단 복귀 10 · 스토캐스틱 RSI 8 · 슈퍼트렌드 7
  손익비 = (목표가 − 현재가) / (현재가 − 손절가)
  목표가 = 지난 고점들 중 가장 낮은 고점, 손절가 = 지난 저점들 중 가장 낮은 저점 × 0.95 (picks.plan)

매집 흔적형 (accum_timing)
  매집 점수 순위 30 · 변동성 수축 15 · OBV 상승(주가 정체) 15 · 최근 거래량 증가 15 · 추세 회복 15 · RSI 중립대 10
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BOX_WEIGHTS = {"pos": 25, "rr": 20, "rsi": 15, "macd": 15, "bb": 10, "stoch": 8, "st": 7}
ACC_WEIGHTS = {"accum": 30, "squeeze": 15, "obv": 15, "volume": 15, "trend": 15, "rsi": 10}

LABELS = {
    "pos": "저점 구간 근접", "rr": "손익비", "rsi": "RSI 반등", "macd": "MACD 전환", "bb": "볼린저 하단",
    "stoch": "스토캐스틱 RSI", "st": "슈퍼트렌드", "accum": "매집 점수", "squeeze": "변동성 수축",
    "obv": "OBV 상승", "volume": "거래량 증가", "trend": "추세 회복",
}


def _c01(x) -> float:
    return float(np.clip(x, 0, 1)) if x is not None and np.isfinite(x) else 0.0


def _macd_part(ind: pd.DataFrame) -> float:
    h = ind["macd_hist"].dropna().tail(6)
    if len(h) < 4:
        return 0.0
    if (h.iloc[:-1] <= 0).any() and h.iloc[-1] > 0:
        return 1.0
    if h.iloc[-1] < 0 and h.diff().iloc[-3:].gt(0).all():
        return 0.6
    return 0.3 if h.iloc[-1] > 0 else 0.0


def _stoch_part(ind: pd.DataFrame) -> float:
    k, d = ind["stoch_k"].tail(4), ind["stoch_d"].tail(4)
    if k.isna().any() or d.isna().any():
        return 0.0
    if (k - d).iloc[:-1].le(0).any() and (k - d).iloc[-1] > 0 and k.min() < 20:
        return 1.0
    return 0.5 if k.iloc[-1] < 20 else 0.0


def _bb_part(c: pd.Series, ind: pd.DataFrame) -> float:
    low = ind["bb_low"]
    if not np.isfinite(low.iloc[-1]):
        return 0.0
    if c.iloc[-1] >= low.iloc[-1] and (c.tail(4).iloc[:-1] < low.tail(4).iloc[:-1]).any():
        return 1.0
    if c.iloc[-1] < low.iloc[-1]:
        return 0.6
    return 0.3 if ind["bb_pctb"].iloc[-1] < 0.2 else 0.0


def _st_part(ind: pd.DataFrame) -> float:
    sd = ind["st_dir"].dropna().tail(11)
    if sd.empty:
        return 0.0
    if sd.iloc[-1] == 1:
        return 1.0 if (sd.iloc[:-1] == -1).any() else 0.5
    return 0.0


def box_timing(df: pd.DataFrame, ind: pd.DataFrame, pos: float, stop: float, target: float) -> dict:
    """pos = 박스 위치 (0 = 기준 저점, 1 = 기준 고점)."""
    c = df["Close"]
    price = float(c.iloc[-1])
    r = ind["rsi"]
    rising = len(r) > 3 and r.iloc[-1] > r.iloc[-4]
    rr = (target - price) / (price - stop) if price > stop and target > price else 0.0
    parts = {
        "pos": 0.0 if pos < -0.3 else _c01(1 - abs(pos - 0.05) / 0.4),
        "rr": _c01(rr / 5),
        "rsi": _c01((55 - r.iloc[-1]) / 25) * (1.0 if rising else 0.5),
        "macd": _macd_part(ind),
        "bb": _bb_part(c, ind),
        "stoch": _stoch_part(ind),
        "st": _st_part(ind),
    }
    score = sum(BOX_WEIGHTS[k] * v for k, v in parts.items())
    return {"timing": round(score, 1), "parts": {k: round(v, 2) for k, v in parts.items()},
            "rr": round(rr, 2), "stop": round(stop, 4), "target": round(target, 4)}


def accum_timing(df: pd.DataFrame, ind: pd.DataFrame, accum_pct: float) -> dict:
    c, v = df["Close"], df["Volume"].fillna(0)
    price = float(c.iloc[-1])
    bw = ind["bb_width"].tail(126).dropna()
    squeeze = 1 - (bw.rank(pct=True).iloc[-1]) if len(bw) > 40 else 0.0
    o = ind["obv"]
    obv_part = 0.0
    if len(c) > 25:
        if o.iloc[-1] > o.iloc[-21] and c.iloc[-1] <= c.iloc[-21] * 1.05:
            obv_part = 1.0
        elif o.iloc[-1] > ind["obv_sma20"].iloc[-1]:
            obv_part = 0.5
    v50 = ind["vol_sma50"].iloc[-1]
    vr = v.tail(5).mean() / v50 if v50 and np.isfinite(v50) else np.nan
    up5 = len(c) > 5 and c.iloc[-1] >= c.iloc[-6]
    trend = (0.5 if np.isfinite(ind["sma50"].iloc[-1]) and price > ind["sma50"].iloc[-1] else 0.0) + \
            (0.5 if ind["st_dir"].iloc[-1] == 1 else 0.0)
    rsi = ind["rsi"].iloc[-1]
    parts = {
        "accum": _c01(accum_pct),
        "squeeze": _c01(squeeze),
        "obv": obv_part,
        "volume": _c01((vr - 1) / 1.5) * (1.0 if up5 else 0.5),
        "trend": trend,
        "rsi": _c01(1 - max(45 - rsi, rsi - 65, 0) / 20) if np.isfinite(rsi) else 0.0,
    }
    score = sum(ACC_WEIGHTS[k] * val for k, val in parts.items())
    stop = float(df["Low"].tail(20).min()) * 0.97
    target = float(c.tail(250).max())
    rr = (target - price) / (price - stop) if price > stop and target > price else 0.0
    return {"timing": round(score, 1), "parts": {k: round(val, 2) for k, val in parts.items()},
            "rr": round(rr, 2), "stop": round(stop, 4), "target": round(target, 4),
            "vol_ratio5": None if not np.isfinite(vr) else round(float(vr), 2)}


def stage(timing: float, risk_level: str) -> str:
    """매수 검토(≥60, 고위험 아님) · 관찰(≥40) · 대기."""
    if timing >= 60 and risk_level != "high":
        return "매수 검토"
    if timing >= 40:
        return "관찰"
    return "대기"
