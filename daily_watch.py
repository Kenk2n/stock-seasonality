"""매일 후보 모니터링 페이지 만들기 (박스 하단 후보 + 매집 흔적 + 이전 후보 추적).

  python daily_watch.py                  # 스캔 2개 실행 → site/index.html
  python daily_watch.py --skip-scan      # 이미 만든 site/*.csv 로 페이지만 다시 만들기

결과 (site/):
  index.html             오늘의 후보, 어제 대비 변화, 이전 후보의 이후 성과
  box_picks.html         박스 하단 후보 전체 리포트 (차트 포함)
  accumulation.html      매집 흔적 리포트 (차트 포함)
  data/history.csv       날짜별 후보 기록 (매일 누적, 성과 추적에 사용)

GitHub Actions(.github/workflows/daily-watch.yml)가 평일 미국 장 마감 후 실행해서 gh-pages 브랜치에 올립니다.
"""

from __future__ import annotations

import argparse
import ast
import html
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from seasonality import data, report, watch  # noqa: E402
from seasonality.watch import badge, pct  # noqa: E402

KST = timezone(timedelta(hours=9))
BENCH = "SPY"


def prune_cache(max_age_days: float) -> int:
    """실적일·내부자 거래 캐시가 오래되면 지워서 다시 받게 한다 (다음 실적일이 바뀌기 때문)."""
    cutoff = time.time() - max_age_days * 86400
    n = 0
    for d, pat in [(Path("data/earnings"), "*.json"), (Path("data/insider"), "*.parquet")]:
        for p in d.glob(pat) if d.exists() else []:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
    return n


def run_scan(cmd: list[str], outputs: list[Path]) -> bool:
    for p in outputs:
        p.unlink(missing_ok=True)
    print("$", " ".join(cmd), flush=True)
    ok = subprocess.run([sys.executable, *cmd], cwd=ROOT).returncode == 0
    return ok and all(p.exists() for p in outputs)


def read_csv(path: Path) -> pd.DataFrame | None:
    return pd.read_csv(path, index_col=0) if path.exists() else None


def load_closes(tickers: list[str], asof: pd.Timestamp) -> dict[str, pd.Series]:
    """추적용 종가. 오늘 스캔에서 갱신 안 된(목록에서 빠진) 종목만 다시 받는다."""
    px = data.update_ohlc(tickers)
    stale = [t for t in tickers if t not in px or px[t].index[-1] < asof]
    if stale:
        px.update(data.update_ohlc(stale, refresh=True))
    return {t: df["Close"] for t, df in px.items()}


def flags_list(v) -> list[str]:
    if isinstance(v, str) and v.startswith("["):
        try:
            return list(ast.literal_eval(v))
        except (ValueError, SyntaxError):
            return []
    return []


def ticker_cell(t: str, new: set[str], streak: dict[str, int], extra: str = "") -> str:
    b = badge("NEW", "new") if t in new else (badge(f"{streak[t]}일째") if streak.get(t, 0) > 1 else "")
    url = f"https://finance.yahoo.com/quote/{t}"
    return f"<a href='{url}' style='color:inherit'><b>{html.escape(t)}</b></a>{b}{extra}"


def box_table(top: pd.DataFrame, new, streak) -> str:
    trs = ""
    for i, (t, r) in enumerate(top.iterrows(), 1):
        ne = "" if pd.isna(r.get("next_earnings")) else pd.Timestamp(r["next_earnings"]).strftime("%m/%d")
        tier = badge("B급", "dim") if r.get("tier") == "B" else ""
        trs += (
            f"<tr><td>{i}. {ticker_cell(t, new, streak, tier)}</td>"
            f"<td class='l'>{html.escape(str(r.get('name') or ''))}<br><span class='note'>{html.escape(str(r.get('industry') or ''))}</span></td>"
            f"<td><b>{r['total']:.0f}</b></td>"
            f"<td>${r['low']:,.2f} ~ ${r['high']:,.2f}<br><span class='note'>{int(r['years'])}년 · {r['band']:.1f}배 · {int(r['legs']) // 2}왕복</span></td>"
            f"<td>${r['price']:,.2f}<br><span class='note'>위치 {r['pos'] * 100:.0f}%</span></td>"
            f"<td>{pct(r['to_high'], digits=0)}</td><td>{r['rsi']:.0f}</td><td>{ne}</td>"
            f"<td class='l'>{html.escape(' · '.join(flags_list(r.get('flags'))) or '-')}</td></tr>"
        )
    return (
        "<div class='card tbl'><table><tr><th>종목</th><th class='l'>이름 / 업종</th><th>점수</th><th>박스 (기간·폭·왕복)</th>"
        "<th>현재가</th><th>박스 상단까지</th><th>RSI</th><th>다음 실적</th><th class='l'>주의</th></tr>" + trs + "</table>"
        "<p class='note'>위치 0% = 박스 하단, 100% = 상단. 점수 = 박스 품질 35 + 하단 근접 20 + KRUS 닮음 20 + 매집 흔적 15 "
        "+ 거래량 증가 10 − 위험 감점. B급 = 박스 하단 아래·3개월 급락·공매도 과다 중 하나 이상. "
        "NEW = 이번에 새로 들어옴, N일째 = 연속으로 목록에 있음.</p></div>"
    )


def combo_table(all_box: pd.DataFrame, min_accum: float = 0.7, n: int = 10) -> str:
    x = all_box[all_box["s_accum"] >= min_accum].sort_values("total", ascending=False).head(n)
    if x.empty:
        return "<div class='card'><p>오늘은 박스 하단이면서 매집 흔적이 강한 종목이 없습니다.</p></div>"
    trs = "".join(
        f"<tr><td><b>{html.escape(t)}</b></td><td class='l'>{html.escape(str(r.get('name') or ''))}</td>"
        f"<td>{r['total']:.0f}</td><td>{r['s_accum'] * 100:.0f}</td><td>{r['stealth']:+.2f}</td><td>{r['cmf']:+.2f}</td>"
        f"<td>{r['vol_ratio']:.2f}배</td><td>${r['price']:,.2f}<br><span class='note'>위치 {r['pos'] * 100:.0f}%</span></td>"
        f"<td>{pct(r['to_high'], digits=0)}</td></tr>"
        for t, r in x.iterrows()
    )
    return (
        "<div class='card tbl'><table><tr><th>종목</th><th class='l'>이름</th><th>박스 점수</th><th>매집 점수</th>"
        "<th>거래량 쏠림</th><th>CMF</th><th>거래량 20일/120일</th><th>현재가</th><th>박스 상단까지</th></tr>" + trs + "</table>"
        f"<p class='note'>박스 하단 근처 후보 전체 중 매집 점수(0~100, 거래량 쏠림 + CMF 를 전 종목과 비교) {min_accum * 100:.0f} 이상, "
        "박스 점수 순.</p></div>"
    )


def accum_table(top: pd.DataFrame, new, streak, box_set: set[str]) -> str:
    trs = ""
    for i, (t, r) in enumerate(top.iterrows(), 1):
        extra = badge("박스 하단", "good") if t in box_set else ""
        ib = r.get("insider_buy_90d")
        trs += (
            f"<tr><td>{i}. {ticker_cell(t, new, streak, extra)}</td>"
            f"<td class='l'>{html.escape(str(r.get('name') or ''))}<br><span class='note'>{html.escape(str(r.get('industry') or ''))}</span></td>"
            f"<td><b>{r['score']:.2f}</b></td><td>{r['stealth']:+.2f}</td><td>{r['cmf']:+.2f}</td><td>{int(r['absorption'])}</td>"
            f"<td>{pct(r['ret'], digits=0)}</td><td>{r['pos52'] * 100:.0f}%</td>"
            f"<td>{pct(r.get('short_float'), signed=False, digits=0)}</td>"
            f"<td>{'' if not ib or not np.isfinite(ib) else f'${ib / 1e3:,.0f}K'}</td></tr>"
        )
    return (
        "<div class='card tbl'><table><tr><th>종목</th><th class='l'>이름 / 업종</th><th>점수</th><th>거래량 쏠림</th><th>CMF</th>"
        "<th>대량거래 일수</th><th>3개월 수익률</th><th>52주 위치</th><th>공매도/유통</th><th>내부자 매수 90일</th></tr>"
        + trs + "</table><p class='note'>거래량 쏠림 = 주가는 별로 안 올랐는데 오르는 날 거래량이 많음. "
        "CMF = 종가가 하루 범위 위쪽에서 끝난 날의 거래량 비중. '박스 하단' 표시 = 박스 하단 후보에도 들어감.</p></div>"
    )


def changes_html(hist: pd.DataFrame) -> str:
    items = []
    for key, label in watch.LIST_NAMES.items():
        new, dropped = watch.changes(hist, key)
        if len(watch.run_dates(hist, key)) < 2:
            items.append(f"<li><b>{label}</b>: 첫 기록이라 비교할 이전 날짜가 없습니다.</li>")
            continue
        items.append(f"<li><b>{label}</b> · 신규 {len(new)}: {html.escape(', '.join(new)) or '없음'}"
                     f" · 제외 {len(dropped)}: {html.escape(', '.join(dropped)) or '없음'}</li>")
    return "<div class='card'><ul class='find'>" + "".join(items) + "</ul></div>"


def track_html(tr: pd.DataFrame, label: str, max_rows: int = 40) -> str:
    if tr.empty:
        return f"<h3>{label}</h3><div class='card'><p class='note'>아직 추적할 기록이 없습니다. 매일 쌓이면서 채워집니다.</p></div>"
    sm = watch.track_summary(tr)
    srows = "".join(
        f"<tr><td>{r['bucket']}</td><td>{r['n']}</td><td>{pct(r['ret'])}</td><td>{pct(r['excess'])}</td>"
        f"<td>{r['win'] * 100:.0f}%</td><td>{r['top'] * 100:.0f}%</td><td>{r['broke'] * 100:.0f}%</td></tr>"
        for _, r in sm.iterrows()
    )
    drows = "".join(
        f"<tr><td><b>{html.escape(r['ticker'])}</b>{badge(r['status'], watch.STATUS_KIND.get(r['status'], ''))}</td>"
        f"<td class='l'>{html.escape(str(r['name'] or ''))}</td><td>{r['first_date']:%m/%d} ({r['first_rank']}위)</td>"
        f"<td>${r['first_price']:,.2f}</td><td>${r['price']:,.2f}</td><td>{pct(r['ret'])}</td><td>{pct(r['excess'])}</td>"
        f"<td>{pct(r['max_up'])} / {pct(r['max_down'])}</td><td>{r['days']}일</td></tr>"
        for _, r in tr.head(max_rows).iterrows()
    )
    return (
        f"<h3>{label}</h3><div class='card tbl'><table><tr><th>처음 오른 뒤</th><th>종목 수</th><th>평균 수익률</th>"
        "<th>S&amp;P 500 대비</th><th>시장을 이긴 비율</th><th>상단 도달</th><th>박스 이탈</th></tr>" + srows + "</table></div>"
        "<div class='card tbl'><table><tr><th>종목</th><th class='l'>이름</th><th>처음 오른 날</th><th>그날 종가</th><th>현재가</th>"
        "<th>수익률</th><th>S&amp;P 500 대비</th><th>최고 / 최저</th><th>경과</th></tr>" + drows + "</table>"
        + (f"<p class='note'>최근 {max_rows}개만 표시. 전체는 data/history.csv.</p>" if len(tr) > max_rows else "") + "</div>"
    )


EVIDENCE = (
    "<div class='card'><ul class='find'>"
    "<li><b>이 목록은 조건 검색 결과이며 매수 추천이 아닙니다.</b> 매수 전에 실적·재무·뉴스를 꼭 확인하세요.</li>"
    "<li>과거 검증: 박스 하단에서 산 경우 3개월 후 시장 대비 +1.9% 였지만 통계적으로 의미가 없었고, 박스 상단에서 산 경우와도 차이가 없었습니다.</li>"
    "<li>박스 아래로 25% 넘게 이탈한 종목은 이후 3개월 시장 대비 −10.7% 였습니다. '박스 이탈' 표시가 붙으면 박스가 깨진 것으로 보세요.</li>"
    "<li>매집 흔적(거래량 쏠림·CMF 등)은 2022.11~2026.6 검증에서 이후 수익률을 예측하지 못했습니다.</li>"
    "<li>'이전 후보 추적'은 이 페이지가 실제로 뽑은 종목의 이후 성과입니다. 몇 달 쌓이면 이 방법이 통하는지 직접 확인할 수 있습니다.</li>"
    "</ul></div>"
)


def tiles(items: list[tuple[str, str]]) -> str:
    return "<div class='tiles'>" + "".join(f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in items) + "</div>"


def main() -> int:
    p = argparse.ArgumentParser(description="매일 후보 모니터링 페이지")
    p.add_argument("--site", default="site")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--lookback", type=int, default=180, help="성과 추적 기간(일)")
    p.add_argument("--cache-days", type=float, default=7, help="실적일·내부자 캐시를 이 일수가 지나면 다시 받기")
    p.add_argument("--skip-scan", action="store_true", help="스캔 없이 site/ 의 CSV 로 페이지만 만들기")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    site = Path(args.site)
    (site / "data").mkdir(parents=True, exist_ok=True)
    (site / ".nojekyll").touch()
    box_csv, box_all_csv, acc_csv = site / "box_picks.csv", site / "box_picks_all.csv", site / "accumulation.csv"

    status = {}
    if not args.skip_scan:
        print(f"오래된 실적일·내부자 캐시 {prune_cache(args.cache_days)}개 삭제")
        status["box"] = run_scan(["box_picks.py", "--refresh", "--top", str(args.top), "--out", str(site / "box_picks.html")],
                                 [box_csv, box_all_csv])
        # 박스 스캔이 방금 가격을 갱신했으므로 매집 스캔은 캐시를 그대로 쓴다
        status["accum"] = run_scan(["accumulation_scan.py", "--exchanges", "us", "--top", str(args.top), "--charts", "10",
                                    "--out", str(site / "accumulation.html")], [acc_csv])
    box_top, box_all, acc_top = read_csv(box_csv), read_csv(box_all_csv), read_csv(acc_csv)

    bench = data.update_ohlc([BENCH], refresh=True)[BENCH]["Close"]
    asof = bench.index[-1]
    hist_path = site / "data" / "history.csv"
    hist = watch.read_history(hist_path)
    today = []
    if box_top is not None and status.get("box", True):
        today.append(watch.picks_from_box(box_top, asof))
    if acc_top is not None and status.get("accum", True):
        today.append(watch.picks_from_accum(acc_top, asof))
    if today:
        hist = watch.append_history(hist, pd.concat(today, ignore_index=True))
        hist.to_csv(hist_path, index=False, date_format="%Y-%m-%d")

    recent = hist[hist["date"] >= asof - pd.Timedelta(days=args.lookback)]
    closes = load_closes(sorted(recent["ticker"].unique()), asof) if len(recent) else {}
    tracks = {k: watch.track(hist, k, closes, bench, asof, args.lookback) for k in watch.LIST_NAMES}

    # ---- 페이지
    box_new, _ = watch.changes(hist, "box")
    acc_new, _ = watch.changes(hist, "accum")
    first_run = {k: len(watch.run_dates(hist, k)) < 2 for k in watch.LIST_NAMES}
    tr_box = tracks["box"]
    old = tr_box[tr_box["days"] >= 30] if len(tr_box) else tr_box
    sections = []
    tile_items = [
        ("기준일 (미국 장 마감)", f"{asof:%Y-%m-%d}"),
        ("박스 하단 후보", f"{0 if box_top is None else len(box_top)}개" + ("" if first_run["box"] else f" · 신규 {len(box_new)}")),
        ("매집 흔적 상위", f"{0 if acc_top is None else len(acc_top)}개" + ("" if first_run["accum"] else f" · 신규 {len(acc_new)}")),
        ("30일 넘은 박스 후보 평균 (S&P 대비)",
         "기록 쌓는 중" if old.empty else f"{old['excess'].mean() * 100:+.1f}% · {len(old)}개"),
    ]
    head = f"<style>{watch.WATCH_CSS}</style>" + tiles(tile_items) + (
        "<p class='links'><a href='box_picks.html'>박스 하단 후보 전체 리포트 (차트) →</a>"
        "<a href='accumulation.html'>매집 흔적 리포트 (차트) →</a><a href='data/history.csv'>기록 CSV</a></p>")
    failed = [watch.LIST_NAMES[k] for k, ok in status.items() if not ok]
    if failed:
        head += f"<div class='card'><p><b>오늘 {', '.join(failed)} 스캔이 실패했습니다.</b> 아래 목록은 비어 있거나 이전 결과일 수 있습니다.</p></div>"
    sections.append(("한눈에", head))

    if box_top is not None:
        sections.append((f"박스 하단 후보 {len(box_top)}개",
                         box_table(box_top, set() if first_run["box"] else set(box_new), watch.streaks(hist, "box"))))
    if box_all is not None:
        sections.append(("박스 하단 + 매집 흔적 강함", combo_table(box_all)))
    if acc_top is not None:
        box_set = set(box_all.index) if box_all is not None else set()
        sections.append((f"매집 흔적 상위 {len(acc_top)}개",
                         accum_table(acc_top, set() if first_run["accum"] else set(acc_new), watch.streaks(hist, "accum"), box_set)))
    sections.append(("어제와 달라진 점", changes_html(hist)))
    sections.append(("이전 후보 추적 (처음 오른 날 종가에 샀다면)",
                     track_html(tracks["box"], "박스 하단 후보") + track_html(tracks["accum"], "매집 흔적 상위")))
    sections.append(("읽는 법과 주의", EVIDENCE))

    updated = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    intro = (f"미국 상장 시총 $3억 이상 · 거래대금 $500만 이상 · 주가 $5 이상 종목 대상. "
             f"평일 미국 장 마감 후 자동 갱신 (마지막 갱신 {updated} KST).")
    (site / "index.html").write_text(report.render_html([], "오늘의 박스 하단 · 매집 후보", intro, sections), encoding="utf-8")
    print(f"페이지: {site / 'index.html'} (기준일 {asof:%Y-%m-%d}, 기록 {len(hist)}줄)")
    return 0 if all(status.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
