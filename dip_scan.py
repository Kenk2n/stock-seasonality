"""밴드 하단 후보: 폭 10~50% 밴드 안에서 위아래로 오가는 우량 대형주 중 지금 밴드 하단 근처인 종목.

  python dip_scan.py                 # 밴드 안 + 200일선 위 (기본)
  python dip_scan.py --loose         # 하단을 깬 종목·200일선 아래도
  python dip_scan.py --backtest      # 과거 검증
  python dip_scan.py --from-high     # 52주 고점 대비 하락 + 실적 점수 (예전 방식, 아래 조건)

--from-high 조건(모두 만족):
  - 미국 상장 보통주, 시총 ≥ $100억, 3개월 평균 거래대금 ≥ $5천만, 주가 ≥ $10
  - 52주 고점 대비 −18% ~ −55% (덜 빠진 건 저점이 아니고, 더 빠진 건 떨어지는 칼일 가능성)
  - 5년 수익률 > 0 (장기 우상향 종목의 눌림만)
  - 흑자(이익률 > 0), 예상 EPS > 0, 매출 성장률 > −10%
점수(합 100):
  하락 폭 25 · 장기 추세 15 · 실적·재무 25 · 밸류·애널리스트 목표가 여력 20 · 바닥 다지기(RSI 회복, 20일 저점 대비 반등) 15
  python dip_scan.py --from-high --backtest   # 같은 가격 조건이 과거에 3·6개월 뒤 어땠는지
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import data, earnings, patterns, swingbox, universe  # noqa: E402

INFO_DIR = Path("data/info")
INFO_FIELDS = [
    "sector", "industry", "forwardPE", "trailingPE", "pegRatio", "priceToSalesTrailing12Months",
    "revenueGrowth", "earningsGrowth", "profitMargins", "operatingMargins", "freeCashflow",
    "debtToEquity", "forwardEps", "targetMeanPrice", "recommendationMean", "numberOfAnalystOpinions",
]


def price_features(c: pd.Series) -> dict | None:
    """종가 시계열 끝 시점의 하락·추세·바닥 지표."""
    if len(c) < 1000:
        return None
    last = c.iloc[-1]
    hi = c.iloc[-252:].max()
    low20 = c.iloc[-20:].min()
    ma200 = c.iloc[-200:].mean()
    r = patterns.rsi(c.iloc[-300:]).iloc[-1]
    ago = lambda n: c.iloc[-n] if len(c) > n else np.nan  # noqa: E731
    return {
        "price": last,
        "dd": last / hi - 1,
        "days_from_high": 251 - int(np.argmax(c.iloc[-252:].to_numpy())),
        "ret_3m": last / ago(63) - 1,
        "ret_3y": last / ago(756) - 1,
        "ret_5y": last / ago(1250) - 1,
        "vs_ma200": last / ma200 - 1,
        "rsi": r,
        "off_low20": last / low20 - 1,
        "new_low5": bool(c.iloc[-5:].min() <= low20),
        "vol": c.pct_change().iloc[-252:].std() * np.sqrt(252),
    }


def passes_price(f: dict | None) -> bool:
    return f is not None and -0.55 <= f["dd"] <= -0.18 and f["ret_5y"] > 0


def load_info(t: str, max_age_h: float = 20) -> dict:
    path = INFO_DIR / f"{t}.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return json.loads(path.read_text())
    import yfinance as yf

    try:
        raw = yf.Ticker(t).info or {}
    except Exception:
        raw = {}
    info = {k: raw.get(k) for k in INFO_FIELDS}
    INFO_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info))
    return info


def passes_fundamentals(i: dict) -> bool:
    pm, eps, rg = i.get("profitMargins"), i.get("forwardEps"), i.get("revenueGrowth")
    return (pm is not None and pm > 0) and (eps is not None and eps > 0) and (rg is None or rg > -0.10)


def score(f: dict, i: dict) -> dict:
    s = {}
    # 하락 폭: −18% → 0, −35% → 만점, −45% 넘으면 조금씩 깎음
    d = -f["dd"]
    s["dip"] = 25 * min(1.0, max(0.0, (d - 0.18) / 0.17)) - 25 * max(0.0, d - 0.45) / 0.10 * 0.4
    # 장기 추세: 5년 수익률(100%면 만점) + 3년 전보다 위
    s["trend"] = 10 * min(1.0, f["ret_5y"] / 1.0) + (5 if f["ret_3y"] > 0 else 0)
    # 실적·재무
    q = 0.0
    q += 7 * min(1.0, max(0.0, (i.get("profitMargins") or 0) / 0.20))
    q += 6 if (i.get("freeCashflow") or 0) > 0 else 0
    q += 7 * min(1.0, max(0.0, ((i.get("revenueGrowth") or 0) + 0.02) / 0.22))
    de = i.get("debtToEquity")
    q += 5 if de is None or de < 100 else (2 if de < 200 else 0)
    s["quality"] = q
    # 밸류: 예상 PER (15 이하 만점, 45 이상 0) + 애널리스트 목표가 여력 (40% 이상 만점)
    fpe = i.get("forwardPE")
    v = 10 * min(1.0, max(0.0, (45 - fpe) / 30)) if fpe and fpe > 0 else 0
    tgt = i.get("targetMeanPrice")
    upside = tgt / f["price"] - 1 if tgt else np.nan
    v += 10 * min(1.0, max(0.0, upside / 0.40)) if upside == upside else 0
    s["value"] = v
    # 바닥 다지기: RSI 35~55 회복 구간, 20일 저점 대비 반등, 최근 5일 신저가 없음
    b = 0.0
    b += 6 if 35 <= f["rsi"] <= 55 else (3 if 28 <= f["rsi"] < 35 else 0)
    b += 5 * min(1.0, f["off_low20"] / 0.06)
    b += 4 if not f["new_low5"] else 0
    s["base"] = b
    s["total"] = sum(s.values())
    s["upside"] = upside
    return s


def backtest(prices: dict[str, pd.Series], spy: pd.Series, step: int = 21, horizons=(63, 126)) -> pd.DataFrame:
    """월마다 그날까지의 데이터로 가격 조건(하락 폭 + 5년 추세)을 다시 적용하고 이후 수익률을 같은 날 전체 평균과 비교.
    재무 조건은 과거 시점 값이 없어 빼고 본다. 지금 대형주만 대상이라 생존 편향이 있다."""
    idx = spy.index
    rows = []
    for k in range(1260, len(idx) - max(horizons), step):
        t = idx[k]
        spy_dd = spy.iloc[k] / spy.iloc[k - 251 : k + 1].max() - 1
        for tk, c in prices.items():
            c = c.loc[:t]
            if len(c) < 1260 or c.index[-1] != t:
                continue
            f = price_features(c)
            if f is None:
                continue
            full = prices[tk]
            pos = full.index.get_loc(t)
            row = {"date": t, "ticker": tk, "dip": passes_price(f), "dd": f["dd"], "spy_dd": spy_dd,
                   "base": 35 <= f["rsi"] <= 55 and not f["new_low5"]}
            for h in horizons:
                row[f"fwd_{h}"] = full.iloc[pos + h] / full.iloc[pos] - 1
            rows.append(row)
    df = pd.DataFrame(rows)
    for h in horizons:
        df[f"ex_{h}"] = df[f"fwd_{h}"] - df.groupby("date")[f"fwd_{h}"].transform("mean")
    return df


def summarize_backtest(df: pd.DataFrame, horizons=(63, 126)) -> None:
    def line(name, sub):
        parts = [f"{name:<28} {len(sub):>6}건"]
        for h in horizons:
            by_date = sub.groupby("date")[f"ex_{h}"].mean()
            t = by_date.mean() / (by_date.std(ddof=1) / np.sqrt(len(by_date))) if len(by_date) > 2 and len(sub) < len(df) else np.nan
            parts.append(f"{h // 21}개월 뒤 평균 {sub[f'fwd_{h}'].mean():+6.1%} · 상승 {(sub[f'fwd_{h}'] > 0).mean():4.0%}"
                         f" · 같은 날 평균 대비 {sub[f'ex_{h}'].mean():+5.1%}p (t≈{t:.1f})")
        print("  ".join(parts))

    print(f"\n검증 {df['date'].min():%Y-%m} ~ {df['date'].max():%Y-%m}, {df['date'].nunique()}개 시점")
    line("전체", df)
    d = df[df["dip"]]
    line("저점 조건 (−18~−55%, 5년↑)", d)
    line("  + 바닥 다지기", d[d["base"]])
    line("  시장(SPY) −5% 이내일 때", d[d["spy_dd"] > -0.05])
    line("  시장(SPY) −10% 넘게 빠졌을 때", d[d["spy_dd"] <= -0.10])
    line("−55% 넘게 빠진 종목 (제외 대상)", df[df["dd"] < -0.55])


# ── 밴드 하단 (기본) ─────────────────────────────────────────────
# 폭 10~50% 밴드 안에서 위아래로 3번 이상 오간 종목 중 지금 밴드 하단 근처인 것.
# 밴드 상단 = 밴드 안 고점들의 중앙값, 하단 = 저점들의 중앙값 (swingbox 와 같은 방법, 되돌림 기준만 작게)
BAND_YEARS = (1, 2, 3)
BAND_THRESHOLDS = (0.06, 0.09, 0.13, 0.18)


def find_band(close: pd.Series, end: pd.Timestamp, min_amp: float = 1.10, max_amp: float = 1.50,
              min_trips: float = 3.0, years=BAND_YEARS, thresholds=BAND_THRESHOLDS) -> dict | None:
    """기간·되돌림 기준 조합 중 조건을 통과한 가장 뚜렷한 밴드."""
    best = None
    for y in years:
        for th in thresholds:
            m = swingbox.swing_box(close, end - pd.DateOffset(years=y), end, th, min_ratio=1.08)
            # 되돌림 기준이 밴드 폭보다 너무 크면 밴드 안 출렁임을 못 잡고, 너무 작으면 잔물결까지 센다
            if m is None or not (min_amp <= m["amp"] <= max_amp) or not (0.35 <= th / (m["amp"] - 1) <= 0.9):
                continue
            if not swingbox.passes(m, min_amp=min_amp, min_trips=min_trips, max_months_since_swing=6):
                continue
            sc = swingbox.score(m)
            if best is None or sc > best["band_score"]:
                best = {"band_years": y, "threshold": th, "band_score": sc, **m}
    return best


def near_bottom(m: dict | None, pos_min: float = -0.3, pos_max: float = 0.35) -> bool:
    return m is not None and pos_min <= m["pos"] <= pos_max


def band_backtest(closes: dict[str, pd.Series], spy: pd.Series, step: int = 42, horizons=(21, 63)) -> pd.DataFrame:
    """두 달마다 그날까지의 데이터로 밴드를 다시 찾고, 밴드 하단 종목의 이후 수익률을 같은 날 전체 평균과 비교."""
    from concurrent.futures import ProcessPoolExecutor

    idx = spy.index
    dates = [idx[k] for k in range(756, len(idx) - max(horizons), step)]
    jobs = [(t, c, dates, horizons) for t, c in closes.items()]
    with ProcessPoolExecutor() as ex:
        rows = [r for part in ex.map(_band_rows, jobs, chunksize=8) for r in part]
    df = pd.DataFrame(rows)
    for h in horizons:
        df[f"ex_{h}"] = df[f"fwd_{h}"] - df.groupby("date")[f"fwd_{h}"].transform("mean")
    return df


def _band_rows(job) -> list[dict]:
    t, c, dates, horizons = job
    out = []
    for d in dates:
        if d not in c.index:
            continue
        pos = c.index.get_loc(d)
        if pos < 756 or pos + max(horizons) >= len(c):
            continue
        m = find_band(c.iloc[: pos + 1], d, thresholds=(0.07, 0.11, 0.16))
        row = {"date": d, "ticker": t, "band": m is not None, "bottom": near_bottom(m),
               "pos": m["pos"] if m else np.nan, "amp": m["amp"] if m else np.nan,
               "trips": m["round_trips"] if m else np.nan, "band_score": m["band_score"] if m else np.nan,
               "rsi": float(patterns.rsi(c.iloc[pos - 299 : pos + 1]).iloc[-1]),
               "ma200": float(c.iloc[pos] / c.iloc[pos - 199 : pos + 1].mean() - 1)}
        for h in horizons:
            row[f"fwd_{h}"] = c.iloc[pos + h] / c.iloc[pos] - 1
        if m is not None:
            row[f"hit_top_{max(horizons)}"] = bool(c.iloc[pos + 1 : pos + max(horizons) + 1].max() >= m["hm"])
            row[f"break_{max(horizons)}"] = bool(c.iloc[pos + 1 : pos + max(horizons) + 1].min() < m["low_zone"][0] * 0.97)
        out.append(row)
    return out


def summarize_band_backtest(df: pd.DataFrame, horizons=(21, 63)) -> None:
    h = max(horizons)

    def line(name, sub):
        parts = [f"{name:<26} {len(sub):>6}건"]
        for k in horizons:
            by_date = sub.groupby("date")[f"ex_{k}"].mean()
            t = by_date.mean() / (by_date.std(ddof=1) / np.sqrt(len(by_date))) if len(by_date) > 2 and len(sub) < len(df) else np.nan
            parts.append(f"{k // 21}개월 뒤 {sub[f'fwd_{k}'].mean():+5.1%} · 상승 {(sub[f'fwd_{k}'] > 0).mean():3.0%}"
                         f" · 평균 대비 {sub[f'ex_{k}'].mean():+5.1%}p (t≈{t:.1f})")
        if f"hit_top_{h}" in sub and sub[f"hit_top_{h}"].notna().any():
            parts.append(f"{h // 21}개월 안에 밴드 상단 도달 {sub[f'hit_top_{h}'].mean():3.0%} · 하단 이탈 {sub[f'break_{h}'].mean():3.0%}")
        print("  ".join(parts))

    print(f"\n검증 {df['date'].min():%Y-%m} ~ {df['date'].max():%Y-%m}, {df['date'].nunique()}개 시점")
    line("전체", df)
    b = df[df["band"]]
    line("밴드 종목 (폭 10~50%)", b)
    bb = b[b["bottom"]]
    line("  밴드 하단 근처", bb)
    line("    밴드 안 (pos 0~35%)", bb[bb["pos"] >= 0])
    line("    하단 아래 (pos −30~0%)", bb[bb["pos"] < 0])
    line("    왕복 4회 이상", bb[bb["trips"] >= 4])
    line("    RSI 40 이상 (반등 시작)", bb[bb["rsi"] >= 40])
    line("    200일선 위", bb[bb["ma200"] > 0])
    line("    밴드 안 + 200일선 위", bb[(bb["pos"] >= 0) & (bb["ma200"] > 0)])
    line("  밴드 중간~상단", b[~b["bottom"]])


def run_band(args, meta: pd.DataFrame, closes: dict[str, pd.Series], spy: pd.Series) -> int:
    if args.backtest:
        bt = band_backtest(closes, spy)
        Path("reports").mkdir(exist_ok=True)
        bt.to_csv("reports/band_backtest.csv", index=False)
        summarize_band_backtest(bt)
        return 0
    end = spy.index[-1]
    amp = (1 + args.min_band, 1 + args.max_band)
    bands = {t: find_band(c, end, *amp) for t, c in closes.items()}
    bands = {t: m for t, m in bands.items() if m is not None}
    cands = [t for t, m in bands.items() if near_bottom(m)]
    print(f"밴드 종목 (폭 {args.min_band:.0%}~{args.max_band:.0%}) {len(bands)}개 · 그중 지금 밴드 하단 근처 {len(cands)}개")
    if not args.loose:  # 검증에서 그나마 나았던 조합: 밴드 안(하단을 깨지 않음) + 200일선 위
        cands = [t for t in cands if bands[t]["pos"] >= 0 and closes[t].iloc[-1] > closes[t].iloc[-200:].mean()]
        print(f"  밴드 안 + 200일선 위 → {len(cands)}개")
    with ThreadPoolExecutor(8) as ex:
        infos = dict(zip(cands, ex.map(load_info, cands)))

    rows = []
    for t in cands:
        m, i, c = bands[t], infos[t], closes[t]
        stop = m["low_zone"][0] * 0.97
        nxt = earnings.next_earnings(earnings.load_earnings_dates(t))
        rows.append({
            "ticker": t, "name": meta.at[t, "name"], "sector": i.get("sector"), "mcap_b": meta.at[t, "market_cap"] / 1e9,
            "price": m["price"], "band_low": m["lm"], "band_top": m["hm"],
            "band_width": m["amp"] - 1, "pos": m["pos"], "to_top": m["hm"] / m["price"] - 1,
            "stop": stop, "to_stop": stop / m["price"] - 1,
            "trips": m["round_trips"], "band_years": m["band_years"], "n_top": m["n_high"], "n_low": m["n_low"],
            "vs_ma200": c.iloc[-1] / c.iloc[-200:].mean() - 1,
            "rsi": patterns.rsi(c.iloc[-300:]).iloc[-1], "fwd_pe": i.get("forwardPE"), "margin": i.get("profitMargins"),
            "next_earnings": nxt.strftime("%Y-%m-%d") if nxt is not None else "", "band_score": m["band_score"],
        })
    df = pd.DataFrame(rows)
    # 정렬: 밴드가 뚜렷하고(왕복 많음) 하단에 가까울수록 위
    df["rank"] = 50 * df["band_score"].rank(pct=True) + 50 * (1 - df["pos"].clip(-0.3, 0.35).add(0.3) / 0.65)
    df["rr"] = df["to_top"] / -df["to_stop"]
    df = df.sort_values("rank", ascending=False)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    show = df.head(args.top).copy()
    for col in ["band_width", "to_top", "to_stop", "margin", "vs_ma200"]:
        show[col] = show[col].map(lambda x: f"{x:+.0%}" if x == x and x is not None else "")
    for col in ["price", "band_low", "band_top", "stop"]:
        show[col] = show[col].round(2)
    show["pos"] = show["pos"].map(lambda x: f"{x:.0%}")
    show["fwd_pe"] = show["fwd_pe"].map(lambda x: f"{x:.0f}" if x else "")
    show[["rsi", "mcap_b"]] = show[["rsi", "mcap_b"]].round(0)
    show["rr"] = show["rr"].round(1)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(show[["ticker", "name", "sector", "mcap_b", "price", "band_low", "band_top", "band_width", "pos", "to_top",
                "stop", "to_stop", "rr", "trips", "band_years", "vs_ma200", "rsi", "fwd_pe", "next_earnings"]].to_string(index=False))
    print(f"\npos = 밴드 안 위치 (0% 하단, 100% 상단) · to_top = 밴드 상단까지 · stop = 밴드 최저 저점 × 0.97 · 전체 → {args.out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="하락장 저점 매수 후보")
    p.add_argument("--min-mcap", type=float, default=1e10)
    p.add_argument("--min-dollar-volume", type=float, default=5e7)
    p.add_argument("--min-price", type=float, default=10.0)
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--from-high", action="store_true", help="밴드 대신 52주 고점 대비 하락 폭으로 고르기")
    p.add_argument("--min-band", type=float, default=0.10, help="밴드 최소 폭 (하단 대비 상단, 0.10 = 10%%)")
    p.add_argument("--max-band", type=float, default=0.50)
    p.add_argument("--loose", action="store_true", help="밴드 하단 아래로 깨진 종목·200일선 아래 종목도 보기")
    p.add_argument("--out", default="reports/dip_picks.csv")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    meta = universe.screen_universe(args.min_mcap, args.min_dollar_volume, args.min_price, "us").set_index("ticker")
    tickers = sorted(set(meta.index) | {"SPY"})
    period, cache = ("10y", Path("data/ohlc10")) if args.backtest else ("5y", data.OHLC_CACHE_DIR)
    ohlc = data.update_ohlc(tickers, period=period, refresh=args.refresh, cache_dir=cache)
    closes = {t: df["Close"] for t, df in ohlc.items() if len(df) > 1000}
    spy = closes.pop("SPY")
    print(f"대상 {len(closes)}개 · 기준일 {spy.index[-1]:%Y-%m-%d} · SPY 52주 고점 대비 {spy.iloc[-1] / spy.iloc[-252:].max() - 1:+.1%}")

    if not args.from_high:
        return run_band(args, meta, closes, spy)
    if args.backtest:
        summarize_backtest(backtest(closes, spy))
        return 0

    feats = {t: price_features(c) for t, c in closes.items()}
    cands = [t for t, f in feats.items() if passes_price(f)]
    print(f"가격 조건 통과 {len(cands)}개 → 재무 확인")
    with ThreadPoolExecutor(8) as ex:
        infos = dict(zip(cands, ex.map(load_info, cands)))

    rows = []
    for t in cands:
        i = infos[t]
        if not passes_fundamentals(i):
            continue
        f, s = feats[t], score(feats[t], infos[t])
        nxt = earnings.next_earnings(earnings.load_earnings_dates(t))
        rows.append({
            "ticker": t, "name": meta.at[t, "name"], "sector": i.get("sector"), "mcap_b": meta.at[t, "market_cap"] / 1e9,
            "price": f["price"], "dd_52w": f["dd"], "ret_3m": f["ret_3m"], "ret_5y": f["ret_5y"],
            "vs_ma200": f["vs_ma200"], "rsi": f["rsi"], "off_low20": f["off_low20"],
            "fwd_pe": i.get("forwardPE"), "rev_growth": i.get("revenueGrowth"), "margin": i.get("profitMargins"),
            "upside": s["upside"], "rec": i.get("recommendationMean"),
            "next_earnings": nxt.strftime("%Y-%m-%d") if nxt is not None else "",
            **{f"s_{k}": round(v, 1) for k, v in s.items() if k not in ("upside",)},
        })
    df = pd.DataFrame(rows).sort_values("s_total", ascending=False)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    show = df.head(args.top).copy()
    for col in ["dd_52w", "ret_3m", "ret_5y", "vs_ma200", "off_low20", "rev_growth", "margin", "upside"]:
        show[col] = show[col].map(lambda x: f"{x:+.0%}" if x == x and x is not None else "")
    show["fwd_pe"] = show["fwd_pe"].map(lambda x: f"{x:.0f}" if x else "")
    show["rsi"] = show["rsi"].round(0)
    show["mcap_b"] = show["mcap_b"].round(0)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(show[["ticker", "name", "sector", "mcap_b", "price", "dd_52w", "ret_3m", "ret_5y", "rsi", "off_low20",
                "fwd_pe", "rev_growth", "margin", "upside", "next_earnings", "s_total"]].to_string(index=False))
    print(f"\n재무 조건 통과 {len(df)}개 · 전체 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
