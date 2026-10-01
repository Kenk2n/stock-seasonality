import numpy as np
import pandas as pd

from seasonality import accumulation as acc


def _ohlc(n=600, seed=0, accumulate=False):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    r = rng.normal(0, 0.02, n)
    close = 50 * np.exp(np.cumsum(r))
    if accumulate:
        # 마지막 60일: 주가는 횡보(평균 0)하지만 오르는 날 거래량이 크고 종가가 고가 근처
        close[-60:] = close[-61] * np.exp(np.cumsum(rng.normal(0, 0.01, 60)))
    up = np.r_[0, np.diff(close)] > 0
    vol = rng.integers(1_000_000, 2_000_000, n).astype(float)
    hi = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    lo = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    if accumulate:
        vol[-60:] = np.where(up[-60:], vol[-60:] * 3, vol[-60:] * 0.6)
        hi[-60:] = close[-60:] * 1.002  # 종가가 고가 근처
        lo[-60:] = close[-60:] * 0.98
    return pd.DataFrame({"Open": close, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=idx)


def test_signal_frame_ranges():
    s = acc.signal_frame(_ohlc()).dropna()
    assert s["vol_balance"].between(-1, 1).all()
    assert s["cmf"].between(-1, 1).all()
    assert (s["absorption"] >= 0).all()
    assert s["pos52"].between(0, 1).all()


def test_accumulating_stock_scores_highest():
    prices = {f"N{i}": _ohlc(seed=10 + i) for i in range(40)}
    prices["ACC"] = _ohlc(seed=1, accumulate=True)
    sig = acc.panel(prices)
    snap = acc.snapshot(sig, prices["ACC"].index[-1], min_price=0, min_dollar_volume=0)
    assert snap["score"].idxmax() == "ACC"
    assert snap.loc["ACC", "cmf"] > 0.5


def test_backtest_shapes():
    prices = {f"N{i}": _ohlc(seed=10 + i) for i in range(60)}
    dates = pd.bdate_range("2023-03-01", periods=4, freq="21B")
    summary, ic = acc.backtest(prices, dates, horizon=21, top=10, min_price=0, min_dollar_volume=0)
    assert len(summary) == len(ic) > 0
    assert summary["n"].min() >= 50
