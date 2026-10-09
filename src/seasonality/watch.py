"""매일 후보 모니터링: 날짜별 기록 누적, 어제 대비 신규·제외, 이전 후보의 이후 성과 추적.

기록(history) 한 줄 = 그날 어떤 목록(box | accum)에 몇 위로 올라온 종목.
  date, list, rank, ticker, name, price, score, box_low, box_high
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HIST_COLS = ["date", "list", "rank", "ticker", "name", "price", "score", "box_low", "box_high"]
LIST_NAMES = {"box": "박스 하단", "accum": "매집 흔적"}


def picks_from_box(top: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """box_picks.csv (index = ticker) → 기록 형식."""
    return pd.DataFrame({
        "date": pd.Timestamp(date).normalize(), "list": "box", "rank": np.arange(1, len(top) + 1),
        "ticker": top.index, "name": top["name"].fillna("").to_numpy(), "price": top["price"].to_numpy(),
        "score": top["total"].to_numpy(), "box_low": top["low"].to_numpy(), "box_high": top["high"].to_numpy(),
    })


def picks_from_accum(top: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """accumulation.csv (index = ticker) → 기록 형식."""
    return pd.DataFrame({
        "date": pd.Timestamp(date).normalize(), "list": "accum", "rank": np.arange(1, len(top) + 1),
        "ticker": top.index, "name": top["name"].fillna("").to_numpy(), "price": top["close"].to_numpy(),
        "score": top["score"].to_numpy(), "box_low": np.nan, "box_high": np.nan,
    })


def append_history(hist: pd.DataFrame, today: pd.DataFrame) -> pd.DataFrame:
    """같은 날짜·목록을 다시 돌리면 덮어쓴다."""
    if hist is None or hist.empty:
        return today[HIST_COLS].reset_index(drop=True)
    keys = set(zip(today["date"], today["list"]))
    keep = hist[[k not in keys for k in zip(hist["date"], hist["list"])]]
    out = pd.concat([keep, today[HIST_COLS]], ignore_index=True)
    return out.sort_values(["date", "list", "rank"]).reset_index(drop=True)


def read_history(path) -> pd.DataFrame:
    try:
        h = pd.read_csv(path, parse_dates=["date"])
    except FileNotFoundError:
        return pd.DataFrame(columns=HIST_COLS)
    return h[HIST_COLS]


def run_dates(hist: pd.DataFrame, list_name: str) -> list[pd.Timestamp]:
    return sorted(hist.loc[hist["list"] == list_name, "date"].unique())


def changes(hist: pd.DataFrame, list_name: str) -> tuple[list[str], list[str]]:
    """가장 최근 실행 vs 그 전 실행: (새로 들어온 종목, 빠진 종목)."""
    dates = run_dates(hist, list_name)
    if not dates:
        return [], []
    h = hist[hist["list"] == list_name]
    cur = list(h.loc[h["date"] == dates[-1], "ticker"])
    if len(dates) < 2:
        return cur, []
    prev = set(h.loc[h["date"] == dates[-2], "ticker"])
    return [t for t in cur if t not in prev], sorted(prev - set(cur))


def streaks(hist: pd.DataFrame, list_name: str) -> dict[str, int]:
    """최근 실행까지 연속으로 목록에 있었던 실행 횟수."""
    dates = run_dates(hist, list_name)
    if not dates:
        return {}
    h = hist[hist["list"] == list_name]
    by_date = {d: set(h.loc[h["date"] == d, "ticker"]) for d in dates}
    out = {}
    for t in by_date[dates[-1]]:
        n = 0
        for d in reversed(dates):
            if t not in by_date[d]:
                break
            n += 1
        out[t] = n
    return out


def track(hist: pd.DataFrame, list_name: str, closes: dict[str, pd.Series], bench: pd.Series,
          asof: pd.Timestamp, lookback_days: int = 180) -> pd.DataFrame:
    """최근 lookback_days 안에 처음 목록에 오른 종목: 처음 오른 날 종가에 샀다면 지금까지 성과.

    status: 박스 이탈(박스 하단보다 20% 넘게 아래) · 상단 도달(이후 박스 상단의 95% 이상 찍음) · 목록 유지 · 목록 제외
    """
    h = hist[hist["list"] == list_name].sort_values("date")
    if h.empty:
        return pd.DataFrame()
    dates = run_dates(hist, list_name)
    today = set(h.loc[h["date"] == dates[-1], "ticker"])
    first = h.groupby("ticker").first()
    first = first[first["date"] >= pd.Timestamp(asof) - pd.Timedelta(days=lookback_days)]
    bench = bench.dropna()
    rows = []
    for t, r in first.iterrows():
        c = closes.get(t)
        if c is None or c.dropna().empty:
            continue
        c = c.dropna()
        since = c[c.index > r["date"]]
        cur = float(c.iloc[-1])
        ret = cur / r["price"] - 1
        b0 = bench[bench.index <= r["date"]]
        b_ret = float(bench.iloc[-1] / b0.iloc[-1] - 1) if len(b0) else np.nan
        peak = float(since.max()) if len(since) else cur
        trough = float(since.min()) if len(since) else cur
        status = "목록 유지" if t in today else "목록 제외"
        if pd.notna(r["box_low"]) and cur < r["box_low"] * 0.8:
            status = "박스 이탈"
        elif pd.notna(r["box_high"]) and peak >= r["box_high"] * 0.95:
            status = "상단 도달"
        rows.append({
            "ticker": t, "name": r["name"], "first_date": r["date"], "first_rank": int(r["rank"]),
            "first_price": float(r["price"]), "price": cur, "ret": ret, "bench_ret": b_ret, "excess": ret - b_ret,
            "max_up": peak / r["price"] - 1, "max_down": trough / r["price"] - 1,
            "days": int((pd.Timestamp(asof) - r["date"]).days), "status": status,
            "box_low": r["box_low"], "box_high": r["box_high"],
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["first_date", "first_rank"], ascending=[False, True]).reset_index(drop=True)


AGE_BUCKETS = [(0, 7, "1주 이내"), (8, 30, "1주~1개월"), (31, 90, "1~3개월"), (91, 10_000, "3개월 넘음")]


def track_summary(tr: pd.DataFrame) -> pd.DataFrame:
    """처음 오른 뒤 경과 기간별: 종목 수, 평균 수익률, 평균 초과수익(S&P 500 대비), 시장을 이긴 비율."""
    rows = []
    for lo, hi, label in AGE_BUCKETS:
        x = tr[tr["days"].between(lo, hi)] if len(tr) else tr
        if len(x) == 0:
            continue
        rows.append({"bucket": label, "n": len(x), "ret": x["ret"].mean(), "excess": x["excess"].mean(),
                     "win": (x["excess"] > 0).mean(), "broke": (x["status"] == "박스 이탈").mean(),
                     "top": (x["status"] == "상단 도달").mean()})
    return pd.DataFrame(rows)
