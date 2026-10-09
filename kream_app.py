"""KREAM 리셀 차익 분석기 (웹 앱):  streamlit run kream_app.py"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from kream_arb import analysis, costs, data, kream  # noqa: E402

st.set_page_config(page_title="KREAM 리셀 차익 분석기", page_icon="👟", layout="wide")

SERIES = "#2a78d6"
GOOD, BAD = "#0ca30c", "#d03b3b"
WON = st.column_config.NumberColumn(format="%,.0f")
PCT = st.column_config.NumberColumn(format="percent")


@st.cache_data(ttl=3600, show_spinner=False)
def live_fx() -> tuple[dict, bool]:
    return data.get_fx()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_kream(ids: tuple) -> tuple[pd.DataFrame, list]:
    return kream.fetch_products(list(ids))


# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.header("1. KREAM 데이터")
    source = st.radio("가져오기", ["예시 데이터", "CSV 업로드", "KREAM 에서 직접 (실험)"], index=0)
    kream_df = pd.DataFrame()
    if source == "예시 데이터":
        kream_df = data.sample_kream()
        st.caption("예시 데이터의 가격은 가상입니다.")
    elif source == "CSV 업로드":
        up = st.file_uploader("KREAM 시세 CSV", type="csv", key="kream_csv")
        if up:
            kream_df = data.load_kream_csv(up)
    else:
        st.caption("상품 페이지를 읽어 오는 기능이라 사이트가 바뀌면 일부 값이 빌 수 있습니다. "
                   "이용약관을 확인하고 적은 양만 쓰세요.")
        list_url = st.text_input("검색·랭킹 페이지 주소", "https://kream.co.kr/search?sort=popular")
        ids_text = st.text_area("또는 상품 번호 (kream.co.kr/products/번호)", placeholder="12345, 67890")
        limit = st.number_input("최대 상품 수", 5, 100, 30, 5)
        if st.button("가져오기", type="primary"):
            try:
                ids = [i.strip() for i in ids_text.replace("\n", ",").split(",") if i.strip()] \
                    or kream.discover_product_ids(list_url, int(limit))
                with st.spinner(f"{len(ids)}개 상품 읽는 중 (상품당 약 1.5초)..."):
                    st.session_state["live"] = fetch_kream(tuple(ids))
            except Exception as e:
                st.error(f"가져오기 실패: {e}")
        if "live" in st.session_state:
            kream_df, errors = st.session_state["live"]
            if errors:
                st.warning(f"{len(errors)}개 실패: " + "; ".join(errors[:3]))

    st.header("2. 해외 몰 가격")
    offers_up = st.file_uploader("해외 가격 CSV (선택)", type="csv", key="offers_csv")

    st.header("3. 판매·비용 가정")
    basis = st.radio("KREAM 판매가 기준", list(analysis.SALE_BASIS),
                     format_func=analysis.SALE_BASIS.get, index=0)
    business = st.toggle("일반과세 사업자", value=False,
                         help="수입 부가세를 돌려받고 판매가의 부가세(1/11)를 냅니다")
    defaults = costs.CostSettings()
    fee = st.number_input("KREAM 판매 수수료 (%)", 0.0, 20.0, defaults.kream_fee_rate * 100, 0.1,
                          help="부가세 포함. 판매자 등급마다 다르니 앱에서 확인하세요") / 100
    broker = st.number_input("통관 수수료 (원/건)", 0, 100_000, int(defaults.broker_fee_krw), 1_000,
                             help="정식 수입신고 관세사·배대지 통관 수수료")
    bundle = st.number_input("묶음 통관 수량", 1, 50, 1, help="여러 개를 한 번에 통관하면 수수료를 나눠 냅니다")
    with st.expander("세부 비용"):
        card = st.number_input("해외 결제 수수료 (%)", 0.0, 5.0, defaults.card_fee_rate * 100, 0.1) / 100
        sales_tax = st.number_input("현지 판매세 (%)", 0.0, 15.0, 0.0, 0.5,
                                    help="미국 무세주(델라웨어·오리건) 배대지면 0") / 100
        inbound = st.number_input("KREAM 발송 택배비 (원)", 0, 20_000, int(defaults.kream_inbound_krw), 500)
        rates = st.data_editor(
            pd.DataFrame({"관세율(%)": {c: v * 100 for c, v in costs.DEFAULT_DUTY.items()},
                          "국제배송비($)": costs.DEFAULT_INTL_SHIPPING_USD}),
            width="stretch", key="rates")

    fx, fx_live = live_fx()
    with st.expander(f"환율 ({'실시간' if fx_live else '기본값 — 직접 확인'})", expanded=not fx_live):
        fx = {c: st.number_input(f"{c} → 원", 0.0, 100_000.0, float(v), key=f"fx_{c}")
              for c, v in fx.items() if c != "KRW"} | {"KRW": 1.0}

    st.header("4. 후보 기준")
    min_trades = st.number_input("최소 거래량", 0, 100_000, 100, 50)
    min_premium = st.slider("최소 프리미엄 (발매가 대비)", -0.5, 2.0, 0.10, 0.05, format="%.2f")
    top = st.slider("후보 수", 5, 200, 30, 5)
    with st.expander("점수 가중치"):
        w = {"trades": st.slider("거래량", 0.0, 1.0, analysis.DEFAULT_WEIGHTS["trades"], 0.05),
             "premium": st.slider("프리미엄", 0.0, 1.0, analysis.DEFAULT_WEIGHTS["premium"], 0.05),
             "wishes": st.slider("관심수", 0.0, 1.0, analysis.DEFAULT_WEIGHTS["wishes"], 0.05)}

settings = costs.CostSettings(
    business=business, kream_fee_rate=fee, kream_inbound_krw=inbound, card_fee_rate=card,
    broker_fee_krw=broker, bundle_size=int(bundle), sales_tax_rate=sales_tax,
    duty_rates=(rates["관세율(%)"] / 100).to_dict(), intl_shipping_usd=rates["국제배송비($)"].to_dict())

st.title("👟 KREAM 리셀 차익 분석기")
st.caption("KREAM 에서 거래량·프리미엄·관심이 높은 상품을 고르고, 해외 몰에서 사서 정식 수입(관세·부가세 납부) 후 "
           "KREAM 에 팔았을 때 남는 돈을 계산합니다.")

if kream_df.empty:
    st.info("왼쪽에서 KREAM 데이터를 넣어 주세요. CSV 양식은 **사용법** 탭에 있습니다.")
    st.stop()

scored = analysis.score_items(kream_df, w)
cands = analysis.pick_candidates(scored, min_trades, min_premium, top)

if offers_up:
    base_offers = data.load_offers_csv(offers_up)
elif source == "예시 데이터":
    base_offers = data.sample_offers()
else:
    base_offers = data.prepare_offers(pd.DataFrame(columns=list(data.OFFER_COLUMNS)))

tab_pick, tab_offers, tab_profit, tab_help = st.tabs(["🔥 KREAM 후보", "🌏 해외 가격", "💰 손익", "ℹ️ 사용법"])

# ---------------------------------------------------------------- 후보
with tab_pick:
    c1, c2, c3 = st.columns(3)
    c1.metric("전체 상품(사이즈)", f"{len(scored):,}")
    c2.metric("기준 통과 후보", f"{len(cands):,}")
    c3.metric("후보 평균 프리미엄", f"{cands['premium_pct'].mean():+.0%}" if cands["premium_pct"].notna().any() else "-")

    plot = scored.dropna(subset=["premium_pct", "trades"]).copy()
    if not plot.empty:
        plot["표시"] = plot["name"] + plot["size"].map(lambda s: f" [{s}]" if s else "")
        fig = px.scatter(plot, x="trades", y="premium_pct", size=plot["wishes"].fillna(1).clip(lower=1),
                         size_max=28, hover_name="표시", log_x=True,
                         hover_data={"trades": ":,.0f", "premium_pct": ":+.0%", "wishes": ":,.0f", "score": ":.1f"},
                         labels={"trades": "거래량 (로그)", "premium_pct": "프리미엄 (발매가 대비)",
                                 "wishes": "관심수", "score": "점수"})
        fig.update_traces(marker=dict(color=SERIES, line=dict(width=2, color="white")))
        fig.add_hline(y=min_premium, line_dash="dot", line_color="#898781",
                      annotation_text=f"최소 프리미엄 {min_premium:.0%}", annotation_position="bottom left")
        if min_trades > 0:  # 로그 축에서는 add_vline 위치가 어긋나서 선을 직접 그림
            ys = [plot["premium_pct"].min(), plot["premium_pct"].max()]
            fig.add_scatter(x=[min_trades, min_trades], y=ys, mode="lines+text", showlegend=False,
                            hoverinfo="skip", line=dict(dash="dot", color="#898781", width=1),
                            text=["", f"최소 거래량 {min_trades:,}"], textposition="top right",
                            textfont=dict(color="#898781"))
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), yaxis_tickformat="+.0%",
                          title="오른쪽 위일수록 잘 팔리고 비싸게 팔림 · 원 크기 = 관심수")
        st.plotly_chart(fig, width="stretch")

    st.subheader("후보 목록")
    st.dataframe(
        cands[["score", "name", "size", "brand", "model_no", "release_price", "last_price", "lowest_ask",
               "highest_bid", "premium_pct", "trades", "wishes"]],
        hide_index=True, width="stretch",
        column_config={"score": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%.0f"),
                       "name": "상품", "size": "사이즈", "brand": "브랜드", "model_no": "모델번호",
                       "release_price": st.column_config.NumberColumn("발매가", format="%,.0f"),
                       "last_price": st.column_config.NumberColumn("최근 거래가", format="%,.0f"),
                       "lowest_ask": st.column_config.NumberColumn("즉시구매가", format="%,.0f"),
                       "highest_bid": st.column_config.NumberColumn("즉시판매가", format="%,.0f"),
                       "premium_pct": st.column_config.NumberColumn("프리미엄", format="percent"),
                       "trades": st.column_config.NumberColumn("거래량", format="%,.0f"),
                       "wishes": st.column_config.NumberColumn("관심", format="%,.0f")})
    st.caption("점수 = 거래량·프리미엄·관심수 각각의 순위(백분위)를 가중 평균한 0~100점. 값이 없는 항목은 중간(50)으로 칩니다.")

# ---------------------------------------------------------------- 해외 가격
with tab_offers:
    st.markdown("후보의 **모델번호**로 해외 몰을 검색해 가격을 표에 적으세요. 사이즈를 비우면 모든 사이즈에 적용됩니다. "
                "통화는 USD·EUR·GBP·JPY·CNY·HKD.")
    with st.expander("후보별 검색 링크", expanded=base_offers.empty):
        links = pd.DataFrame([{"상품": r["name"], "사이즈": r["size"], "모델번호": r["model_no"],
                               **data.search_links(r["model_no"], r["name"])}
                              for _, r in cands.drop_duplicates(["model_key"]).iterrows()])
        link_cols = {s: st.column_config.LinkColumn(s, display_text="검색") for s in data.SEARCH_SITES}
        st.dataframe(links, hide_index=True, width="stretch", column_config=link_cols)

    missing = cands[~cands["model_key"].isin(base_offers["model_key"])].drop_duplicates("model_key")
    blank = pd.DataFrame({"model_no": missing["model_no"], "size": "", "store": "", "currency": "USD",
                          "price": None, "local_shipping": 0.0, "intl_shipping_usd": None, "url": ""})
    editable = pd.concat([base_offers[list(data.OFFER_COLUMNS)], blank], ignore_index=True)
    for col in data.OFFER_NUMERIC:
        editable[col] = pd.to_numeric(editable[col], errors="coerce")
    editable["size"] = editable["size"].astype(str)
    edited = st.data_editor(
        editable, num_rows="dynamic", width="stretch", hide_index=True, key="offers_editor",
        column_config={"model_no": "모델번호", "size": "사이즈", "store": "쇼핑몰",
                       "currency": st.column_config.SelectboxColumn("통화", options=[c for c in fx if c != "KRW"]),
                       "price": st.column_config.NumberColumn("가격 (현지 통화)", format="%.2f"),
                       "local_shipping": st.column_config.NumberColumn("현지 배송비", format="%.2f"),
                       "intl_shipping_usd": st.column_config.NumberColumn("국제배송비 $ (비우면 기본값)", format="%.2f"),
                       "url": st.column_config.LinkColumn("링크")})
    offers = data.prepare_offers(edited)
    st.download_button("해외 가격 CSV 저장", offers[list(data.OFFER_COLUMNS)].to_csv(index=False).encode("utf-8-sig"),
                       "overseas_prices.csv", "text/csv")

# ---------------------------------------------------------------- 손익
with tab_profit:
    comp = analysis.compare(cands, offers, fx, settings, basis)
    best = analysis.best_offers(comp)
    if best.empty:
        st.info("해외 가격이 입력된 후보가 없습니다. **해외 가격** 탭에서 가격을 넣어 주세요.")
    else:
        win = best[best["profit"] > 0]
        c1, c2, c3 = st.columns(3)
        c1.metric("이익 나는 상품", f"{len(win)} / {len(best)}")
        c2.metric("최고 이익", f"{best['profit'].max():,.0f}원")
        c3.metric("이익 상품 평균 마진", f"{win['margin'].mean():.1%}" if len(win) else "-")

        chart = best.copy()
        chart["표시"] = chart["name"].str.slice(0, 30) + chart["size"].map(lambda s: f" [{s}]" if s else "") \
            + " · " + chart["store"]
        chart["결과"] = chart["profit"].map(lambda p: "✅ 이익" if p > 0 else "❌ 손해")
        fig = px.bar(chart.sort_values("profit"), x="profit", y="표시", orientation="h", color="결과",
                     color_discrete_map={"✅ 이익": GOOD, "❌ 손해": BAD},
                     hover_data={"profit": ":,.0f", "margin": ":+.1%", "landed_cost": ":,.0f",
                                 "sale_price": ":,.0f", "breakeven_price": ":,.0f", "표시": False},
                     labels={"profit": "개당 순이익 (원)", "표시": "", "margin": "마진", "landed_cost": "도착 원가",
                             "sale_price": "판매가", "breakeven_price": "손익분기 판매가", "결과": ""})
        fig.update_traces(marker_line_width=0)
        fig.update_layout(height=max(300, 34 * len(chart) + 80), margin=dict(l=10, r=10, t=30, b=10),
                          xaxis_tickformat=",.0f", bargap=0.35, legend_title_text="")
        st.plotly_chart(fig, width="stretch")

        cols = {"name": "상품", "size": "사이즈", "store": "구매처", "currency": "통화",
                "offer_price": st.column_config.NumberColumn("해외가", format="%,.2f"),
                "landed_cost": st.column_config.NumberColumn("도착 원가", format="%,.0f"),
                "tax": st.column_config.NumberColumn("관부가세", format="%,.0f"),
                "sale_price": st.column_config.NumberColumn("KREAM 판매가", format="%,.0f"),
                "kream_fee": st.column_config.NumberColumn("수수료", format="%,.0f"),
                "profit": st.column_config.NumberColumn("순이익", format="%,.0f"),
                "margin": st.column_config.NumberColumn("마진", format="percent"),
                "roi": st.column_config.NumberColumn("ROI", format="percent"),
                "breakeven_price": st.column_config.NumberColumn("손익분기 판매가", format="%,.0f"),
                "safety": st.column_config.NumberColumn("하락 여유", format="percent",
                                                        help="판매가가 이만큼 떨어져도 손해가 아님"),
                "url": st.column_config.LinkColumn("링크", display_text="열기")}
        st.subheader("상품별 최적 구매처")
        st.dataframe(best[list(cols)], hide_index=True, width="stretch", column_config=cols)
        st.download_button("결과 CSV 저장", best.to_csv(index=False).encode("utf-8-sig"), "kream_best.csv", "text/csv")

        with st.expander("모든 구매처 비교"):
            st.dataframe(comp[list(cols)], hide_index=True, width="stretch", column_config=cols)

        st.subheader("비용 내역 자세히")
        labels = [f"{r['name']} {r['size']} · {r['store']}" for _, r in comp.iterrows()]
        pick = st.selectbox("상품·구매처", range(len(comp)), format_func=lambda i: labels[i])
        r = comp.iloc[pick]
        o = offers.loc[r["offer_idx"]]
        cost = costs.landed_cost(o["price"], o["currency"], r["category"], fx, settings, o["local_shipping"],
                                 o["intl_shipping_usd"] if pd.notna(o["intl_shipping_usd"]) else None)
        p = costs.profit(r["sale_price"], cost, settings)
        lines = [("물품가 (+현지 판매세)", cost["goods"]), ("현지 배송비", cost["local_shipping"]),
                 ("국제 배송비", cost["intl_shipping"]), ("해외 결제 수수료", cost["card_fee"]),
                 ("관세", cost["duty"]), ("개별소비세", cost["excise"]), ("교육세", cost["education_tax"]),
                 ("부가세", cost["vat"]), ("통관 수수료", cost["broker"]), ("= 도착 원가", cost["total"]),
                 ("KREAM 판매가", p["sale_price"]), ("− 판매 수수료", p["kream_fee"]),
                 ("− 발송 택배비", settings.kream_inbound_krw)]
        if business:
            lines += [("사업자: 부가세 뺀 판매 수령액", p["proceeds"]), ("사업자: 부가세 환급 후 원가", p["cost_basis"])]
        lines += [("= 순이익", p["profit"]), ("손익분기 판매가", p["breakeven_price"])]
        lines = [(k, v) for k, v in lines if v or k.startswith("=")]
        st.dataframe(pd.DataFrame(lines, columns=["항목", "원"]),
                     hide_index=True, width="stretch",
                     column_config={"원": st.column_config.NumberColumn(format="%,.0f")})
        st.caption(f"과세가격 {cost['customs_value']:,.0f}원 = (물품가 + 현지 배송비 + 국제 배송비) × 환율. "
                   f"관세율 {settings.duty_rates.get(r['category'], 0):.0%} ({r['category']}).")

# ---------------------------------------------------------------- 사용법
with tab_help:
    st.markdown("""
### 흐름
1. **KREAM 후보** — 거래량(잘 팔림)·프리미엄(발매가 대비 웃돈)·관심수(인기)로 점수를 매겨 후보를 고릅니다.
2. **해외 가격** — 후보 모델번호로 StockX·GOAT·나이키 US·SNKRDUNK 등을 검색해 가격을 적습니다.
3. **손익** — 해외가 → 배송 → 관세·부가세 → KREAM 수수료까지 넣어 개당 순이익과 손익분기 판매가를 계산합니다.

### ⚠️ 꼭 알아둘 것
- **면세(목록통관)로 들여와 되팔면 관세법 위반(밀수입)입니다.** 미국 $200 / 그 외 $150 이하 자가사용 면세는
  판매 목적이면 쓸 수 없어서, 이 앱은 항상 관세·부가세를 낸 **정식 수입신고**를 가정합니다.
- 반복해서 사고팔면 사업자 등록·종합소득세 신고 대상이 될 수 있습니다.
- KREAM 수수료는 판매자 등급·이벤트마다 다르고, 시세는 배송 기간(2~4주) 동안 바뀝니다.
  **하락 여유**(판매가가 얼마나 떨어져도 버티는지)를 함께 보세요. 검수 불합격·사이즈 표기 차이(US → mm)도 위험입니다.
- 관세율 기본값: 신발·의류 13%, 그 외 8%. 품목·원산지(FTA)에 따라 다르니 관세청 HS 코드로 확인하세요.
  가방·시계는 과세가격+관세가 200만원을 넘으면 넘는 부분에 개별소비세 20%(+교육세)가 붙습니다.

### CSV 양식
KREAM 시세 (머리글은 영문 또는 한글):
""")
    st.code("product_id,name,brand,model_no,category,size,release_price,last_price,lowest_ask,highest_bid,trades,wishes\n"
            "상품번호,상품명,브랜드,모델번호,카테고리,사이즈,발매가,최근거래가,즉시구매가,즉시판매가,거래량,관심수", "text")
    st.markdown("해외 가격:")
    st.code("model_no,size,store,currency,price,local_shipping,intl_shipping_usd,url", "text")
    st.markdown("`examples/` 폴더의 예시 파일을 복사해서 쓰면 됩니다. "
                "카테고리는 신발·의류·가방·시계·액세서리·테크·컬렉터블·기타 (영문 sneakers, apparel 등도 인식).")
