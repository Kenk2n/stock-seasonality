"""선거일 전후 1개월(±21거래일) 일별 주가 변동: 과거 선거(2000년~) vs 올해.

  python election_daily.py                       # S&P 500, 2000~2024 선거 + 2026 (지금까지)
  python election_daily.py --index ^IXIC         # 나스닥
  python election_daily.py --refresh             # 오늘까지 데이터 새로 받아서 2026 칸 채우기

0일 = 선거일 종가 (장이 쉬었으면 직전 거래일). −21일 종가를 0% 로 놓고 누적 변동을 본다.
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

BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#898781"
UP, DOWN = "#e34948", "#2a78d6"
N = 21
NAMES = {"^GSPC": "S&P 500", "^IXIC": "나스닥", "^RUT": "러셀2000", "^DJI": "다우"}


def nyse_days(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """평일 중 10~1월에 걸리는 NYSE 휴장일(추수감사절·성탄절·새해)을 뺀 거래일 (미래 날짜 계산용)."""
    days = pd.bdate_range(start, end)
    hol = set()
    for y in range(start.year, end.year + 1):
        nov1 = pd.Timestamp(year=y, month=11, day=1)
        thanksgiving = nov1 + pd.Timedelta(days=(3 - nov1.weekday()) % 7 + 21)
        hol |= {thanksgiving, pd.Timestamp(year=y, month=12, day=25), pd.Timestamp(year=y, month=1, day=1)}
    return days[~days.isin(list(hol))]


def year_window(close: pd.Series, year: int) -> pd.DataFrame:
    """한 선거의 −21~+21일: 날짜, 종가, 일간 변동, 누적(−21일 대비). 미래 날짜는 예정일만."""
    e = el.election_day(year)
    i = el.base_index(close, e)
    if close.index[i] < e - pd.Timedelta(days=4) or e > close.index[-1]:
        # 아직 선거 전: 오늘 이후는 예정 거래일로 채움
        future = nyse_days(close.index[-1] + pd.Timedelta(days=1), e + pd.Timedelta(days=60))
        axis = close.index.append(future)
        i = int(axis.searchsorted(e, side="right") - 1)
    else:
        axis = close.index
    rows = []
    for d in range(-N - 1, N + 1):
        j = i + d
        date = axis[j] if 0 <= j < len(axis) else pd.NaT
        c = close.get(date, np.nan) if pd.notna(date) else np.nan
        rows.append({"day": d, "date": date, "close": c})
    df = pd.DataFrame(rows).set_index("day")
    df["daily"] = df["close"].pct_change(fill_method=None)
    df["cum"] = df["close"] / df.loc[-N, "close"] - 1
    return df.loc[-N:]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--index", default="^GSPC")
    p.add_argument("--start", type=int, default=2000)
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--out", default="reports/election_daily.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    path = Path("data/index") / f"{args.index.strip('^')}.parquet"
    if args.refresh or not path.exists():
        import yfinance as yf

        df = yf.download(args.index, period="max", auto_adjust=True, progress=False, multi_level_index=False)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    close = pd.read_parquet(path)["Close"]
    name = NAMES.get(args.index, args.index)
    last = close.index[-1]

    past = [y for y in range(args.start, args.year, 2)]
    win = {y: year_window(close, y) for y in past}
    cur = year_window(close, args.year)
    kind = lambda y: "대선" if y % 4 == 0 else "중간선거"  # noqa: E731

    daily = pd.DataFrame({y: w["daily"] for y, w in win.items()})
    cum = pd.DataFrame({y: w["cum"] for y, w in win.items()})
    mid = [y for y in past if kind(y) == "중간선거"]
    q = lambda df, a: df.quantile(a, axis=1)  # noqa: E731
    have = cur["close"].notna()
    cur_days = cur.index[have & (cur.index > -N)]
    today_day = int(cur.index[have].max())

    # ---- 2026 vs 과거: 백분위
    def pctile(series, value):
        s = series.dropna()
        return float((s < value).mean()) if len(s) and np.isfinite(value) else np.nan

    compare = []
    for d in cur_days:
        compare.append({"day": d, "date": cur.loc[d, "date"], "daily": cur.loc[d, "daily"], "cum": cur.loc[d, "cum"],
                        "daily_med": daily.loc[d].median(), "daily_lo": q(daily, 0.1)[d], "daily_hi": q(daily, 0.9)[d],
                        "daily_pct": pctile(daily.loc[d], cur.loc[d, "daily"]),
                        "cum_med": cum.loc[d].median(), "cum_lo": q(cum, 0.1)[d], "cum_hi": q(cum, 0.9)[d],
                        "cum_pct": pctile(cum.loc[d], cur.loc[d, "cum"])})
    comp = pd.DataFrame(compare)

    # ---- 요약 통계
    absmove_pre = daily.loc[-N + 1 : 0].abs().mean().mean()
    absmove_post = daily.loc[1:N].abs().mean().mean()
    big = daily.stack().rename("r").reset_index().rename(columns={"level_1": "year"})
    big = big.reindex(big["r"].abs().sort_values(ascending=False).index).head(10)
    up_days = (daily > 0).mean(axis=1)

    print(f"{name} · 데이터 {last:%Y-%m-%d} · {args.year} 선거일 {el.election_day(args.year):%Y-%m-%d} · 오늘 = {today_day}일")
    print(f"과거 {len(past)}번 ({past[0]}~{past[-1]}) 평균 하루 변동폭: 선거 전 {absmove_pre * 100:.2f}% / 선거 후 {absmove_post * 100:.2f}%")
    for r in comp.itertuples():
        print(f"  {r.day:+3d}일 {r.date:%m/%d}  {args.year}: 일간 {r.daily * 100:+.2f}% (과거 중앙 {r.daily_med * 100:+.2f}%, "
              f"80% 범위 {r.daily_lo * 100:+.2f}~{r.daily_hi * 100:+.2f}%, 백분위 {r.daily_pct:.0%}) | 누적 {r.cum * 100:+.2f}% "
              f"(과거 중앙 {r.cum_med * 100:+.2f}%, 80% 범위 {r.cum_lo * 100:+.2f}~{r.cum_hi * 100:+.2f}%, 백분위 {r.cum_pct:.0%})")

    # ---- 차트 1: 누적 경로
    fig = go.Figure()
    for y in past:
        fig.add_trace(go.Scatter(x=cum.index, y=cum[y] * 100, mode="lines", name=f"{y} ({kind(y)})",
                                 line=dict(color="rgba(137,135,129,0.45)", width=1), visible=True, showlegend=False,
                                 hovertemplate=f"{y} {kind(y)} %{{x}}일: %{{y:+.1f}}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=cum.index, y=q(cum, 0.9) * 100, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=cum.index, y=q(cum, 0.1) * 100, mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor="rgba(42,120,214,0.12)", name="과거 80% 범위", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=cum.index, y=cum.median(axis=1) * 100, mode="lines", name=f"과거 {len(past)}번 중앙값",
                             line=dict(color=BLUE, width=2.5), hovertemplate="중앙값 %{x}일: %{y:+.2f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=cum.index, y=cum[mid].median(axis=1) * 100, mode="lines", name=f"중간선거만 중앙값 ({len(mid)}번)",
                             line=dict(color=BLUE, width=1.5, dash="dash"), hovertemplate="중간선거 중앙값 %{x}일: %{y:+.2f}%<extra></extra>"))
    cc = cur.loc[cur.index[have]]
    fig.add_trace(go.Scatter(x=cc.index, y=cc["cum"] * 100, mode="lines+markers", name=f"{args.year} (지금까지)",
                             line=dict(color=ORANGE, width=3), marker=dict(size=8),
                             customdata=[d.strftime("%m/%d") for d in cc["date"]],
                             hovertemplate=f"{args.year} %{{x}}일 (%{{customdata}}): %{{y:+.2f}}%<extra></extra>"))
    charts._layout(fig, f"{name} 선거 전후 ±21거래일 누적 변동 (−21일 종가 = 0%)", height=500)
    fig.add_vline(x=0, line=dict(color=GRAY, width=1, dash="dot"))
    fig.add_annotation(x=0, y=1, yref="paper", text="선거일", showarrow=False, yanchor="bottom", font=dict(color=GRAY))
    fig.add_vline(x=today_day, line=dict(color=ORANGE, width=1, dash="dot"))
    fig.add_hline(y=0, line=dict(color=GRAY, width=1))
    fig.update_xaxes(title_text="선거일 기준 거래일", dtick=3)
    fig.update_yaxes(ticksuffix="%")
    fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0), margin=dict(b=110),
                      hovermode="closest")

    # ---- 차트 2: 일별 변동 (과거 범위 막대 + 중앙값 + 올해 점)
    lo, hi, med = q(daily, 0.1) * 100, q(daily, 0.9) * 100, daily.median(axis=1) * 100
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=daily.index, y=hi - lo, base=lo, name="과거 80% 범위", marker_color="rgba(42,120,214,0.22)",
                          hovertemplate="%{x}일 범위 %{base:+.2f}% ~ %{y:.2f}%p 폭<extra></extra>"))
    fig2.add_trace(go.Scatter(x=daily.index, y=med, mode="markers", name="과거 중앙값",
                              marker=dict(color=BLUE, size=7, symbol="line-ew-open", line=dict(width=3, color=BLUE)),
                              customdata=up_days * 100,
                              hovertemplate="%{x}일 중앙값 %{y:+.2f}% · 오른 해 %{customdata:.0f}%<extra></extra>"))
    cd = cur.loc[cur_days]
    fig2.add_trace(go.Scatter(x=cd.index, y=cd["daily"] * 100, mode="markers", name=f"{args.year}",
                              marker=dict(color=ORANGE, size=11, line=dict(color="white", width=1.5)),
                              customdata=[d.strftime("%m/%d") for d in cd["date"]],
                              hovertemplate=f"{args.year} %{{x}}일 (%{{customdata}}): %{{y:+.2f}}%<extra></extra>"))
    charts._layout(fig2, f"{name} 선거 전후 일별 변동: 과거 범위 vs {args.year}", height=420)
    fig2.update_layout(bargap=0.25, barcornerradius=2, legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1))
    fig2.add_vline(x=0, line=dict(color=GRAY, width=1, dash="dot"))
    fig2.add_hline(y=0, line=dict(color=GRAY, width=1))
    fig2.update_xaxes(title_text="선거일 기준 거래일", dtick=3)
    fig2.update_yaxes(ticksuffix="%")

    # ---- 표: 일별 (과거 각 연도 + 범위 + 올해)
    def color(v):
        if not np.isfinite(v):
            return ""
        a = min(abs(v) / 0.02, 1) * 0.35
        return f"background:rgba({'227,73,72' if v > 0 else '42,120,214'},{a:.2f})"

    head = ("<tr><th>일</th><th>{y} 날짜</th>".format(y=args.year) + "".join(f"<th>{y}<br><span class='note'>{'대' if kind(y) == '대선' else '중'}</span></th>" for y in past)
            + "<th>중앙값</th><th>80% 범위</th><th>오른 해</th><th>" + str(args.year) + "</th></tr>")
    def fmt26(v):
        return "" if not np.isfinite(v) else f"{v * 100:+.2f}"

    body = ""
    for d in daily.index:
        v26 = cur.loc[d, "daily"] if d in cur.index else np.nan
        dt = cur.loc[d, "date"]
        bold = ' style="font-weight:600"' if d == 0 else ""
        dts = "" if pd.isna(dt) else dt.strftime("%m/%d")
        body += (f"<tr{bold}><td>{d:+d}</td><td>{dts}</td>"
                 + "".join(f"<td style='{color(daily.loc[d, y])}'>{daily.loc[d, y] * 100:+.1f}</td>" for y in past)
                 + f"<td>{med[d]:+.2f}</td><td>{lo[d]:+.1f} ~ {hi[d]:+.1f}</td><td>{up_days[d]:.0%}</td>"
                 + f"<td style='{color(v26)}'><b>{fmt26(v26)}</b></td></tr>")
    table = ("<div class='card tbl'><table style='font-size:12px'>" + head + body + "</table>"
             "<p class='note'>단위 %. 빨강 = 상승, 파랑 = 하락 (진할수록 큼). 대 = 대선, 중 = 중간선거. "
             f"{args.year} 날짜는 예정 거래일 (추수감사절 휴장 반영).</p></div>")

    # ---- 요약
    last_c = comp.iloc[-1] if len(comp) else None
    findings = [
        f"**{args.year}년 선거일 {el.election_day(args.year):%m월 %d일}**, 데이터 기준일 {last:%m월 %d일} = 선거 **{today_day}거래일**. "
        f"±21일 구간 중 {len(cur_days)}일치 결과가 나왔습니다.",
    ]
    if last_c is not None:
        findings.append(
            f"**지금까지 {args.year}년**: −21일 대비 누적 {last_c.cum * 100:+.2f}% · 같은 날({last_c.day:+d}일) 과거 중앙값 "
            f"{last_c.cum_med * 100:+.2f}%, 과거 80% 범위 {last_c.cum_lo * 100:+.2f}% ~ {last_c.cum_hi * 100:+.2f}% → "
            f"**{'범위 안' if last_c.cum_lo <= last_c.cum <= last_c.cum_hi else '범위 밖'}** (과거 {len(past)}번 중 {last_c.cum_pct:.0%} 지점).")
        findings.append("**일별**: " + ", ".join(
            f"{r.day:+d}일({r.date:%m/%d}) {r.daily * 100:+.2f}% (과거 범위 {r.daily_lo * 100:+.1f}~{r.daily_hi * 100:+.1f}%)" for r in comp.itertuples()))
    findings += [
        f"**과거 평균 하루 변동폭**: 선거 전 21일 {absmove_pre * 100:.2f}% → 선거 후 21일 {absmove_post * 100:.2f}% "
        f"(절댓값 평균, {past[0]}~{past[-1]}).",
        "**과거 가장 큰 하루 변동**: " + ", ".join(f"{int(r.year)}년 {int(r.day):+d}일 {r.r * 100:+.1f}%" for r in big.itertuples()),
        f"**선거 다음 날(+1일)**: 과거 중앙값 {med[1]:+.2f}%, 오른 해 {up_days[1]:.0%}, 범위 {daily.loc[1].min() * 100:+.1f}% ~ {daily.loc[1].max() * 100:+.1f}%.",
    ]
    to_html = lambda f: "<div class='card'>" + report.pio.to_html(  # noqa: E731
        f, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}) + "</div>"
    sections = [
        ("요약", "<div class='card'><ul class='find'>" + "".join(f"<li>{report._md(f)}</li>" for f in findings) + "</ul></div>"),
        ("누적 흐름: 과거 선거 vs " + str(args.year), to_html(fig)),
        ("일별 변동: 과거 범위 vs " + str(args.year), to_html(fig2)),
        ("일별 변동 표 (−21일 ~ +21일)", table),
        ("주의", "<div class='card'><ul class='find'><li>과거 {n}번(대선 {a}, 중간선거 {b})뿐이라 날짜별 범위는 대략적인 참고치입니다. "
                 "2008년(금융위기)이 범위를 크게 넓힙니다.</li><li>{y}년 칸은 데이터가 쌓이면 채워집니다. "
                 "<code>python election_daily.py --refresh</code> 로 다시 만들면 됩니다.</li></ul></div>".format(
                     n=len(past), a=len(past) - len(mid), b=len(mid), y=args.year)),
    ]
    intro = f"{name} · {past[0]}~{past[-1]} 선거 {len(past)}번 + {args.year} · 데이터 기준일 {last:%Y-%m-%d} · 0일 = 선거일 종가"
    out.write_text(report.render_html([], f"선거 전후 1개월 일별 주가: {args.year} vs 과거", intro, sections,
                                      inline_js=args.inline_js), encoding="utf-8")
    pd.concat({"daily": daily, "cum": cum}, axis=1).assign(**{f"{args.year}_daily": cur["daily"], f"{args.year}_cum": cur["cum"],
                                                            f"{args.year}_date": cur["date"]}).to_csv(out.with_suffix(".csv"))
    print(f"리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
