"""매매 지표 (일봉 OHLCV) 와 최근 신호.

지표: 이동평균(20·50·200일), 볼린저 밴드(20, 2σ), RSI(14), MACD(12·26·9), 스토캐스틱 RSI(14·14·3·3),
ATR(14), 슈퍼트렌드(10, 3), ADX(14)·±DI, MFI(14), OBV, 52주 최저점 기준 앵커드 VWAP.
모든 값은 그날까지의 데이터만 쓴다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .patterns import rsi as _rsi


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def macd(c: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = c.ewm(span=fast, adjust=False).mean() - c.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(c: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = c.rolling(n).mean()
    sd = c.rolling(n).std(ddof=0)
    up, low = mid + k * sd, mid - k * sd
    return pd.DataFrame({"bb_up": up, "bb_mid": mid, "bb_low": low,
                         "bb_pctb": (c - low) / (up - low), "bb_width": (up - low) / mid})


def stoch_rsi(c: pd.Series, rsi_n: int = 14, n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    r = _rsi(c, rsi_n)
    lo, hi = r.rolling(n).min(), r.rolling(n).max()
    raw = ((r - lo) / (hi - lo)).where(hi > lo, 0.5) * 100
    kk = raw.rolling(k).mean()
    return pd.DataFrame({"stoch_k": kk, "stoch_d": kk.rolling(d).mean()})


def supertrend(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """슈퍼트렌드: ATR 밴드를 따라가는 추세선. dir = 1 상승, -1 하락."""
    atr = _wilder(true_range(h, l, c), n).to_numpy()
    hl2 = ((h + l) / 2).to_numpy()
    cc = c.to_numpy()
    up_b, lo_b = hl2 + mult * atr, hl2 - mult * atr
    line = np.full(len(cc), np.nan)
    dirn = np.zeros(len(cc))
    fu, fl = np.nan, np.nan
    for i in range(len(cc)):
        if not np.isfinite(atr[i]):
            continue
        if not np.isfinite(fu):
            fu, fl, dirn[i] = up_b[i], lo_b[i], 1
        else:
            fu = up_b[i] if (up_b[i] < fu or cc[i - 1] > fu) else fu
            fl = lo_b[i] if (lo_b[i] > fl or cc[i - 1] < fl) else fl
            if dirn[i - 1] == -1 and cc[i] > fu:
                dirn[i] = 1
            elif dirn[i - 1] == 1 and cc[i] < fl:
                dirn[i] = -1
            else:
                dirn[i] = dirn[i - 1]
        line[i] = fl if dirn[i] == 1 else fu
    return pd.DataFrame({"st_line": line, "st_dir": dirn}, index=c.index).replace({"st_dir": {0: np.nan}})


def adx(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.DataFrame:
    up, down = h.diff(), -l.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = _wilder(true_range(h, l, c), n)
    pdi = 100 * _wilder(plus_dm, n) / atr
    mdi = 100 * _wilder(minus_dm, n) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return pd.DataFrame({"adx": _wilder(dx, n), "plus_di": pdi, "minus_di": mdi})


def mfi(h: pd.Series, l: pd.Series, c: pd.Series, v: pd.Series, n: int = 14) -> pd.Series:
    tp = (h + l + c) / 3
    flow = tp * v
    pos = flow.where(tp > tp.shift(1), 0.0).rolling(n).sum()
    neg = flow.where(tp < tp.shift(1), 0.0).rolling(n).sum()
    out = 100 - 100 / (1 + pos / neg)
    return out.where(neg > 0, 100.0).where(pos.notna())


def obv(c: pd.Series, v: pd.Series) -> pd.Series:
    return (np.sign(c.diff()).fillna(0) * v.fillna(0)).cumsum()


def anchored_vwap(h: pd.Series, l: pd.Series, c: pd.Series, v: pd.Series, lookback: int = 252) -> pd.Series:
    """최근 lookback 거래일 중 최저 종가 날짜부터 누적한 VWAP (그 이전은 NaN)."""
    if len(c) == 0:
        return c * np.nan
    w = c.tail(lookback)
    anchor = w.idxmin()
    tp = (h + l + c) / 3
    m = c.index >= anchor
    vv = v.fillna(0)[m]
    out = pd.Series(np.nan, index=c.index)
    out[m] = (tp[m] * vv).cumsum() / vv.cumsum().replace(0, np.nan)
    return out


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV → 지표 표 (같은 인덱스)."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"].fillna(0)
    out = pd.DataFrame(index=df.index)
    for n in (20, 50, 200):
        out[f"sma{n}"] = c.rolling(n).mean()
    out = out.join(bollinger(c)).join(macd(c)).join(stoch_rsi(c)).join(supertrend(h, l, c)).join(adx(h, l, c))
    out["rsi"] = _rsi(c)
    out["atr"] = _wilder(true_range(h, l, c), 14)
    out["mfi"] = mfi(h, l, c, v)
    out["obv"] = obv(c, v)
    out["obv_sma20"] = out["obv"].rolling(20).mean()
    out["avwap"] = anchored_vwap(h, l, c, v)
    out["vol_sma20"] = v.rolling(20).mean()
    out["vol_sma50"] = v.rolling(50).mean()
    return out


def _crossed_up(a: pd.Series, b: pd.Series | float, within: int) -> bool:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    d = (a - b).tail(within + 1)
    return bool(len(d) > 1 and (d.iloc[:-1] <= 0).any() and d.iloc[-1] > 0 and np.isfinite(d.iloc[-1]))


def _crossed_down(a: pd.Series, b: pd.Series | float, within: int) -> bool:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    d = (a - b).tail(within + 1)
    return bool(len(d) > 1 and (d.iloc[:-1] >= 0).any() and d.iloc[-1] < 0 and np.isfinite(d.iloc[-1]))


def rsi_bull_divergence(c: pd.Series, r: pd.Series, recent: int = 10, prior: int = 40) -> bool:
    """최근 저점이 이전 저점보다 낮은데 RSI 저점은 더 높음 (하락 힘이 약해짐)."""
    if len(c) < recent + prior:
        return False
    a, b = c.tail(recent), c.iloc[-(recent + prior):-recent]
    ia, ib = a.idxmin(), b.idxmin()
    return bool(a[ia] < b[ib] and r[ia] > r[ib] + 2 and r[ib] < 40)


def signals(df: pd.DataFrame, ind: pd.DataFrame) -> list[dict]:
    """마지막 날 기준 신호 목록: {key, label, tone(bull|bear|neutral), detail}."""
    c, v = df["Close"], df["Volume"].fillna(0)
    x = ind.iloc[-1]
    out: list[dict] = []

    def add(key, label, tone, detail=""):
        out.append({"key": key, "label": label, "tone": tone, "detail": detail})

    r = ind["rsi"]
    if np.isfinite(x["rsi"]):
        if x["rsi"] < 30:
            add("rsi_oversold", "RSI 과매도", "bull", f"RSI {x['rsi']:.0f} < 30")
        elif x["rsi"] > 70:
            add("rsi_overbought", "RSI 과매수", "bear", f"RSI {x['rsi']:.0f} > 70")
        if _crossed_up(r, 30.0, 5):
            add("rsi_exit_oversold", "RSI 과매도 탈출", "bull", "최근 5일 안에 30 위로 올라섬")
    if rsi_bull_divergence(c, r):
        add("rsi_divergence", "RSI 강세 다이버전스", "bull", "주가 저점은 낮아졌는데 RSI 저점은 높아짐")
    if _crossed_up(ind["macd"], ind["macd_signal"], 3):
        add("macd_golden", "MACD 골든크로스", "bull", "최근 3일 안에 시그널선 위로")
    elif _crossed_down(ind["macd"], ind["macd_signal"], 3):
        add("macd_dead", "MACD 데드크로스", "bear", "최근 3일 안에 시그널선 아래로")
    hist = ind["macd_hist"].tail(4)
    if len(hist) == 4 and hist.iloc[-1] < 0 and hist.diff().iloc[1:].gt(0).all():
        add("macd_turning", "하락 힘 둔화 (MACD)", "bull", "MACD 히스토그램이 3일 연속 상승")
    if np.isfinite(x["bb_low"]):
        if c.iloc[-1] < x["bb_low"]:
            add("bb_below", "볼린저 하단 아래", "neutral", "과매도 구간, 반등 또는 추가 하락")
        elif (c.tail(4).iloc[:-1] < ind["bb_low"].tail(4).iloc[:-1]).any():
            add("bb_reentry", "볼린저 하단 복귀", "bull", "하단 밖에 있다가 안으로 들어옴")
        bw = ind["bb_width"].tail(126).dropna()
        if len(bw) > 60 and x["bb_width"] <= bw.quantile(0.1):
            add("bb_squeeze", "볼린저 수축 (스퀴즈)", "neutral", "6개월 중 변동폭이 가장 좁음, 큰 움직임 전조")
    k, d = ind["stoch_k"], ind["stoch_d"]
    if _crossed_up(k, d, 3) and k.tail(4).min() < 20:
        add("stoch_up", "스토캐스틱 RSI 상향 교차", "bull", "20 아래에서 K가 D를 상향 돌파")
    elif _crossed_down(k, d, 3) and k.tail(4).max() > 80:
        add("stoch_down", "스토캐스틱 RSI 하향 교차", "bear", "80 위에서 K가 D를 하향 돌파")
    sd = ind["st_dir"].dropna()
    if len(sd) > 5:
        if sd.iloc[-1] == 1 and (sd.tail(6).iloc[:-1] == -1).any():
            add("st_flip_up", "슈퍼트렌드 상승 전환", "bull", "최근 5일 안에 하락 → 상승")
        elif sd.iloc[-1] == -1 and (sd.tail(6).iloc[:-1] == 1).any():
            add("st_flip_down", "슈퍼트렌드 하락 전환", "bear", "최근 5일 안에 상승 → 하락")
        elif sd.iloc[-1] == -1:
            add("st_down", "슈퍼트렌드 하락 중", "bear", f"추세선 ${x['st_line']:,.2f} 위로 올라서야 전환")
    if np.isfinite(x["adx"]):
        if x["adx"] > 25 and x["minus_di"] > x["plus_di"]:
            add("adx_down", "강한 하락 추세 (ADX)", "bear", f"ADX {x['adx']:.0f}, -DI > +DI")
        elif x["adx"] > 25 and x["plus_di"] > x["minus_di"]:
            add("adx_up", "강한 상승 추세 (ADX)", "bull", f"ADX {x['adx']:.0f}, +DI > -DI")
        elif x["adx"] < 20:
            add("adx_flat", "추세 약함 (횡보)", "neutral", f"ADX {x['adx']:.0f} < 20, 박스권에 유리")
    if np.isfinite(x["mfi"]):
        if x["mfi"] < 20:
            add("mfi_low", "MFI 과매도", "bull", f"MFI {x['mfi']:.0f} < 20 (돈이 과하게 빠짐)")
        elif x["mfi"] > 80:
            add("mfi_high", "MFI 과매수", "bear", f"MFI {x['mfi']:.0f} > 80")
    o = ind["obv"]
    if len(c) > 25 and o.iloc[-1] > x["obv_sma20"] and o.iloc[-1] > o.iloc[-21] and c.iloc[-1] <= c.iloc[-21] * 1.02:
        add("obv_div", "OBV 상승 (주가는 정체)", "bull", "주가는 안 올랐는데 매수 거래량이 쌓임")
    if np.isfinite(x["sma200"]):
        if _crossed_up(ind["sma50"], ind["sma200"], 10):
            add("golden_cross", "골든크로스 (50일 > 200일)", "bull", "최근 10일 안")
        elif _crossed_down(ind["sma50"], ind["sma200"], 10):
            add("death_cross", "데드크로스 (50일 < 200일)", "bear", "최근 10일 안")
    if np.isfinite(x["avwap"]):
        if _crossed_up(c, ind["avwap"], 5):
            add("avwap_reclaim", "앵커드 VWAP 회복", "bull", "52주 최저점 이후 평균 매수단가 위로 올라섬")
    if np.isfinite(x["vol_sma50"]) and x["vol_sma50"] > 0:
        vr = v.iloc[-1] / x["vol_sma50"]
        chg = c.iloc[-1] / c.iloc[-2] - 1 if len(c) > 1 else 0
        if vr > 2 and chg > 0:
            add("vol_up", "거래량 실린 상승", "bull", f"평소의 {vr:.1f}배 거래량, {chg * 100:+.1f}%")
        elif vr > 2 and chg < 0:
            add("vol_down", "거래량 실린 하락", "bear", f"평소의 {vr:.1f}배 거래량, {chg * 100:+.1f}%")
    return out


def latest(df: pd.DataFrame, ind: pd.DataFrame) -> dict:
    """표에 쓰는 마지막 날 값."""
    x = ind.iloc[-1]
    c = float(df["Close"].iloc[-1])

    def f(v, nd=2):
        return None if v is None or not np.isfinite(v) else round(float(v), nd)

    return {
        "rsi": f(x["rsi"], 1), "macd_hist": f(x["macd_hist"], 3), "stoch_k": f(x["stoch_k"], 1),
        "bb_pctb": f(x["bb_pctb"], 2), "adx": f(x["adx"], 1), "mfi": f(x["mfi"], 1),
        "st_dir": None if not np.isfinite(x["st_dir"]) else int(x["st_dir"]),
        "vs_sma50": f(c / x["sma50"] - 1, 3) if np.isfinite(x["sma50"]) else None,
        "vs_sma200": f(c / x["sma200"] - 1, 3) if np.isfinite(x["sma200"]) else None,
        "atr_pct": f(x["atr"] / c, 3) if np.isfinite(x["atr"]) else None,
    }
