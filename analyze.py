"""기준 종목의 차트 패턴 리포트 + 비슷한 계절성을 가진 종목 찾기.

예)
  python analyze.py HHH KRUS                         # 두 종목 리포트 (reports/report.html)
  python analyze.py HHH KRUS --similar --universe us # 미국 전체에서 각 종목과 비슷한 종목 찾기
  python analyze.py KRUS --years 3                   # 최근 3년 기준
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import data, earnings, patterns, report, universe  # noqa: E402

BENCH = "IWM"  # 소형주 지수 (HHH, KRUS 같은 중소형주 비교용)


def ticker_name(t: str) -> str:
    try:
        import yfinance as yf

        info = yf.Ticker(t).get_info()
        return info.get("shortName") or info.get("longName") or t
    except Exception:
        return t


def main() -> int:
    p = argparse.ArgumentParser(description="차트 패턴 리포트 + 유사 종목 찾기")
    p.add_argument("tickers", nargs="+", help="기준 종목 (예: HHH KRUS)")
    p.add_argument("--years", type=int, default=5, help="분석 기간(년), 기본 5")
    p.add_argument("--similar", action="store_true", help="비슷한 계절성 종목 찾기")
    p.add_argument("--universe", default="us", help="유사 종목 검색 범위: us(미국 전체) | nasdaq | nasdaq100")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--min-dollar-volume", type=float, default=2e6, help="일평균 거래대금 하한(달러), 기본 200만")
    p.add_argument("--excess", action="store_true", help="유사 종목을 IWM 대비 초과수익 기준으로 비교 (시장 흐름 제거)")
    p.add_argument("--refresh", action="store_true", help="가격·실적 캐시 업데이트")
    p.add_argument("--out", default="reports/report.html")
    p.add_argument("--inline-js", action="store_true", help="차트 라이브러리를 파일에 포함 (인터넷 없이 열기)")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)

    refs = [t.upper() for t in args.tickers]
    prices = data.update_many(refs + [BENCH], refresh=args.refresh)
    bench = prices.get(BENCH)

    reps, names = [], {}
    for t in refs:
        if t not in prices:
            print(f"{t}: 데이터 없음")
            continue
        names[t] = ticker_name(t)
        ed = earnings.load_earnings_dates(t, refresh=args.refresh)
        rep = report.build_report(t, prices[t], ed, names[t], args.years, bench)
        reps.append(rep)
        print(f"\n===== {t} · {names[t]} =====")
        for f in rep.findings:
            print(" -", f.replace("**", ""))
        print()
        s = rep.summary.copy()
        for c in ("수익률", "최대낙폭"):
            s[c] = (s[c] * 100).map("{:+.1f}%".format)
        s["현재가 위치"] = (s["현재가 위치"] * 100).map("{:.0f}%".format)
        for c in ("RSI 평균", "RSI 최고", "RSI 최저"):
            s[c] = s[c].map("{:.0f}".format)
        print(s.to_string())

    sections = []
    if args.similar and reps:
        tickers = universe.get_universe(args.universe)
        print(f"\n유사 종목 검색: {len(tickers)}개 종목 데이터 준비 중 (처음엔 10~20분)...")
        cand = data.update_many(tickers, batch_size=100, pause=0.5,
                                on_progress=lambda d, n: print(f"\r  {d}/{n}", end="", flush=True))
        print()
        for rep in reps:
            sim = patterns.find_similar(rep.prices, {k: v for k, v in cand.items() if k not in refs},
                                        years=args.years, min_dollar_volume=args.min_dollar_volume,
                                        bench=bench if args.excess else None)
            top = sim.head(args.top)
            top_names = {t: ticker_name(t) for t in top["ticker"]}
            print(f"\n----- {rep.ticker} 와 계절성이 비슷한 종목 (최근 {args.years}년) -----")
            view = top.assign(name=top["ticker"].map(top_names))
            print(view[["ticker", "name", "similarity", "corr_monthly", "corr_weekly", "amplitude", "consistency"]]
                  .round(2).to_string(index=False))
            sections.append((f"{rep.ticker} 와 비슷한 종목" + (" (시장 흐름 제거)" if args.excess else ""),
                             '<div class="card">' + report.similar_table(top, top_names, args.top) +
                             f'<p class="note">비교 대상 {len(sim)}개 종목 (최근 {args.years}년 데이터 있음, '
                             f'거래대금 ${args.min_dollar_volume / 1e6:.0f}M 이상, 주가 $3 이상). '
                             "유사도 = 월별·주별 계절성 지문 상관계수의 평균. 해마다 일치 = 최근 몇 년 중 같은 방향으로 움직인 해의 비율"
                             + (" · IWM 대비 초과수익 기준" if args.excess else "") + ".</p></div>"))
            sim.to_csv(Path(args.out).with_name(f"similar_{rep.ticker}.csv"), index=False)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    title = " · ".join(r.ticker for r in reps) + " 차트 패턴 리포트"
    intro = (f"데이터 기준일 {max(r.prices.index[-1] for r in reps):%Y-%m-%d} · 최근 {args.years}년 기준 · "
             "상승 = 빨강, 하락 = 파랑 · 차트 위 버튼으로 1개월~5년 전환")
    out.write_text(report.render_html(reps, title, intro, sections, inline_js=args.inline_js), encoding="utf-8")
    print(f"\n리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
