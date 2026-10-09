"""비슷한 차트 점수 과거 검증.

과거 날짜마다 그날까지 '이후 10일 변동이 이미 알려진' 과거 차트만으로 비슷한 차트를 찾아 점수를 매기고,
실제 그 뒤 10거래일 등락과 비교한다 (미래 정보 사용 없음).

  python similar_backtest.py                       # 2024.6 ~ 최근, 21거래일 간격, 날짜마다 250종목
  python similar_backtest.py --sample 500 --step 10

결과: 점수 구간별 실제 상승 확률과 평균 등락, 같은 날 표본 평균 대비 초과 등락, 점수와 실제 등락의 순위 상관(IC)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import data, universe  # noqa: E402
from seasonality import similar as sim  # noqa: E402


def report(df: pd.DataFrame, label: str) -> str:
    d = df.dropna(subset=["score", "actual"]).copy()
    d["excess"] = d["actual"] - d.groupby("date")["actual"].transform("mean")
    lines = [f"\n== {label}: {d['date'].nunique()}개 시점, 표본 {len(d)}개, 실제 상승 확률 전체 {(d['actual'] > 0).mean():.1%}"]
    for name, m in [("점수 6 이상", d["score"] >= 6), ("점수 4~6", d["score"].between(4, 6, inclusive="neither")),
                    ("점수 4 이하", d["score"] <= 4)]:
        x = d[m]
        if len(x):
            lines.append(f"  {name}: {len(x)}개 · 10일 뒤 상승 확률 {(x['actual'] > 0).mean():.1%} · 평균 {x['actual'].mean() * 100:+.2f}% "
                         f"· 같은 날 평균 대비 {x['excess'].mean() * 100:+.2f}%p")
    ic = d.groupby("date").apply(lambda g: g["score"].corr(g["actual"], method="spearman") if len(g) > 20 else np.nan,
                                 include_groups=False).dropna()
    t = ic.mean() / ic.std() * np.sqrt(len(ic)) if len(ic) > 2 and ic.std() > 0 else np.nan
    lines.append(f"  IC (점수 순위 vs 실제 등락 순위) 평균 {ic.mean():+.3f} (t={t:+.2f}, 양수인 시점 {(ic > 0).mean():.0%})")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-06-01")
    p.add_argument("--step", type=int, default=21, help="몇 거래일 간격")
    p.add_argument("--sample", type=int, default=250, help="날짜마다 검증할 종목 수")
    p.add_argument("--lengths", default=",".join(map(str, sim.LENGTHS)))
    p.add_argument("--out", default="results/similar_backtest.csv")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    meta = universe.screen_universe(1e7, 2e5, 0.5, "us").set_index("ticker")
    bm = meta[(meta["market_cap"] >= 1.5e8) & (meta["dollar_volume"] >= 2e6) & (meta["price"] >= 3)]
    prices = {t: df for t, df in data.update_ohlc(sorted(bm.index)).items() if len(df) > 300}
    series = sim.Series.from_prices(prices)
    cal = data.update_ohlc(["SPY"])["SPY"].index
    cal = cal[(cal >= pd.Timestamp(args.start))][: -(sim.HORIZON + 1)][:: args.step]
    lengths = [int(v) for v in args.lengths.split(",")]
    rng = np.random.default_rng(0)
    print(f"종목 {len(series.tickers)}개, 시점 {len(cal)}개 ({cal[0]:%Y-%m-%d} ~ {cal[-1]:%Y-%m-%d}), 길이 {lengths}", flush=True)

    rows = []
    for d in cal:
        dn = int(np.datetime64(d.date(), "D").astype(np.int64))
        pos = {}
        for i, dd in enumerate(series.days):
            e = int(np.searchsorted(dd, dn))
            if e < len(dd) and dd[e] == dn and e >= max(lengths) + 5 and e + sim.HORIZON < len(dd):
                pos[i] = e
        pick = rng.choice(sorted(pos), size=min(args.sample, len(pos)), replace=False)
        for L in lengths:
            lib = sim.build(series, L, until_day=dn)
            base = lib.base()
            qs = [(int(i), pos[int(i)]) for i in pick]
            for (i, e), st in zip(qs, sim.search(lib, series, qs)):
                lc = series.logc[i]
                rows.append({"date": d, "L": L, "ticker": series.tickers[i], "score": sim.score(st, base),
                             "rise": st.get("rise"), "avg": st.get("avg"),
                             "actual": float(np.exp(lc[e + sim.HORIZON] - lc[e]) - 1)})
        print(f"  {d:%Y-%m-%d}: {len(pick)}종목", flush=True)

    res = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(args.out, index=False)
    for L in lengths:
        print(report(res[res["L"] == L], f"{L}거래일 차트"))
    comb = res.groupby(["date", "ticker"]).agg(score=("score", "mean"), actual=("actual", "first")).reset_index()
    print(report(comb, "종합 (길이별 점수 평균)"))
    print(f"\n결과: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
