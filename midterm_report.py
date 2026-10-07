"""미국 중간선거 전후 주가 리포트.

  python midterm_report.py                 # reports/midterm.html
  python midterm_report.py --inline-js     # 인터넷 없이 열리는 파일
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import charts, elections as el, report  # noqa: E402

INDEXES = {"S&P 500": "^GSPC", "나스닥": "^IXIC", "러셀2000(소형주)": "^RUT", "다우": "^DJI"}
BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "rgba(137,135,129,0.35)"


def load_index(sym: str, refresh: bool) -> pd.Series:
    path = Path("data/index") / f"{sym.strip('^')}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)["Close"]
    import yfinance as yf

    df = yf.download(sym, period="max", auto_adjust=True, progress=False, multi_level_index=False)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df["Close"]


def pct(v, signed=True):
    return "" if v is None or not np.isfinite(v) else f"{v * 100:{'+' if signed else ''}.1f}%"


def to_html(fig) -> str:
    return "<div class='card'>" + report.pio.to_html(fig, full_html=False, include_plotlyjs=False,
                                                     config={"displaylogo": False, "responsive": True}) + "</div>"


def main() -> int:
    p = argparse.ArgumentParser(description="중간선거 전후 주가 리포트")
    p.add_argument("--year", type=int, default=2026, help="다가오는 중간선거 연도")
    p.add_argument("--refresh", action="store_true", help="지수 데이터 새로 받기")
    p.add_argument("--out", default="reports/midterm.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    data = {n: load_index(s, args.refresh) for n, s in INDEXES.items()}
    sp = data["S&P 500"]
    vix = load_index("^VIX", args.refresh)
    e_next = el.election_day(args.year)
    k = el.trading_days_until(sp, e_next)  # 오늘이 선거 며칠 전인가
    mt = el.midterm_years(1950, args.year - 4)
    others = [y for y in range(1950, args.year) if y not in mt]
    w = el.window_returns(sp, mt)
    b = el.window_returns(sp, others)
    dd = el.drawdown_stats(sp, mt)
    vol = el.realized_vol(sp, mt)
    vx = el.vix_around(vix, mt)
    cols = list(el.WINDOWS)

    # ---- 올해 상황
    last = sp.index[-1]
    prev_close = sp[sp.index < f"{args.year}-01-01"].iloc[-1]
    ytd = sp[sp.index >= f"{args.year}-01-01"]
    ytd_ret = sp.iloc[-1] / prev_close - 1
    ytd_dd = (ytd / ytd.cummax() - 1).min()
    from_high = sp.iloc[-1] / ytd.max() - 1

    # ---- 오늘(선거 k일 전)에 샀다면
    pk = el.paths(sp, mt, pre=252, post=252, anchor=-k)
    fwd_rows = []
    for h, lab in [(0, "선거일까지"), (21, "선거 1개월 후"), (63, "선거 3개월 후"), (126, "선거 6개월 후"), (252, "선거 1년 후")]:
        v = pk.loc[h].dropna() / 100 - 1
        fwd_rows.append((lab, v.mean(), v.median(), int((v > 0).sum()), len(v), v.min(), v.idxmin(), v.max(), v.idxmax()))

    # ---- 올해처럼 '이미 오른' 중간선거 해 vs '빠진' 해
    ytd_e = dd["ytd_to_election"]
    strong = [y for y in ytd_e.index if ytd_e[y] > 0.05]
    weak = [y for y in ytd_e.index if ytd_e[y] <= 0.05]

    def grp(years):
        g = w.loc[years]
        return {c: (g[c].mean(), (g[c] > 0).mean()) for c in cols}

    gs, gw = grp(strong), grp(weak)

    # ---- 콘솔 요약
    print(f"다음 중간선거 {e_next:%Y-%m-%d} (데이터 {last:%Y-%m-%d} 기준 약 {k}거래일 전)")
    print(f"S&P 500 {args.year}: YTD {pct(ytd_ret)}, 연중 최대낙폭 {pct(ytd_dd)}, 고점 대비 {pct(from_high)}")
    for c in cols:
        print(f"  {c:16s} 중간선거 {pct(w[c].mean())} ({(w[c] > 0).mean():.0%}) | 다른 해 {pct(b[c].mean())} "
              f"({(b[c] > 0).mean():.0%}) | 이미 오른 해 {pct(gs[c][0])} | 빠졌던 해 {pct(gw[c][0])}")

    # ---- 차트 1: 경로 (오늘 = 100)
    fig = go.Figure()
    for y in pk.columns:
        fig.add_trace(go.Scatter(x=pk.index, y=pk[y], mode="lines", line=dict(color=GRAY, width=1), showlegend=False,
                                 hovertemplate=f"{y}년 %{{x}}일: %{{y:.1f}}<extra></extra>"))
    q25, q75 = pk.quantile(0.25, axis=1), pk.quantile(0.75, axis=1)
    fig.add_trace(go.Scatter(x=pk.index, y=q75, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=pk.index, y=q25, mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor="rgba(42,120,214,0.12)", name="가운데 50% 범위", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=pk.index, y=pk.median(axis=1), mode="lines", name="과거 19번 중앙값",
                             line=dict(color=BLUE, width=2.5), hovertemplate="중앙값 %{x}일: %{y:.1f}<extra></extra>"))
    i_now = len(sp) - 1
    seg = sp.iloc[i_now - (252 - k) : i_now + 1].to_numpy()
    fig.add_trace(go.Scatter(x=list(range(-252, -k + 1)), y=seg / seg[-1] * 100, mode="lines", name=f"{args.year}년 (지금까지)",
                             line=dict(color=ORANGE, width=2.5), hovertemplate=f"{args.year} %{{x}}일: %{{y:.1f}}<extra></extra>"))
    charts._layout(fig, f"S&P 500 중간선거 전후 1년 (오늘 = 선거 {k}거래일 전 = 100)", height=520)
    fig.add_vline(x=0, line=dict(color="#898781", width=1, dash="dot"))
    fig.add_annotation(x=0, y=1, yref="paper", text="선거일", showarrow=False, yanchor="bottom", font=dict(color="#898781"))
    fig.add_vline(x=-k, line=dict(color=ORANGE, width=1, dash="dot"))
    fig.add_hline(y=100, line=dict(color="#898781", width=1, dash="dot"))
    fig.update_xaxes(title_text="선거일 기준 거래일 (−252 = 1년 전, +252 = 1년 후)")
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1), hovermode="closest")
    lo, hi = np.nanpercentile(pk.to_numpy(), [1, 99])
    fig.update_yaxes(range=[lo - 3, hi + 3])

    # ---- 차트 2: 선거마다 선거일 → 6개월·1년
    fig2 = go.Figure()
    for c, color in [("선거일→6개월 후", BLUE), ("선거일→1년 후", ORANGE)]:
        fig2.add_trace(go.Bar(x=[str(y) for y in w.index], y=w[c] * 100, name=c.replace("선거일→", ""), marker_color=color,
                              hovertemplate="%{x}년 " + c + " %{y:+.1f}%<extra></extra>"))
    charts._layout(fig2, "선거일 종가 기준: 6개월 후 · 1년 후 수익률 (S&P 500)", height=380)
    fig2.update_layout(barmode="group", bargap=0.25, bargroupgap=0.05, barcornerradius=3,
                       legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1))
    fig2.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor="#898781")

    # ---- 차트 3: 구간별 평균, 중간선거 vs 다른 해
    fig3 = go.Figure()
    for lab, src, color in [("중간선거 해", w, BLUE), ("다른 해 (같은 날짜)", b, ORANGE)]:
        fig3.add_trace(go.Bar(x=cols, y=[src[c].mean() * 100 for c in cols], name=lab, marker_color=color,
                              customdata=[(src[c] > 0).mean() * 100 for c in cols],
                              hovertemplate="%{x}<br>평균 %{y:+.1f}% · 오른 비율 %{customdata:.0f}%<extra>" + lab + "</extra>"))
    charts._layout(fig3, "구간별 평균 수익률: 중간선거 해 vs 다른 해 (S&P 500, 1950~)", height=380)
    fig3.update_layout(barmode="group", bargap=0.25, bargroupgap=0.05, barcornerradius=3,
                       legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1))
    fig3.update_yaxes(ticksuffix="%")

    # ---- 표: 선거별 상세
    def vix_cell(y):
        return "" if y not in vx.index else f"{vx.loc[y, 'vix_before']:.0f} → {vx.loc[y, 'vix_after']:.0f}"

    rows = "".join(
        f"<tr><td>{y}</td><td>{pct(dd.loc[y, 'ytd_to_election'])}</td><td>{pct(dd.loc[y, 'max_drawdown'])}</td>"
        f"<td>{dd.loc[y, 'low_date']:%m-%d}</td>"
        + "".join(f"<td>{pct(w.loc[y, c])}</td>" for c in cols)
        + f"<td>{pct(vol.loc[y, 'vol_before'], False) if y in vol.index else ''} → "
          f"{pct(vol.loc[y, 'vol_after'], False) if y in vol.index else ''}</td>"
        + f"<td>{vix_cell(y)}</td></tr>"
        for y in w.index)
    avg_row = (f"<tr><td><b>평균</b></td><td>{pct(dd['ytd_to_election'].mean())}</td><td>{pct(dd['max_drawdown'].mean())}</td><td></td>"
               + "".join(f"<td><b>{pct(w[c].mean())}</b><br><span class='note'>{(w[c] > 0).mean():.0%} 상승</span></td>" for c in cols)
               + f"<td>{pct(vol['vol_before'].mean(), False)} → {pct(vol['vol_after'].mean(), False)}</td>"
               + f"<td>{vx['vix_before'].mean():.0f} → {vx['vix_after'].mean():.0f}</td></tr>")
    base_row = ("<tr><td>다른 해</td><td></td><td></td><td></td>"
                + "".join(f"<td>{pct(b[c].mean())}<br><span class='note'>{(b[c] > 0).mean():.0%} 상승</span></td>" for c in cols)
                + "<td></td><td></td></tr>")
    detail = ("<div class='card tbl'><table><tr><th>연도</th><th>연초→선거일</th><th>연중 최대낙폭</th><th>그해 저점</th>"
              + "".join(f"<th>{html.escape(c)}</th>" for c in cols)
              + "<th>변동성 (전3개월→후3개월)</th><th>VIX (전1개월→후1개월)</th></tr>"
              + rows + avg_row + base_row + "</table></div>")

    # ---- 표: 지수별
    idx_rows = ""
    for n, c in data.items():
        yrs = [y for y in el.midterm_years(1950, args.year - 4) if c.index[0] < el.election_day(y) - pd.Timedelta(days=200)]
        wi = el.window_returns(c, yrs)
        bi = el.window_returns(c, [y for y in range(max(1950, c.index[0].year + 1), args.year) if y not in yrs])
        idx_rows += (f"<tr><td>{n}<br><span class='note'>{yrs[0]}~{yrs[-1]}, {len(wi)}회</span></td>"
                     + "".join(f"<td>{pct(wi[col].mean())}<br><span class='note'>{(wi[col] > 0).mean():.0%} · 다른 해 "
                               f"{pct(bi[col].mean())}</span></td>" for col in cols) + "</tr>")
    idx_table = ("<div class='card tbl'><table><tr><th>지수</th>" + "".join(f"<th>{html.escape(c)}</th>" for c in cols)
                 + "</tr>" + idx_rows + "</table></div>")

    # ---- 표: 오늘 사면 / 올해 같은 해
    fwd_table = ("<div class='card tbl'><table><tr><th>보유 기간</th><th>평균</th><th>중앙값</th><th>오른 횟수</th>"
                 "<th>최악</th><th>최고</th></tr>"
                 + "".join(f"<tr><td>{lab}</td><td>{pct(m)}</td><td>{pct(md)}</td><td>{up}/{n}</td>"
                           f"<td>{pct(mn)} ({ymn})</td><td>{pct(mx)} ({ymx})</td></tr>"
                           for lab, m, md, up, n, mn, ymn, mx, ymx in fwd_rows) + "</table></div>")
    cond_table = ("<div class='card tbl'><table><tr><th>그룹</th>" + "".join(f"<th>{html.escape(c)}</th>" for c in cols) + "</tr>"
                  + f"<tr><td>선거 전에 이미 +5% 넘게 오른 해<br><span class='note'>{', '.join(map(str, strong))}</span></td>"
                  + "".join(f"<td>{pct(gs[c][0])}<br><span class='note'>{gs[c][1]:.0%} 상승</span></td>" for c in cols) + "</tr>"
                  + f"<tr><td>선거 전까지 부진했던 해<br><span class='note'>{', '.join(map(str, weak))}</span></td>"
                  + "".join(f"<td>{pct(gw[c][0])}<br><span class='note'>{gw[c][1]:.0%} 상승</span></td>" for c in cols) + "</tr>"
                  + "</table></div>")

    six, one = w["선거일→6개월 후"], w["선거일→1년 후"]
    findings = [
        f"**{args.year}년 중간선거는 {e_next:%m월 %d일}**, 데이터 기준일({last:%m월 %d일})로부터 약 {k}거래일 남았습니다.",
        f"**선거 후가 강했습니다**: 1950년 이후 19번 모두 S&P 500이 선거일 6개월 뒤(평균 {pct(six.mean())}), "
        f"1년 뒤(평균 {pct(one.mean())}) 올라 있었습니다. 같은 날짜 기준 다른 해는 6개월 {pct(b['선거일→6개월 후'].mean())}, "
        f"1년 {pct(b['선거일→1년 후'].mean())} ({(b['선거일→1년 후'] > 0).mean():.0%} 상승).",
        f"**선거 전엔 흔들렸습니다**: 중간선거 해에는 연중 평균 {pct(dd['max_drawdown'].mean())} 하락 구간이 있었고, "
        f"그해 저점에서 1년 뒤 평균 {pct(dd['low_to_1y'].mean())} (19번 모두 상승). 저점은 9~10월에 몰렸습니다 "
        f"({', '.join(str(y) for y in dd.index if dd.loc[y, 'low_date'].month in (9, 10))}).",
        f"**변동성은 선거 후 줄었습니다**: S&P 500 일간 변동성(연율) 선거 전 3개월 {pct(vol['vol_before'].mean(), False)} → "
        f"후 3개월 {pct(vol['vol_after'].mean(), False)} (19번 중 {(vol['vol_after'] < vol['vol_before']).sum()}번 감소), "
        f"VIX 선거 전 1개월 평균 {vx['vix_before'].mean():.0f} → 후 1개월 {vx['vix_after'].mean():.0f} (1990년 이후).",
        f"**{args.year}년은 이례적입니다**: S&P 500이 이미 연초 대비 {pct(ytd_ret)}이고 고점 대비 {pct(from_high)}, "
        f"연중 최대낙폭도 {pct(ytd_dd)}로 작습니다. 과거엔 '선거 전 하락 → 선거 후 반등'이 전형이었는데, 올해는 하락 없이 올라온 경우입니다.",
        f"**이미 오른 해만 보면**: 선거 전 +5% 넘게 올라 있던 해({len(strong)}번)도 선거 1년 뒤 평균 "
        f"{pct(gs['선거일→1년 후'][0])} ({gs['선거일→1년 후'][1]:.0%} 상승)였지만, 부진했던 해(평균 {pct(gw['선거일→1년 후'][0])})보다 "
        f"상승폭이 작았습니다.",
    ]
    caveats = (
        "<ul class='find'><li><b>표본이 19번뿐</b>입니다. 19번 연속이라도 미래를 보장하지 않습니다. "
        "(중간선거 주기는 잘 알려진 패턴이라 시장이 미리 반영할 수도 있습니다.)</li>"
        "<li>선거 결과(의회 다수당 변화)를 나누지 않았고, 금리·경기 등 다른 요인이 섞여 있습니다.</li>"
        "<li>지수 가격 기준(배당 제외)입니다. 1957년 이전 S&P 지수는 구성 종목 수가 달랐습니다.</li>"
        "<li>'다른 해'는 중간선거가 아닌 해의 11월 첫 화요일을 같은 기준일로 잡아 비교했습니다.</li></ul>"
    )
    sections = [
        ("핵심 요약", "<div class='card'><ul class='find'>" + "".join(f"<li>{report._md(f)}</li>" for f in findings) + "</ul></div>"),
        (f"지금(선거 {k}거래일 전) 샀다면: 과거 19번의 결과", fwd_table + to_html(fig)),
        ("구간별 평균 수익률", to_html(fig3)),
        ("선거마다", to_html(fig2) + detail),
        (f"{args.year}년처럼 선거 전에 이미 오른 해 vs 부진했던 해", cond_table),
        ("다른 지수 (나스닥·러셀2000·다우)", idx_table),
        ("주의할 점", "<div class='card'>" + caveats + "</div>"),
    ]
    intro = (f"S&P 500 1950~{args.year - 4}년 중간선거 {len(w)}번 · 데이터 기준일 {last:%Y-%m-%d} · "
             "선거일 = 11월 첫 월요일 다음 화요일, 장이 쉰 해는 직전 거래일 종가 기준")
    out.write_text(report.render_html([], f"미국 중간선거 전후 주가 ({args.year}년 {e_next:%m월 %d일} 선거)", intro, sections,
                                      inline_js=args.inline_js), encoding="utf-8")
    print(f"\n리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
