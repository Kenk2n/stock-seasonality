"""테마(관련 종목 묶음) 스캐너.

영상·뉴스에서 나온 투자 테마를 종목 묶음으로 정리하고,
각 종목의 급락 폭·회복 정도·추세·11~12월 계절성을 한 표로 뽑습니다.

예)
  python theme_scan.py                       # 기본 테마: AI 설비투자(빅테크 capex) 수혜
  python theme_scan.py --theme ai_capex --out results/ai_capex.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# 테마 → 세부 그룹 → 종목
THEMES: dict[str, dict[str, list[str]]] = {
    # "AI 거품 꺼진다는데, 빅테크는 왜 돈을 더 쓸까? | 나스닥 급락 뒤 AI 투자의 다음 방향" (백색나무, 2026.11)
    # 빅테크가 쓰는 돈(capex)이 흘러가는 곳 = 수혜 종목
    "ai_capex": {
        "1. 돈을 쓰는 쪽 (하이퍼스케일러)": ["MSFT", "AMZN", "GOOGL", "META", "ORCL"],
        "2. AI 칩·커스텀 ASIC": ["NVDA", "AVGO", "AMD", "MRVL", "TSM"],
        "3. 메모리·저장장치": ["MU", "SNDK", "WDC", "STX"],
        "4. 네트워킹·광통신": ["ANET", "CSCO", "COHR", "LITE", "CIEN", "CRDO", "ALAB"],
        "5. 서버·조립": ["DELL", "SMCI", "CLS"],
        "6. 반도체 장비": ["ASML", "LRCX", "AMAT", "KLAC"],
        "7. 전력·냉각·전기설비": ["VRT", "ETN", "GEV", "PWR", "VST", "CEG", "BE"],
        "8. 네오클라우드 (GPU 임대)": ["CRWV", "NBIS", "IREN"],
        "9. 데이터센터 리츠": ["EQIX", "DLR"],
    },
}


def rsi(c: pd.Series, n: int = 14) -> float:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1])) if dn.iloc[-1] > 0 else 100.0


def ret(c: pd.Series, days: int) -> float:
    return float(c.iloc[-1] / c.iloc[-1 - days] - 1) if len(c) > days else np.nan


def month_stats(c: pd.Series, month: int) -> tuple[float, float, int]:
    """그 달의 평균 수익률, 상승 비율, 표본 연도 수 (최근 10년, 올해 제외 미완성 달)."""
    m = c.resample("ME").last().pct_change().dropna()
    m = m[m.index.month == month]
    m = m[m.index < c.index[-1].normalize() - pd.offsets.MonthBegin(1)]
    m = m.tail(10)
    if m.empty:
        return np.nan, np.nan, 0
    return float(m.mean()), float((m > 0).mean()), len(m)


def analyze(ticker: str, df: pd.DataFrame, bench: pd.Series) -> dict:
    c = df["Close"].dropna()
    last = c.iloc[-1]
    hi52 = c.tail(252).max()
    hi3m = c.tail(63).max()
    lo_since_hi = c.loc[c.tail(63).idxmax():].min()
    ma50, ma200 = c.tail(50).mean(), c.tail(200).mean()
    b = bench.reindex(c.index).ffill()
    rel3m = ret(c, 63) - ret(b, 63)
    nov, nov_hit, n = month_stats(c, 11)
    dec, dec_hit, _ = month_stats(c, 12)
    return {
        "ticker": ticker,
        "price": round(float(last), 2),
        "1w": ret(c, 5),
        "1m": ret(c, 21),
        "3m": ret(c, 63),
        "ytd": float(last / c[c.index.year == c.index[-1].year].iloc[0] - 1),
        "from_3m_high": float(last / hi3m - 1),
        "from_52w_high": float(last / hi52 - 1),
        "bounce_from_low": float(last / lo_since_hi - 1),
        "vs_qqq_3m": rel3m,
        "above_50d": bool(last > ma50),
        "above_200d": bool(last > ma200),
        "rsi14": round(rsi(c), 1),
        "nov_avg": nov,
        "nov_hit": nov_hit,
        "dec_avg": dec,
        "dec_hit": dec_hit,
        "years": n,
    }


def stance(r: dict) -> str:
    """단순 위치 분류 (매수 추천 아님)."""
    if r["above_200d"] and r["vs_qqq_3m"] > 0 and r["from_3m_high"] > -0.15:
        return "주도 (급락에도 추세 유지)"
    if r["above_200d"] and r["from_3m_high"] <= -0.15:
        return "조정 (200일선 위, 고점 대비 큰 하락)"
    if not r["above_200d"] and r["rsi14"] < 35:
        return "약세·과매도"
    if not r["above_200d"]:
        return "약세 (200일선 아래)"
    return "중립"


def main() -> int:
    p = argparse.ArgumentParser(description="테마 관련 종목 추출·점검")
    p.add_argument("--theme", default="ai_capex", choices=sorted(THEMES))
    p.add_argument("--out", default="results/theme_ai_capex.csv")
    args = p.parse_args()

    groups = THEMES[args.theme]
    tickers = [t for ts in groups.values() for t in ts]
    raw = yf.download(tickers + ["QQQ"], period="12y", auto_adjust=True, group_by="ticker",
                      progress=False, threads=True)
    bench = raw["QQQ"]["Close"].dropna()

    rows = []
    for g, ts in groups.items():
        for t in ts:
            try:
                df = raw[t].dropna(how="all")
            except KeyError:
                continue
            if len(df) < 260:
                print(f"  {t}: 데이터 부족 ({len(df)}일)", file=sys.stderr)
            if len(df) < 70:
                continue
            r = analyze(t, df, bench)
            r["group"] = g
            r["stance"] = stance(r) if len(df) >= 200 else "상장 1년 미만"
            rows.append(r)

    out = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)

    pct = lambda v: "" if pd.isna(v) else f"{v * 100:+.1f}%"  # noqa: E731
    qqq = bench
    print(f"기준일 {qqq.index[-1].date()} | QQQ 3개월 고점 대비 {pct(qqq.iloc[-1] / qqq.tail(63).max() - 1)}, "
          f"1개월 {pct(ret(qqq, 21))}\n")
    for g, sub in out.groupby("group", sort=False):
        print(f"[{g}]")
        for _, r in sub.iterrows():
            print(f"  {r.ticker:6s} ${r.price:>9.2f}  1M {pct(r['1m']):>7s}  3M고점대비 {pct(r.from_3m_high):>7s}  "
                  f"저점후반등 {pct(r.bounce_from_low):>7s}  QQQ대비3M {pct(r.vs_qqq_3m):>7s}  RSI {r.rsi14:5.1f}  "
                  f"11월 {pct(r.nov_avg):>6s}({r.nov_hit * 100 if r.years else 0:.0f}%)  "
                  f"12월 {pct(r.dec_avg):>6s}  | {r.stance}")
        print()
    print(f"전체 결과: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
