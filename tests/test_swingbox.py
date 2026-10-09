import numpy as np
import pandas as pd

from seasonality import picks
from seasonality import swingbox as sb


def _path(points, days_per_leg=60, noise=0.01, seed=0):
    """꼭짓점(가격)들을 잇는 일봉 경로 + 작은 잡음."""
    rng = np.random.default_rng(seed)
    seg = [np.geomspace(a, b, days_per_leg, endpoint=False) for a, b in zip(points[:-1], points[1:])]
    v = np.concatenate(seg + [[points[-1]]])
    v = v * np.exp(rng.normal(0, noise, len(v)))
    return pd.Series(v, index=pd.bdate_range("2021-01-04", periods=len(v)))


def test_user_example_is_a_kura_box():
    # 고점 100 저점 50 고점 120 저점 60 고점 90 저점 40 고점 100 저점 40 (다시 반등 중)
    c = _path([70, 100, 50, 120, 60, 90, 40, 100, 40, 48], days_per_leg=110)
    m = sb.swing_box(c)
    assert sb.passes(m)
    assert 2.0 < m["amp"] < 2.6 and m["round_trips"] >= 3.5
    assert m["n_high"] == 4 and m["n_low"] == 4
    assert 85 < m["high_zone"][0] < 95 and 115 < m["high_zone"][1] < 125
    assert 38 < m["low_zone"][0] < 42 and 55 < m["low_zone"][1] < 65
    assert -0.2 < m["pos"] < 0.35  # 지금 저점 구간 근처
    pl = picks.plan(m)
    assert np.isclose(pl["stop"], m["low_zone"][0] * 0.95) and np.isclose(pl["target"], m["high_zone"][0])


def test_small_swings_trend_and_single_cycle_fail():
    narrow = _path([100, 125, 100, 128, 98, 124, 100, 126, 99, 110], days_per_leg=110)  # 1.25배 출렁임
    trend = _path([40, 80, 60, 120, 90, 180, 135, 270, 200, 260], days_per_leg=110)   # 고점·저점이 계속 올라감
    once = _path([50, 100, 52, 60, 55, 62, 54, 61, 56, 58], days_per_leg=110)          # 한 번만 크게 오감
    for c in (narrow, trend, once):
        assert not sb.passes(sb.swing_box(c))


def test_long_excursion_outside_box_fails():
    # 박스를 오가다가 박스 위로 한참 머무름 (85% 넘게 박스 근처여야 함)
    c = _path([50, 100, 50, 100, 50, 100, 50, 260, 255, 265, 250, 100, 52], days_per_leg=80)
    m = sb.swing_box(c)
    assert m is None or not sb.passes(m)


def test_scan_picks_best_period():
    c = _path([70, 100, 50, 120, 60, 90, 40, 100, 40, 48], days_per_leg=110)
    df = pd.DataFrame({"Close": c})
    res = sb.scan({"AAA": df, "BBB": pd.DataFrame({"Close": c * 0 + 10.0})}, c.index[-1], years=(3, 4))
    assert list(res.index) == ["AAA"] and res.loc["AAA", "box_years"] in (3, 4)
