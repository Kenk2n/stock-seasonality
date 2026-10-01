"""매집 흔적 스캐너 + 백테스트.

예)
  python accumulation_scan.py                       # 나스닥, 현재 매집 흔적 상위 30개
  python accumulation_scan.py --exchanges us --backtest   # 미국 전체 + 과거 검증
  python accumulation_scan.py --min-mcap 3e8 --min-dollar-volume 5e6
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import accumulation as acc  # noqa: E402
from seasonality import charts, data, patterns, report, universe  # noqa: E402


def fmt_pct(v, signed=True):
    if v is None or not np.isfinite(v):
        return ""
    return f"{v * 100:{'+' if signed else ''}.1f}%"


def main() -> int:
    p = argparse.ArgumentParser(description="매집 흔적 스캐너")
    p.add_argument("--exchanges", default="nasdaq", help="nasdaq | nyse | us")
    p.add_argument("--min-mcap", type=float, default=3e8)
    p.add_argument("--min-dollar-volume", type=float, default=5e6)
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--window", type=int, default=60, help="신호 계산 기간(거래일)")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--charts", type=int, default=8)
    p.add_argument("--backtest", action="store_true", help="과거 매달 말 기준 상위 종목의 이후 3개월 성과 검증")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--out", default="reports/accumulation.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print("종목 목록 (야후 스크리너)...")
    meta = universe.screen_universe(args.min_mcap, args.min_dollar_volume, args.min_price, args.exchanges)
    print(f"  {len(meta)}개")
    prices = data.update_ohlc(list(meta["ticker"]), refresh=args.refresh,
                              on_progress=lambda d, n: print(f"\r  가격 {d}/{n}", end="", flush=True))
    print()
    sig = acc.panel(prices, args.window)
    last = max(df.index[-1] for df in prices.values())
    snap = acc.snapshot(sig, last, args.min_price, args.min_dollar_volume).sort_values("score", ascending=False)
    # 변동성이 거의 0 이고 52주 고점 근처 = 인수 발표 등으로 주가가 고정된 경우가 많아 매집과 무관 → 제외
    pinned = (snap["contraction"] < 0.15) & (snap["pos52"] > 0.9)
    print(f"  가격 고정(인수 발표 등 추정)으로 제외: {', '.join(snap.index[pinned][:15])}{' ...' if pinned.sum() > 15 else ''}")
    top = snap[~pinned].head(args.top).copy()

    print("상위 후보 기관·내부자·공매도 정보...")
    with ThreadPoolExecutor(8) as ex:
        holders = pd.DataFrame(list(ex.map(acc.holder_info, top.index)))
        txs = dict(zip(top.index, ex.map(acc.load_insider_transactions, top.index)))
    top = top.join(holders.set_index("ticker"))
    recent = last - pd.Timedelta(days=90)
    def buys_since(t):
        b = acc.insider_buys(txs[t])
        return float(b.loc[b["date"] >= recent, "value"].sum()) if len(b) else 0.0

    top["insider_buy_90d"] = [buys_since(t) for t in top.index]
    top = top.join(meta.set_index("ticker")[["name", "market_cap"]])

    cols = ["name", "market_cap", "score", "stealth", "cmf", "absorption", "contraction", "ret", "pos52",
            "inst_pct", "inst_top_adders", "short_float", "insider_buy_90d", "sector"]
    view = top[cols].copy()
    view["market_cap"] = (view["market_cap"] / 1e9).round(1)
    print(f"\n===== 매집 흔적 상위 {args.top}개 ({last:%Y-%m-%d}, 최근 {args.window}거래일) =====")
    print(view.round(2).to_string())
    top.to_csv(out.with_suffix(".csv"))

    # ---- 백테스트
    bt_html = ""
    if args.backtest:
        ref = next(iter(prices.values())).index
        months = pd.period_range(ref[0].to_period("M") + 13, (last - pd.Timedelta(days=95)).to_period("M"), freq="M")
        dates = [ref[ref.to_period("M") == m][-1] for m in months if (ref.to_period("M") == m).any()]
        print(f"\n백테스트: {len(dates)}개 시점...")
        s, ic = acc.backtest(prices, dates, horizon=63, top=args.top, min_price=args.min_price,
                             min_dollar_volume=args.min_dollar_volume, window=args.window, sig=sig)
        ex = s["top"] - s["all"]
        tstat = ex.mean() / ex.std() * np.sqrt(len(ex))
        icm = ic.drop(columns="date").mean()
        ict = icm / (ic.drop(columns="date").std() / np.sqrt(len(ic)))
        msg = (f"{s['date'].min():%Y-%m}~{s['date'].max():%Y-%m} 매달 말 {len(s)}회: 상위 {args.top}개의 이후 3개월 평균 "
               f"{s['top'].mean() * 100:+.2f}% vs 전체 {s['all'].mean() * 100:+.2f}% → 초과 {ex.mean() * 100:+.2f}% "
               f"(t={tstat:+.2f}, 이긴 달 {(ex > 0).mean():.0%}). 상위 10% {s['decile_top'].mean() * 100:+.2f}% / "
               f"하위 10% {s['decile_bottom'].mean() * 100:+.2f}%.")
        print(msg)
        names = {"score": "종합 점수", "stealth": "주가 대비 거래량 쏠림", "vol_balance": "상승일 거래량 비중",
                 "cmf": "CMF", "absorption": "흡수형 대량거래", "contraction": "변동성 수축(값↑=수축 약함)",
                 "ret": "직전 수익률", "pos52": "52주 위치"}
        rows = "".join(f"<tr><td>{names.get(c, c)}</td><td>{icm[c]:+.3f}</td><td>{ict[c]:+.1f}</td></tr>" for c in icm.index)
        good = tstat > 2
        bt_html = (
            f"<div class='card'><p><b>{html.escape(msg)}</b></p>"
            f"<p>{'과거에 효과가 있었습니다.' if good else '과거에는 이 흔적이 이후 수익률을 예측하지 못했습니다. 참고용으로만 보세요.'}</p>"
            "<h3>신호별 예측력 (IC = 신호 순위와 이후 3개월 수익률 순위의 상관, 매달 평균)</h3>"
            "<div class='tbl'><table><tr><th>신호</th><th>IC</th><th>t</th></tr>" + rows + "</table></div>"
            "<p class='note'>IC 가 +0.05 이상이고 t 가 2 이상이면 의미 있는 신호로 봅니다. "
            "현재 상장 종목만 대상이라 상장폐지 종목은 빠져 있습니다(생존 편향).</p></div>"
        )

    # ---- HTML
    def row(i, t, r):
        return (
            f"<tr><td>{i}. {html.escape(t)}</td><td style='text-align:left'>{html.escape(str(r['name'] or ''))}</td>"
            f"<td>${r['market_cap'] / 1e9:.1f}B</td><td>{r['score']:.2f}</td><td>{r['stealth']:+.2f}</td>"
            f"<td>{r['cmf']:+.2f}</td><td>{int(r['absorption'])}</td><td>{r['contraction']:.2f}</td>"
            f"<td>{fmt_pct(r['ret'])}</td><td>{r['pos52'] * 100:.0f}%</td><td>{fmt_pct(r.get('inst_pct'), False)}</td>"
            f"<td>{'' if pd.isna(r.get('inst_top_adders')) else f'{int(r.inst_top_adders)}/{int(r.inst_top_n)}'}</td>"
            f"<td>{fmt_pct(r.get('short_float'), False)}</td>"
            f"<td>{'' if not r['insider_buy_90d'] else f'${r.insider_buy_90d / 1e3:,.0f}K'}</td>"
            f"<td style='text-align:left'>{html.escape(str(r.get('industry') or ''))}</td></tr>"
        )

    table = (
        "<div class='tbl'><table><tr><th>종목</th><th style='text-align:left'>이름</th><th>시총</th><th>점수</th>"
        "<th>거래량 쏠림</th><th>CMF</th><th>대량거래 일수</th><th>변동성 수축</th><th>기간 수익률</th><th>52주 위치</th>"
        "<th>기관 보유</th><th>상위 기관 증가</th><th>공매도/유통</th><th>내부자 매수 90일</th>"
        "<th style='text-align:left'>업종</th></tr>"
        + "".join(row(i, t, r) for i, (t, r) in enumerate(top.iterrows(), 1)) + "</table></div>"
    )
    explain = (
        "<ul class='find'><li><b>거래량 쏠림</b>: 상승일 거래량 − 하락일 거래량을 주가 상승분으로 설명되는 만큼 뺀 값 "
        "(주가는 별로 안 올랐는데 사는 쪽 거래량이 많음)</li>"
        "<li><b>CMF</b>: 종가가 하루 범위의 위쪽에서 끝난 날의 거래량 비중 (-1~1)</li>"
        "<li><b>대량거래 일수</b>: 50일 평균의 2.5배 이상 거래되면서 종가가 위쪽에서 끝나고 크게 안 빠진 날</li>"
        "<li><b>변동성 수축</b>: 최근 20일 변동성 / 120일 변동성 (1보다 작을수록 박스권이 좁아짐)</li>"
        "<li><b>상위 기관 증가</b>: 상위 10개 기관 중 직전 분기보다 보유를 늘린 곳 수</li></ul>"
    )
    sections = []
    if bt_html:
        sections.append(("먼저: 이 흔적이 과거에 통했나 (백테스트)", bt_html))
    sections.append((f"현재 매집 흔적 상위 {args.top}개", "<div class='card'>" + table + explain + "</div>"))
    figs = ""
    for t in list(top.index[: args.charts]):
        df = prices[t][prices[t].index > last - pd.DateOffset(years=1)]
        fig = charts.price_rsi_chart(df, None, patterns.find_swings(df), None, f"{t} · {top.loc[t, 'name']} (1년)")
        figs += f"<h3>{html.escape(t)}</h3><div class='card'>" + report.pio.to_html(
            fig, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}) + "</div>"
    sections.append((f"상위 {args.charts}개 차트", figs))
    intro = (f"기준일 {last:%Y-%m-%d} · {args.exchanges}, 시총 ≥ ${args.min_mcap / 1e8:.0f}억, "
             f"거래대금 ≥ ${args.min_dollar_volume / 1e6:.0f}M, 주가 ≥ ${args.min_price:.0f} · 비교 {len(snap)}개")
    out.write_text(report.render_html([], "매집 흔적 스캐너", intro, sections, inline_js=args.inline_js), encoding="utf-8")
    print(f"\n리포트: {out}\nCSV: {out.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
