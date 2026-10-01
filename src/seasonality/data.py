"""주가 데이터 다운로드와 로컬 캐시.

- 처음에는 상장일부터 전체 기간(period="max")을 받아 data/prices/<TICKER>.parquet 에 저장
- 다음부터는 마지막 날짜 이후만 받아서 이어 붙임(증분 업데이트)
- 배당·분할로 과거 수정주가가 바뀌었으면 전체를 다시 받음
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/prices")
COLUMNS = ["Close", "Volume"]


def cache_path(ticker: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return Path(cache_dir) / f"{ticker.upper().replace('/', '-')}.parquet"


def read_cache(ticker: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> pd.DataFrame | None:
    path = cache_path(ticker, cache_dir)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def write_cache(ticker: str, df: pd.DataFrame, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
    path = cache_path(ticker, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def _missing_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "_missing.txt"


def read_missing(cache_dir: Path = DEFAULT_CACHE_DIR) -> set[str]:
    """예전에 받으려다 데이터가 없었던 종목 (상장폐지 등). 매번 다시 시도하지 않도록 기록."""
    p = _missing_path(cache_dir)
    return set(p.read_text().split()) if p.exists() else set()


def _add_missing(tickers: Iterable[str], cache_dir: Path) -> None:
    tickers = set(tickers)
    if not tickers:
        return
    p = _missing_path(cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(sorted(read_missing(cache_dir) | tickers)))


def _download(tickers: list[str], **kwargs) -> dict[str, pd.DataFrame]:
    """yfinance 로 여러 종목을 한 번에 받아 {종목: DataFrame[Close, Volume]} 로 돌려준다."""
    import yfinance as yf

    raw = yf.download(
        tickers,
        auto_adjust=True,  # 배당·분할 반영한 수정주가
        group_by="ticker",
        progress=False,
        threads=True,
        **kwargs,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for t in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if t not in raw.columns.get_level_values(0):
                continue
            df = raw[t]
        else:
            df = raw
        df = df[[c for c in COLUMNS if c in df.columns]].dropna(subset=["Close"])
        if df.empty:
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "Date"
        out[t] = df.astype("float64")
    return out


def _merge_update(cached: pd.DataFrame, new: pd.DataFrame, tol: float = 0.005) -> pd.DataFrame | None:
    """증분 데이터를 이어 붙인다. 겹치는 날짜의 가격이 다르면(수정주가 변경) None."""
    overlap = cached.index.intersection(new.index)
    if len(overlap):
        diff = (cached.loc[overlap, "Close"] / new.loc[overlap, "Close"] - 1).abs()
        if (diff > tol).any():
            return None
    merged = pd.concat([cached, new])
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def load_prices(
    ticker: str,
    refresh: bool = False,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> pd.DataFrame:
    """한 종목의 전체 기간 일봉을 돌려준다. 캐시가 있으면 캐시를 쓴다."""
    result = update_many([ticker], refresh=refresh, cache_dir=cache_dir)
    if ticker not in result:
        raise ValueError(f"{ticker}: 데이터를 받지 못했습니다 (종목 코드나 네트워크를 확인하세요)")
    return result[ticker]


def update_many(
    tickers: Iterable[str],
    refresh: bool = False,
    batch_size: int = 50,
    pause: float = 1.0,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, pd.DataFrame]:
    """여러 종목을 배치로 받는다.

    refresh=False 이면 캐시가 있는 종목은 그대로 쓰고(중간에 끊겨도 이어받기),
    refresh=True 이면 캐시가 있는 종목은 마지막 날짜 이후만 증분으로 받는다.
    """
    tickers = [t.upper() for t in tickers]
    result: dict[str, pd.DataFrame] = {}
    need_full: list[str] = []
    need_update: dict[str, pd.DataFrame] = {}

    missing = set() if refresh else read_missing(cache_dir)
    for t in tickers:
        cached = read_cache(t, cache_dir)
        if cached is None:
            if t not in missing:
                need_full.append(t)
        elif refresh:
            need_update[t] = cached
        else:
            result[t] = cached

    total = len(need_full) + len(need_update)
    done = 0

    def batches(items: list[str]):
        for i in range(0, len(items), batch_size):
            yield items[i : i + batch_size]

    # 1) 캐시 없는 종목: 전체 기간
    for batch in batches(need_full):
        data = _download(batch, period="max")
        for t, df in data.items():
            write_cache(t, df, cache_dir)
            result[t] = df
        for t in set(batch) - data.keys():
            log.warning("%s: 데이터 없음", t)
        _add_missing(set(batch) - data.keys(), cache_dir)
        done += len(batch)
        if on_progress:
            on_progress(done, total)
        if pause and done < total:
            time.sleep(pause)

    # 2) 캐시 있는 종목: 같은 시작일끼리 묶어서 증분
    by_start: dict[pd.Timestamp, list[str]] = {}
    for t, cached in need_update.items():
        start = cached.index.max() - pd.Timedelta(days=7)
        by_start.setdefault(start.normalize(), []).append(t)

    refetch: list[str] = []
    for start, group in by_start.items():
        for batch in batches(group):
            data = _download(batch, start=start.strftime("%Y-%m-%d"))
            for t in batch:
                cached = need_update[t]
                if t not in data:
                    result[t] = cached
                    continue
                merged = _merge_update(cached, data[t])
                if merged is None:
                    refetch.append(t)
                else:
                    write_cache(t, merged, cache_dir)
                    result[t] = merged
            done += len(batch)
            if on_progress:
                on_progress(done, total)
            if pause and done < total:
                time.sleep(pause)

    # 3) 수정주가가 바뀐 종목은 전체 재다운로드
    for batch in batches(refetch):
        data = _download(batch, period="max")
        for t in batch:
            if t in data:
                write_cache(t, data[t], cache_dir)
                result[t] = data[t]
            else:
                result[t] = need_update[t]

    return result
