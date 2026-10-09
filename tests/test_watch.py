import numpy as np
import pandas as pd

from seasonality import watch


def _box_top(tickers, prices, low=10.0, high=20.0):
    return pd.DataFrame({"name": [f"{t} Inc" for t in tickers], "price": prices, "total": np.linspace(80, 60, len(tickers)),
                         "low": low, "high": high}, index=pd.Index(tickers, name="ticker"))


def _hist(days):
    """days = [(날짜, [종목...], [가격...]), ...] → 박스 목록 기록"""
    h = watch.read_history("does-not-exist.csv")
    for d, ts, ps in days:
        h = watch.append_history(h, watch.picks_from_box(_box_top(ts, ps), pd.Timestamp(d)))
    return h


def test_append_history_overwrites_same_day():
    h = _hist([("2026-10-01", ["A", "B"], [11, 12]), ("2026-10-01", ["C"], [13])])
    assert list(h["ticker"]) == ["C"]
    acc = pd.DataFrame({"name": ["X"], "close": [5.0], "score": [1.2]}, index=pd.Index(["X"], name="ticker"))
    h = watch.append_history(h, watch.picks_from_accum(acc, pd.Timestamp("2026-10-01")))
    assert set(h["list"]) == {"box", "accum"} and len(h) == 2


def test_changes_and_streaks():
    h = _hist([("2026-10-01", ["A", "B"], [11, 12]), ("2026-10-02", ["A", "C"], [11, 13]),
               ("2026-10-05", ["A", "C", "D"], [11, 13, 14])])
    new, dropped = watch.changes(h, "box")
    assert new == ["D"] and dropped == []
    assert watch.streaks(h, "box") == {"A": 3, "C": 2, "D": 1}
    assert watch.changes(h[h["date"] <= "2026-10-02"], "box") == (["C"], ["B"])


def test_track_returns_and_status():
    idx = pd.bdate_range("2026-09-28", "2026-10-09")
    closes = {
        "A": pd.Series(np.linspace(11, 22, len(idx)), idx),   # 박스 상단(20) 도달
        "B": pd.Series(np.linspace(12, 7, len(idx)), idx),    # 하단(10)의 80% 아래 → 박스 이탈
        "C": pd.Series(np.full(len(idx), 13.0), idx),
    }
    bench = pd.Series(np.linspace(100, 110, len(idx)), idx)
    h = _hist([("2026-09-28", ["A", "B"], [11, 12]), ("2026-10-09", ["C"], [13])])
    tr = watch.track(h, "box", closes, bench, idx[-1]).set_index("ticker")
    assert tr.loc["A", "status"] == "상단 도달"
    assert tr.loc["B", "status"] == "박스 이탈"
    assert tr.loc["C", "status"] == "목록 유지"
    assert np.isclose(tr.loc["A", "ret"], 22 / 11 - 1)
    assert np.isclose(tr.loc["A", "bench_ret"], 0.10)
    assert np.isclose(tr.loc["A", "excess"], 1.0 - 0.10)
    sm = watch.track_summary(tr)
    assert sm["n"].sum() == 3


def test_track_skips_old_and_missing():
    idx = pd.bdate_range("2026-01-01", "2026-10-09")
    closes = {"A": pd.Series(15.0, idx)}
    bench = pd.Series(100.0, idx)
    h = _hist([("2026-01-02", ["A"], [11]), ("2026-10-09", ["Z"], [13])])
    assert watch.track(h, "box", closes, bench, idx[-1], lookback_days=90).empty
