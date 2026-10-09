import pandas as pd
import pytest

from kream_arb import analysis, costs, data, kream

FX = {"KRW": 1.0, "USD": 1400.0, "JPY": 9.0}


def test_parse_number_handles_korean_formats():
    assert data.parse_number("129,000원") == 129000
    assert data.parse_number("1.2만") == 12000
    assert data.parse_number("$110.50") == 110.5
    assert pd.isna(data.parse_number("-"))


def test_normalize_model_ignores_case_and_separators():
    assert data.normalize_model("dd1391-100") == data.normalize_model("DD1391 100") == "DD1391100"


def test_korean_headers_are_recognized():
    df = data.prepare_kream(pd.DataFrame({"상품명": ["A"], "모델번호": ["x-1"], "발매가": ["100,000"],
                                          "최근거래가": ["150,000원"], "거래량": [10], "관심수": ["1만"]}))
    assert df.loc[0, "release_price"] == 100000
    assert df.loc[0, "last_price"] == 150000
    assert df.loc[0, "wishes"] == 10000
    assert df.loc[0, "model_key"] == "X1"


def test_landed_cost_matches_hand_calculation():
    s = costs.CostSettings(card_fee_rate=0.0, broker_fee_krw=20000)
    c = costs.landed_cost(100, "USD", "sneakers", FX, s, local_shipping=0, intl_shipping_usd=20)
    cv = 120 * 1400  # 과세가격 = (물품 + 국제배송) × 환율
    duty = cv * 0.13
    vat = (cv + duty) * 0.10
    assert c["customs_value"] == pytest.approx(cv)
    assert c["duty"] == pytest.approx(duty)
    assert c["vat"] == pytest.approx(vat)
    assert c["total"] == pytest.approx(cv + duty + vat + 20000)


def test_excise_only_above_threshold_for_bags():
    s = costs.CostSettings(card_fee_rate=0.0, broker_fee_krw=0)
    cheap = costs.landed_cost(1000, "USD", "가방", FX, s, intl_shipping_usd=0)
    assert cheap["excise"] == 0
    dear = costs.landed_cost(3000, "USD", "가방", FX, s, intl_shipping_usd=0)
    base = 3000 * 1400 * 1.08
    assert dear["excise"] == pytest.approx((base - 2_000_000) * 0.2)
    assert dear["education_tax"] == pytest.approx(dear["excise"] * 0.3)
    shoes = costs.landed_cost(3000, "USD", "신발", FX, s, intl_shipping_usd=0)
    assert shoes["excise"] == 0


@pytest.mark.parametrize("business", [False, True])
def test_breakeven_price_gives_zero_profit(business):
    s = costs.CostSettings(business=business)
    c = costs.landed_cost(150, "USD", "신발", FX, s)
    be = costs.breakeven_price(c, s)
    assert costs.profit(be, c, s)["profit"] == pytest.approx(0, abs=1e-6)
    assert costs.profit(be * 1.1, c, s)["profit"] > 0


def test_business_mode_recovers_import_vat():
    biz = costs.CostSettings(business=True)
    c = costs.landed_cost(150, "USD", "신발", FX, biz)
    p = costs.profit(440000, c, biz)
    assert p["cost_basis"] == pytest.approx(c["total"] - c["vat"] - c["broker"] + c["broker"] / 1.1)
    assert p["proceeds"] == pytest.approx((440000 * (1 - biz.kream_fee_rate) - biz.kream_inbound_krw) / 1.1)


def test_sale_price_basis_and_fallback():
    row = pd.Series({"highest_bid": 100000.0, "last_price": 120000.0, "lowest_ask": 130000.0})
    assert analysis.sale_price(row, "bid") == 100000
    assert analysis.sale_price(row, "last") == 120000
    assert analysis.sale_price(row, "ask") == 129000
    row = pd.Series({"highest_bid": float("nan"), "last_price": 120000.0, "lowest_ask": 130000.0})
    assert analysis.sale_price(row, "bid") == 120000


def test_score_prefers_high_volume_and_premium():
    df = data.prepare_kream(pd.DataFrame({
        "name": ["hot", "cold"], "model_no": ["A", "B"], "release_price": [100, 100],
        "last_price": [200, 105], "trades": [1000, 10], "wishes": [5000, 50]}))
    scored = analysis.score_items(df)
    assert scored.loc[0, "name"] == "hot"
    assert scored.loc[0, "premium_pct"] == pytest.approx(1.0)
    cands = analysis.pick_candidates(scored, min_trades=100, min_premium_pct=0.1)
    assert list(cands["name"]) == ["hot"]


def test_compare_matches_sizes():
    k = data.prepare_kream(pd.DataFrame({
        "name": ["J1", "J1"], "model_no": ["DZ5485-612"] * 2, "category": ["신발"] * 2, "size": [260, 270],
        "highest_bid": [400000, 500000]}))
    o = data.prepare_offers(pd.DataFrame({
        "model_no": ["dz5485 612", "DZ5485-612"], "size": ["270", ""], "store": ["S270", "ANY"],
        "currency": ["USD", "USD"], "price": [150, 200]}))
    comp = analysis.compare(k, o, FX, costs.CostSettings())
    assert set(zip(comp["size"], comp["store"])) == {("260", "ANY"), ("270", "ANY"), ("270", "S270")}
    best = analysis.best_offers(comp)
    assert best.set_index("size").loc["270", "store"] == "S270"


def test_sample_data_runs_end_to_end():
    scored = analysis.score_items(data.sample_kream())
    comp = analysis.compare(scored, data.sample_offers(), data.FALLBACK_FX, costs.CostSettings())
    assert not comp.empty
    assert (comp["profit"] > 0).any() and (comp["profit"] < 0).any()


def test_parse_product_page_reads_json_ld_and_text():
    page = """<html><head><title>Nike Dunk Low | KREAM</title>
    <script type="application/ld+json">{"@type":"Product","name":"Nike Dunk Low Retro","sku":"DD1391-100",
     "brand":{"@type":"Brand","name":"Nike"},"offers":{"@type":"AggregateOffer","lowPrice":"138000"}}</script>
    </head><body><div>최근 거래가</div><div>135,000원</div><div>즉시 판매가 130,000원</div>
    <dl><dt>발매가</dt><dd>129,000원</dd><dt>모델번호</dt><dd>DD1391-100</dd></dl>
    <span>관심상품 9.8만</span></body></html>"""
    row = kream.parse_product_page(page, 123)
    assert row["name"] == "Nike Dunk Low Retro"
    assert row["brand"] == "Nike"
    assert row["model_no"] == "DD1391-100"
    assert row["lowest_ask"] == 138000
    assert row["last_price"] == 135000
    assert row["highest_bid"] == 130000
    assert row["release_price"] == 129000
    assert row["wishes"] == 98000
