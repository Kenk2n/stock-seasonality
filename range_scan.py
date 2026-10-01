"""박스권 왕복 종목 찾기 (같은 가격 범위 안에서 위아래로 여러 번 오가는 종목).

예)
  python range_scan.py                          # 미국 전체, 최근 5년, 6번 이상 왕복(3 왕복)
  python range_scan.py --years 3 --min-legs 4   # 최근 3년, 2 왕복 이상
  python range_scan.py --exchanges nasdaq --validate
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import charts, data, patterns, rangebound as rb, report, universe  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="박스권 왕복 종목 찾기")
    p.add_argument("--exchanges", default="us", help="nasdaq | nyse | us")
    p.add_argument("--min-mcap", type=float, default=3e8)
    p.add_argument("--min-dollar-volume", type=float, default=3e6)
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--years", default="5", help="분석 기간(년). 3,4,5 처럼 여러 개 주면 종목마다 가장 뚜렷한 박스를 고름")
    p.add_argument("--bottom", action="store_true", help="현재가가 박스 하단 근처(박스 아래 25% ~ 박스 30% 위치)인 종목만")
    p.add_argument("--min-band", type=float, default=1.5, help="최소 박스 폭 (상단/하단, 기본 1.5배)")
    p.add_argument("--max-trend", type=float, default=0.35, help="추세가 박스 폭에서 차지하는 최대 비율")
    p.add_argument("--min-legs", type=int, default=6, help="최소 박스 횡단 횟수 (6 = 3번 왕복)")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--charts", type=int, default=12)
    p.add_argument("--validate", action="store_true", help="2년 전까지로 찾은 박스가 이후에도 유지됐는지 검증")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--out", default="reports/range.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    meta = universe.screen_universe(args.min_mcap, args.min_dollar_volume, args.min_price, args.exchanges)
    print(f"대상 {len(meta)}개")
    prices = data.update_ohlc(list(meta["ticker"]), refresh=args.refresh,
                              on_progress=lambda d, n: print(f"\r  가격 {d}/{n}", end="", flush=True))
    end = max(df.index[-1] for df in prices.values())
    year_list = [int(y) for y in str(args.years).split(",")]
    frames = []
    for y in year_list:
        r = rb.scan_rangebound(prices, end - pd.DateOffset(years=y), end, args.min_band, args.max_trend, args.min_legs)
        frames.append(r.assign(years=y))
    # 종목마다 가장 점수 높은 기간 하나만 (통과한 것 우선)
    res = (pd.concat(frames).sort_values(["passes", "score"], ascending=False)
           .drop_duplicates("ticker").reset_index(drop=True))
    start = end - pd.DateOffset(years=max(year_list))
    res = res.merge(meta[["ticker", "name", "exchange", "market_cap"]], on="ticker", how="left")
    res["status"] = res["pos"].map(rb.box_status)
    res["rsi"] = [patterns.rsi(prices[t]["Close"]).iloc[-1] for t in res["ticker"]]
    res["ret3m"] = [prices[t]["Close"].iloc[-1] / prices[t]["Close"].iloc[-64] - 1 for t in res["ticker"]]
    res["to_high"] = res["high"] / res["price"] - 1
    res.to_csv(out.with_suffix(".csv"), index=False)
    sel = res["passes"]
    if args.bottom:
        sel &= res["pos"].between(-0.25, 0.3)
    hits = res[sel].head(args.top)
    label = "박스 하단 근처 " if args.bottom else ""
    print(f"\n최근 {args.years}년 {label}박스 왕복형 {int(sel.sum())}개 (박스 ≥ {args.min_band}배, "
          f"횡단 ≥ {args.min_legs}회, 추세 비율 ≤ {args.max_trend})")
    v = hits.assign(market_cap=(hits["market_cap"] / 1e9).round(1))
    print(v[["ticker", "name", "market_cap", "years", "score", "band", "low", "high", "legs", "trend_per_year",
             "pos", "status", "price", "to_high", "rsi", "ret3m"]].round(2).to_string(index=False))

    note = ""
    if args.validate:
        val = rb.validate_persistence(prices, end - pd.DateOffset(years=2))
        if val:
            b, c = val["box"], val["control"]
            note = (f"검증: {val['cut'] - pd.DateOffset(years=3):%Y-%m}~{val['cut']:%Y-%m} 데이터로 찾은 박스형 {b['n']}개가 "
                    f"이후 2년 동안 같은 박스 안에 머문 비율 {b['inside']:.0%}, 같은 박스 횡단 {b['legs']:.2f}회 · "
                    f"같은 폭의 박스형 아닌 종목 {c['n']}개는 {c['inside']:.0%}, {c['legs']:.2f}회. "
                    "박스형은 이후에도 조금 더 왕복하지만, 박스를 벗어나는 비율은 다른 종목과 비슷합니다.")
            print("\n" + note)

    def row(i, r):
        return (
            f"<tr><td>{i}. {html.escape(r.ticker)}</td><td style='text-align:left'>{html.escape(str(r.name))}</td>"
            f"<td>${r.market_cap / 1e9:.1f}B</td><td>{r.score:.2f}</td><td>{r.band:.2f}배</td>"
            f"<td>${r.low:,.2f} ~ ${r.high:,.2f}</td><td>{r.legs}</td><td>{r.trend_per_year * 100:+.0f}%</td>"
            f"<td>{r.peak_disp:.2f}</td><td>{r.trough_disp:.2f}</td><td>${r.price:,.2f}</td>"
            f"<td>{r.pos * 100:.0f}%</td><td>{r.status}</td><td>{r.to_high * 100:+.0f}%</td>"
            f"<td>{r.rsi:.0f}</td><td>{r.ret3m * 100:+.0f}%</td><td>{r.years}년</td></tr>"
        )

    table = (
        "<div class='tbl'><table><tr><th>종목</th><th style='text-align:left'>이름</th><th>시총</th><th>점수</th>"
        "<th>박스 폭</th><th>박스 (하위10%~상위10%)</th><th>횡단 횟수</th><th>연 추세</th><th>고점 흩어짐</th>"
        "<th>저점 흩어짐</th><th>현재가</th><th>박스 안 위치</th><th>상태</th><th>박스 상단까지</th><th>RSI</th>"
        "<th>최근 3개월</th><th>기간</th></tr>"
        + "".join(row(i, r) for i, r in enumerate(hits.itertuples(), 1)) + "</table></div>"
    )
    explain = (
        "<ul class='find'><li><b>박스</b>: 최근 기간 주봉 종가의 하위 10% ~ 상위 10% 가격대</li>"
        "<li><b>횡단 횟수</b>: 박스 하단(아래 20%)에서 상단(위 20%)으로, 또는 반대로 건너간 횟수 (2회 = 1 왕복)</li>"
        "<li><b>연 추세</b>: 기간 전체 회귀선의 연간 변화율 (0 에 가까울수록 옆으로 기는 박스)</li>"
        "<li><b>고점·저점 흩어짐</b>: 스윙 고점(저점)들의 높이 차이 ÷ 박스 폭. 작을수록 매번 비슷한 가격에서 꺾임</li>"
        "<li><b>박스 안 위치</b>: 0% = 박스 하단, 100% = 상단. -25% 아래는 하단 이탈, 125% 위는 상단 돌파</li>"
        "<li><b>박스 상단까지</b>: 현재가에서 박스 상단(상위 10% 가격)까지 남은 거리</li>"
        "<li><b>기간</b>: 3·4·5년 중 박스가 가장 뚜렷했던 기간</li></ul>"
    )
    if args.bottom:
        note = (note + " " if note else "") + (
            "참고 (2024.9~2026.6 매달 말 백테스트, 3년 박스 기준): 박스 하단에서 산 경우 3개월 후 시장 대비 +1.9%(통계적으로 "
            "의미 없음, 순위로는 평균), 박스 상단에서 산 경우 +2.1%로 차이가 없었고, 박스 아래로 25% 넘게 이탈한 종목은 "
            "−10.7%로 계속 약했습니다. '박스 하단 = 반등'은 데이터로 확인되지 않았고, 이탈 여부가 더 중요한 신호였습니다.")
    sections = [(f"최근 {args.years}년 {label}박스 왕복형 {len(hits)}개",
                 "<div class='card'>" + table + (f"<p class='note'><b>{html.escape(note)}</b></p>" if note else "")
                 + explain + "</div>")]
    figs = ""
    for r in hits.head(args.charts).itertuples():
        df = prices[r.ticker]
        df = df[df.index > end - pd.DateOffset(years=int(r.years)) - pd.Timedelta(days=20)]
        fig = charts.price_rsi_chart(df, None, patterns.find_swings(df, min(max(r.band ** 0.5 - 1, 0.08), 0.6)), None,
                                     f"{r.ticker} · {r.name} — 박스 ${r.low:,.2f} ~ ${r.high:,.2f} ({r.status})")
        fig.add_hrect(y0=r.low, y1=r.high, fillcolor="#eda100", opacity=0.10, line_width=0, row=1, col=1)
        for y in (r.low, r.high):
            fig.add_hline(y=y, line=dict(color="#eda100", width=1.2, dash="dash"), row=1, col=1)
        figs += f"<h3>{html.escape(r.ticker)}</h3><div class='card'>" + report.pio.to_html(
            fig, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}) + "</div>"
    sections.append((f"상위 {min(args.charts, len(hits))}개 차트 (노란 띠 = 박스)", figs))
    intro = (f"기준일 {end:%Y-%m-%d} · {args.exchanges}, 시총 ≥ ${args.min_mcap / 1e8:.0f}억, "
             f"거래대금 ≥ ${args.min_dollar_volume / 1e6:.0f}M, 주가 ≥ ${args.min_price:.0f} · 비교 {len(res)}개")
    out.write_text(report.render_html([], f"{label}박스권 왕복 종목 (최근 {args.years}년)", intro, sections,
                                      inline_js=args.inline_js), encoding="utf-8")
    print(f"\n리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
