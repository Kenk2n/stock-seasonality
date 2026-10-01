"""터미널용 계절성 스캐너.

예)
  python scan.py                               # 나스닥 100, 전체 기간
  python scan.py --years 10 --top 30           # 최근 10년 기준
  python scan.py --universe nasdaq --min-dollar-volume 5e6   # 나스닥 전체 (오래 걸림)
  python scan.py --tickers AAPL NVDA TSLA --excess           # 지정 종목, QQQ 대비 초과수익
  python scan.py --refresh                     # 캐시를 최신 날짜까지 업데이트
  python scan.py --demo                        # 네트워크 없이 가짜 데이터로 동작 확인
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import data, scanner, synthetic, universe  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="미국 주식 계절성 스캐너")
    p.add_argument("--universe", default="nasdaq100", help="nasdaq100 (기본) | nasdaq (나스닥 전체)")
    p.add_argument("--tickers", nargs="*", help="직접 지정한 종목 (유니버스 대신)")
    p.add_argument("--years", type=int, default=None, help="최근 N년만 분석 (기본: 전체 기간)")
    p.add_argument("--recent-years", type=int, default=5, help="패턴 유지 확인용 최근 기간 (기본 5)")
    p.add_argument("--min-years", type=int, default=8, help="최소 표본 연도 수 (기본 8)")
    p.add_argument("--alpha", type=float, default=0.05, help="유의수준 (기본 0.05)")
    p.add_argument("--min-hit-rate", type=float, default=0.7, help="최소 적중률 (기본 0.7)")
    p.add_argument("--strict", action="store_true", help="다중검정 보정(q값)까지 통과한 것만 표시")
    p.add_argument("--excess", action="store_true", help="벤치마크(QQQ) 대비 초과수익으로 분석")
    p.add_argument("--min-dollar-volume", type=float, default=0.0, help="최근 60일 일평균 거래대금 하한(달러)")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--refresh", action="store_true", help="캐시를 최신 날짜까지 증분 업데이트")
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--out", default="results/scan.csv", help="전체 결과 CSV 저장 경로")
    p.add_argument("--demo", action="store_true", help="가짜 데이터로 실행 (네트워크 불필요)")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    bench = None
    if args.demo:
        prices = synthetic.demo_universe()
        if args.excess:
            bench = synthetic.make_prices(seed=999)
    else:
        tickers = args.tickers or universe.get_universe(args.universe)
        print(f"종목 {len(tickers)}개 데이터 준비 중... (처음에는 시간이 걸립니다)")

        def progress(done: int, total: int) -> None:
            print(f"\r  다운로드 {done}/{total}", end="", flush=True)

        prices = data.update_many(tickers, refresh=args.refresh, batch_size=args.batch_size, on_progress=progress)
        print(f"\n  준비 완료: {len(prices)}개")
        if args.excess:
            bench = data.load_prices(universe.BENCHMARKS["nasdaq100"], refresh=args.refresh)

    result = scanner.scan(
        prices,
        bench_prices=bench,
        lookback_years=args.years,
        recent_years=args.recent_years,
        min_years=args.min_years,
        alpha=args.alpha,
        min_hit_rate=args.min_hit_rate,
        min_dollar_volume=args.min_dollar_volume,
    )
    if result.empty:
        print("분석할 데이터가 없습니다.")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False)

    top = scanner.top_patterns(result, top=args.top, use_q=args.strict, alpha=args.alpha)
    if top.empty:
        print("조건을 만족하는 계절성 패턴이 없습니다.")
    else:
        view = top.copy()
        for c in ("mean", "diff", "recent_mean"):
            view[c] = (view[c] * 100).map("{:+.2f}%".format)
        view["hit_rate"] = (view["hit_rate"] * 100).map("{:.0f}%".format)
        view["p"] = view["p"].map("{:.4f}".format)
        view["q"] = view["q"].map("{:.3f}".format)
        view["score"] = view["score"].map("{:.2f}".format)
        view.columns = ["종목", "월", "방향", "평균", "다른달대비", "적중률", "연도수", "p값", "q값", "최근평균", "점수"]
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(view.to_string(index=False))
    print(f"\n전체 결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
