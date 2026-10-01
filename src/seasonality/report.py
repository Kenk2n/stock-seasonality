"""종목별 차트 패턴 리포트 (자동 요약 문장 + HTML)."""

from __future__ import annotations

import html
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import plotly.io as pio
from plotly.offline import get_plotlyjs, get_plotlyjs_version

from . import analysis, charts, earnings, patterns
from .analysis import MONTH_NAMES


@dataclass
class TickerReport:
    ticker: str
    name: str
    prices: pd.DataFrame
    earnings_dates: pd.DatetimeIndex
    years: int = 5
    findings: list[str] = field(default_factory=list)
    figures: list = field(default_factory=list)
    summary: pd.DataFrame | None = None


def _m(m: int) -> str:
    return MONTH_NAMES[m - 1]


def _months(ms) -> str:
    return ", ".join(_m(m) for m in ms)


def build_findings(prices: pd.DataFrame, earnings_dates: pd.DatetimeIndex, years: int = 5,
                   bench: pd.DataFrame | None = None) -> list[str]:
    """숫자를 사람이 읽는 문장으로."""
    close = prices["Close"]
    out: list[str] = []
    d = patterns.describe_pattern(prices, years)
    sw_all = patterns.find_swings(prices, d["threshold"])
    sw = d["swings"]
    th = d["threshold"]

    # 1) 월별 강약
    mean, win = d["mean"] * 100, d["win_rate"] * 100
    strong = [m for m in mean.sort_values(ascending=False).index if mean[m] > 0 and win[m] >= 60][:3]
    weak = [m for m in mean.sort_values().index if mean[m] < 0 and win[m] <= 40][:3]
    if strong:
        out.append("**강한 달** (최근 %d년): " % years + ", ".join(
            f"{_m(m)} 평균 {mean[m]:+.1f}% (상승 {win[m]:.0f}%)" for m in strong))
    if weak:
        out.append("**약한 달** (최근 %d년): " % years + ", ".join(
            f"{_m(m)} 평균 {mean[m]:+.1f}% (상승 {win[m]:.0f}%)" for m in weak))

    # 2) 고점·저점 시기
    lows, highs = d["low_months"], d["high_months"]
    if len(sw):
        top_l = [m for m in lows.sort_values(ascending=False).index if lows[m] >= 2][:3]
        top_h = [m for m in highs.sort_values(ascending=False).index if highs[m] >= 2][:3]
        s = f"**스윙** ({th * 100:.0f}% 이상 되돌림 기준): 최근 {years}년 저점 {int(lows.sum())}번, 고점 {int(highs.sum())}번."
        if top_l:
            s += f" 저점은 {_months(top_l)}에 자주,"
        if top_h:
            s += f" 고점은 {_months(top_h)}에 자주 나왔습니다."
        out.append(s.rstrip(","))
        cs = patterns.cycle_stats(sw_all[sw_all["date"] >= sw["date"].min()])
        if cs["n_up"] and cs["n_down"]:
            out.append(
                f"**사이클**: 저점→고점 보통 {cs['up_days']:.0f}거래일 동안 {cs['up_move'] * 100:+.0f}%, "
                f"고점→저점 보통 {cs['down_days']:.0f}거래일 동안 {cs['down_move'] * 100:+.0f}% (중앙값)."
            )

    # 3) RSI
    rm = d["rsi_mean"]
    out.append(
        f"**RSI 계절성**: 평균 RSI가 가장 낮은 달 {_m(int(rm.idxmin()))} ({rm.min():.0f}), "
        f"가장 높은 달 {_m(int(rm.idxmax()))} ({rm.max():.0f})."
    )

    # 4) 실적
    if len(earnings_dates):
        since = close.index[-1] - pd.DateOffset(years=years)
        ed = earnings_dates[(earnings_dates >= since)]
        reac = earnings.earnings_reactions(prices, ed)
        if len(reac):
            dist = earnings.swing_earnings_distance(sw, earnings_dates)
            near = (dist.abs() <= 15).mean() * 100 if len(dist) else np.nan
            out.append(
                f"**실적 발표**: 발표 다음날 평균 변동폭 ±{reac['reaction'].abs().mean() * 100:.1f}% "
                f"(오른 경우 {(reac['reaction'] > 0).mean() * 100:.0f}%). 발표 전 20일 평균 {reac['before'].mean() * 100:+.1f}% "
                f"(상승 {(reac['before'] > 0).mean() * 100:.0f}%), 발표 후 20일 평균 {reac['after'].mean() * 100:+.1f}%. "
                f"고점·저점의 {near:.0f}%가 실적일 ±15일 안에 있습니다."
            )
        nxt = earnings.next_earnings(earnings_dates, close.index[-1])
        if nxt is not None:
            out.append(f"**다음 실적 발표 예정**: {nxt:%Y-%m-%d}")

    # 5) 시장 대비
    if bench is not None:
        r = close.pct_change()
        rb = bench["Close"].pct_change()
        j = pd.concat([r, rb], axis=1, sort=True).dropna()
        j = j[j.index > close.index[-1] - pd.DateOffset(years=years)]
        if len(j) > 60:
            corr = j.corr().iloc[0, 1]
            beta = np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / j.iloc[:, 1].var()
            kind = "시장(소형주 지수)과 같이 움직이는 편" if corr >= 0.55 else "시장과 따로 움직이는 편 (종목 고유 요인이 큼)"
            out.append(f"**소형주 지수(IWM)와 관계**: 일간 상관 {corr:.2f}, 베타 {beta:.2f} → {kind}.")

    # 6) 현재 위치
    rsi_now = patterns.rsi(close).iloc[-1]
    if len(sw_all):
        last = sw_all.iloc[-1]
        since_move = close.iloc[-1] / last["price"] - 1
        out.append(
            f"**현재**: 마지막 확정 스윙은 {last['date']:%Y-%m-%d} {last['kind']} (${last['price']:.2f}), "
            f"이후 {since_move * 100:+.1f}%. 현재가 ${close.iloc[-1]:.2f}, RSI {rsi_now:.0f}."
        )
    return out


def build_report(ticker: str, prices: pd.DataFrame, earnings_dates: pd.DatetimeIndex, name: str = "",
                 years: int = 5, bench: pd.DataFrame | None = None) -> TickerReport:
    rep = TickerReport(ticker, name or ticker, prices, earnings_dates, years)
    rep.findings = build_findings(prices, earnings_dates, years, bench)
    rep.summary = patterns.window_summary(prices)
    swings = patterns.find_swings(prices)
    since = prices.index[-1] - pd.DateOffset(years=years)
    rets = analysis.last_n_years(analysis.monthly_returns(prices), years)
    stats = analysis.find_patterns(prices, lookback_years=years, min_years=min(years, 8))

    rep.figures = [
        ("가격 · RSI · 고점/저점 · 실적일 (버튼으로 기간 전환)",
         charts.price_rsi_chart(prices, pd.DateOffset(years=years), swings, earnings_dates,
                                f"{ticker} 가격과 RSI", buttons=True)),
        ("연도별 흐름 겹쳐보기",
         charts.yearly_paths_chart(analysis.yearly_paths(prices, years + 1), f"{ticker} 연도별 가격 흐름 (연초 = 100)")),
        ("월별 평균 수익률", charts.monthly_bar(stats, f"{ticker} 월별 평균 수익률 (최근 {years}년)")),
        ("월별 저점·고점", charts.swing_calendar_chart(patterns.swing_calendar(swings, since),
                                                f"{ticker} 월별 저점·고점 횟수 (최근 {years}년)")),
        ("매수 시점별 1·3개월 후 수익률", charts.forward_heatmap(patterns.forward_returns_by_month(prices, years=years),
                                                       f"{ticker} 매달 초에 샀다면 (최근 {years}년, 평균 · 상승확률)")),
        ("월별 RSI", charts.rsi_month_chart(patterns.rsi_by_month(prices, years), f"{ticker} 월별 평균 RSI (최근 {years}년)")),
        ("연도 × 월 수익률", charts.heatmap(analysis.year_month_table(rets), f"{ticker} 연도 × 월 수익률")),
    ]
    reac = earnings.earnings_reactions(prices, earnings_dates[earnings_dates >= since])
    if len(reac):
        rep.figures.append(("실적 반응", charts.earnings_reaction_chart(reac, f"{ticker} 실적 발표 반응 (전날 → 다음날)")))
    return rep


# ------------------------------------------------------------------ HTML
_CSS = """
:root{--bg:#f9f9f7;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--up:#e34948;--down:#2a78d6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0d0d0d;--card:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--line:#2c2c2a}}
:root[data-theme="dark"]{--bg:#0d0d0d;--card:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--line:#2c2c2a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Noto Sans KR",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:21px;margin:40px 0 8px}h3{font-size:15px;color:var(--ink2);margin:24px 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}
ul.find{margin:0;padding-left:20px}ul.find li{margin:4px 0}
nav a{color:var(--down);margin-right:14px;text-decoration:none}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:500}
.tbl{overflow-x:auto}.pos{color:var(--up)}.neg{color:var(--down)}
.note{color:var(--muted);font-size:13px}
.js-plotly-plot .main-svg text{fill:var(--ink2)!important}
"""


def _md(s: str) -> str:
    s = html.escape(s)
    parts = s.split("**")
    return "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))


def _pct(v, signed=True) -> str:
    if v is None or not np.isfinite(v):
        return ""
    cls = "pos" if v > 0 else "neg" if v < 0 else ""
    return f'<span class="{cls}">{v * 100:{"+" if signed else ""}.1f}%</span>'


def _summary_table(df: pd.DataFrame) -> str:
    rows = []
    for label, r in df.iterrows():
        rows.append(
            f"<tr><td>{label}</td><td>{_pct(r['수익률'])}</td><td>{_pct(r['최대낙폭'])}</td>"
            f"<td>{r['최고가 날짜']}</td><td>{r['최저가 날짜']}</td><td>{r['현재가 위치'] * 100:.0f}%</td>"
            f"<td>{r['RSI 평균']:.0f}</td><td>{r['RSI 최저']:.0f} ~ {r['RSI 최고']:.0f}</td>"
            f"<td>{r['과매수 일수']} / {r['과매도 일수']}</td><td>{r['스윙 수']}</td></tr>"
        )
    return (
        '<div class="tbl"><table><tr><th>기간</th><th>수익률</th><th>최대낙폭</th><th>최고가</th><th>최저가</th>'
        "<th>현재가 위치<br>(최저 0 ~ 최고 100)</th><th>RSI 평균</th><th>RSI 범위</th><th>과매수/과매도 일수</th><th>스윙</th></tr>"
        + "".join(rows) + "</table></div>"
    )


def similar_table(sim: pd.DataFrame, names: dict[str, str] | None = None, top: int = 25) -> str:
    names = names or {}
    rows = []
    for i, r in enumerate(sim.head(top).itertuples(), 1):
        rows.append(
            f"<tr><td>{i}. {html.escape(r.ticker)}</td><td style='text-align:left'>{html.escape(names.get(r.ticker, ''))}</td>"
            f"<td>{r.similarity:.2f}</td><td>{r.corr_monthly:.2f}</td><td>{r.corr_weekly:.2f}</td>"
            f"<td>{r.amplitude * 100:.1f}%</td>"
            f"<td>{'' if not np.isfinite(r.consistency) else f'{r.consistency * 100:.0f}%'}</td></tr>"
        )
    return (
        '<div class="tbl"><table><tr><th>종목</th><th style="text-align:left">이름</th><th>유사도</th><th>월별 상관</th>'
        "<th>주별 상관</th><th>계절 진폭</th><th>해마다 일치</th></tr>" + "".join(rows) + "</table></div>"
    )


def render_html(reports: list[TickerReport], title: str, intro: str = "",
                extra_sections: list[tuple[str, str]] = (), inline_js: bool = False) -> str:
    """inline_js=True 면 plotly.js(약 4.6MB)를 파일 안에 넣어 인터넷 없이도 열린다."""
    body = [f"<h1>{html.escape(title)}</h1>"]
    if intro:
        body.append(f'<p class="sub">{_md(intro)}</p>')
    nav = [f'<a href="#{r.ticker}">{html.escape(r.ticker)}</a>' for r in reports]
    nav += [f'<a href="#sec{i}">{html.escape(t)}</a>' for i, (t, _) in enumerate(extra_sections)]
    body.append("<nav>" + "".join(nav) + "</nav>")
    for r in reports:
        body.append(f'<h2 id="{r.ticker}">{html.escape(r.ticker)} · {html.escape(r.name)}</h2>')
        body.append('<div class="card"><ul class="find">' + "".join(f"<li>{_md(f)}</li>" for f in r.findings) + "</ul></div>")
        body.append("<h3>기간별 요약 (1개월 · 3개월 · 1년 · 3년 · 5년)</h3>")
        body.append(f'<div class="card">{_summary_table(r.summary)}</div>')
        for sub, fig in r.figures:
            body.append(f"<h3>{html.escape(sub)}</h3>")
            body.append('<div class="card">' + pio.to_html(fig, full_html=False, include_plotlyjs=False,
                                                         config={"displaylogo": False, "responsive": True}) + "</div>")
    for i, (t, content) in enumerate(extra_sections):
        body.append(f'<h2 id="sec{i}">{html.escape(t)}</h2>{content}')
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style>"
        + (f"<script>{get_plotlyjs()}</script>" if inline_js else
           f'<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{get_plotlyjs_version()}/plotly.min.js"></script>')
        + "</head><body><main>"
        + "".join(body) + "</main></body></html>"
    )
