"""쿠라 스시형 박스: 큰 고점·저점이 같은 구간에서 여러 번 되풀이되는 종목.

예) 고점 100 → 저점 50 → 고점 120 → 저점 60 → 고점 90 → 저점 40 → 고점 100 → 저점 40
    고점들은 90~120 (고점 구간), 저점들은 40~60 (저점 구간) 에 모이고, 그 사이를 크게 오간다.

방법 (일봉 종가)
  1. 지그재그로 threshold 이상 되돌린 고점·저점을 찾는다.
  2. 기준 고점 = 고점들의 중앙값, 기준 저점 = 저점들의 중앙값, 진폭 = 기준 고점 / 기준 저점.
  3. 로그 가격으로 기준 저점 = 0, 기준 고점 = 1 인 자(z)를 만든다.
     z ≥ 0.6 인 고점만 '고점 구간에 닿은 고점', z ≤ 0.4 인 저점만 '저점 구간에 닿은 저점'.
     기준 고점·저점은 이렇게 인정된 스윙만으로 두 번 다시 잡는다 (중간의 작은 출렁임 제외).
     (예시: 기준 100/45 이면 고점 90~120 은 z 0.87~1.23, 저점 40~60 은 z −0.15~0.36 → 모두 인정)
  4. 시간순으로 저점 구간 → 고점 구간 → 저점 구간 … 을 오간 횟수 = 왕복.
     한 번 오갈 때 박스 높이의 절반 이상 움직인 것만 센다.
  5. 고점들·저점들이 시간이 지나며 한쪽으로 계속 올라가거나 내려가면(추세) 박스가 아니다.
     (100·120·90·100 처럼 오르내리는 것은 괜찮고, 14·17·22 처럼 계속 오르면 추세)
  마지막 확정 스윙 뒤 지금 진행 중인 움직임도 threshold 이상이면 스윙으로 센다 (지금 막 저점을 찍은 종목).

결과의 pos 는 현재가의 z (0 = 기준 저점, 1 = 기준 고점).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .patterns import find_swings

HIGH_Z, LOW_Z = 0.6, 0.4          # 고점 구간 · 저점 구간 경계
HIGH_Z_MAX, LOW_Z_MIN = 1.6, -0.6  # 이보다 크게 벗어난 고점·저점은 박스 밖 (돌출)
MIN_LEG = 0.5                      # 왕복 한 번으로 세려면 박스 높이(로그)의 절반 이상 움직여야 함


def swing_box(close: pd.Series, start=None, end=None, threshold: float = 0.25) -> dict | None:
    s = close.dropna()
    if start is not None:
        s = s[s.index > pd.Timestamp(start)]
    if end is not None:
        s = s[s.index <= pd.Timestamp(end)]
    if len(s) < 400 or (s <= 0).any():
        return None
    sw = find_swings(s, threshold)
    sw = _with_pending(sw, s, threshold)
    hi = sw[sw["kind"] == "고점"]
    lo = sw[sw["kind"] == "저점"]
    if len(hi) < 3 or len(lo) < 3:
        return None
    hp, lp = hi["price"].to_numpy(), lo["price"].to_numpy()
    hm, lm = float(np.median(hp)), float(np.median(lp))
    for _ in range(2):  # 기준 고점·저점을 제 구간에 닿은 스윙만으로 다시 잡는다 (작은 출렁임 제외)
        if hm <= lm * 1.2:
            return None
        width = np.log(hm / lm)
        zh, zl = np.log(hp / lm) / width, np.log(lp / lm) / width
        ok_h = (zh >= HIGH_Z) & (zh <= HIGH_Z_MAX)
        ok_l = (zl <= LOW_Z) & (zl >= LOW_Z_MIN)
        if ok_h.sum() < 2 or ok_l.sum() < 2:
            return None
        hm, lm = float(np.median(hp[ok_h])), float(np.median(lp[ok_l]))
    width = np.log(hm / lm)
    zh, zl = np.log(hp / lm) / width, np.log(lp / lm) / width
    ok_h = (zh >= HIGH_Z) & (zh <= HIGH_Z_MAX)
    ok_l = (zl <= LOW_Z) & (zl >= LOW_Z_MIN)

    # 시간순으로 저점 구간 ↔ 고점 구간을 오간 횟수. 한 번 오갈 때 박스 높이의 절반 이상 움직여야 센다.
    seq = sorted([(d, "H", p) for d, p, ok in zip(hi["date"], hp, ok_h) if ok]
                 + [(d, "L", p) for d, p, ok in zip(lo["date"], lp, ok_l) if ok])
    flips: list[list] = []
    for _, k, p in seq:
        if not flips:
            flips.append([k, p])
        elif k == flips[-1][0]:
            if (k == "H" and p > flips[-1][1]) or (k == "L" and p < flips[-1][1]):
                flips[-1][1] = p
        elif abs(np.log(p / flips[-1][1])) >= MIN_LEG * width:
            flips.append([k, p])
    round_trips = (len(flips) - 1) / 2

    years = (s.index[-1] - s.index[0]).days / 365.25

    def drift(dates, prices) -> float:
        """고점들(또는 저점들)이 기간 동안 한 방향으로 움직인 폭 / 박스 폭 (추세 정도).

        회귀 기울기로 움직인 폭에 순서 상관(스피어만)의 크기를 곱한다:
        100·120·90·100 처럼 오르내리면 작게, 14·17·22 처럼 계속 오르면 그대로 남는다.
        """
        if len(prices) < 3:
            return np.nan
        t = (pd.DatetimeIndex(dates) - s.index[0]).days.to_numpy() / 365.25
        slope = np.polyfit(t, np.log(prices), 1)[0]
        rp, rt = pd.Series(prices).rank().to_numpy(), pd.Series(t).rank().to_numpy()
        mono = 0.0 if rp.std() == 0 or rt.std() == 0 else abs(float(np.corrcoef(rp, rt)[0, 1]))
        return float(abs(slope * years) / width * mono)

    vh, vl = hi[ok_h], lo[ok_l]
    price = float(s.iloc[-1])
    z_all = np.log(s.to_numpy() / lm) / width
    inside = float(((z_all >= -0.5) & (z_all <= 1.5)).mean())  # 박스에서 크게 벗어나지 않은 날의 비율
    last_swing = sw["date"].max()
    return {
        "years": float(years),
        "amp": float(hm / lm), "hm": hm, "lm": lm,
        "high_zone": (float(vh["price"].min()), float(vh["price"].max())) if len(vh) else (np.nan, np.nan),
        "low_zone": (float(vl["price"].min()), float(vl["price"].max())) if len(vl) else (np.nan, np.nan),
        "n_high": int(ok_h.sum()), "n_low": int(ok_l.sum()), "n_swings": int(len(hi) + len(lo)),
        "valid_frac": float((ok_h.sum() + ok_l.sum()) / (len(hi) + len(lo))),
        "round_trips": float(round_trips),
        "drift_high": drift(vh["date"], vh["price"].to_numpy()),
        "drift_low": drift(vl["date"], vl["price"].to_numpy()),
        "spread_high": float(np.std(zh[ok_h])) if ok_h.sum() > 1 else np.nan,
        "spread_low": float(np.std(zl[ok_l])) if ok_l.sum() > 1 else np.nan,
        "months_since_swing": float((s.index[-1] - last_swing).days / 30.4),
        "inside": inside,
        "pos": float(np.log(price / lm) / width),
        "price": price,
        "swings": [{"d": d.strftime("%Y-%m-%d"), "p": float(p), "k": "H" if k == "고점" else "L", "ok": o}
                   for d, p, k, o in zip(sw["date"], sw["price"], sw["kind"], _ok_in_order(sw, ok_h, ok_l))],
    }


def _with_pending(sw: pd.DataFrame, s: pd.Series, threshold: float) -> pd.DataFrame:
    """마지막 확정 스윙 이후 반대 방향으로 threshold 이상 움직였으면 그 끝점을 '진행 중' 스윙으로 붙인다.

    예) 고점 100 다음에 40 까지 내려왔다가 48 로 반등 중 → 25% 반등이 안 돼 아직 확정 전이지만 저점 40 으로 본다.
    """
    if sw.empty:
        return sw
    last = sw.iloc[-1]
    after = s[s.index > last["date"]]
    if after.empty:
        return sw
    if last["kind"] == "고점":
        d, p, kind = after.idxmin(), float(after.min()), "저점"
        moved = 1 - p / last["price"]
    else:
        d, p, kind = after.idxmax(), float(after.max()), "고점"
        moved = p / last["price"] - 1
    if moved < threshold:
        return sw
    row = {"date": d, "price": p, "kind": kind, "move": np.nan, "days": np.nan}
    return pd.concat([sw, pd.DataFrame([row])], ignore_index=True)


def _ok_in_order(sw: pd.DataFrame, ok_h: np.ndarray, ok_l: np.ndarray) -> list[bool]:
    ih, il, out = iter(ok_h), iter(ok_l), []
    for k in sw["kind"]:
        out.append(bool(next(ih) if k == "고점" else next(il)))
    return out


def passes(m: dict | None, min_amp: float = 1.8, min_trips: float = 3.0, min_valid: float = 0.6,
           max_drift: float = 0.5, max_months_since_swing: float = 15, min_inside: float = 0.85) -> bool:
    """쿠라 스시형 조건: 진폭(기준 고점/기준 저점) 1.8배 이상, 저점↔고점 3번 이상 왕복, 고점·저점 3개 이상씩,
    스윙의 60% 이상이 제 구간, 고점·저점에 추세 없음, 85% 이상의 날을 박스 근처에서 보냄, 최근 15개월 안에 스윙."""
    if m is None:
        return False
    return bool(
        m["amp"] >= min_amp and m["round_trips"] >= min_trips and m["n_high"] >= 3 and m["n_low"] >= 3
        and m["valid_frac"] >= min_valid and m["inside"] >= min_inside
        and np.nan_to_num(m["drift_high"], nan=9) <= max_drift and np.nan_to_num(m["drift_low"], nan=9) <= max_drift
        and m["months_since_swing"] <= max_months_since_swing and m["pos"] <= 1.4
    )


def score(m: dict) -> float:
    """쿠라 스시형 점수: 왕복이 많고, 진폭이 크고, 고점·저점이 제 구간에 잘 모이고, 추세가 없을수록 높다."""
    spread = np.nanmean([m["spread_high"], m["spread_low"]])
    drift = np.nanmax([m["drift_high"], m["drift_low"]])
    return float(
        (m["round_trips"] / max(m["years"], 1)) * np.log(m["amp"]) * m["valid_frac"]
        * max(0.0, 1 - (0.0 if not np.isfinite(spread) else spread) / 0.5)
        * max(0.0, 1 - (0.0 if not np.isfinite(drift) else drift) / 0.8)
    )


def scan(prices: dict[str, pd.DataFrame], end: pd.Timestamp, years=(3, 4, 5), threshold: float = 0.25,
         **kw) -> pd.DataFrame:
    """종목마다 3·4·5년 중 점수가 가장 높은 쿠라 스시형 박스 (조건 통과한 것만)."""
    rows = []
    for t, df in prices.items():
        best = None
        for y in years:
            m = swing_box(df["Close"], end - pd.DateOffset(years=y), end, threshold)
            if not passes(m, **kw):
                continue
            sc = score(m)
            if best is None or sc > best["score"]:
                best = {"ticker": t, "box_years": y, "score": sc, **m}
        if best:
            rows.append(best)
    out = pd.DataFrame(rows)
    return out.set_index("ticker").sort_values("score", ascending=False) if len(out) else out
