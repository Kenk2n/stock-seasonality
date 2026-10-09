import numpy as np
import pandas as pd

from seasonality import indicators as ind
from seasonality import timing


def _ohlcv(close, seed=0):
    rng = np.random.default_rng(seed)
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2022-01-03", periods=len(close))
    hi = close * (1 + np.abs(rng.normal(0, 0.01, len(close))))
    lo = close * (1 - np.abs(rng.normal(0, 0.01, len(close))))
    vol = rng.integers(1_000_000, 2_000_000, len(close)).astype(float)
    return pd.DataFrame({"Open": close, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=idx)


def test_basic_indicator_math():
    c = pd.Series(np.full(60, 10.0), index=pd.bdate_range("2024-01-01", periods=60))
    m = ind.macd(c)
    assert np.allclose(m.dropna().to_numpy(), 0)
    bb = ind.bollinger(pd.Series(np.arange(1.0, 41.0)))
    assert np.isclose(bb["bb_mid"].iloc[-1], np.mean(np.arange(21.0, 41.0)))
    assert (bb["bb_up"].dropna() > bb["bb_low"].dropna()).all()
    obv = ind.obv(pd.Series([1.0, 2, 3, 2]), pd.Series([10.0, 10, 10, 5]))
    assert list(obv) == [0, 10, 20, 15]


def test_trend_indicators_follow_direction():
    up = _ohlcv(np.linspace(10, 30, 300))
    down = _ohlcv(np.linspace(30, 10, 300))
    iu, idn = ind.compute(up), ind.compute(down)
    assert iu["st_dir"].iloc[-1] == 1 and idn["st_dir"].iloc[-1] == -1
    assert iu["plus_di"].iloc[-1] > iu["minus_di"].iloc[-1] and iu["adx"].iloc[-1] > 25
    assert idn["minus_di"].iloc[-1] > idn["plus_di"].iloc[-1]
    assert iu["mfi"].dropna().between(0, 100).all()
    assert iu["stoch_k"].dropna().between(0, 100).all()
    # 앵커드 VWAP 은 52주 최저 종가 날짜부터 시작
    assert idn["avwap"].notna().sum() == 1 and iu["avwap"].notna().sum() == 252


def test_signals_on_rebound():
    # 300일 횡보 → 30일 급락 → 6일 반등
    rng = np.random.default_rng(1)
    base = 20 + rng.normal(0, 0.3, 300)
    fall = np.linspace(base[-1], 14, 30)
    reb = np.linspace(14, 15.5, 6)
    df = _ohlcv(np.r_[base, fall, reb])
    i = ind.compute(df)
    sig = ind.signals(df, i)
    keys = {s["key"] for s in sig}
    assert all(s["tone"] in {"bull", "bear", "neutral"} for s in sig)
    assert keys & {"rsi_exit_oversold", "bb_reentry", "macd_turning", "macd_golden", "stoch_up"}
    lt = ind.latest(df, i)
    assert 0 <= lt["rsi"] <= 100 and lt["st_dir"] in (1, -1)


def test_box_timing_prefers_bottom_rebound():
    rng = np.random.default_rng(2)
    base = 20 + rng.normal(0, 0.3, 300)
    bottom = _ohlcv(np.r_[base, np.linspace(base[-1], 14, 30), np.linspace(14, 14.8, 6)])
    top = _ohlcv(np.r_[base, np.linspace(base[-1], 27, 36)])
    tb = timing.box_timing(bottom, ind.compute(bottom), low=14.0, high=28.0, pos=0.05)
    tt = timing.box_timing(top, ind.compute(top), low=14.0, high=28.0, pos=0.95)
    assert tb["timing"] > tt["timing"] + 20
    assert tb["rr"] > 3 and 0 <= tb["timing"] <= 100
    assert timing.box_timing(bottom, ind.compute(bottom), 20.0, 40.0, -0.5)["parts"]["pos"] == 0


def test_accum_timing_range_and_stage():
    df = _ohlcv(20 + np.random.default_rng(3).normal(0, 0.3, 400))
    t = timing.accum_timing(df, ind.compute(df), accum_pct=0.9)
    assert 0 <= t["timing"] <= 100 and t["parts"]["accum"] == 0.9
    assert timing.stage(70, "low") == "매수 검토"
    assert timing.stage(70, "high") == "관찰"
    assert timing.stage(10, "low") == "대기"
