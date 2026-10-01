"""Streamlit 웹 앱:  streamlit run app.py"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import analysis, charts, data, earnings, patterns, report, scanner, synthetic, universe  # noqa: E402

st.set_page_config(page_title="주식 계절성 탐색기", page_icon="📈", layout="wide")

PERIODS = {"1년": 1, "5년": 5, "10년": 10, "20년": 20, "전체": None}

# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.header("설정")
    demo = st.toggle("데모 데이터 사용", value=False, help="네트워크 없이 가짜 데이터로 화면을 확인합니다")
    period_label = st.radio("분석 기간", list(PERIODS), index=3, horizontal=True)
    lookback = PERIODS[period_label]
    excess = st.toggle("QQQ 대비 초과수익으로 보기", value=False,
                       help="시장 전체가 오른 영향을 빼고 그 종목만의 계절성을 봅니다")
    with st.expander("판정 기준"):
        min_years = st.slider("최소 연도 수", 3, 20, 8)
        alpha = st.select_slider("유의수준 (p값)", [0.01, 0.05, 0.1], value=0.05)
        min_hit = st.slider("최소 적중률", 0.5, 1.0, 0.7, 0.05)
        recent_years = st.slider("최근 유지 확인 기간(년)", 2, 10, 5)
    if lookback is not None and lookback < min_years:
        st.caption(f"⚠️ {period_label} 기간은 표본이 {lookback}개뿐이라 통계 판정(★)은 나오지 않습니다. "
                   "차트로 흐름만 참고하세요.")


@st.cache_data(show_spinner=False)
def get_prices(ticker: str, demo: bool) -> pd.DataFrame:
    if demo:
        demo_data = synthetic.demo_universe()
        if ticker in demo_data:
            return demo_data[ticker]
        return synthetic.make_prices(seed=sum(map(ord, ticker)))
    return data.load_prices(ticker)


@st.cache_data(show_spinner=False)
def get_bench(demo: bool) -> pd.DataFrame:
    if demo:
        return synthetic.make_prices(seed=999)
    return data.load_prices(universe.BENCHMARKS["nasdaq100"])


def pattern_kwargs() -> dict:
    return dict(lookback_years=lookback, recent_years=recent_years, min_years=min_years,
                alpha=alpha, min_hit_rate=min_hit)


@st.cache_data(show_spinner=False)
def get_earnings(ticker: str, demo: bool) -> pd.DatetimeIndex:
    if demo:
        return pd.DatetimeIndex([])
    return earnings.load_earnings_dates(ticker)


@st.cache_data(show_spinner=False)
def get_small_cap_bench(demo: bool) -> pd.DataFrame:
    if demo:
        return synthetic.make_prices(seed=998)
    return data.load_prices("IWM")


st.title("📈 주식 계절성 탐색기")
tab_pattern, tab_similar, tab_one, tab_scan = st.tabs(["차트 패턴", "유사 종목 찾기", "월별 계절성", "스캐너"])

# ---------------------------------------------------------------- 차트 패턴
with tab_pattern:
    c1, c2 = st.columns([2, 1])
    pt_ticker = c1.text_input("종목 코드", value="DEMO_NOV" if demo else "KRUS", key="pt_ticker").strip().upper()
    pt_years = c2.selectbox("계절성 분석 기간", [3, 5, 10], index=1, format_func=lambda y: f"최근 {y}년")
    if pt_ticker:
        try:
            with st.spinner(f"{pt_ticker} 불러오는 중..."):
                pt_prices = get_prices(pt_ticker, demo)
                pt_earn = get_earnings(pt_ticker, demo)
                pt_bench = get_small_cap_bench(demo)
        except Exception as e:
            st.error(f"데이터를 불러오지 못했습니다: {e}")
            st.stop()

        rep = report.build_report(pt_ticker, pt_prices, pt_earn, pt_ticker, pt_years, pt_bench)
        st.markdown("\n".join(f"- {f}" for f in rep.findings).replace("$", "\\$"))  # $ 를 수식으로 읽지 않게

        win_label = st.radio("차트 기간", list(patterns.WINDOWS), index=4, horizontal=True)
        swings = patterns.find_swings(pt_prices)
        st.plotly_chart(
            charts.price_rsi_chart(pt_prices, patterns.WINDOWS[win_label], swings, pt_earn,
                                   f"{pt_ticker} 가격과 RSI ({win_label})"),
            width="stretch",
        )
        st.subheader("기간별 요약")
        st.dataframe(
            rep.summary.style.format({"수익률": "{:+.1%}", "최대낙폭": "{:+.1%}", "현재가 위치": "{:.0%}",
                                      "RSI 평균": "{:.0f}", "RSI 최고": "{:.0f}", "RSI 최저": "{:.0f}"}),
            width="stretch",
        )
        for title, fig in rep.figures[1:]:
            st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- 유사 종목
with tab_similar:
    st.write("기준 종목과 **언제 오르고 언제 내리는지**(월별·주별 계절성 지문)가 비슷한 종목을 찾습니다.")
    c1, c2, c3 = st.columns([2, 1, 1])
    sim_ref = c1.text_input("기준 종목", value="DEMO_NOV" if demo else "HHH", key="sim_ref").strip().upper()
    sim_years = c2.selectbox("기간", [3, 5, 10], index=1, format_func=lambda y: f"최근 {y}년", key="sim_years")
    sim_uni = c3.selectbox("검색 범위", ["받아둔 종목 전체", "나스닥 100", "미국 전체 (오래 걸림)"] if not demo else ["데모 종목"])
    min_dv = st.slider("최소 일평균 거래대금 ($M)", 0, 50, 2)
    if st.button("유사 종목 찾기", type="primary"):
        if demo:
            cands = synthetic.demo_universe()
        elif sim_uni == "나스닥 100":
            cands = data.update_many(universe.NASDAQ100)
        elif sim_uni.startswith("미국 전체"):
            bar = st.progress(0.0, text="데이터 준비 중...")
            cands = data.update_many(universe.us_all(), batch_size=100, pause=0.5,
                                     on_progress=lambda d, t: bar.progress(d / max(t, 1), text=f"다운로드 {d}/{t}"))
            bar.empty()
        else:
            tickers = [f.stem for f in data.DEFAULT_CACHE_DIR.glob("*.parquet")]
            cands = data.update_many(tickers)
        ref_prices = get_prices(sim_ref, demo)
        with st.spinner(f"{len(cands)}개 종목 비교 중..."):
            sim = patterns.find_similar(ref_prices, {k: v for k, v in cands.items() if k != sim_ref},
                                        years=sim_years, min_dollar_volume=min_dv * 1e6)
        st.session_state["sim_result"] = (sim_ref, sim)

    if "sim_result" in st.session_state:
        ref, sim = st.session_state["sim_result"]
        st.subheader(f"{ref} 와 비슷한 종목 {min(len(sim), 50)}개")
        st.dataframe(
            sim.head(50).rename(columns={"ticker": "종목", "similarity": "유사도", "corr_monthly": "월별 상관",
                                         "corr_weekly": "주별 상관", "amplitude": "계절 진폭"})
            .style.format({"유사도": "{:.2f}", "월별 상관": "{:.2f}", "주별 상관": "{:.2f}", "계절 진폭": "{:.1%}"}),
            width="stretch", hide_index=True,
        )
        st.caption("종목 코드를 '차트 패턴' 탭에 넣으면 자세한 차트를 볼 수 있습니다.")

# ---------------------------------------------------------------- 종목 분석
with tab_one:
    default = "DEMO_NOV" if demo else "AAPL"
    ticker = st.text_input("종목 코드", value=default).strip().upper()
    if ticker:
        try:
            with st.spinner(f"{ticker} 데이터 불러오는 중..."):
                prices = get_prices(ticker, demo)
                bench = get_bench(demo) if excess else None
        except Exception as e:
            st.error(f"데이터를 불러오지 못했습니다: {e}")
            st.stop()

        stats = analysis.find_patterns(prices, bench_prices=bench, **pattern_kwargs())
        rets = analysis.monthly_returns(prices)
        if bench is not None:
            rets = analysis.excess_returns(rets, analysis.monthly_returns(bench))
        rets = analysis.last_n_years(rets, lookback)

        first, last = prices.index[0], prices.index[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric("데이터 기간", f"{first:%Y.%m} ~ {last:%Y.%m}")
        c2.metric("분석 연도 수", f"{rets.index.year.nunique()}년")
        sig = stats[stats["significant"]]
        c3.metric("유의미한 계절성", f"{len(sig)}개월")

        if not sig.empty:
            lines = [
                f"- **{r.month_name}**: {r.direction} 경향 — 평균 {r.mean * 100:+.2f}%, "
                f"다른 달 대비 {r.diff * 100:+.2f}%p, 적중률 {r.hit_rate * 100:.0f}% ({int(r.n)}년), p={r.p:.3f}"
                for r in sig.sort_values("score", ascending=False).itertuples()
            ]
            st.success("발견된 패턴\n\n" + "\n".join(lines))

        suffix = " (QQQ 대비 초과수익)" if excess else ""
        st.plotly_chart(charts.monthly_bar(stats, f"{ticker} 월별 평균 수익률{suffix} · ★ = 유의미"),
                        width="stretch")
        st.plotly_chart(charts.yearly_paths_chart(analysis.yearly_paths(prices, lookback),
                                                  f"{ticker} 연도별 가격 흐름 (연초 = 100)"),
                        width="stretch")
        st.plotly_chart(charts.win_rate_bar(stats, "월별 상승 확률"), width="stretch")
        st.plotly_chart(charts.heatmap(analysis.year_month_table(rets), f"연도 × 월 수익률{suffix}"),
                        width="stretch")

        with st.expander("월별 통계 표"):
            table = stats[["month_name", "direction", "mean", "median", "diff", "win_rate", "n", "p", "recent_mean", "significant"]]
            st.dataframe(
                table.style.format({"mean": "{:+.2%}", "median": "{:+.2%}", "diff": "{:+.2%}", "win_rate": "{:.0%}",
                                    "p": "{:.4f}", "recent_mean": "{:+.2%}"}),
                width="stretch",
            )

# ---------------------------------------------------------------- 스캐너
with tab_scan:
    st.write("여러 종목을 한꺼번에 분석해서 계절성이 강한 (종목, 월) 조합을 찾습니다.")
    uni = st.selectbox("대상", ["나스닥 100", "직접 입력"] if not demo else ["데모 종목"])
    custom = ""
    if uni == "직접 입력":
        custom = st.text_area("종목 코드 (쉼표/공백 구분)", "AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA")
    strict = st.checkbox("다중검정 보정(q값 < 유의수준)까지 통과한 것만", value=False,
                         help="종목이 많을수록 우연히 p<0.05 가 나오는 '가짜 패턴'이 늘어납니다")

    if st.button("스캔 시작", type="primary"):
        if demo:
            prices_map = synthetic.demo_universe()
        else:
            tickers = universe.NASDAQ100 if uni == "나스닥 100" else \
                [t.strip().upper() for t in custom.replace(",", " ").split() if t.strip()]
            bar = st.progress(0.0, text="데이터 준비 중...")
            prices_map = data.update_many(
                tickers, on_progress=lambda d, t: bar.progress(d / max(t, 1), text=f"다운로드 {d}/{t}")
            )
            bar.empty()
        bench = get_bench(demo) if excess else None
        with st.spinner("분석 중..."):
            result = scanner.scan(prices_map, bench_prices=bench, **pattern_kwargs())
        st.session_state["scan_result"] = result

    result = st.session_state.get("scan_result")
    if result is not None and not result.empty:
        top = scanner.top_patterns(result, top=100, use_q=strict, alpha=alpha)
        st.subheader(f"계절성 패턴 {len(top)}개")
        if top.empty:
            st.info("조건을 만족하는 패턴이 없습니다. 판정 기준을 완화해 보세요.")
        else:
            view = top.rename(columns={
                "ticker": "종목", "month_name": "월", "direction": "방향", "mean": "평균", "diff": "다른달대비",
                "hit_rate": "적중률", "n": "연도수", "p": "p값", "q": "q값", "recent_mean": "최근평균", "score": "점수",
            })
            st.dataframe(
                view.style.format({"평균": "{:+.2%}", "다른달대비": "{:+.2%}", "적중률": "{:.0%}", "p값": "{:.4f}",
                                   "q값": "{:.3f}", "최근평균": "{:+.2%}", "점수": "{:.2f}"}),
                width="stretch", hide_index=True,
            )
        st.download_button("전체 결과 CSV 다운로드", result.to_csv(index=False).encode("utf-8-sig"),
                           "seasonality_scan.csv", "text/csv")
