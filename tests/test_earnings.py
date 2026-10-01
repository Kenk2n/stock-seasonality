import numpy as np
import pandas as pd

from seasonality import earnings


def _stock(jump: float, seed: int, n: int = 1300, vol: float = 0.015):
    """분기마다(63거래일) 실적일에 ±jump 만큼 튀는 가짜 종목."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    r = rng.normal(0, vol, n)
    ev = np.arange(40, n - 40, 63)
    r[ev] += jump * rng.choice([-1, 1], len(ev))
    close = 50 * np.exp(np.cumsum(r))
    return pd.DataFrame({"Close": close, "Volume": 1e6}, index=idx), pd.DatetimeIndex(idx[ev])


def test_fingerprint_detects_earnings_driven_stock():
    p_big, d_big = _stock(0.15, 1)
    p_none, d_none = _stock(0.0, 2)
    big = earnings.earnings_fingerprint(p_big, d_big)
    none = earnings.earnings_fingerprint(p_none, d_none)
    assert big["react_ratio"] > 3 * none["react_ratio"]
    assert big["var_share"] > 0.2 > none["var_share"]
    assert none["var_share"] < 3 * none["var_share_expected"]


def test_lookalikes_rank_same_behaviour_first():
    ref = _stock(0.15, 1)
    cands = {"TWIN": _stock(0.14, 3), "CALM": _stock(0.0, 4), "MILD": _stock(0.05, 5)}
    res = earnings.find_earnings_lookalikes(*ref, cands)
    assert list(res["ticker"])[0] == "TWIN"
    assert list(res["ticker"])[-1] == "CALM"


def test_fingerprint_period_split():
    p, d = _stock(0.15, 1)
    a = earnings.earnings_fingerprint(p, d, end=p.index[650])
    b = earnings.earnings_fingerprint(p, d, start=p.index[650])
    assert a and b and a["events"] + b["events"] <= len(d)
