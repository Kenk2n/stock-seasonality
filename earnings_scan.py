"""'실적 때 크게 출렁이는' 패턴이 기준 종목과 비슷한 종목 찾기.

예)
  python earnings_scan.py KRUS                                   # 나스닥, 시총 $3억↑, 거래대금 $500만↑
  python earnings_scan.py KRUS --exchanges us --validate         # 나스닥+NYSE, 표본 외 검증 포함
  python earnings_scan.py KRUS --min-mcap 1e9 --min-dollar-volume 2e7
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

from seasonality import charts, data, earnings, patterns, report, universe  # noqa: E402

EXCH_NAME = {"NMS": "Nasdaq GS", "NGM": "Nasdaq GM", "NCM": "Nasdaq CM", "NYQ": "NYSE", "ASE": "NYSE American",
             "PCX": "NYSE Arca"}


def load_all_earnings(tickers: list[str], refresh: bool = False) -> dict[str, pd.DatetimeIndex]:
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda t: earnings.load_earnings_dates(t, refresh=refresh), tickers))
    return dict(zip(tickers, res))


def validate(ref: str, cands: dict, start, cut, end, top: int = 20) -> dict:
    """표본 외 검증: 앞 구간(start~cut) 지문만으로 고른 상위 종목이 뒤 구간(cut~end)에도 같은 성격을 유지하나.

    '기준 종목형' 조건(실적 3일 비중·실적 반응 배수·변동성이 기준 종목의 60~70% 이상)을
    뒤 구간에도 만족하는 비율을 상위 종목과 전체 종목에서 비교한다.
    """
    a = earnings.find_earnings_lookalikes(*cands[ref], {k: v for k, v in cands.items() if k != ref}, start, cut)
    ref_a = a.attrs["reference"]
    ref_b = earnings.earnings_fingerprint(*cands[ref], cut, end)
    rows = {}
    for t in a["ticker"]:
        fb = earnings.earnings_fingerprint(*cands[t], cut, end)
        if fb:
            rows[t] = fb
    b = pd.DataFrame(rows).T.astype(float)

    def is_like(x):
        return ((x["var_share"] >= 0.6 * ref_a["var_share"]) & (x["react_ratio"] >= 0.65 * ref_a["react_ratio"])
                & (x["threshold"] >= 0.7 * ref_a["threshold"]))

    top_b = b.reindex(a["ticker"].head(top)).dropna()
    cols = ["react_abs", "react_ratio", "var_share", "threshold"]
    # 항목별 유지율: 앞 구간 값과 뒤 구간 값의 종목 간 순위 상관
    j = a.set_index("ticker")[earnings.FP_COLS].join(b[earnings.FP_COLS], rsuffix="_b", how="inner")
    return {
        "top_rate": float(is_like(top_b).mean()),
        "all_rate": float(is_like(b).mean()),
        "medians": pd.DataFrame({"기준(뒤 구간)": pd.Series({c: ref_b[c] for c in cols}) if ref_b else np.nan,
                                 f"앞 구간 상위 {top}": top_b[cols].median(), "전체": b[cols].median()}),
        "persistence": {c: float(j[c].corr(j[f"{c}_b"], method="spearman")) for c in earnings.FP_COLS},
    }


LABELS = {
    "react_ratio": "실적 반응 / 평소 등락",
    "var_share": "출렁임 중 실적 3일 비중",
    "swing_align": "고점·저점 실적 ±15일",
    "runup_hit": "실적 전 20일 상승 확률",
    "threshold": "변동성(스윙 기준)",
}


def results_table(df: pd.DataFrame, meta: pd.DataFrame, ref: dict | None, top: int) -> str:
    m = meta.set_index("ticker")

    def row(label, r, name="", exch="", mcap=np.nan, dist=""):
        return (
            f"<tr><td>{label}</td><td style='text-align:left'>{html.escape(name)}</td><td>{exch}</td>"
            f"<td>{'' if not np.isfinite(mcap) else f'${mcap / 1e9:.1f}B'}</td><td>{dist}</td>"
            f"<td>{r['react_abs'] * 100:.1f}%</td><td>{r['react_ratio']:.1f}배</td><td>{r['var_share'] * 100:.0f}%</td>"
            f"<td>{r['swing_align'] * 100:.0f}%</td><td>{r['runup_hit'] * 100:.0f}% ({r['runup_mean'] * 100:+.1f}%)</td>"
            f"<td>{r['threshold'] * 100:.0f}%</td><td>{int(r['events'])}</td></tr>"
        )

    rows = []
    if ref:
        rows.append(row("<b>기준</b>", ref))
    for i, r in enumerate(df.head(top).to_dict("records"), 1):
        mm = m.loc[r["ticker"]] if r["ticker"] in m.index else {}
        rows.append(row(f"{i}. {html.escape(r['ticker'])}", r, mm.get("name", ""), EXCH_NAME.get(mm.get("exchange"), ""),
                        mm.get("market_cap", np.nan), f"{r['distance']:.2f}"))
    return (
        '<div class="tbl"><table><tr><th>종목</th><th style="text-align:left">이름</th><th>거래소</th><th>시총</th>'
        "<th>차이</th><th>실적 반응(중앙값)</th><th>평소 대비</th><th>실적 3일 비중</th><th>스윙-실적 일치</th>"
        "<th>실적 전 상승</th><th>변동성</th><th>실적 수</th></tr>" + "".join(rows) + "</table></div>"
    )


def main() -> int:
    p = argparse.ArgumentParser(description="실적 주기 패턴 유사 종목 찾기")
    p.add_argument("ticker", help="기준 종목 (예: KRUS)")
    p.add_argument("--exchanges", default="nasdaq", help="nasdaq | nyse | us")
    p.add_argument("--min-mcap", type=float, default=3e8, help="최소 시가총액 (기본 $3억)")
    p.add_argument("--min-dollar-volume", type=float, default=5e6, help="최소 3개월 평균 거래대금 (기본 $500만)")
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--years", type=int, default=5, help="분석 기간(년)")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--charts", type=int, default=8, help="차트로 보여줄 상위 종목 수")
    p.add_argument("--validate", action="store_true", help="앞 구간으로 고른 종목이 뒤 구간에서도 비슷한지 검증")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--out", default="reports/earnings_lookalikes.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    ref_t = args.ticker.upper()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print("종목 목록 받는 중 (야후 스크리너)...")
    meta = universe.screen_universe(args.min_mcap, args.min_dollar_volume, args.min_price, args.exchanges)
    tickers = sorted(set(meta["ticker"]) | {ref_t})
    print(f"  조건 통과: {len(meta)}개 (시총 ≥ ${args.min_mcap / 1e8:.0f}억, 거래대금 ≥ ${args.min_dollar_volume / 1e6:.0f}M, "
          f"주가 ≥ ${args.min_price:.0f}, {args.exchanges})")

    prices = data.update_many(tickers, refresh=args.refresh, batch_size=100, pause=0.5,
                              on_progress=lambda d, n: print(f"\r  가격 {d}/{n}", end="", flush=True))
    print(f"\n  실적 발표일 받는 중... (처음엔 종목당 약 0.3초)")
    edates = load_all_earnings([t for t in tickers if t in prices], args.refresh)
    cands = {t: (prices[t], edates[t]) for t in prices if len(edates.get(t, [])) > 0}

    end = prices[ref_t].index[-1]
    start = end - pd.DateOffset(years=args.years)
    res = earnings.find_earnings_lookalikes(*cands[ref_t], {k: v for k, v in cands.items() if k != ref_t}, start, end)
    ref_fp = res.attrs["reference"]
    print(f"\n비교 종목 {len(res)}개 (최근 {args.years}년 실적 6번 이상)")
    print(f"기준 {ref_t}: " + ", ".join(f"{LABELS[c]} {ref_fp[c]:.2f}" for c in earnings.FP_COLS))
    view = res.head(args.top).merge(meta[["ticker", "name", "exchange", "market_cap"]], on="ticker", how="left")
    view["market_cap"] = (view["market_cap"] / 1e9).round(1)
    print(view[["ticker", "name", "exchange", "market_cap", "distance", "react_abs", "react_ratio", "var_share",
                "swing_align", "runup_hit", "threshold"]].round(2).to_string(index=False))
    res.merge(meta, on="ticker", how="left").to_csv(out.with_suffix(".csv"), index=False)

    note = ""
    if args.validate:
        cut = end - pd.DateOffset(years=2)
        v = validate(ref_t, cands, start, cut, end)
        ok = v["top_rate"] >= max(2 * v["all_rate"], v["all_rate"] + 0.2)
        verdict = (f"의미 있음: 앞 구간에서 고른 상위 20개 중 {v['top_rate'] * 100:.0f}%가 뒤 구간에도 '{ref_t}형'을 유지 "
                   f"(전체 종목은 {v['all_rate'] * 100:.0f}%)" if ok else
                   f"의미 약함: 상위 20개 중 {v['top_rate'] * 100:.0f}%만 유지 (전체 {v['all_rate'] * 100:.0f}%)")
        pers = ", ".join(f"{LABELS[c]} {r:+.2f}" for c, r in v["persistence"].items())
        note = (f"검증 ({start:%Y-%m}~{cut:%Y-%m} 데이터만으로 고른 뒤 {cut:%Y-%m}~{end:%Y-%m} 확인): {verdict}. "
                f"항목별 유지율(앞·뒤 구간 순위 상관, 1에 가까울수록 꾸준한 성격): {pers}")
        print("\n뒤 구간 중앙값:\n" + v["medians"].round(3).to_string())
        print("\n" + note)

    # ---- HTML 리포트
    ref_meta = meta[meta["ticker"] == ref_t]
    ref_name = ref_meta["name"].iloc[0] if len(ref_meta) else ref_t
    figs = []
    for t in [ref_t] + list(res["ticker"].head(args.charts)):
        p_, d_ = cands[t]
        p5 = p_[p_.index > start - pd.Timedelta(days=30)]
        sw = patterns.find_swings(p5)
        nm = meta.set_index("ticker")["name"].get(t, t)
        figs.append((t, charts.price_rsi_chart(p5, None, sw, d_, f"{t} · {nm}")))
    explain = (
        "<ul class='find'>"
        "<li><b>실적 반응</b>: 발표 전날 종가 → 다음날 종가 등락폭의 중앙값 (방향 무관)</li>"
        "<li><b>평소 대비</b>: 실적 반응이 평범한 2일 등락보다 몇 배 큰가</li>"
        "<li><b>실적 3일 비중</b>: 1년 전체 출렁임(일간 수익률 제곱합) 중 실적 전날~다음날 3일이 차지하는 비율. "
        "실적이 특별하지 않다면 약 5%</li>"
        "<li><b>스윙-실적 일치</b>: 큰 고점·저점이 실적일 ±15일 안에 있는 비율. 분기 실적이면 무작위로도 약 34%</li>"
        "<li><b>실적 전 상승</b>: 발표 전 20거래일 동안 오른 비율 (평균 수익률)</li>"
        "<li><b>변동성</b>: 스윙(고점·저점)으로 볼 되돌림 기준 = 월 변동성 × 1.5. 클수록 파도가 큼</li>"
        "<li><b>차이</b>: 위 다섯 항목(평소 대비, 실적 3일 비중, 스윙-실적 일치, 실적 전 상승, 변동성)이 기준 종목과 "
        "얼마나 다른지. 0 = 같음, 1 ≈ 평균적으로 한 단계 다름</li></ul>"
    )
    body_cards = [
        ("기준 종목과 실적 주기 패턴이 비슷한 종목",
         '<div class="card">' + results_table(res, meta, ref_fp, args.top) +
         (f'<p class="note"><b>{html.escape(note)}</b></p>' if note else "") + explain + "</div>"),
    ]
    chart_html = "".join(
        f"<h3>{html.escape(t)}</h3><div class='card'>" + report.pio.to_html(
            f, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}) + "</div>"
        for t, f in figs)
    body_cards.append((f"차트 (기준 + 상위 {args.charts}개, 최근 {args.years}년 · 실적일 점선)", chart_html))
    title = f"{ref_t} 와 실적 주기 패턴이 비슷한 종목"
    intro = (f"{ref_name} · 데이터 기준일 {end:%Y-%m-%d} · 대상: {args.exchanges} 상장, 시총 ≥ ${args.min_mcap / 1e8:.0f}억, "
             f"거래대금 ≥ ${args.min_dollar_volume / 1e6:.0f}M, 주가 ≥ ${args.min_price:.0f} → 비교 {len(res)}개")
    out.write_text(report.render_html([], title, intro, body_cards, inline_js=args.inline_js), encoding="utf-8")
    print(f"\n리포트: {out}\nCSV: {out.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
