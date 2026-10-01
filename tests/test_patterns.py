import numpy as np
import pandas as pd
import pytest

from seasonality import earnings, patterns
from seasonality.synthetic import make_prices


def _series(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype="float64")


def test_rsi_bounds_and_extremes():
    up = _series(np.linspace(100, 200, 60))
    down = _series(np.linspace(200, 100, 60))
    assert patterns.rsi(up).iloc[-1] == pytest.approx(100)
    assert patterns.rsi(down).iloc[-1] == pytest.approx(0)
    r = patterns.rsi(make_prices()).dropna()
    assert r.between(0, 100).all()
    assert 40 < r.mean() < 60


def test_find_swings_zigzag():
    # 100 → 150 → 90 → 140 : 저점, 고점, 저점 확정 (마지막 상승은 진행 중)
    vals = list(np.linspace(100, 150, 20)) + list(np.linspace(150, 90, 20)) + list(np.linspace(90, 140, 20))
    sw = patterns.find_swings(_series(vals), threshold=0.2)
    assert list(sw["kind"]) == ["저점", "고점", "저점"]
    assert sw["price"].tolist() == pytest.approx([100, 150, 90])
    assert sw["move"].iloc[1] == pytest.approx(90 / 150 - 1)
    assert sw["move"].iloc[-1] == pytest.approx(140 / 90 - 1)  # 마지막은 현재가까지


def test_small_wiggles_are_not_swings():
    vals = 100 + np.sin(np.arange(200) / 5) * 3  # ±3% 출렁임
    assert patterns.find_swings(_series(vals), threshold=0.1).empty


def test_swing_calendar_counts():
    sw = pd.DataFrame({"date": pd.to_datetime(["2020-03-02", "2021-03-10", "2020-07-01"]),
                       "price": [1, 1, 1], "kind": ["저점", "저점", "고점"], "move": [0, 0, 0], "days": [1, 1, 1]})
    cal = patterns.swing_calendar(sw)
    assert cal.loc[3, "lows"] == 2 and cal.loc[7, "highs"] == 1 and cal["lows"].sum() == 2


def test_forward_returns_detect_planted_month():
    p = make_prices("2010-01-01", "2025-12-31", monthly_effect={11: 0.06}, seed=3)
    fwd = patterns.forward_returns_by_month(p, horizons={"1개월 후": 21})
    means = fwd.loc[("1개월 후", "mean")]
    assert means.idxmax() == 11


def test_window_summary_has_all_windows():
    s = patterns.window_summary(make_prices("2015-01-01", "2025-12-31"))
    assert list(s.index) == ["1개월", "3개월", "1년", "3년", "5년"]
    assert (s["최대낙폭"] <= 0).all()
    assert s["현재가 위치"].between(0, 1).all()


def test_seasonal_profile_removes_trend():
    a = patterns.seasonal_profile(make_prices(drift=0.002, seed=1), years=5)
    assert a is not None and abs(a.mean()) < 1e-12


def test_find_similar_ranks_same_pattern_first():
    eff = {3: -0.06, 4: -0.05, 11: 0.08}
    ref = make_prices(monthly_effect=eff, seed=10)
    cands = {"TWIN": make_prices(monthly_effect=eff, seed=11)}
    cands["OPPOSITE"] = make_prices(monthly_effect={k: -v for k, v in eff.items()}, seed=12)
    for i in range(8):
        cands[f"N{i}"] = make_prices(seed=100 + i)
    sim = patterns.find_similar(ref, cands, years=10, min_dollar_volume=0)
    assert sim.iloc[0]["ticker"] == "TWIN"
    assert sim.iloc[-1]["ticker"] == "OPPOSITE"


def test_earnings_reactions_and_distance():
    vals = [100.0] * 30 + [120.0] * 30
    p = _series(vals)
    d = pd.DatetimeIndex([p.index[30]])
    r = earnings.earnings_reactions(p, d, before=5, after=5)
    assert r["reaction"].iloc[0] == pytest.approx(0.2)
    sw = pd.DataFrame({"date": [p.index[25]], "kind": ["저점"]})
    assert earnings.swing_earnings_distance(sw, d).iloc[0] == (p.index[30] - p.index[25]).days


def _wave(period_days, amp, n=1300, seed=0, phase=0.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    logp = amp * np.sin(2 * np.pi * t / period_days + phase) + rng.normal(0, 0.01, n).cumsum() * 0.2
    idx = pd.bdate_range("2021-01-01", periods=n)
    return pd.DataFrame({"Close": 50 * np.exp(logp), "Volume": 1e6}, index=idx)


def test_find_similar_cycles_prefers_same_rhythm():
    ref = _wave(120, 0.35, seed=1)
    cands = {"SAME": _wave(120, 0.35, seed=2, phase=1.0), "SLOW": _wave(400, 0.35, seed=3),
             "FLAT": _wave(120, 0.05, seed=4)}
    sim = patterns.find_similar_cycles(ref, cands, years=4, threshold=0.25, min_dollar_volume=0)
    assert sim.iloc[0]["ticker"] == "SAME"
    assert "FLAT" not in set(sim["ticker"])  # 25% 스윙이 거의 없어 제외


def test_validate_similar_detects_shared_pattern():
    eff = {3: -0.06, 4: -0.05, 11: 0.08}
    cands = {"REF": make_prices("2015-01-01", "2025-12-31", monthly_effect=eff, seed=1)}
    for i in range(5):
        cands[f"TWIN{i}"] = make_prices("2015-01-01", "2025-12-31", monthly_effect=eff, seed=10 + i)
    for i in range(30):
        cands[f"N{i}"] = make_prices("2015-01-01", "2025-12-31", seed=100 + i)
    v = patterns.validate_similar("REF", cands, pd.Timestamp("2023-12-31"), top=5, min_dollar_volume=0)
    assert v["top"] > v["random"] + 0.1


def test_rangebound_prefers_oscillator_over_trend():
    from seasonality import rangebound as rb

    idx = pd.bdate_range("2021-01-01", periods=1300)
    t = np.arange(1300)
    rng = np.random.default_rng(0)
    osc = pd.Series(50 * np.exp(0.35 * np.sin(2 * np.pi * t / 160) + rng.normal(0, 0.01, 1300)), index=idx)
    trend = pd.Series(50 * np.exp(0.0015 * t + 0.1 * np.sin(2 * np.pi * t / 160)), index=idx)
    mo, mt = rb.range_metrics(osc), rb.range_metrics(trend)
    assert mo["legs"] >= 10 and mo["trend_ratio"] < 0.2
    assert mt["trend_ratio"] > 0.8
    assert rb.range_score(mo) > rb.range_score(mt)
    assert rb.box_status(-0.1) == "박스 아래" and rb.box_status(1.5) == "상단 돌파"
