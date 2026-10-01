"""매집 흔적 신호 (가격·거래량 기반) 와 백테스트.

'세력'을 직접 볼 수는 없지만, 큰 자금이 조용히 살 때 남는 흔적은 측정할 수 있다.
모든 신호는 특정 날짜 시점까지의 데이터만 쓰므로(미래 데이터 X) 과거 시점으로 백테스트할 수 있다.

신호 (기본 60거래일 ≈ 3개월):
  vol_balance  상승일 거래량 − 하락일 거래량, 전체 거래량 대비 (= OBV 증가분 / 거래량, -1~1)
  cmf          Chaikin Money Flow: 종가가 하루 범위의 위쪽에서 끝난 날의 거래량 비중 (-1~1)
  absorption   대량 거래(50일 평균의 2.5배↑)인데 종가가 하루 범위 위쪽에서 끝나고 크게 안 빠진 날 수
  contraction  최근 20일 변동성 / 최근 120일 변동성 (작을수록 박스권이 좁아짐)
  ret          같은 기간 주가 수익률 (다이버전스 계산용)
  pos52        52주 범위 안에서 현재가 위치 (0 = 52주 최저, 1 = 52주 최고)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SIGNALS = ["vol_balance", "cmf", "absorption", "contraction", "ret", "pos52", "dollar_volume"]


def signal_frame(df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """한 종목의 날짜별 매집 신호 (각 날짜는 그날까지의 데이터만 사용)."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"].fillna(0)
    ret1 = c.pct_change()
    sign = np.sign(ret1).fillna(0)
    vol_sum = v.rolling(window).sum()
    vol_balance = (sign * v).rolling(window).sum() / vol_sum

    rng = (h - l).replace(0, np.nan)
    clv = (((c - l) - (h - c)) / rng).fillna(0)
    cmf = (clv * v).rolling(window).sum() / vol_sum

    avg50 = v.rolling(50).mean().shift(1)
    absorb_day = (v > 2.5 * avg50) & (clv > 0) & (ret1 > -0.01)
    absorption = absorb_day.astype(float).rolling(window).sum()

    contraction = ret1.rolling(20).std() / ret1.rolling(120).std()
    ret = c / c.shift(window) - 1
    hi52, lo52 = h.rolling(252).max(), l.rolling(252).min()
    pos52 = (c - lo52) / (hi52 - lo52)
    dollar_volume = (c * v).rolling(60).median()
    return pd.DataFrame({"vol_balance": vol_balance, "cmf": cmf, "absorption": absorption,
                         "contraction": contraction, "ret": ret, "pos52": pos52, "dollar_volume": dollar_volume,
                         "close": c})


def _z(s: pd.Series) -> pd.Series:
    """이상치에 강한 표준화 (중앙값·MAD, ±3 에서 자름)."""
    med = s.median()
    mad = (s - med).abs().median() * 1.4826
    if not np.isfinite(mad) or mad == 0:
        return s * 0
    return ((s - med) / mad).clip(-3, 3)


def score_cross_section(x: pd.DataFrame) -> pd.DataFrame:
    """같은 날짜의 여러 종목 신호 → 매집 점수.

    stealth: 주가 상승으로 설명되는 만큼을 뺀 '거래량 쏠림' (주가는 안 올랐는데 사는 거래량이 많음)
    score  : stealth, cmf, absorption, 변동성 수축(작을수록 +) 의 표준화 평균
    """
    x = x.dropna(subset=["vol_balance", "cmf", "ret", "contraction"]).copy()
    if len(x) < 30:
        return x.assign(stealth=np.nan, score=np.nan)
    # vol_balance 를 주가 수익률로 회귀하고 남은 부분
    a = np.vstack([np.ones(len(x)), x["ret"].clip(-0.8, 2)]).T
    coef, *_ = np.linalg.lstsq(a, x["vol_balance"].to_numpy(), rcond=None)
    x["stealth"] = x["vol_balance"] - a @ coef
    x["score"] = (_z(x["stealth"]) + _z(x["cmf"]) + 0.5 * _z(x["absorption"]) + _z(-np.log(x["contraction"]))) / 3.5
    return x


def panel(prices: dict[str, pd.DataFrame], window: int = 60) -> dict[str, pd.DataFrame]:
    return {t: signal_frame(df, window) for t, df in prices.items() if len(df) > 260 and {"High", "Low"} <= set(df)}


def snapshot(sig: dict[str, pd.DataFrame], date: pd.Timestamp, min_price: float = 5.0,
             min_dollar_volume: float = 5e6) -> pd.DataFrame:
    """특정 날짜의 전 종목 신호 + 점수 (그날 이전 마지막 거래일 기준)."""
    rows = {}
    for t, s in sig.items():
        i = s.index.searchsorted(date, side="right") - 1
        if i < 260:
            continue
        r = s.iloc[i]
        if r["close"] < min_price or r["dollar_volume"] < min_dollar_volume:
            continue
        rows[t] = r
    x = pd.DataFrame(rows).T.astype(float)
    return score_cross_section(x) if len(x) else x


def forward_returns(prices: dict[str, pd.DataFrame], date: pd.Timestamp, days: int) -> pd.Series:
    out = {}
    for t, df in prices.items():
        c = df["Close"]
        i = c.index.searchsorted(date, side="right") - 1
        if i < 0 or i + days >= len(c):
            continue
        out[t] = c.iloc[i + days] / c.iloc[i] - 1
    return pd.Series(out, dtype=float)


def backtest(prices: dict[str, pd.DataFrame], dates, horizon: int = 63, top: int = 30,
             min_price: float = 5.0, min_dollar_volume: float = 5e6, window: int = 60,
             sig: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """날짜마다 점수 상위 top 종목 vs 전체의 이후 horizon 거래일 수익률.

    반환: (날짜별 요약, 신호별 IC[순위 상관] 표)
    """
    sig = sig or panel(prices, window)
    rows, ics = [], []
    cols = ["score", "stealth", "vol_balance", "cmf", "absorption", "contraction", "ret", "pos52"]
    for d in dates:
        x = snapshot(sig, d, min_price, min_dollar_volume)
        if x.empty or x["score"].isna().all():
            continue
        fwd = forward_returns({t: prices[t] for t in x.index}, d, horizon)
        x = x.join(fwd.rename("fwd"), how="inner").dropna(subset=["fwd", "score"])
        if len(x) < 50:
            continue
        x = x.sort_values("score", ascending=False)
        q = pd.qcut(x["score"].rank(method="first"), 10, labels=False)
        rows.append({
            "date": d, "n": len(x),
            "top": x["fwd"].head(top).mean(), "top_median": x["fwd"].head(top).median(),
            "top_hit": (x["fwd"].head(top) > x["fwd"].median()).mean(),
            "all": x["fwd"].mean(), "all_median": x["fwd"].median(),
            "decile_top": x.loc[q == 9, "fwd"].mean(), "decile_bottom": x.loc[q == 0, "fwd"].mean(),
        })
        ics.append({"date": d, **{c: x[c].corr(x["fwd"], method="spearman") for c in cols}})
    return pd.DataFrame(rows), pd.DataFrame(ics)


# ------------------------------------------------------------------ 기관·내부자·공매도 (상위 후보에만)
def holder_info(ticker: str) -> dict:
    """야후에서 기관 보유·내부자 매매·공매도 정보를 모은다 (없으면 빈 값)."""
    import yfinance as yf

    out = {"ticker": ticker}
    tk = yf.Ticker(ticker)
    try:
        info = tk.get_info()
        out.update({
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "inst_pct": info.get("heldPercentInstitutions"),
            "insider_pct": info.get("heldPercentInsiders"),
            "short_float": info.get("shortPercentOfFloat"),
            "short_change": (info["sharesShort"] / info["sharesShortPriorMonth"] - 1)
            if info.get("sharesShort") and info.get("sharesShortPriorMonth") else None,
        })
    except Exception:
        pass
    try:
        ih = tk.get_institutional_holders()
        if ih is not None and len(ih) and "pctChange" in ih:
            # 상위 기관들의 직전 분기 대비 보유 주식 수 변화 (중앙값, 늘린 기관 수)
            out["inst_top_change"] = float(ih["pctChange"].median())
            out["inst_top_adders"] = int((ih["pctChange"] > 0).sum())
            out["inst_top_n"] = int(len(ih))
    except Exception:
        pass
    try:
        ip = tk.get_insider_purchases()
        if ip is not None and len(ip):
            col = ip.columns[0]
            row = ip.set_index(col)
            shares = row.iloc[:, 0]
            out["insider_buys_6m"] = float(shares.get("Purchases", np.nan))
            out["insider_sells_6m"] = float(shares.get("Sales", np.nan))
            out["insider_net_6m"] = float(shares.get("Net Shares Purchased (Sold)", np.nan))
    except Exception:
        pass
    return out


INSIDER_CACHE_DIR = Path("data/insider")


def load_insider_transactions(ticker: str, refresh: bool = False, cache_dir: Path = INSIDER_CACHE_DIR) -> pd.DataFrame:
    """내부자 거래 내역 (야후, 대략 최근 1.5~2년). 캐시해 둔다."""
    path = Path(cache_dir) / f"{ticker.upper()}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    cols = ["date", "insider", "position", "text", "shares", "value"]
    try:
        import yfinance as yf

        it = yf.Ticker(ticker).get_insider_transactions()
        if it is None or it.empty:
            df = pd.DataFrame(columns=cols)
        else:
            df = pd.DataFrame({
                "date": pd.to_datetime(it["Start Date"]),
                "insider": it["Insider"].astype(str),
                "position": it["Position"].astype(str),
                "text": it["Text"].fillna("").astype(str),
                "shares": pd.to_numeric(it["Shares"], errors="coerce"),
                "value": pd.to_numeric(it["Value"], errors="coerce"),
            })
    except Exception:
        df = pd.DataFrame(columns=cols)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def insider_buys(tx: pd.DataFrame, min_value: float = 50_000) -> pd.DataFrame:
    """장내 매수만 ('Purchase at price ...'), 금액 하한 이상. 스톡옵션·증여·보상은 제외."""
    if tx.empty:
        return tx
    b = tx[tx["text"].str.startswith("Purchase at price") & (tx["value"].fillna(0) >= min_value)]
    return b.sort_values("date")


def insider_events(tx_by_ticker: dict[str, pd.DataFrame], min_value: float = 50_000, cluster_days: int = 30) -> pd.DataFrame:
    """내부자 장내 매수를 이벤트로 묶는다 (같은 종목에서 30일 안의 매수는 한 이벤트).

    반환: ticker, date(첫 매수일), last_date, value(총액), insiders(사람 수), top_exec(CEO/CFO/회장 포함 여부)
    """
    rows = []
    for t, tx in tx_by_ticker.items():
        b = insider_buys(tx, min_value)
        if b.empty:
            continue
        start, cur = None, []
        for r in b.itertuples():
            if start is not None and (r.date - start).days > cluster_days:
                rows.append(_event_row(t, cur))
                cur = []
            if not cur:
                start = r.date
            cur.append(r)
        if cur:
            rows.append(_event_row(t, cur))
    return pd.DataFrame(rows)


def _event_row(t, rs) -> dict:
    pos = " ".join(r.position for r in rs).lower()
    return {
        "ticker": t,
        "date": rs[0].date,
        "last_date": rs[-1].date,
        "value": float(sum(r.value for r in rs)),
        "insiders": len({r.insider for r in rs}),
        "top_exec": any(k in pos for k in ("chief executive", "chief financial", "ceo", "cfo", "chairman", "president")),
    }


def event_returns(events: pd.DataFrame, closes: pd.DataFrame, horizons=(21, 63), lag: int = 3) -> pd.DataFrame:
    """이벤트 lag 거래일 뒤 매수 → horizon 거래일 뒤 수익률, 같은 기간 전체 종목 평균 대비 초과수익.

    closes: 날짜 × 종목 종가 표
    """
    idx = closes.index
    out = []
    for e in events.itertuples():
        if e.ticker not in closes:
            continue
        i = idx.searchsorted(e.date) + lag
        row = {"ticker": e.ticker, "date": e.date}
        if i >= len(idx):
            continue
        c0 = closes[e.ticker].iloc[i]
        if not np.isfinite(c0):
            continue
        row["entry"] = idx[i]
        prev = closes[e.ticker].iloc[max(i - 60, 0)]
        row["ret_before60"] = c0 / prev - 1 if np.isfinite(prev) else np.nan
        for h in horizons:
            if i + h >= len(idx):
                row[f"ret{h}"] = row[f"ex{h}"] = np.nan
                continue
            r = closes[e.ticker].iloc[i + h] / c0 - 1
            mkt = (closes.iloc[i + h] / closes.iloc[i] - 1).clip(-0.9, 5).mean()
            row[f"ret{h}"], row[f"ex{h}"] = r, r - mkt
        out.append(row)
    return events.merge(pd.DataFrame(out), on=["ticker", "date"], how="inner")
