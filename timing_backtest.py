"""타이밍 점수 과거 검증: 과거 날짜마다 그날까지의 데이터로 후보와 타이밍 점수를 다시 계산하고 이후 수익률과 비교.

  python timing_backtest.py                     # 2023.10 ~ 최근, 2개월 간격
  python timing_backtest.py --step 1            # 매달

박스 하단권: 그날 기준 쿠라 스시형 박스(3·4·5년) + 박스 위치 −30%~35% → box_timing
매집 흔적:   그날 기준 매집 점수 상위 40 → accum_timing
비교: 타이밍 상위 절반 vs 하위 절반의 이후 1·3개월 수익률(S&P 500 대비), 순위 상관(IC)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import accumulation as acc  # noqa: E402
from seasonality import data, indicators, picks, timing, universe  # noqa: E402
from seasonality import swingbox as sb  # noqa: E402


def fwd(c: pd.Series, d: pd.Timestamp, n: int) -> float:
    i = c.index.searchsorted(d, side="right") - 1
    return float(c.iloc[i + n] / c.iloc[i] - 1) if 0 <= i and i + n < len(c) else np.nan


def summarize(df: pd.DataFrame, label: str) -> str:
    out = [f"\n== {label}: {df['date'].nunique()}개 시점, 표본 {len(df)}개"]
    for h in ("x21", "x63"):
        d = df.dropna(subset=[h])
        top = d[d["half"] == "상위"].groupby("date")[h].mean()
        bot = d[d["half"] == "하위"].groupby("date")[h].mean()
        diff = (top - bot).dropna()
        t = diff.mean() / diff.std() * np.sqrt(len(diff)) if len(diff) > 2 and diff.std() > 0 else np.nan
        ic = d.groupby("date").apply(lambda g: g["timing"].corr(g[h], method="spearman") if len(g) > 4 else np.nan).dropna()
        hi = d[d["timing"] >= 60][h]
        out.append(f"  {'1개월' if h == 'x21' else '3개월'} 후 S&P 500 대비: 전체 {d[h].mean() * 100:+.2f}% · 타이밍 상위 절반 {top.mean() * 100:+.2f}% "
                   f"· 하위 절반 {bot.mean() * 100:+.2f}% · 차이 {diff.mean() * 100:+.2f}%p (t={t:+.2f}, 이긴 시점 {(diff > 0).mean():.0%}) "
                   f"· IC {ic.mean():+.3f} · 타이밍 60↑ {hi.mean() * 100:+.2f}% ({len(hi)}개)")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-10-01")
    p.add_argument("--step", type=int, default=2, help="몇 달 간격")
    p.add_argument("--out", default="results/timing_backtest.csv")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    meta = universe.screen_universe(1e7, 2e5, 0.5, "us").set_index("ticker")
    prices = data.update_ohlc(sorted(meta.index))
    prices = {t: df for t, df in prices.items() if len(df) > 260}
    spy = data.update_ohlc(["SPY"])["SPY"]["Close"]
    big = set(meta.index[(meta["market_cap"] >= 1.5e8) & (meta["dollar_volume"] >= 2e6) & (meta["price"] >= 3)])
    last = spy.index[-64]
    ref = spy.index
    months = pd.period_range(pd.Timestamp(args.start).to_period("M"), last.to_period("M"), freq="M")[:: args.step]
    dates = [ref[ref.to_period("M") == m][-1] for m in months if (ref.to_period("M") == m).any()]
    print(f"종목 {len(prices)}개, 시점 {len(dates)}개 ({dates[0]:%Y-%m} ~ {dates[-1]:%Y-%m})", flush=True)

    inds = {}

    def ind_of(t):
        if t not in inds:
            inds[t] = indicators.compute(prices[t])
        return inds[t]

    sig = acc.panel(prices)
    rows = []
    for d in dates:
        # 박스 하단권
        box_px = {t: df[df.index <= d] for t, df in prices.items() if t in big and (df.index <= d).sum() > 500}
        box = sb.scan(box_px, d)
        box = box[box["pos"].between(-0.3, 0.35)] if len(box) else box
        snap = acc.snapshot({t: sig[t] for t in box.index if t in sig}, d, 3, 2e6) if len(box) else pd.DataFrame()
        pinned = picks.pinned_tickers(snap) if len(snap) else set()
        sp21, sp63 = fwd(spy, d, 21), fwd(spy, d, 63)
        for t, r in box.iterrows():
            if t in pinned:
                continue
            df = prices[t]
            m = df.index <= d
            pl = picks.plan(r)
            tm = timing.box_timing(df[m], ind_of(t)[m], r["pos"], pl["stop"], pl["target"])
            rows.append({"date": d, "list": "box", "ticker": t, "timing": tm["timing"],
                         "x21": fwd(df["Close"], d, 21) - sp21, "x63": fwd(df["Close"], d, 63) - sp63})
        # 매집 흔적
        s = acc.snapshot(sig, d, 0.5, 2e5)
        s = s[~s.index.isin(picks.pinned_tickers(s))].sort_values("score", ascending=False).head(40)
        n = len(s)
        for k, t in enumerate(s.index):
            df = prices[t]
            m = df.index <= d
            tm = timing.accum_timing(df[m], ind_of(t)[m], (n - 1 - k) / max(n - 1, 1))
            rows.append({"date": d, "list": "accum", "ticker": t, "timing": tm["timing"],
                         "x21": fwd(df["Close"], d, 21) - sp21, "x63": fwd(df["Close"], d, 63) - sp63})
        print(f"  {d:%Y-%m-%d}: 박스 하단 {len(box)}개, 매집 {n}개", flush=True)

    res = pd.DataFrame(rows)
    for c in ("x21", "x63"):  # 극단값(상장폐지 직전 등) 영향 줄이기
        res[c] = res[c].clip(-0.9, 2.0)
    res["half"] = res.groupby(["date", "list"])["timing"].transform(lambda s: np.where(s >= s.median(), "상위", "하위"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(args.out, index=False)
    print(summarize(res[res["list"] == "box"], "박스 하단권 타이밍"))
    print(summarize(res[res["list"] == "accum"], "매집 흔적 타이밍"))
    print(f"\n결과: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
