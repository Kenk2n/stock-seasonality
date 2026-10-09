"""매일 후보 모니터링 사이트 만들기: 박스 하단권 · 매집 흔적 두 갈래, 매수 타이밍 순위, 매수 주의, 지표 차트.

  python daily_watch.py                   # 데이터 갱신 + 스캔 → site/
  python daily_watch.py --no-refresh      # 캐시된 주가로 (개발·확인용)

결과 (site/):
  index.html, app.js, style.css, vendor/   웹 화면 (web/ 폴더를 그대로 복사)
  data/latest.json                         오늘의 후보 두 목록 + 지표·신호·위험·뉴스 + 업황 + 이전 후보 성과
  data/charts/<종목>.json                  차트용 일봉 + 지표
  data/history.csv                         날짜별 후보 기록 (매일 누적, 성과 추적용)

GitHub Actions(.github/workflows/daily-watch.yml)가 평일 미국 장 마감 후 실행해서 gh-pages 브랜치에 올립니다.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from seasonality import accumulation as acc  # noqa: E402
from seasonality import data, earnings, indicators, picks, risk, timing, universe, watch  # noqa: E402

KST = timezone(timedelta(hours=9))
log = logging.getLogger("daily_watch")

CHART_IND = {  # 차트에 싣는 지표와 소수 자릿수 (None = 가격 자릿수)
    "sma20": None, "sma50": None, "sma200": None, "bb_up": None, "bb_mid": None, "bb_low": None,
    "st_line": None, "st_dir": 0, "avwap": None, "rsi": 1, "macd": 4, "macd_signal": 4, "macd_hist": 4,
    "stoch_k": 1, "stoch_d": 1, "adx": 1, "plus_di": 1, "minus_di": 1, "mfi": 1, "obv": 0,
}


# ---------------------------------------------------------------- 유틸
def clean(o):
    """JSON 으로 쓸 수 있게: NaN/inf → None, numpy·pandas 값 → 파이썬 값."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not math.isfinite(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return None if pd.isna(o) else o.strftime("%Y-%m-%d")
    if o is pd.NaT:
        return None
    return o


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def cached_profile(t: str, cache_dir: Path = Path("data/profile"), max_age_h: float = 20) -> dict:
    p = cache_dir / f"{t}.json"
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return json.loads(p.read_text())
    try:
        prof = acc.holder_info(t)
    except Exception as e:
        log.warning("%s 프로필 실패: %s", t, e)
        prof = {"ticker": t}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean(prof)))
    return prof


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


def price_decimals(c: pd.Series) -> int:
    m = float(c.tail(250).median())
    return 2 if m >= 10 else 3 if m >= 1 else 4


def rlist(s: pd.Series, nd: int) -> list:
    a = s.to_numpy(dtype=float)
    return [None if not np.isfinite(v) else (int(round(v)) if nd == 0 else round(float(v), nd)) for v in a]


def chart_payload(t: str, df: pd.DataFrame, ind: pd.DataFrame, eds, box_row=None, first_seen=None) -> dict:
    nd = price_decimals(df["Close"])
    out = {
        "t": t, "nd": nd, "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        "o": rlist(df["Open"], nd), "h": rlist(df["High"], nd), "l": rlist(df["Low"], nd), "c": rlist(df["Close"], nd),
        "v": rlist(df["Volume"].fillna(0), 0),
        "ind": {k: rlist(ind[k], nd if d is None else d) for k, d in CHART_IND.items()},
        "earnings": [d.strftime("%Y-%m-%d") for d in pd.DatetimeIndex(eds) if df.index[0] <= d <= df.index[-1] + pd.Timedelta(days=120)],
        "first_seen": first_seen,
    }
    if box_row is not None:
        start = df.index[-1] - pd.DateOffset(years=int(box_row["years"]))
        out["box"] = {"lm": box_row["lm"], "hm": box_row["hm"], "low_zone": list(box_row["low_zone"]),
                      "high_zone": list(box_row["high_zone"]), "start": start.strftime("%Y-%m-%d"),
                      "stop": box_row["stop"], "target": box_row["target"], "swings": box_row["swings"]}
    return out


def weekly_spark(c: pd.Series, years: int, asof: pd.Timestamp) -> dict:
    """박스 기간의 주봉 종가 (차트 모아보기용)."""
    w = c[c.index > asof - pd.DateOffset(years=years)].resample("W-FRI").last().dropna()
    return {"start": w.index[0].strftime("%Y-%m-%d"), "end": w.index[-1].strftime("%Y-%m-%d"),
            "v": rlist(w, price_decimals(c))}


# ---------------------------------------------------------------- 메인
def main() -> int:
    p = argparse.ArgumentParser(description="매일 후보 모니터링 사이트")
    p.add_argument("--site", default="site")
    p.add_argument("--web", default=str(ROOT / "web"), help="웹 화면 파일 폴더")
    p.add_argument("--no-refresh", action="store_true", help="주가를 새로 받지 않고 캐시 사용")
    p.add_argument("--box-min-mcap", type=float, default=1.5e8)
    p.add_argument("--box-min-dv", type=float, default=2e6)
    p.add_argument("--box-min-price", type=float, default=3.0)
    p.add_argument("--acc-min-mcap", type=float, default=1e7)
    p.add_argument("--acc-min-dv", type=float, default=2e5)
    p.add_argument("--acc-min-price", type=float, default=0.5)
    p.add_argument("--acc-top", type=int, default=40)
    p.add_argument("--box-max", type=int, default=40)
    p.add_argument("--lookback", type=int, default=180, help="성과 추적 기간(일)")
    p.add_argument("--cache-days", type=float, default=7)
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    t0 = time.time()
    site = Path(args.site)

    def step(msg):
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    step(f"오래된 실적일·내부자 캐시 {prune_cache(args.cache_days)}개 삭제")

    # 1) 종목 목록과 주가
    meta = universe.screen_universe(args.acc_min_mcap, args.acc_min_dv, args.acc_min_price, "us").set_index("ticker")
    box_meta = meta[(meta["market_cap"] >= args.box_min_mcap) & (meta["dollar_volume"] >= args.box_min_dv)
                    & (meta["price"] >= args.box_min_price)]
    step(f"종목 목록: 전체 {len(meta)}개 (박스 대상 {len(box_meta)}개)")
    prices = data.update_ohlc(sorted(set(meta.index) | {picks.REF}), refresh=not args.no_refresh,
                              on_progress=lambda d, n: print(f"\r  주가 {d}/{n}", end="", flush=True))
    print()
    etf_px = data.update_ohlc(risk.all_etfs(), refresh=not args.no_refresh)
    bench = etf_px["SPY"]["Close"]
    asof = bench.index[-1]
    prices = {t: df for t, df in prices.items() if len(df) > 260 and df.index[-1] >= asof - pd.Timedelta(days=7)}
    step(f"주가 {len(prices)}개 · 기준일 {asof:%Y-%m-%d}")

    # 2) 박스 하단 후보
    box_prices = {t: prices[t] for t in set(box_meta.index) | {picks.REF} if t in prices and len(prices[t]) > 500}
    sig = acc.panel(prices)
    box_snap = acc.snapshot({t: sig[t] for t in box_prices if t in sig}, asof, args.box_min_price, args.box_min_dv)
    box = picks.box_scan(box_prices, asof)
    box_x, eds = picks.box_candidates(box_prices, box, box_snap, asof)
    box_x = box_x.head(args.box_max)
    step(f"쿠라 스시형 박스 {len(box)}개 → 저점 구간 근처 {len(box_x)}개")

    # 3) 매집 흔적 (소형주 포함)
    snap = acc.snapshot(sig, asof, args.acc_min_price, args.acc_min_dv)
    snap = snap[~snap.index.isin(picks.pinned_tickers(snap))].sort_values("score", ascending=False)
    acc_x = snap.head(args.acc_top + 20).copy()

    # 4) 후보별 부가 정보 (기관·재무·뉴스·애널리스트·공시·실적일·내부자)
    with ThreadPoolExecutor(8) as ex:
        cand = sorted(set(box_x.index) | set(acc_x.index))
        profiles = dict(zip(cand, ex.map(cached_profile, cand)))
    # 스팩(SPAC) 등 껍데기 회사는 신탁 가격 근처에서만 움직여 의미가 없으므로 뺀다
    shells = {t for t, pr in profiles.items() if (pr.get("industry") or "") == "Shell Companies"}
    box_x = box_x[~box_x.index.isin(shells)]
    acc_x = acc_x[~acc_x.index.isin(shells)].head(args.acc_top)
    n = len(acc_x)
    acc_x["accum_pct"] = (n - np.arange(1, n + 1)) / max(n - 1, 1)  # 상위 목록 안 순위 (1위 = 1, 꼴찌 = 0)
    step(f"매집 흔적 비교 {len(snap)}개 → 상위 {len(acc_x)}개 (스팩 등 {len(shells)}개 제외)")

    tickers = sorted(set(box_x.index) | set(acc_x.index))
    cik_map = risk.load_sec_cik_map()
    with ThreadPoolExecutor(8) as ex:
        news = dict(zip(tickers, ex.map(risk.load_news, tickers)))
        analyst = dict(zip(tickers, ex.map(risk.load_analyst_actions, tickers)))
        for t, e in zip([t for t in tickers if t not in eds], ex.map(earnings.load_earnings_dates, [t for t in tickers if t not in eds])):
            eds[t] = e
        insider = dict(zip(tickers, ex.map(acc.load_insider_transactions, tickers)))
    sec = {t: risk.load_sec_filings(t, cik_map) for t in tickers} if cik_map else {t: [] for t in tickers}
    sector_tab = risk.sector_table({k: v["Close"] for k, v in etf_px.items()})
    step(f"부가 정보 {len(tickers)}개 (SEC 공시 {'사용' if cik_map else '불가'})")

    # 5) 기록 갱신 (신규·연속 일수·처음 오른 날)
    hist_path = site / "data" / "history.csv"
    hist = watch.read_history(hist_path)
    today = []
    if len(box_x):
        today.append(watch.picks_from_box(box_x.assign(name=[meta["name"].get(t, "") for t in box_x.index],
                                                       total=box_x["raw"]), asof))
    if len(acc_x):
        today.append(watch.picks_from_accum(acc_x.assign(name=[meta["name"].get(t, "") for t in acc_x.index]), asof))
    if today:
        hist = watch.append_history(hist, pd.concat(today, ignore_index=True))
    first_seen = {k: hist[hist["list"] == k].groupby("ticker")["date"].min() for k in watch.LIST_NAMES}
    streak = {k: watch.streaks(hist, k) for k in watch.LIST_NAMES}
    new = {}
    for k in watch.LIST_NAMES:
        nw, dropped = watch.changes(hist, k)
        new[k] = set(nw) if len(watch.run_dates(hist, k)) > 1 else set()
        new[k + "_dropped"] = dropped

    # 6) 종목별 지표·타이밍·위험 → 행 + 차트 파일
    charts_dir = site / "data" / "charts"
    if charts_dir.exists():
        shutil.rmtree(charts_dir)

    def common(t: str, df: pd.DataFrame, ind: pd.DataFrame, floor=None) -> dict:
        prof = profiles.get(t, {})
        c = df["Close"]
        price = float(c.iloc[-1])
        nxt = earnings.next_earnings(eds.get(t, pd.DatetimeIndex([])), asof) if len(eds.get(t, [])) else None
        dv = float((c * df["Volume"]).tail(60).median())
        etf = risk.etf_for(prof.get("sector"), prof.get("industry"))
        flags = (risk.news_flags(news.get(t, []), asof) + risk.sec_flags(sec.get(t, []), asof)
                 + risk.analyst_flags(analyst.get(t, []), asof) + risk.finance_flags(prof, price)
                 + risk.price_flags(df, None, nxt, asof, dv, floor=floor) + risk.sector_flags(etf, sector_tab))
        tx = insider.get(t)
        buys = acc.insider_buys(tx) if tx is not None and len(tx) else pd.DataFrame()
        ib90 = float(buys.loc[buys["date"] >= asof - pd.Timedelta(days=90), "value"].sum()) if len(buys) else 0.0
        sigs = indicators.signals(df, ind)
        return {
            "t": t, "name": prof.get("long_name") or meta["name"].get(t, ""), "sector": prof.get("sector"),
            "industry": prof.get("industry"), "country": prof.get("country"), "exchange": meta["exchange"].get(t),
            "mcap": meta["market_cap"].get(t), "price": price, "dv": dv,
            "chg1d": price / float(c.iloc[-2]) - 1, "ret1m": price / float(c.iloc[-22]) - 1,
            "ret3m": price / float(c.iloc[-64]) - 1, "ret1y": price / float(c.iloc[-252]) - 1 if len(c) > 252 else None,
            "ind": indicators.latest(df, ind), "signals": sigs,
            "bull": sum(s["tone"] == "bull" for s in sigs), "bear": sum(s["tone"] == "bear" for s in sigs),
            "risk": risk.summarize(flags), "next_earn": nxt, "earn_days": (nxt - asof).days if nxt is not None else None,
            "short_float": prof.get("short_float"), "inst_pct": prof.get("inst_pct"), "insider_buy_90d": ib90,
            "insider_net_6m": prof.get("insider_net_6m"), "target_mean": prof.get("target_mean"),
            "recommendation": prof.get("recommendation"), "n_analysts": prof.get("n_analysts"),
            "summary": prof.get("summary"), "etf": etf, "spark": rlist(c.tail(250), price_decimals(c)),
            "news": [{**n, "flag": max((lv for _, lv, _ in risk.classify_headline(n["title"])),
                                       key=risk.LEVEL_ORDER.get, default=None)} for n in news.get(t, [])[:8]],
            "analyst": analyst.get(t, [])[:6],
            "filings": [f for f in sec.get(t, []) if (asof - pd.Timestamp(f["date"])).days <= 60][:8],
        }

    rows = {"box": [], "accum": []}
    for t, r in box_x.iterrows():
        df = box_prices[t]
        ind = indicators.compute(df)
        tm = timing.box_timing(df, ind, r["pos"], r["stop"], r["target"])
        row = common(t, df, ind, floor=r["low_zone"][0])
        ok = [x for x in r["swings"] if x["ok"]]
        row.update({
            "score": r["raw"], "timing": tm["timing"], "parts": tm["parts"], "rr": tm["rr"], "stop": tm["stop"],
            "target": tm["target"], "stage": timing.stage(tm["timing"], row["risk"]["level"]),
            "box": {**{k: r[k] for k in ("years", "amp", "lm", "hm", "round_trips", "n_high", "n_low", "pos",
                                         "to_target", "to_stop", "valid_frac", "inside")},
                    "low_zone": list(r["low_zone"]), "high_zone": list(r["high_zone"]),
                    "highs": [x["p"] for x in ok if x["k"] == "H"], "lows": [x["p"] for x in ok if x["k"] == "L"],
                    "swings": r["swings"]},
            "is_ref": bool(r["is_ref"]), "spark_w": weekly_spark(df["Close"], int(r["years"]), asof),
            "krus": r["earn_dist"], "s": {k[2:]: r[k] for k in r.index if k.startswith("s_")},
            "stealth": r["stealth"], "cmf": r["cmf"], "vol_ratio": r["vol_ratio"], "in_accum": t in acc_x.index,
            "new": t in new["box"], "streak": streak["box"].get(t, 1), "first_seen": first_seen["box"].get(t),
        })
        rows["box"].append(row)
        write_json(charts_dir / f"{t}.json", chart_payload(t, df, ind, eds.get(t, []), r, row["first_seen"]))
    for t, r in acc_x.iterrows():
        df = prices[t]
        ind = indicators.compute(df)
        tm = timing.accum_timing(df, ind, r["accum_pct"])
        row = common(t, df, ind)
        row.update({
            "score": r["score"], "timing": tm["timing"], "parts": tm["parts"], "rr": tm["rr"], "stop": tm["stop"],
            "target": tm["target"], "stage": timing.stage(tm["timing"], row["risk"]["level"]), "vol_ratio5": tm["vol_ratio5"],
            "acc": {k: r[k] for k in ("stealth", "cmf", "absorption", "contraction", "ret", "pos52")},
            "in_box": t in box_x.index,
            "new": t in new["accum"], "streak": streak["accum"].get(t, 1), "first_seen": first_seen["accum"].get(t),
        })
        rows["accum"].append(row)
        if t not in box_x.index:
            write_json(charts_dir / f"{t}.json", chart_payload(t, df, ind, eds.get(t, []), None, row["first_seen"]))
    step(f"지표·타이밍·위험 계산 (박스 {len(rows['box'])}, 매집 {len(rows['accum'])})")

    # 7) 이전 후보 성과
    recent = hist[hist["date"] >= asof - pd.Timedelta(days=args.lookback)]
    need = sorted(set(recent["ticker"]) - set(prices))
    extra = data.update_ohlc(need, refresh=not args.no_refresh) if need else {}
    closes = {t: df["Close"] for t, df in {**prices, **extra}.items() if t in set(recent["ticker"])}
    tracking = {}
    for k in watch.LIST_NAMES:
        tr = watch.track(hist, k, closes, bench, asof, args.lookback)
        tracking[k] = {"rows": tr.to_dict("records") if len(tr) else [],
                       "summary": watch.track_summary(tr).to_dict("records") if len(tr) else []}

    # 8) 쓰기
    if Path(args.web).exists():
        shutil.copytree(args.web, site, dirs_exist_ok=True)
    (site / ".nojekyll").touch()
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist.to_csv(hist_path, index=False, date_format="%Y-%m-%d")
    latest = {
        "asof": asof, "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "universe": {"all": len(meta), "box": len(box_prices), "box_patterns": len(box), "accum": len(snap)},
        "filters": {k: v for k, v in vars(args).items() if k.startswith(("box_", "acc_"))},
        "lists": rows,
        "changes": {k: {"new": sorted(new[k]), "dropped": new[k + "_dropped"]} for k in watch.LIST_NAMES},
        "sectors": sector_tab.reset_index().sort_values("vs_spy3m").to_dict("records") if len(sector_tab) else [],
        "tracking": tracking,
        "risk_categories": risk.CATEGORIES, "sec_enabled": bool(cik_map),
        "weights": {"box": timing.BOX_WEIGHTS, "accum": timing.ACC_WEIGHTS, "labels": timing.LABELS},
    }
    write_json(site / "data" / "latest.json", latest)
    step(f"완료: {site / 'index.html'} (기록 {len(hist)}줄)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
