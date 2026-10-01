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
                x=x, y=avg, mode="lines", name="평균",
                line=dict(color=AVG, width=2.5),
                hovertemplate="평균 %{x|%m/%d}: %{y:.1f}<extra></extra>",
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
    return fig
