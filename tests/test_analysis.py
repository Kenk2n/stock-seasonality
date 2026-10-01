import numpy as np
import pandas as pd
import pytest

from seasonality import analysis, data, scanner, universe
from seasonality.synthetic import demo_universe, make_prices


def test_monthly_returns_values():
    idx = pd.to_datetime(["2020-01-31", "2020-02-28", "2020-03-31"])
    close = pd.Series([100.0, 110.0, 99.0], index=idx)
    r = analysis.monthly_returns(close)
    assert list(r.index.astype(str)) == ["2020-02", "2020-03"]
    assert r.iloc[0] == pytest.approx(0.10)
    assert r.iloc[1] == pytest.approx(-0.10)


def test_monthly_returns_drops_incomplete_last_month():
    idx = pd.to_datetime(["2020-01-31", "2020-02-28", "2020-03-10"])
    r = analysis.monthly_returns(pd.Series([100.0, 110.0, 120.0], index=idx))
    assert list(r.index.astype(str)) == ["2020-02"]


def test_year_month_table_shape():
    r = analysis.monthly_returns(make_prices("2015-01-01", "2019-12-31"))
    t = analysis.year_month_table(r)
    assert list(t.columns) == list(range(1, 13))
    assert list(t.index) == [2015, 2016, 2017, 2018, 2019]
    assert np.isnan(t.loc[2015, 1])  # 첫 달은 이전 달이 없어서 수익률 없음


def test_detects_planted_november_effect():
    s = analysis.find_patterns(make_prices(monthly_effect={11: 0.05}, seed=1))
    assert s.loc[11, "significant"]
    assert s.loc[11, "direction"] == "상승"
    assert s.loc[11, "mean"] > 0.03
    assert s["score"].idxmax() == 11


@pytest.mark.parametrize("month,effect,direction", [(9, -0.05, "하락"), (11, 0.05, "상승")])
def test_detection_rate_across_seeds(month, effect, direction):
    found = 0
    for seed in range(20):
        s = analysis.find_patterns(make_prices(monthly_effect={month: effect}, seed=seed))
        found += bool(s.loc[month, "significant"]) and s.loc[month, "direction"] == direction
    assert found >= 15  # 월 5% 효과는 대부분 잡아내야 함


def test_uptrend_alone_is_not_seasonality():
    # 꾸준히 오르기만 하는 종목은 모든 달이 플러스지만 계절성은 아니다
    hits = sum(analysis.find_patterns(make_prices(drift=0.002, seed=s))["significant"].sum() for s in range(10))
    assert hits <= 3


def test_noise_has_few_false_positives():
    hits = sum(analysis.find_patterns(make_prices(seed=s))["significant"].sum() for s in range(10))
    assert hits <= 6  # 120번 검정 중 우연한 발견은 소수여야 함


def test_lookback_limits_sample():
    s = analysis.find_patterns(make_prices(), lookback_years=5)
    assert s["n"].max() <= 5
    assert not s["significant"].any()  # min_years=8 미만이면 판정 안 함


def test_excess_returns_removes_market_move():
    market = make_prices(seed=5)
    r = analysis.excess_returns(analysis.monthly_returns(market), analysis.monthly_returns(market))
    assert np.allclose(r, 0)


def test_yearly_paths_start_at_100():
    paths = analysis.yearly_paths(make_prices("2018-01-01", "2020-12-31"))
    first = paths.apply(lambda c: c.dropna().iloc[0])
    assert np.allclose(first, 100)
    assert len(analysis.average_path(paths).dropna()) > 300


def test_benjamini_hochberg_monotone_and_bounded():
    p = pd.Series([0.01, 0.04, 0.03, 0.5, np.nan])
    q = scanner.benjamini_hochberg(p)
    assert np.isnan(q.iloc[4])
    assert (q.dropna() >= p.dropna()).all() and (q.dropna() <= 1).all()
    assert q.iloc[0] == pytest.approx(0.04)


def test_scan_ranks_planted_patterns_first():
    result = scanner.scan(demo_universe())
    top = scanner.top_patterns(result, top=10)
    found = set(zip(top["ticker"], top["month_name"]))
    assert ("DEMO_NOV", "11월") in found
    assert ("DEMO_SEP", "9월") in found
    assert ("DEMO_APR", "4월") in found
    assert top.iloc[0]["ticker"].startswith("DEMO")


def test_cache_merge_detects_adjustment():
    a = make_prices("2020-01-01", "2020-03-31")
    b = a.loc["2020-03-20":].copy()
    assert data._merge_update(a, b) is not None
    b["Close"] *= 0.9  # 배당/분할로 과거 수정주가가 바뀐 경우
    assert data._merge_update(a, b) is None


def test_update_many_uses_cache_without_network(tmp_path, monkeypatch):
    df = make_prices("2020-01-01", "2020-12-31")
    data.write_cache("AAA", df, tmp_path)
    monkeypatch.setattr(data, "_download", lambda *a, **k: pytest.fail("network called"))
    out = data.update_many(["aaa"], cache_dir=tmp_path)
    pd.testing.assert_frame_equal(out["AAA"], df, check_freq=False)


def test_parse_nasdaq_listed():
    text = (
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
        "AAPL|Apple Inc.|Q|N|N|100|N|N\n"
        "QQQ|Invesco QQQ|G|N|N|100|Y|N\n"
        "ZZZT|Test|Q|Y|N|100|N|N\n"
        "ABCDW|Warrant|Q|N|N|100|N|N\n"
        "File Creation Time: 0101202600:00|||||||\n"
    )
    syms = universe.parse_nasdaq_listed(text)
    assert "AAPL" in syms and "QQQ" not in syms and "ZZZT" not in syms
    assert "QQQ" in universe.parse_nasdaq_listed(text, include_etf=True)
