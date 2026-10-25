import numpy as np
import pandas as pd

from seasonality import similar as sim

DROP = np.log(np.linspace(1.0, 0.85, 16))   # 16일 동안 15% 하락하는 모양
JUMP = np.log(np.linspace(1.0, 1.12, 11))[1:]  # 그 뒤 10일 동안 12% 상승


def _series(seed=0, n_tk=30, n=600, plant=range(20), query_tk=25):
    rng = np.random.default_rng(seed)
    prices = {}
    for i in range(n_tk):
        r = rng.normal(0, 0.01, n)
        lc = np.cumsum(r) + np.log(50)
        if i in plant:  # 과거 여러 곳에 '하락 뒤 상승' 심기
            for p in range(60, n - 40, 120):
                lc[p:p + 16] = lc[p - 1] + DROP + rng.normal(0, 0.002, 16)
                lc[p + 16:p + 26] = lc[p + 15] + JUMP + rng.normal(0, 0.002, 10)
                lc[p + 26:] += lc[p + 25] - lc[p + 26]
        if i == query_tk:  # 지금(마지막 16일)이 하락 모양
            lc[-16:] = lc[-17] + DROP + rng.normal(0, 0.002, 16)
        idx = pd.bdate_range("2020-01-01", periods=n)
        prices[f"T{i:02d}"] = pd.DataFrame({"Close": np.exp(lc)}, index=idx)
    return sim.Series.from_prices(prices)


def test_planted_pattern_is_found_and_scores_high():
    s = _series()
    lib = sim.build(s, 16)
    q = s.tickers.index("T25")
    [st] = sim.search(lib, s, [(q, len(s.logc[q]) - 1)])
    # 출렁임 크기가 비슷한 차트만 인정하므로 심어 둔 모양(20종목 × 최대 2개) 위주로 뽑힌다
    assert 40 <= st["n"] <= sim.K
    assert st["rise"] > 0.8 and st["avg"] > 0.05
    assert sim.score(st, lib.base()) > 7
    picked_tk = {s.tickers[lib.tick[r]] for r in st["rows"]}
    assert len(picked_tk & {f"T{i:02d}" for i in range(20)}) >= 15


def test_rules_self_overlap_per_ticker_and_until_day():
    s = _series()
    lib = sim.build(s, 16)
    q = s.tickers.index("T03")
    e = len(s.logc[q]) - 1
    [st] = sim.search(lib, s, [(q, e)])
    rows = st["rows"]
    own = rows[lib.tick[rows] == q]
    assert (lib.end[own] <= e - 16).all()  # 지금 구간과 겹치는 자기 차트 없음
    for t in set(lib.tick[rows]):
        ends = np.sort(lib.end[rows][lib.tick[rows] == t])
        assert len(ends) <= sim.PER_TICKER and (np.diff(ends) >= 16).all()
    # until_day: 이후 10일이 그날까지 다 알려진 차트만
    cut = int(s.days[0][300])
    lib2 = sim.build(s, 16, until_day=cut)
    for r in range(0, len(lib2.fut), 97):
        t, en = lib2.tick[r], lib2.end[r]
        assert s.days[t][en + sim.HORIZON] <= cut


def test_path_and_score_bounds():
    s = _series()
    q = s.tickers.index("T25")
    p = sim.path(s, q, len(s.logc[q]) - 1 - sim.HORIZON, 64)
    assert p["w"][-1] == 1.0 and len(p["f"]) == sim.HORIZON and p["x"][0] == -63 and p["x"][-1] == 0
    base = {"rise": 0.5, "avg": 0.0, "sd": 0.05}
    assert sim.score({"n": 50, "rise": 1.0, "avg": 0.5}, base) == 10
    assert sim.score({"n": 50, "rise": 0.0, "avg": -0.5}, base) == 0
    assert np.isnan(sim.score({"n": 3}, base))
