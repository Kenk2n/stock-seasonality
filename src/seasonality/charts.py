"""Plotly 차트.

색 규칙 (한국식): 상승 = 빨강, 하락 = 파랑, 0 근처 = 회색.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .analysis import MONTH_NAMES, average_path

UP = "#e34948"      # 상승
DOWN = "#2a78d6"    # 하락
NEUTRAL = "#f0efec"
AVG = "#2a78d6"     # 평균 경로
CURRENT = "#eb6834" # 올해
PAST = "rgba(137,135,129,0.35)"  # 지난 해들 (회색, 뒤로 물러나게)
MUTED = "#898781"
GRID = "rgba(137,135,129,0.25)"

DIVERGING = [[0.0, DOWN], [0.5, NEUTRAL], [1.0, UP]]


def _layout(fig: go.Figure, title: str, height: int = 420) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=16)),
        height=height,
        margin=dict(l=48, r=16, t=56, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(font_size=13),
        font=dict(family="system-ui, -apple-system, 'Noto Sans KR', sans-serif", size=13),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED))
    return fig


def heatmap(table: pd.DataFrame, title: str = "연도 × 월 수익률") -> go.Figure:
    """연도(행) × 월(열) 수익률 히트맵. 최근 연도가 위."""
    t = table.sort_index(ascending=False)
    z = t.to_numpy(dtype="float64") * 100
    finite = np.abs(z[np.isfinite(z)])
    lim = float(np.nanpercentile(finite, 95)) if finite.size else 1.0
    text = np.where(np.isfinite(z), np.vectorize(lambda v: f"{v:+.1f}")(np.nan_to_num(z)), "")
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=MONTH_NAMES,
            y=[str(y) for y in t.index],
            colorscale=DIVERGING,
            zmid=0,
            zmin=-lim,
            zmax=lim,
            xgap=2,
            ygap=2,
            text=text,
            texttemplate="%{text}" if len(t) <= 25 else None,
            textfont=dict(size=11),
            hovertemplate="%{y}년 %{x}: %{z:+.2f}%<extra></extra>",
            colorbar=dict(title="수익률 %", ticksuffix="%", thickness=12),
        )
    )
    _layout(fig, title, height=max(320, 26 * len(t) + 120))
    fig.update_yaxes(type="category", gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(tickangle=0)
    return fig


def monthly_bar(stats: pd.DataFrame, title: str = "월별 평균 수익률") -> go.Figure:
    """월별 평균 수익률 막대. 유의미한 달에만 ★ 라벨."""
    mean = stats["mean"] * 100
    colors = [UP if v >= 0 else DOWN for v in mean.fillna(0)]
    labels = [
        f"★ {v:+.1f}%" if sig else ""
        for v, sig in zip(mean.fillna(0), stats.get("significant", pd.Series(False, index=stats.index)))
    ]
    custom = np.stack(
        [
            stats["win_rate"].fillna(0) * 100,
            stats["n"],
            stats["p"].fillna(1),
            stats["median"].fillna(0) * 100,
        ],
        axis=-1,
    )
    fig = go.Figure(
        go.Bar(
            x=MONTH_NAMES,
            y=mean,
            marker=dict(color=colors, line=dict(width=0)),
            text=labels,
            textposition="outside",
            cliponaxis=False,
            customdata=custom,
            hovertemplate=(
                "<b>%{x}</b><br>평균 %{y:+.2f}%<br>중앙값 %{customdata[3]:+.2f}%"
                "<br>상승 확률 %{customdata[0]:.0f}% (%{customdata[1]:.0f}년)"
                "<br>p값 %{customdata[2]:.3f}<extra></extra>"
            ),
        )
    )
    _layout(fig, title)
    fig.update_layout(bargap=0.35, barcornerradius=4)
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=MUTED, zerolinewidth=1)
    return fig


def win_rate_bar(stats: pd.DataFrame, title: str = "월별 상승 확률") -> go.Figure:
    wr = stats["win_rate"] * 100
    colors = [UP if v >= 50 else DOWN for v in wr.fillna(50)]
    fig = go.Figure(
        go.Bar(
            x=MONTH_NAMES,
            y=wr,
            marker=dict(color=colors, line=dict(width=0)),
            customdata=stats["n"],
            hovertemplate="<b>%{x}</b><br>상승 확률 %{y:.0f}% (%{customdata}년)<extra></extra>",
        )
    )
    _layout(fig, title, height=320)
    fig.update_layout(bargap=0.35, barcornerradius=4)
    fig.update_yaxes(range=[0, 100], ticksuffix="%")
    fig.add_hline(y=50, line=dict(color=MUTED, width=1, dash="dot"))
    return fig


def yearly_paths_chart(paths: pd.DataFrame, title: str = "연도별 가격 흐름 (연초 = 100)") -> go.Figure:
    """지난 해들은 회색 가는 선, 평균 경로와 올해만 색으로 강조."""
    fig = go.Figure()
    # 날짜 축: 윤년이 아닌 해(2001년)를 기준으로 day-of-year 를 날짜로 표시
    x = pd.Timestamp("2001-01-01") + pd.to_timedelta(paths.index - 1, unit="D")
    cols = list(paths.columns)
    current = cols[-1] if cols else None
    current_done = current is not None and (paths[current].last_valid_index() or 0) >= 360

    for y in cols:
        if y == current and not current_done:
            continue
        fig.add_trace(
            go.Scatter(
                x=x, y=paths[y], mode="lines", name=str(y),
                line=dict(color=PAST, width=1),
                hovertemplate=f"{y}년 %{{x|%m/%d}}: %{{y:.1f}}<extra></extra>",
                showlegend=False,
            )
        )
    avg = average_path(paths)
    if not avg.empty:
        fig.add_trace(
            go.Scatter(
                x=x, y=avg, mode="lines", name="중앙값 (지난 해들)",
                line=dict(color=AVG, width=2.5),
                hovertemplate="중앙값 %{x|%m/%d}: %{y:.1f}<extra></extra>",
            )
        )
    if current is not None and not current_done:
        fig.add_trace(
            go.Scatter(
                x=x, y=paths[current], mode="lines", name=f"{current}년 (올해)",
                line=dict(color=CURRENT, width=2.5),
                hovertemplate=f"{current}년 %{{x|%m/%d}}: %{{y:.1f}}<extra></extra>",
            )
        )
    _layout(fig, title, height=460)
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        hovermode="closest",
    )
    fig.update_xaxes(tickformat="%m월", dtick="M1")
    fig.add_hline(y=100, line=dict(color=MUTED, width=1, dash="dot"))
    vals = paths.to_numpy(dtype="float64")
    vals = vals[np.isfinite(vals)]
    if vals.size:
        lo, hi = np.percentile(vals, [1, 99])
        pad = (hi - lo) * 0.05
        fig.update_yaxes(range=[min(lo, 95) - pad, max(hi, 105) + pad])  # 극단적인 해 하나가 축을 찌그러뜨리지 않게
    return fig


# ------------------------------------------------------------------ 차트 흐름 (patterns.py)
PRICE = "#52514e"   # 가격선 (중립 잉크)
EARN = "#eda100"    # 실적 발표일


def price_rsi_chart(
    prices: pd.DataFrame | pd.Series,
    window: pd.DateOffset | None = None,
    swings: pd.DataFrame | None = None,
    earnings_dates: pd.DatetimeIndex | None = None,
    title: str = "가격 · RSI",
    buttons: bool = False,
) -> go.Figure:
    """위: 가격 + 고점(▼)·저점(▲) + 실적일 세로선 / 아래: RSI(14) 와 70·30 기준선."""
    from plotly.subplots import make_subplots

    from .patterns import rsi as calc_rsi

    close = prices["Close"] if isinstance(prices, pd.DataFrame) else prices
    r = calc_rsi(close)
    if window is not None:
        start = close.index[-1] - window
        close, r = close[close.index > start], r[r.index > start]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.04)
    fig.add_trace(
        go.Scatter(x=close.index, y=close, mode="lines", name="종가", line=dict(color=PRICE, width=1.6),
                   hovertemplate="%{x|%Y-%m-%d}<br>$%{y:.2f}<extra></extra>"),
        row=1, col=1,
    )
    if swings is not None and not swings.empty:
        sw = swings[swings["date"] >= close.index[0]]
        for kind, sym, color, label in [("저점", "triangle-up", UP, "저점 (이후 상승)"),
                                        ("고점", "triangle-down", DOWN, "고점 (이후 하락)")]:
            s = sw[sw["kind"] == kind]
            if s.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=s["date"], y=s["price"], mode="markers", name=label,
                    marker=dict(symbol=sym, size=11, color=color, line=dict(color="white", width=1.5)),
                    customdata=np.stack([s["move"] * 100, s["days"]], axis=-1),
                    hovertemplate=(f"{kind} %{{x|%Y-%m-%d}}<br>$%{{y:.2f}}"
                                   "<br>다음 스윙까지 %{customdata[0]:+.1f}% (%{customdata[1]}거래일)<extra></extra>"),
                ),
                row=1, col=1,
            )
    if earnings_dates is not None and len(earnings_dates):
        ed = [d for d in pd.DatetimeIndex(earnings_dates) if close.index[0] <= d <= close.index[-1] + pd.Timedelta(days=60)]
        for d in ed:
            fig.add_vline(x=d, line=dict(color=EARN, width=1, dash="dot"), row=1, col=1)
        if ed:
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name="실적 발표일",
                                     line=dict(color=EARN, width=1, dash="dot")), row=1, col=1)
    fig.add_trace(
        go.Scatter(x=r.index, y=r, mode="lines", name="RSI(14)", line=dict(color=AVG, width=1.4),
                   hovertemplate="%{x|%Y-%m-%d}<br>RSI %{y:.0f}<extra></extra>", showlegend=False),
        row=2, col=1,
    )
    fig.add_hrect(y0=70, y1=100, fillcolor=UP, opacity=0.08, line_width=0, row=2, col=1)
    fig.add_hrect(y0=0, y1=30, fillcolor=DOWN, opacity=0.08, line_width=0, row=2, col=1)
    for y in (30, 70):
        fig.add_hline(y=y, line=dict(color=MUTED, width=1, dash="dot"), row=2, col=1)
    _layout(fig, title, height=560)
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
                      hovermode="closest")
    if buttons:
        from .patterns import WINDOWS

        end = close.index[-1]
        btns = []
        for label, off in WINDOWS.items():
            s = close[close.index > end - off]
            if len(s) < 2 or close.index[0] > end - off + pd.Timedelta(days=10):
                continue  # 데이터가 그 기간보다 짧음
            pad = (s.max() - s.min()) * 0.06 or s.max() * 0.05
            btns.append(dict(label=label, method="relayout",
                             args=[{"xaxis.range": [s.index[0], end + pd.Timedelta(days=2)],
                                    "xaxis2.range": [s.index[0], end + pd.Timedelta(days=2)],
                                    "yaxis.range": [s.min() - pad, s.max() + pad]}]))
        fig.update_layout(
            updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left", y=1.01, yanchor="bottom",
                              buttons=btns, active=len(btns) - 1, showactive=True, pad=dict(r=4, t=0),
                              bgcolor="rgba(137,135,129,0.10)", font=dict(size=12))],
            margin=dict(t=110),
        )
        fig.update_layout(legend=dict(y=1.01))
    fig.update_yaxes(tickprefix="$", row=1, col=1)
    fig.update_yaxes(range=[0, 100], tickvals=[30, 50, 70], title_text="RSI", row=2, col=1)
    return fig


def swing_calendar_chart(cal: pd.DataFrame, title: str = "월별 저점·고점 횟수") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=MONTH_NAMES, y=cal["lows"], name="저점 (이후 상승)", marker_color=UP,
                         hovertemplate="%{x} 저점 %{y}회<extra></extra>"))
    fig.add_trace(go.Bar(x=MONTH_NAMES, y=cal["highs"], name="고점 (이후 하락)", marker_color=DOWN,
                         hovertemplate="%{x} 고점 %{y}회<extra></extra>"))
    _layout(fig, title, height=320)
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08, barcornerradius=4,
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1))
    fig.update_yaxes(dtick=1)
    return fig


def forward_heatmap(fwd: pd.DataFrame, title: str = "매달 초에 샀다면 (평균 수익률 · 상승 확률)") -> go.Figure:
    labels = list(dict.fromkeys(fwd.index.get_level_values(0)))
    z = np.array([fwd.loc[(l, "mean")].to_numpy(dtype="float64") * 100 for l in labels])
    win = np.array([fwd.loc[(l, "win_rate")].to_numpy(dtype="float64") * 100 for l in labels])
    n = np.array([fwd.loc[(l, "n")].to_numpy(dtype="float64") for l in labels])
    finite = np.abs(z[np.isfinite(z)])
    lim = float(np.nanpercentile(finite, 95)) if finite.size else 1.0
    text = np.vectorize(lambda v, w: "" if not np.isfinite(v) else f"{v:+.1f}%<br>{w:.0f}%")(z, win)
    fig = go.Figure(
        go.Heatmap(
            z=z, x=MONTH_NAMES, y=labels, colorscale=DIVERGING, zmid=0, zmin=-lim, zmax=lim,
            xgap=2, ygap=2, text=text, texttemplate="%{text}", textfont=dict(size=11),
            customdata=np.stack([win, n], axis=-1),
            hovertemplate="%{x} 초 매수 → %{y}<br>평균 %{z:+.2f}%<br>상승 확률 %{customdata[0]:.0f}% (%{customdata[1]:.0f}회)<extra></extra>",
            colorbar=dict(title="평균 %", ticksuffix="%", thickness=12),
        )
    )
    _layout(fig, title, height=240)
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    return fig


def rsi_month_chart(rsi_m: pd.DataFrame, title: str = "월별 평균 RSI") -> go.Figure:
    v = rsi_m["rsi_mean"]
    colors = [UP if x >= 50 else DOWN for x in v.fillna(50)]
    fig = go.Figure(
        go.Bar(
            x=MONTH_NAMES, y=v - 50, base=50, marker_color=colors,
            customdata=np.stack([rsi_m["overbought"] * 100, rsi_m["oversold"] * 100, v], axis=-1),
            hovertemplate="%{x} 평균 RSI %{customdata[2]:.1f}<br>과매수(>70) %{customdata[0]:.0f}% · "
                          "과매도(<30) %{customdata[1]:.0f}%의 날<extra></extra>",
        )
    )
    _layout(fig, title, height=300)
    fig.update_layout(bargap=0.35, barcornerradius=4)
    fig.add_hline(y=50, line=dict(color=MUTED, width=1))
    lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
    fig.update_yaxes(range=[min(35, lo - 3), max(65, hi + 3)])
    return fig


def earnings_reaction_chart(reac: pd.DataFrame, title: str = "실적 발표 반응 (전날 → 다음날)") -> go.Figure:
    v = reac["reaction"] * 100
    fig = go.Figure(
        go.Bar(
            x=reac["date"].dt.strftime("%Y-%m"), y=v, marker_color=[UP if x >= 0 else DOWN for x in v],
            customdata=np.stack([reac["before"] * 100, reac["after"] * 100], axis=-1),
            hovertemplate="%{x} 실적<br>반응 %{y:+.1f}%<br>발표 전 20일 %{customdata[0]:+.1f}%"
                          "<br>발표 후 20일 %{customdata[1]:+.1f}%<extra></extra>",
        )
    )
    _layout(fig, title, height=300)
    fig.update_layout(bargap=0.3, barcornerradius=4)
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=MUTED)
    fig.update_xaxes(type="category")
    return fig
