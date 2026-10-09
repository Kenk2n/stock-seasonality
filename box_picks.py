"""'쿠라 스시 같은' 박스권 왕복 종목 중 지금 박스 하단에 있는 후보 고르기.

조건(모두 만족):
  - 3·4·5년 중 가장 뚜렷한 박스: 폭 ≥ 1.5배, 박스 횡단 ≥ 4회(2 왕복), 추세가 박스 폭의 35% 이하
  - 현재가가 박스 안 위치 −20% ~ 30% (박스를 크게 깨고 내려간 종목은 제외)
  - 시총 ≥ $3억, 3개월 평균 거래대금 ≥ $500만, 주가 ≥ $5, 인수 발표 등으로 가격이 고정된 종목 제외
점수(합 100):
  박스 품질 35 · 박스 하단에 가까움 20 · KRUS 와 닮음(박스 폭 + 실적 때 출렁이는 성격) 20
  · 매집 흔적(주가 대비 거래량 쏠림, CMF) 15 · 최근 거래량 증가 10
  − 위험 감점: 박스 하단 아래(최대 10) · 3개월 −25% 넘는 급락(8) · 공매도 유통주식의 30% 이상(5)

  python box_picks.py --top 20
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
from seasonality import charts, data, patterns, picks, report, universe  # noqa: E402
from seasonality.picks import REF, WEIGHTS  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="박스 하단 후보 고르기")
    p.add_argument("--exchanges", default="us")
    p.add_argument("--min-mcap", type=float, default=3e8)
    p.add_argument("--min-dollar-volume", type=float, default=5e6)
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--years", default="3,4,5")
    p.add_argument("--min-legs", type=int, default=4)
    p.add_argument("--pos-min", type=float, default=-0.2)
    p.add_argument("--pos-max", type=float, default=0.3)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--out", default="reports/box_picks.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    meta = universe.screen_universe(args.min_mcap, args.min_dollar_volume, args.min_price, args.exchanges)
    tickers = sorted(set(meta["ticker"]) | {REF})
    prices = data.update_ohlc(tickers, refresh=args.refresh)
    prices = {t: df for t, df in prices.items() if len(df) > 500}
    end = max(df.index[-1] for df in prices.values())
    print(f"대상 {len(prices)}개 · 기준일 {end:%Y-%m-%d}")

    # 1) 박스: 종목마다 3·4·5년 중 점수 높은 기간
    box = picks.box_scan(prices, end, [int(v) for v in args.years.split(",")], args.min_legs)
    print(f"박스 왕복형 {len(box)}개")

    # 2) 매집 신호: 전 종목 단면으로 계산(표준화 기준)
    sig = acc.panel(prices)
    snap = acc.snapshot(sig, end, args.min_price, args.min_dollar_volume)

    # 3) 박스 하단 근처 후보 + 점수 (KRUS 닮음: 박스 폭 + 실적 지문)
    x, eds = picks.box_candidates(prices, box, snap, end, args.pos_min, args.pos_max, REF)
    print(f"박스 하단 근처 {len(x)}개")
    if len(x) == 0:
        print("조건을 만족하는 종목이 없습니다.")
        return 1

    # 4) 기관·공매도·내부자 정보 (모든 후보)
    with ThreadPoolExecutor(8) as ex:
        hold = pd.DataFrame(list(ex.map(acc.holder_info, x.index))).set_index("ticker")
        txs = dict(zip(x.index, ex.map(acc.load_insider_transactions, x.index)))
    x = x.join(hold)

    # 5) 위험 감점: 박스 하단 아래(최대 −10), 3개월 −25% 넘는 급락(−8), 공매도 30% 이상(−5)
    sf = pd.to_numeric(x.get("short_float"), errors="coerce").fillna(0)
    x["penalty"] = (10 * np.clip(-x["pos"] / 0.2, 0, 1) + 8 * (x["ret3m"] < -0.25) + 5 * (sf >= 0.30))
    x["total"] = x["raw"] - x["penalty"]
    x = x.join(meta.set_index("ticker")[["name", "exchange", "market_cap"]], how="left")
    x = x.sort_values("total", ascending=False)
    x.to_csv(out.with_name(out.stem + "_all.csv"))
    top = x.head(args.top).copy()
    top["tier"] = np.where(top["penalty"] == 0, "A", "B")

    def buys_90d(t):
        b = acc.insider_buys(txs[t])
        return float(b.loc[b["date"] >= end - pd.Timedelta(days=90), "value"].sum()) if len(b) else 0.0

    top["insider_buy_90d"] = [buys_90d(t) for t in top.index]

    def flags(t, r):
        f = []
        if r["pos"] < 0:
            f.append(f"박스 하단 ${r['low']:,.2f} 아래 ({r['to_low'] * 100:+.0f}%)")
        if r["next_earnings"] is not None and pd.notna(r["next_earnings"]):
            dd = (r["next_earnings"] - end).days
            if 0 <= dd <= 30:
                f.append(f"실적 D-{dd} ({r['next_earnings']:%m/%d})")
        if r["ret3m"] < -0.25:
            f.append(f"3개월 {r['ret3m'] * 100:+.0f}% 급락")
        if r["trend_per_year"] < -0.05:
            f.append(f"약한 하락 추세 {r['trend_per_year'] * 100:+.0f}%/년")
        sf = r.get("short_float")
        if sf is not None and np.isfinite(sf) and sf > 0.15:
            f.append(f"공매도 {sf * 100:.0f}%")
        if r["rsi"] < 30:
            f.append(f"RSI {r['rsi']:.0f} 과매도")
        return f

    top["flags"] = [flags(t, r) for t, r in top.iterrows()]
    top.to_csv(out.with_suffix(".csv"))

    pd.set_option("display.width", 250)
    v = top.assign(market_cap=(top["market_cap"] / 1e9).round(1), dv=(top["dollar_volume"] / 1e6).round(0))
    print(v[["tier", "name", "market_cap", "dv", "total", "penalty", "years", "band", "low", "high", "legs", "pos", "price", "to_high", "rsi",
             "vol_ratio", "stealth", "cmf", "earn_dist", "next_earnings"]].round(2).to_string())
    for t, r in top.iterrows():
        print(f"  {t}: {' · '.join(r['flags']) or '-'}")

    # ---- HTML
    def pc(vv, signed=True):
        return "" if vv is None or not np.isfinite(vv) else f"{vv * 100:{'+' if signed else ''}.0f}%"

    trs = ""
    for i, (t, r) in enumerate(top.iterrows(), 1):
        ne = r["next_earnings"]
        trs += (
            f"<tr><td>{i}. <b>{html.escape(t)}</b><br><span class='note'>{r['tier']}급</span></td><td style='text-align:left'>{html.escape(str(r['name']))}"
            f"<br><span class='note'>{html.escape(str(r.get('industry') or ''))}</span></td>"
            f"<td>${r['market_cap'] / 1e9:.1f}B</td><td>${r['dollar_volume'] / 1e6:.0f}M</td><td><b>{r['total']:.0f}</b></td>"
            f"<td>${r['low']:,.2f} ~ ${r['high']:,.2f}<br><span class='note'>{r['years']}년 · {r['band']:.2f}배 · {r['legs'] // 2}왕복</span></td>"
            f"<td>${r['price']:,.2f}<br><span class='note'>위치 {r['pos'] * 100:.0f}%</span></td><td>{pc(r['to_high'])}</td>"
            f"<td>{r['rsi']:.0f}</td><td>{r['vol_ratio']:.2f}배</td><td>{r['stealth']:+.2f} / {r['cmf']:+.2f}</td>"
            f"<td>{'' if not np.isfinite(r['earn_dist']) else f'{r.earn_dist:.2f}'}<br><span class='note'>실적반응 {pc(r['react_abs'], False)}</span></td>"
            f"<td>{pc(r.get('short_float'), False)}</td>"
            f"<td>{'' if pd.isna(ne) else ne.strftime('%m/%d')}</td>"
            f"<td style='text-align:left'>{html.escape(' · '.join(r['flags']) or '-')}</td></tr>"
        )
    table = (
        "<div class='card tbl'><table style='font-size:12.5px'><tr><th>종목</th><th style='text-align:left'>이름 / 업종</th>"
        "<th>시총</th><th>거래대금</th><th>점수</th><th>박스</th><th>현재가</th><th>박스 상단까지</th><th>RSI</th>"
        "<th>거래량 20일/120일</th><th>매집(쏠림/CMF)</th><th>KRUS 실적지문 차이</th><th>공매도</th><th>다음 실적</th>"
        "<th style='text-align:left'>주의</th></tr>" + trs + "</table>"
        f"<p class='note'>점수 = 박스 품질 {WEIGHTS['box']} + 박스 하단에 가까움 {WEIGHTS['bottom']} + KRUS 와 닮음 {WEIGHTS['krus']} "
        f"+ 매집 흔적 {WEIGHTS['accum']} + 최근 거래량 증가 {WEIGHTS['volume']} (100점 만점) − 위험 감점 "
        "(박스 하단 아래 최대 −10, 3개월 −25% 넘는 급락 −8, 공매도 30% 이상 −5). "
        "KRUS 실적지문 차이는 작을수록 '실적 때 출렁이는 방식'이 KRUS 와 비슷함. "
        "A급 = 위험 감점 없음, B급 = 박스 하단 아래·급락·공매도 과다 중 하나 이상.</p></div>"
    )
    evidence = (
        "<div class='card'><ul class='find'>"
        "<li><b>박스 하단 매수</b>: 2024.9~2026.6 매달 말 백테스트에서 박스 하단에서 산 경우 3개월 후 시장 대비 +1.9% "
        "(통계적으로 의미 없음, 박스 상단에서 산 경우 +2.1% 와 차이 없음).</li>"
        "<li><b>박스 이탈</b>: 박스 아래로 25% 넘게 이탈한 종목은 이후 3개월 시장 대비 −10.7%. 그래서 −20% 밑은 제외했고, "
        "하단 아래에 있는 종목은 '주의'에 표시했습니다. 하단 아래로 더 밀리면 박스가 깨진 것으로 봐야 합니다.</li>"
        "<li><b>매집 흔적</b>: 2022.11~2026.6 백테스트에서 이후 수익률을 예측하지 못했습니다. 그래서 점수 비중을 15점으로 낮게 뒀습니다.</li>"
        "<li><b>KRUS 형(실적 때 크게 출렁임)</b>: 이 성격은 이후에도 잘 유지됐지만(상위 20개 중 55%), 오를지 내릴지는 알려주지 않았습니다. "
        "다음 실적일이 가까운 종목은 그날 ±10% 이상 움직일 수 있습니다.</li>"
        "<li>이 목록은 조건 검색 결과이며, 투자 권유가 아닙니다. 매수 전 실적·재무·뉴스를 꼭 확인하세요.</li></ul></div>"
    )
    figs = ""
    for t, r in top.iterrows():
        df = prices[t][prices[t].index > end - pd.DateOffset(years=int(r["years"])) - pd.Timedelta(days=20)]
        th = min(max(r["band"] ** 0.5 - 1, 0.08), 0.6)
        fig = charts.price_rsi_chart(df, None, patterns.find_swings(df, th), eds.get(t),
                                     f"{t} · {r['name']} — 박스 ${r['low']:,.2f} ~ ${r['high']:,.2f}, 현재 ${r['price']:,.2f}")
        fig.add_hrect(y0=r["low"], y1=r["high"], fillcolor="#eda100", opacity=0.10, line_width=0, row=1, col=1)
        for yv in (r["low"], r["high"]):
            fig.add_hline(y=yv, line=dict(color="#eda100", width=1.2, dash="dash"), row=1, col=1)
        figs += f"<h3>{html.escape(t)}</h3><div class='card'>" + report.pio.to_html(
            fig, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}) + "</div>"
    sections = [
        (f"박스 하단 후보 {len(top)}개", table),
        ("먼저 알아둘 것 (과거 검증 결과)", evidence),
        ("차트 (노란 띠 = 박스, 점선 = 실적일)", figs),
    ]
    intro = (f"기준일 {end:%Y-%m-%d} · 미국 상장, 시총 ≥ ${args.min_mcap / 1e8:.0f}억, 거래대금 ≥ ${args.min_dollar_volume / 1e6:.0f}M, "
             f"주가 ≥ ${args.min_price:.0f} · 박스 왕복형 {len(box)}개 중 하단 근처 {len(x)}개에서 선정")
    out.write_text(report.render_html([], "쿠라 스시형 박스권 · 박스 하단 후보", intro, sections, inline_js=args.inline_js),
                   encoding="utf-8")
    print(f"리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
