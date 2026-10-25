"""관심 종목 추적: 기준일에 뽑은 목록(box_picks.py 결과 CSV)이 그 뒤 어떻게 됐는지.

  python track_list.py reports/box_picks.csv                       # 기준 CSV 대비 지금
  python track_list.py old.csv --labels labels.csv --refresh       # 그룹 이름 붙이기, 가격·실적일 새로 받기

계산: 기준일 이후 등락(시장 대비), 같은 박스 안 위치, 박스 이탈 여부(하단 −10% 아래 종가),
기준일 이후 나온 실적의 반응(발표 전날 → 다음날), 다음 실적일.
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seasonality import charts, data, earnings, patterns, rangebound as rb, report  # noqa: E402

BREAK = 0.10  # 박스 하단에서 이만큼 더 내려간 종가를 '박스 깨짐'으로 봄


def track(base: pd.DataFrame, prices: dict[str, pd.DataFrame], edates: dict[str, pd.DatetimeIndex],
          base_date: pd.Timestamp, bench: pd.Series | None) -> pd.DataFrame:
    rows = []
    for t, r in base.iterrows():
        if t not in prices:
            continue
        c = prices[t]["Close"]
        c0 = float(c[c.index <= base_date].iloc[-1])
        now = float(c.iloc[-1])
        since = c[c.index > base_date]
        lo, hi = float(r["low"]), float(r["high"])
        pos = (np.log(now) - np.log(lo)) / (np.log(hi) - np.log(lo))
        broke = bool((since < lo * (1 - BREAK)).any()) if len(since) else False
        mkt = (float(bench.iloc[-1]) / float(bench[bench.index <= base_date].iloc[-1]) - 1) if bench is not None else np.nan
        ed = pd.DatetimeIndex(edates.get(t, []))
        done = ed[(ed > base_date) & (ed <= c.index[-1])]
        reac = earnings.earnings_reactions(c, done, before=5, after=1) if len(done) else pd.DataFrame()
        rows.append({
            "ticker": t, "name": r.get("name", ""), "label": r.get("label", ""),
            "price_then": c0, "price_now": now, "ret": now / c0 - 1, "excess": now / c0 - 1 - mkt,
            "low": lo, "high": hi, "pos_then": float(r["pos"]), "pos_now": pos, "status": rb.box_status(pos),
            "min_since": float(since.min()) if len(since) else np.nan, "broke": broke,
            "earn_date": done[-1] if len(done) else pd.NaT,
            "earn_reaction": float(reac["reaction"].iloc[-1]) if len(reac) else np.nan,
            "next_earnings": earnings.next_earnings(ed, c.index[-1] + pd.Timedelta(days=1)) if len(ed) else None,
            "rsi": float(patterns.rsi(c).iloc[-1]),
        })
    return pd.DataFrame(rows).set_index("ticker")


def main() -> int:
    p = argparse.ArgumentParser(description="관심 종목 추적")
    p.add_argument("baseline", help="box_picks.py 가 만든 CSV (기준일 목록)")
    p.add_argument("--labels", help="ticker,label 열이 있는 CSV (그룹 이름)")
    p.add_argument("--base-date", help="기준일 YYYY-MM-DD (목록을 뽑은 날. 기본: 데이터 마지막 날의 14일 전)")
    p.add_argument("--refresh", action="store_true", help="가격·실적일 새로 받기")
    p.add_argument("--out", default="reports/track_list.html")
    p.add_argument("--inline-js", action="store_true")
    args = p.parse_args()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    base = pd.read_csv(args.baseline, index_col=0)
    if args.labels:
        lab = pd.read_csv(args.labels).set_index("ticker")["label"]
        base["label"] = lab.reindex(base.index).fillna("")
    tickers = list(base.index)
    prices = data.update_ohlc(tickers + ["SPY", "IWM"], refresh=args.refresh)
    edates = {t: earnings.load_earnings_dates(t, refresh=args.refresh) for t in tickers}
    base_date = pd.Timestamp(args.base_date) if args.base_date else prices["SPY"].index[-1] - pd.Timedelta(days=14)
    last = prices["SPY"].index[-1]
    spy = prices["SPY"]["Close"]
    iwm = prices["IWM"]["Close"]
    x = track(base, prices, edates, base_date, spy)
    spy_ret = spy.iloc[-1] / spy[spy.index <= base_date].iloc[-1] - 1
    iwm_ret = iwm.iloc[-1] / iwm[iwm.index <= base_date].iloc[-1] - 1
    x.to_csv(out.with_suffix(".csv"))

    pd.set_option("display.width", 250)
    v = x.copy()
    for c in ("ret", "excess", "earn_reaction"):
        v[c] = (v[c] * 100).round(1)
    for c in ("pos_then", "pos_now"):
        v[c] = (v[c] * 100).round(0)
    print(f"기준일 {base_date:%Y-%m-%d} → {last:%Y-%m-%d} · S&P500 {spy_ret * 100:+.1f}% · 러셀2000(IWM) {iwm_ret * 100:+.1f}%")
    print(v[["label", "price_then", "price_now", "ret", "excess", "pos_then", "pos_now", "status", "broke", "earn_date",
             "earn_reaction", "next_earnings", "rsi"]].round(2).sort_values("ret", ascending=False).to_string())

    # ---- HTML
    def pc(val):
        return "" if val is None or not np.isfinite(val) else f"{val * 100:+.1f}%"

    def color(val):
        if not np.isfinite(val):
            return ""
        a = min(abs(val) / 0.2, 1) * 0.35
        return f"background:rgba({'227,73,72' if val > 0 else '42,120,214'},{a:.2f})"

    trs = ""
    for t, r in x.sort_values(["label", "ret"], ascending=[True, False]).iterrows():
        ed = "" if pd.isna(r["earn_date"]) else f"{r['earn_date']:%m/%d} {pc(r['earn_reaction'])}"
        ne = "" if r["next_earnings"] is None or pd.isna(r["next_earnings"]) else f"{r['next_earnings']:%m/%d}"
        warn = "⚠️ 박스 깨짐" if r["broke"] else ("하단 아래" if r["pos_now"] < 0 else "")
        trs += (f"<tr><td><b>{html.escape(t)}</b><br><span class='note'>{html.escape(str(r['label']))}</span></td>"
                f"<td style='text-align:left'>{html.escape(str(r['name']))}</td>"
                f"<td>${r['price_then']:,.2f} → ${r['price_now']:,.2f}</td><td style='{color(r['ret'])}'><b>{pc(r['ret'])}</b></td>"
                f"<td>{pc(r['excess'])}</td><td>{r['pos_then'] * 100:.0f}% → {r['pos_now'] * 100:.0f}%<br>"
                f"<span class='note'>{r['status']}</span></td><td>${r['low']:,.2f} ~ ${r['high']:,.2f}</td>"
                f"<td>{ed}</td><td>{ne}</td><td>{r['rsi']:.0f}</td><td>{warn}</td></tr>")
    table = ("<div class='card tbl'><table style='font-size:12.5px'><tr><th>종목</th><th style='text-align:left'>이름</th>"
             "<th>가격</th><th>등락</th><th>S&P 대비</th><th>박스 위치</th><th>박스</th><th>기준일 이후 실적 (반응)</th>"
             "<th>다음 실적</th><th>RSI</th><th>경고</th></tr>" + trs + "</table>"
             f"<p class='note'>박스 깨짐 = 기준일 이후 종가가 박스 하단보다 {BREAK * 100:.0f}% 넘게 내려간 적이 있음. "
             "실적 반응 = 발표 전날 종가 → 다음날 종가.</p></div>")
    figs = ""
    for t in x.sort_values("ret").index:
        df = prices[t][prices[t].index > base_date - pd.DateOffset(months=6)]
        r = x.loc[t]
        fig = charts.price_rsi_chart(df, None, None, edates.get(t), f"{t} · {r['name']} ({pc(r['ret'])} since {base_date:%m/%d})")
        fig.add_hrect(y0=r["low"], y1=r["high"], fillcolor="#eda100", opacity=0.08, line_width=0, row=1, col=1)
        fig.add_hline(y=r["low"], line=dict(color="#eda100", width=1.2, dash="dash"), row=1, col=1)
        fig.add_hline(y=r["low"] * (1 - BREAK), line=dict(color="#2a78d6", width=1, dash="dot"), row=1, col=1)
        fig.add_vline(x=base_date, line=dict(color="#eb6834", width=1.2, dash="dot"), row=1, col=1)
        figs += f"<h3>{html.escape(t)}</h3><div class='card'>" + report.pio.to_html(
            fig, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}) + "</div>"
    intro = (f"기준일 {base_date:%Y-%m-%d} → {last:%Y-%m-%d} · S&P 500 {pc(spy_ret)} · 러셀2000 {pc(iwm_ret)} · "
             f"평균 {pc(x['ret'].mean())}, 오른 종목 {int((x['ret'] > 0).sum())}/{len(x)}")
    sections = [("종목별 변화", table), ("차트 (주황 점선 = 기준일, 노란 점선 = 박스 하단, 파란 점선 = 박스 깨짐 기준)", figs)]
    out.write_text(report.render_html([], "관심 종목 추적", intro, sections, inline_js=args.inline_js), encoding="utf-8")
    print(f"리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
