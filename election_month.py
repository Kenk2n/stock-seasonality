"""앞으로의 미국 선거일별 '선거 전후 1개월' 주가 예상 범위 (과거 통계 기반).

  python election_month.py                  # reports/election_month.html
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import charts, elections as el, report  # noqa: E402

BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#898781"
W = {"선거 전 1개월": (-21, 0), "선거 후 1개월": (0, 21), "전후 2개월": (-21, 21)}


def kind(y: int) -> str:
    return "대선" if y % 4 == 0 else "중간선거"


def pct(v):
    return "" if v is None or not np.isfinite(v) else f"{v * 100:+.1f}%"


def stats(v: pd.Series) -> dict:
    v = v.dropna()
    return {"n": len(v), "mean": v.mean(), "med": v.median(), "up": (v > 0).mean(),
            "p10": v.quantile(0.1), "p90": v.quantile(0.9), "min": v.min(), "miny": v.idxmin(),
            "max": v.max(), "maxy": v.idxmax()}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--upcoming", default="2026,2028,2030,2032")
    p.add_argument("--near-high", type=float, default=-0.05, help="'고점 근처' 기준: 52주 고점 대비 이 값 이내")
    p.add_argument("--out", default="reports/election_month.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    sp = pd.read_parquet("data/index/GSPC.parquet")["Close"]
    last = sp.index[-1]
    past = [y for y in range(1950, last.year + 1, 2) if el.election_day(y) + pd.Timedelta(days=40) < last]
    w = el.window_returns(sp, past, W)
    w["kind"] = [kind(y) for y in w.index]
    # 선거 21거래일 전 시점에 52주 고점에서 얼마나 떨어져 있었나
    fh = {}
    for y in w.index:
        j = el.base_index(sp, el.election_day(y)) - 21
        fh[y] = sp.iloc[j] / sp.iloc[j - 252 : j + 1].max() - 1
    w["from_high"] = pd.Series(fh)
    now_fh = sp.iloc[-1] / sp.iloc[-253:].max() - 1

    # ---- 예상표
    up_rows = []
    for y in [int(x) for x in args.upcoming.split(",")]:
        e = el.election_day(y)
        g = w[w["kind"] == kind(y)]
        cond = None
        if y == last.year and e > last:  # 올해 선거: 현재 시장 상태로 조건부
            near = now_fh > args.near_high
            cond = g[(g["from_high"] > args.near_high) == near]
        up_rows.append((y, e, kind(y), {k: stats(g[k]) for k in W}, None if cond is None else {k: stats(cond[k]) for k in W},
                        None if cond is None else list(cond.index)))

    def cell(s):
        return (f"<b>{pct(s['med'])}</b> (평균 {pct(s['mean'])})<br><span class='note'>오른 비율 {s['up']:.0%} · "
                f"10명 중 8명 범위 {pct(s['p10'])} ~ {pct(s['p90'])}</span>")

    rows = ""
    for y, e, kd, s, c, cy in up_rows:
        window = f"{(e - pd.tseries.offsets.BDay(21)):%m/%d} ~ {(e + pd.tseries.offsets.BDay(21)):%m/%d}"
        rows += (f"<tr><td><b>{e:%Y-%m-%d}</b><br>{kd}<br><span class='note'>대략 {window}</span></td>"
                 + "".join(f"<td>{cell(s[k])}</td>" for k in W) + f"<td>{s['선거 전 1개월']['n']}번</td></tr>")
        if c:
            rows += (f"<tr><td style='padding-left:20px'>↳ 올해처럼 52주 고점 근처에서 맞은 {kd}<br>"
                     f"<span class='note'>{', '.join(map(str, cy))}</span></td>"
                     + "".join(f"<td>{cell(c[k])}</td>" for k in W) + f"<td>{c['선거 전 1개월']['n']}번</td></tr>")
    forecast = ("<div class='card tbl'><table><tr><th>선거일</th>" + "".join(f"<th>{k}</th>" for k in W)
                + "<th>표본</th></tr>" + rows + "</table>"
                "<p class='note'>굵은 숫자 = 과거 중앙값(가장 '보통'이었던 결과). 범위는 과거 결과의 하위 10% ~ 상위 10%. "
                "S&P 500, 1950년 이후, 선거일(11월 첫 월요일 다음 화요일) 종가 기준 21거래일(약 1개월).</p></div>")

    # ---- 차트: ±21일 평균 경로
    fig = go.Figure()
    for kd, color in [("중간선거", BLUE), ("대선", ORANGE)]:
        yrs = [y for y in past if kind(y) == kd]
        pth = el.paths(sp, yrs, pre=21, post=21, anchor=-21)
        q25, q75 = pth.quantile(0.25, axis=1), pth.quantile(0.75, axis=1)
        rgba = "rgba(42,120,214,0.12)" if color == BLUE else "rgba(235,104,52,0.12)"
        fig.add_trace(go.Scatter(x=pth.index, y=q75, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=pth.index, y=q25, mode="lines", line=dict(width=0), fill="tonexty", fillcolor=rgba,
                                 name=f"{kd} 가운데 50%", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=pth.index, y=pth.median(axis=1), mode="lines", name=f"{kd} 중앙값 ({len(yrs)}번)",
                                 line=dict(color=color, width=2.5),
                                 hovertemplate=kd + " %{x}일: %{y:.1f}<extra></extra>"))
    charts._layout(fig, "선거 전후 1개월 흐름 (S&P 500, 선거 21거래일 전 = 100)", height=480)
    fig.add_vline(x=0, line=dict(color=GRAY, width=1, dash="dot"))
    fig.add_annotation(x=0, y=1, yref="paper", text="선거일", showarrow=False, yanchor="bottom", font=dict(color=GRAY))
    fig.add_hline(y=100, line=dict(color=GRAY, width=1, dash="dot"))
    fig.update_xaxes(title_text="선거일 기준 거래일")
    fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0), hovermode="x unified",
                      margin=dict(b=110))

    # ---- 차트: 선거마다
    fig2 = go.Figure()
    for k, color in [("선거 전 1개월", BLUE), ("선거 후 1개월", ORANGE)]:
        fig2.add_trace(go.Bar(x=[f"{y} {'대' if kind(y) == '대선' else '중'}" for y in w.index], y=w[k] * 100, name=k,
                              marker_color=color, hovertemplate="%{x} " + k + " %{y:+.1f}%<extra></extra>"))
    charts._layout(fig2, "선거마다: 전 1개월 · 후 1개월 (대 = 대선, 중 = 중간선거)", height=400)
    fig2.update_layout(barmode="group", bargap=0.2, bargroupgap=0.05, barcornerradius=3,
                       legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1))
    fig2.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=GRAY)
    fig2.update_xaxes(tickangle=-60)

    # ---- 조건표: 고점 근처 vs 아래
    cond_rows = ""
    for lab, m in [("52주 고점 근처(−5% 이내)", w["from_high"] > args.near_high), ("고점에서 5% 넘게 아래", w["from_high"] <= args.near_high)]:
        for kd in ["중간선거", "대선"]:
            x = w[m & (w["kind"] == kd)]
            if x.empty:
                continue
            cond_rows += (f"<tr><td>{lab} · {kd}<br><span class='note'>{', '.join(map(str, x.index))}</span></td>"
                          + "".join(f"<td>{pct(x[k].mean())}<br><span class='note'>{(x[k] > 0).mean():.0%} 상승</span></td>" for k in W)
                          + f"<td>{len(x)}</td></tr>")
    cond = ("<div class='card tbl'><table><tr><th>선거 1개월 전 시장 상태</th>" + "".join(f"<th>{k} 평균</th>" for k in W)
            + "<th>n</th></tr>" + cond_rows + "</table>"
            "<p class='note'>중간선거 전 1개월 상승은 대부분 '그 전에 크게 빠졌다가 반등한 해'(1974, 1982, 1998, 2002 등)에서 나왔습니다.</p></div>")

    to_html = lambda f: "<div class='card'>" + report.pio.to_html(  # noqa: E731
        f, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}) + "</div>"
    y0 = up_rows[0]
    c0 = y0[4] or y0[3]
    findings = [
        f"**{y0[1]:%Y-%m-%d} 중간선거**: 지금 S&P 500이 52주 고점 대비 {pct(now_fh)}라서, 과거 '고점 근처에서 맞은 중간선거' "
        f"{c0['선거 전 1개월']['n']}번을 기준으로 보면 선거 전 1개월 중앙값 {pct(c0['선거 전 1개월']['med'])}, "
        f"선거 후 1개월 중앙값 {pct(c0['선거 후 1개월']['med'])} (오른 비율 {c0['선거 후 1개월']['up']:.0%}).",
        "**중간선거 vs 대선**: 중간선거는 선거 전 1개월이 강했고(평균 +3.8%, 대부분 급락 뒤 반등), 대선은 선거 전엔 거의 제자리(+0.6%), "
        "선거 후 1개월은 결과에 따라 크게 갈렸습니다 (2008년 −16.0%, 2020년 +8.8%).",
        "**선거 후 1개월은 대체로 플러스**: 중간선거 79%, 대선 63%가 올랐습니다. 하지만 선거가 없는 해의 같은 시기도 71%가 올랐습니다 "
        "(11월 초~12월 초는 원래 강한 계절). 선거 자체의 효과는 '1년' 단위에서 더 뚜렷했습니다.",
        "**변동성**: 선거 전 1개월이 선거 후 1개월보다 출렁임이 컸습니다 (중간선거 17.7% → 15.7%, 대선 16.0% → 14.6%, 연율).",
    ]
    sections = [
        ("요약", "<div class='card'><ul class='find'>" + "".join(f"<li>{report._md(f)}</li>" for f in findings) + "</ul></div>"),
        ("앞으로의 선거일별 예상 범위 (과거 통계)", forecast),
        ("선거 전후 1개월 평균 흐름", to_html(fig)),
        ("시장 상태에 따라 (올해 같은 '고점 근처' vs 아래)", cond),
        ("과거 선거마다", to_html(fig2)),
        ("주의", "<div class='card'><ul class='find'><li>예측이 아니라 <b>과거 19번(종류별)의 분포</b>입니다. 표본이 작아 한두 해가 평균을 크게 바꿉니다.</li>"
                 "<li>선거 결과, 금리, 경기, 지정학 이벤트는 반영하지 않았습니다. 2008년처럼 선거와 무관한 사건이 결과를 좌우하기도 합니다.</li>"
                 "<li>2028·2030·2032년은 그때의 시장 상태를 알 수 없어 무조건부(전체) 통계만 적었습니다.</li></ul></div>"),
    ]
    intro = f"S&P 500 1950년 이후 · 데이터 기준일 {last:%Y-%m-%d} · 1개월 = 21거래일"
    out.write_text(report.render_html([], "미국 선거일 전후 1개월 주가: 앞으로의 선거별 예상 범위", intro, sections,
                                      inline_js=args.inline_js), encoding="utf-8")
    for y, e, kd, s, c, cy in up_rows:
        print(f"{e:%Y-%m-%d} {kd}: " + " | ".join(f"{k} 중앙 {pct(s[k]['med'])} ({s[k]['up']:.0%}↑, {pct(s[k]['p10'])}~{pct(s[k]['p90'])})" for k in W))
        if c:
            print("   고점 근처 조건부: " + " | ".join(f"{k} 중앙 {pct(c[k]['med'])} 평균 {pct(c[k]['mean'])} ({c[k]['up']:.0%}↑, {pct(c[k]['p10'])}~{pct(c[k]['p90'])})" for k in W))
    print(f"리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
