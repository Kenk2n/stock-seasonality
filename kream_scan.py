"""KREAM 리셀 차익 스캐너 (터미널).

  python kream_scan.py                                     # 예시 데이터
  python kream_scan.py --kream my_kream.csv --offers my_offers.csv
  python kream_scan.py --list-url "https://kream.co.kr/search?sort=popular" --limit 30   # 실험: KREAM 에서 직접
  python kream_scan.py --business --bundle 5               # 사업자, 5개씩 묶어 통관
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from kream_arb import analysis, costs, data, kream  # noqa: E402

RESULTS = Path(__file__).parent / "results"


def won(x) -> str:
    return "-" if pd.isna(x) else f"{x:,.0f}"


def pct(x) -> str:
    return "-" if pd.isna(x) else f"{x:+.0%}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kream", help="KREAM 시세 CSV (없으면 예시 데이터)")
    ap.add_argument("--offers", help="해외 몰 가격 CSV (없으면 예시 데이터)")
    ap.add_argument("--ids", help="실험: KREAM 상품 번호들 (쉼표 구분)")
    ap.add_argument("--list-url", help="실험: 이 KREAM 검색·랭킹 페이지에 보이는 상품을 가져옴")
    ap.add_argument("--limit", type=int, default=30, help="--list-url 에서 가져올 최대 상품 수")
    ap.add_argument("--basis", choices=list(analysis.SALE_BASIS), default="bid", help="KREAM 판매가 기준")
    ap.add_argument("--min-trades", type=float, default=100, help="최소 거래량")
    ap.add_argument("--min-premium", type=float, default=0.10, help="최소 프리미엄 (0.1 = 발매가 +10%%)")
    ap.add_argument("--top", type=int, default=30, help="후보 수")
    ap.add_argument("--business", action="store_true", help="일반과세 사업자 (부가세 환급·납부 반영)")
    ap.add_argument("--fee", type=float, default=costs.CostSettings.kream_fee_rate, help="KREAM 판매 수수료율")
    ap.add_argument("--broker", type=float, default=costs.CostSettings.broker_fee_krw, help="통관 수수료(원/건)")
    ap.add_argument("--bundle", type=int, default=1, help="한 번에 묶어 통관하는 수량")
    ap.add_argument("--usd", type=float, help="원/달러 환율 직접 지정")
    args = ap.parse_args()

    if args.ids or args.list_url:
        ids = args.ids.split(",") if args.ids else kream.discover_product_ids(args.list_url, args.limit)
        print(f"KREAM 상품 {len(ids)}개 가져오는 중...")
        items, errors = kream.fetch_products(ids)
        for e in errors:
            print("  실패", e)
        if items.empty:
            sys.exit("KREAM 에서 가져온 상품이 없습니다. CSV(--kream)로 넣어 주세요.")
        RESULTS.mkdir(exist_ok=True)
        items.drop(columns="model_key").to_csv(RESULTS / "kream_fetched.csv", index=False, encoding="utf-8-sig")
        print(f"  → {RESULTS / 'kream_fetched.csv'} (빈 값은 채워서 --kream 으로 다시 쓸 수 있음)")
    else:
        items = data.load_kream_csv(args.kream) if args.kream else data.sample_kream()
    offers = data.load_offers_csv(args.offers) if args.offers else data.sample_offers()
    if not (args.kream or args.offers or args.ids or args.list_url):
        print("※ 예시 데이터입니다 (가격은 가상). 실제 분석은 --kream / --offers 로 CSV 를 넣으세요.\n")

    fx, live = data.get_fx()
    if args.usd:
        fx["USD"] = args.usd
    print(f"환율 ({'실시간' if live else '기본값'}): " + ", ".join(f"{c} {v:,.2f}" for c, v in fx.items() if c != "KRW"))

    settings = costs.CostSettings(business=args.business, kream_fee_rate=args.fee,
                                  broker_fee_krw=args.broker, bundle_size=args.bundle)
    scored = analysis.score_items(items)
    cands = analysis.pick_candidates(scored, args.min_trades, args.min_premium, args.top)

    print(f"\n■ KREAM 후보 {len(cands)}개 (거래량 ≥ {args.min_trades:,.0f}, 프리미엄 ≥ {args.min_premium:.0%})")
    for _, r in cands.iterrows():
        size = f" [{r['size']}]" if r["size"] else ""
        print(f"  {r['score']:5.1f}점  {r['name'][:42]:42}{size:7} {r['model_no'] or '':14} "
              f"발매 {won(r['release_price']):>9}  시세 {won(r['last_price']):>9}  {pct(r['premium_pct']):>6}  "
              f"거래 {won(r['trades']):>6}  관심 {won(r['wishes']):>8}")

    best = analysis.best_offers(analysis.compare(cands, offers, fx, settings, args.basis))
    print(f"\n■ 해외 구매 손익 (판매가 기준: {analysis.SALE_BASIS[args.basis]})")
    if best.empty:
        print("  해외 가격이 입력된 후보가 없습니다.")
    for _, r in best.iterrows():
        size = f" [{r['size']}]" if r["size"] else ""
        mark = "✅" if r["profit"] > 0 else "❌"
        print(f"  {mark} {r['name'][:38]:38}{size:7} {r['store'][:16]:16} {r['currency']} {r['offer_price']:>9,.2f}"
              f"  도착원가 {won(r['landed_cost']):>9}  판매 {won(r['sale_price']):>9}  이익 {won(r['profit']):>9}"
              f" ({pct(r['margin'])})  손익분기 {won(r['breakeven_price'])}")

    missing = cands[~cands["model_key"].isin(offers["model_key"])]
    RESULTS.mkdir(exist_ok=True)
    if not best.empty:
        best.to_csv(RESULTS / "kream_best.csv", index=False, encoding="utf-8-sig")
    if not missing.empty:
        todo = missing[["name", "model_no", "size", "last_price"]].copy()
        for site in ["StockX", "GOAT", "Google쇼핑"]:
            todo[site] = [data.search_links(m, n)[site] for m, n in zip(missing["model_no"], missing["name"])]
        todo.to_csv(RESULTS / "kream_to_check.csv", index=False, encoding="utf-8-sig")
        print(f"\n해외 가격이 없는 후보 {len(missing)}개 → {RESULTS / 'kream_to_check.csv'} (검색 링크 포함)")


if __name__ == "__main__":
    main()
