"""비슷한 차트 찾기 (similarchart.com 방식): 지금 차트와 닮은 과거 차트들이 그 뒤에 어떻게 움직였나.

각 종목의 최근 N거래일 차트(N = 8 · 16 · 32 · 64 · 128)와 가장 비슷한 과거 차트 50개를 전 종목의 지난 데이터에서 찾고,
그 차트들의 이후 10거래일 변동으로 전망을 만든다.

비슷함
  로그 종가를 구간 안에서 평균 0 · 표준편차 1 로 맞춘 '모양'의 상관계수 (가격 수준·변동 크기와 무관).
  64 · 128일 구간은 32개 점으로 줄여 비교한다.
  하루 변동 크기가 0.5~2배 범위인 차트만 인정 (모양만 같고 출렁임이 전혀 다른 차트 제외).
  같은 종목에서 지금 구간과 겹치는 차트는 빼고, 한 종목에서는 서로 겹치지 않는 차트를 최대 2개까지.
결과
  상승 비율 = 비슷한 차트 중 10거래일 뒤 오른 비율, 평균 변동률 = 10거래일 뒤 평균 등락률 (±50% 로 자름)
  점수 0~10: 5 = 전체 과거 차트의 평균과 같음. 상승 비율과 평균 변동률이 평균보다 높을수록 올라간다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

LENGTHS = (8, 16, 32, 64, 128)
DIMS = {8: 8, 16: 16, 32: 32, 64: 32, 128: 32}
STRIDES = {8: 2, 16: 2, 32: 2, 64: 3, 128: 4}  # 과거 차트를 며칠 간격으로 모을지 (속도)
HORIZON = 10
K = 50
PER_TICKER = 2
POOL = 800
VOL_BAND = (0.5, 2.0)


@dataclass
class Series:
    """종목별 로그 종가와 날짜(일 번호)."""
    tickers: list[str]
    logc: list[np.ndarray]
    days: list[np.ndarray]

    @classmethod
    def from_prices(cls, prices: dict[str, pd.DataFrame]) -> "Series":
        tk, lc, dd = [], [], []
        for t, df in prices.items():
            c = df["Close"].dropna()
            c = c[c > 0.01]
            if len(c) < 200:
                continue
            tk.append(t)
            lc.append(np.log(c.to_numpy(dtype=float)))
            dd.append(c.index.values.astype("datetime64[D]").astype(np.int64))
        return cls(tk, lc, dd)


@dataclass
class Library:
    L: int
    vec: np.ndarray    # (n, d) 모양 (단위 벡터 → 내적 = 상관계수)
    vol: np.ndarray    # (n,) 하루 변동 크기
    fut: np.ndarray    # (n,) 이후 HORIZON 거래일 등락률
    tick: np.ndarray   # (n,) 종목 번호
    end: np.ndarray    # (n,) 구간 끝 위치 (그 종목 배열 안)
    rows: np.ndarray   # (종목 수 + 1,) 종목별 행 시작 위치

    def base(self) -> dict:
        f = np.clip(self.fut, -0.5, 0.5)
        return {"rise": float((self.fut > 0).mean()), "avg": float(f.mean()), "sd": float(f.std())}


def _resample(w: np.ndarray, d: int) -> np.ndarray:
    L = w.shape[1]
    if L == d:
        return w
    x = np.linspace(0, L - 1, d)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, L - 1)
    f = x - i0
    return w[:, i0] * (1 - f) + w[:, i1] * f


def shape(w: np.ndarray, d: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """로그 가격 구간들 (m, L) → (모양 단위벡터 (m, d), 하루 변동 크기 (m,), 쓸 수 있는 행 (m,))."""
    vol = np.diff(w, axis=1).std(axis=1)
    r = _resample(w, d)
    r = r - r.mean(axis=1, keepdims=True)
    sd = r.std(axis=1)
    ok = np.isfinite(sd) & (sd > 1e-6) & np.isfinite(vol) & (vol > 0)
    z = np.zeros_like(r)
    z[ok] = r[ok] / (sd[ok, None] * np.sqrt(d))
    return z.astype(np.float32), vol.astype(np.float32), ok


def build(series: Series, L: int, until_day: int | None = None, horizon: int = HORIZON,
          stride: int | None = None) -> Library:
    """과거 차트 모음. until_day 가 있으면 그날까지 이후 변동이 다 알려진 차트만 (과거 검증용)."""
    stride = stride or STRIDES[L]
    d = DIMS[L]
    vecs, vols, futs, ticks, ends, rows = [], [], [], [], [], [0]
    for i, (lc, dd) in enumerate(zip(series.logc, series.days)):
        last = len(lc) - 1 - horizon
        e = np.arange(L - 1, last + 1, stride)
        if until_day is not None and len(e):
            e = e[dd[e + horizon] <= until_day]
        if len(e) == 0:
            rows.append(rows[-1])
            continue
        w = sliding_window_view(lc, L)[e - (L - 1)]
        v, vol, ok = shape(w, d)
        e, v, vol = e[ok], v[ok], vol[ok]
        vecs.append(v)
        vols.append(vol)
        futs.append((np.exp(lc[e + horizon] - lc[e]) - 1).astype(np.float32))
        ticks.append(np.full(len(e), i, dtype=np.int32))
        ends.append(e.astype(np.int32))
        rows.append(rows[-1] + len(e))
    cat = (lambda xs, dt: np.concatenate(xs) if xs else np.zeros(0, dt))
    vec = np.concatenate(vecs) if vecs else np.zeros((0, d), np.float32)
    return Library(L, vec, cat(vols, np.float32), cat(futs, np.float32), cat(ticks, np.int32),
                   cat(ends, np.int32), np.asarray(rows, dtype=np.int64))


def search(lib: Library, series: Series, queries: list[tuple[int, int]], k: int = K,
           batch: int = 16) -> list[dict]:
    """queries = [(종목 번호, 구간 끝 위치)]. 각 질의마다 비슷한 차트 k개와 이후 변동 요약."""
    L, d = lib.L, DIMS[lib.L]
    out: list[dict] = []
    for b0 in range(0, len(queries), batch):
        qs = queries[b0:b0 + batch]
        w = np.stack([series.logc[t][e - L + 1:e + 1] for t, e in qs])
        qv, qvol, qok = shape(w, d)
        sims = qv @ lib.vec.T
        lo, hi = qvol * VOL_BAND[0], qvol * VOL_BAND[1]
        bad = lib.vol[None, :] < lo[:, None]
        bad |= lib.vol[None, :] > hi[:, None]
        sims[bad] = -2
        for j, (t, e) in enumerate(qs):  # 같은 종목에서 지금 구간과 겹치는 차트 제외
            s0, s1 = lib.rows[t], lib.rows[t + 1]
            if s1 > s0:
                sims[j, s0:s1][lib.end[s0:s1] > e - L] = -2
        pool = min(POOL, sims.shape[1] - 1)
        cand = np.argpartition(-sims, pool, axis=1)[:, :pool] if pool > 0 else np.zeros((len(qs), 0), int)
        for j, (t, e) in enumerate(qs):
            if not qok[j]:
                out.append({"n": 0})
                continue
            picked = _pick(lib, sims[j], cand[j], k)
            if len(picked) < k and pool < sims.shape[1] - 1:  # 후보가 모자라면 전체에서 다시
                picked = _pick(lib, sims[j], np.arange(sims.shape[1]), k)
            out.append(summarize(lib, sims[j], np.asarray(picked, dtype=np.int64)))
    return out


def _pick(lib: Library, sim_row: np.ndarray, cand: np.ndarray, k: int) -> list[int]:
    """비슷한 순서대로, 한 종목에서는 서로 겹치지 않는 차트를 PER_TICKER 개까지."""
    picked, used = [], {}
    for r in cand[np.argsort(-sim_row[cand])]:
        if sim_row[r] <= -1:
            break
        tt, ee = int(lib.tick[r]), int(lib.end[r])
        prev = used.setdefault(tt, [])
        if len(prev) >= PER_TICKER or any(abs(ee - p) < lib.L for p in prev):
            continue
        prev.append(ee)
        picked.append(int(r))
        if len(picked) >= k:
            break
    return picked


def summarize(lib: Library, sim_row: np.ndarray, picked: np.ndarray) -> dict:
    if len(picked) == 0:
        return {"n": 0}
    f = lib.fut[picked]
    return {"n": int(len(picked)), "rise": float((f > 0).mean()), "avg": float(np.clip(f, -0.5, 0.5).mean()),
            "med": float(np.median(f)), "sim": float(sim_row[picked].mean()), "rows": picked,
            "sims": sim_row[picked].astype(float)}


def fan(lib: Library, series: Series, picked: np.ndarray, horizon: int = HORIZON) -> dict:
    """비슷한 차트들의 이후 horizon 일 경로 분포 (구간 끝 = 1): 10·25·50·75·90 백분위."""
    paths = np.stack([np.exp(series.logc[t][e + 1:e + 1 + horizon] - series.logc[t][e])
                      for t, e in zip(lib.tick[picked], lib.end[picked])])
    q = np.percentile(paths, [10, 25, 50, 75, 90], axis=0)
    return {k: [round(float(v), 4) for v in row] for k, row in zip(("p10", "p25", "p50", "p75", "p90"), q)}


def score(st: dict, base: dict) -> float:
    """0~10, 5 = 전체 과거 차트 평균. 상승 비율·평균 변동률이 평균보다 높을수록 높다."""
    n = st.get("n", 0)
    if n < 10:
        return np.nan
    z_rise = (st["rise"] - base["rise"]) / np.sqrt(base["rise"] * (1 - base["rise"]) / n)
    z_avg = (st["avg"] - base["avg"]) / (base["sd"] / np.sqrt(n))
    return float(np.clip(5 + (z_rise + z_avg) / 2, 0, 10))


def path(series: Series, t: int, e: int, L: int, horizon: int = HORIZON, points: int = 32) -> dict:
    """그림용: 구간(최대 points 개 점으로 줄임) + 이후 horizon 일, 구간 끝 가격 = 1 기준 비율."""
    lc = series.logc[t]
    w = lc[e - L + 1:e + 1]
    idx = np.unique(np.linspace(0, L - 1, min(points, L)).round().astype(int))
    after = lc[e + 1:e + 1 + horizon]
    days = series.days[t]
    return {"x": [int(i - (L - 1)) for i in idx], "w": [round(float(np.exp(w[i] - lc[e])), 4) for i in idx],
            "f": [round(float(np.exp(v - lc[e])), 4) for v in after],
            "start": str(np.datetime64(int(days[e - L + 1]), "D")), "end": str(np.datetime64(int(days[e]), "D"))}
