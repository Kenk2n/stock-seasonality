"""분석 대상 종목 목록(유니버스)."""

from __future__ import annotations

import io
import urllib.request

import pandas as pd

# 나스닥 100 구성 종목 (정기 리밸런싱으로 바뀌므로 필요하면 직접 수정하세요)
NASDAQ100 = [
    "AAPL", "ABNB", "ADBE", "ADI", "ADP", "ADSK", "AEP", "AMAT", "AMD", "AMGN",
    "AMZN", "ANSS", "APP", "ARM", "ASML", "AVGO", "AXON", "AZN", "BIIB", "BKNG",
    "BKR", "CCEP", "CDNS", "CDW", "CEG", "CHTR", "CMCSA", "COST", "CPRT", "CRWD",
    "CSCO", "CSGP", "CSX", "CTAS", "CTSH", "DASH", "DDOG", "DXCM", "EA", "EXC",
    "FANG", "FAST", "FTNT", "GEHC", "GFS", "GILD", "GOOG", "GOOGL", "HON", "IDXX",
    "INTC", "INTU", "ISRG", "KDP", "KHC", "KLAC", "LIN", "LRCX", "LULU", "MAR",
    "MCHP", "MDLZ", "MELI", "META", "MNST", "MRVL", "MSFT", "MSTR", "MU", "NFLX",
    "NVDA", "NXPI", "ODFL", "ON", "ORLY", "PANW", "PAYX", "PCAR", "PDD", "PEP",
    "PLTR", "PYPL", "QCOM", "REGN", "ROP", "ROST", "SBUX", "SHOP", "SNPS", "TEAM",
    "TMUS", "TSLA", "TTD", "TTWO", "TXN", "VRSK", "VRTX", "WBD", "WDAY", "XEL",
    "ZS",
]

# 비교용 지수 ETF
BENCHMARKS = {"nasdaq100": "QQQ", "sp500": "SPY"}

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
# 보통주가 아닌 것 (워런트, 유닛, 권리, 우선주, 채권형, 폐쇄형 펀드)
_SPECIAL = (r"\bwarrants?\b|\bunits?\b|\brights?\b|preferred|\bnotes\b|debentures|"
            r"depositary shares, each representing|\bfund\b|municipal|closed[- ]end")


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def nasdaq_all(include_etf: bool = False) -> list[str]:
    """나스닥에 상장된 전체 종목 코드를 nasdaqtrader.com 에서 받아온다."""
    return parse_nasdaq_listed(_fetch_text(NASDAQ_LISTED_URL), include_etf=include_etf)


def parse_other_listed(text: str, include_etf: bool = False) -> list[str]:
    """otherlisted.txt (NYSE, NYSE American 등 나스닥 외 거래소) 를 종목 코드 목록으로 변환한다."""
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str)
    df = df[~df["ACT Symbol"].fillna("").str.startswith("File Creation Time")]
    df = df[df["Test Issue"] == "N"]
    if not include_etf:
        df = df[df["ETF"] == "N"]
    df = df[df["ACT Symbol"].str.fullmatch(r"[A-Z]{1,5}")]
    df = df[~df["Security Name"].fillna("").str.contains(_SPECIAL, case=False, regex=True)]
    return sorted(df["ACT Symbol"].tolist())


def us_all(include_etf: bool = False) -> list[str]:
    """미국 주요 거래소(나스닥 + NYSE 등) 상장 보통주 전체."""
    nasdaq = set(nasdaq_all(include_etf))
    other = set(parse_other_listed(_fetch_text(OTHER_LISTED_URL), include_etf))
    return sorted(nasdaq | other)


def parse_nasdaq_listed(text: str, include_etf: bool = False) -> list[str]:
    """nasdaqlisted.txt (파이프 구분) 내용을 종목 코드 목록으로 변환한다."""
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str)
    df = df[~df["Symbol"].fillna("").str.startswith("File Creation Time")]
    df = df[df["Test Issue"] == "N"]
    if not include_etf:
        df = df[df["ETF"] == "N"]
    # 워런트·유닛·우선주 등 특수 종목 제외
    df = df[df["Symbol"].str.fullmatch(r"[A-Z]{1,5}")]
    df = df[~df["Security Name"].fillna("").str.contains(_SPECIAL, case=False, regex=True)]
    return sorted(df["Symbol"].tolist())


def get_universe(name: str) -> list[str]:
    name = name.lower()
    if name == "nasdaq100":
        return list(NASDAQ100)
    if name in ("nasdaq", "nasdaq_all"):
        return nasdaq_all()
    if name in ("us", "us_all"):
        return us_all()
    raise ValueError(f"알 수 없는 유니버스: {name} (nasdaq100, nasdaq, us 중 선택)")


# 야후 거래소 코드
NASDAQ_EXCHANGES = {"NMS", "NGM", "NCM"}   # Global Select, Global Market, Capital Market
NYSE_EXCHANGES = {"NYQ", "ASE", "PCX"}     # NYSE, NYSE American, NYSE Arca


def screen_universe(
    min_market_cap: float = 3e8,
    min_dollar_volume: float = 5e6,
    min_price: float = 5.0,
    exchanges: str = "nasdaq",
) -> pd.DataFrame:
    """야후 스크리너로 시가총액·거래대금 조건을 만족하는 미국 상장 보통주 목록.

    반환 열: ticker, name, exchange, market_cap, price, dollar_volume (3개월 평균 거래대금)
    exchanges: "nasdaq" | "nyse" | "us"(둘 다)
    """
    import yfinance as yf
    from yfinance import EquityQuery as Q

    query = Q("and", [Q("eq", ["region", "us"]), Q("gte", ["intradaymarketcap", min_market_cap])])
    rows, offset = [], 0
    while True:
        r = yf.screen(query, size=250, offset=offset, sortField="intradaymarketcap", sortAsc=False)
        quotes = r.get("quotes", [])
        for x in quotes:
            if x.get("quoteType") != "EQUITY":
                continue
            price = x.get("regularMarketPrice") or 0
            rows.append({
                "ticker": x.get("symbol"),
                "name": x.get("shortName") or x.get("longName") or "",
                "exchange": x.get("exchange"),
                "market_cap": x.get("marketCap") or 0,
                "price": price,
                "dollar_volume": (x.get("averageDailyVolume3Month") or 0) * price,
            })
        offset += len(quotes)
        if not quotes or offset >= r.get("total", 0):
            break
    df = pd.DataFrame(rows).drop_duplicates("ticker")
    allowed = {"nasdaq": NASDAQ_EXCHANGES, "nyse": NYSE_EXCHANGES, "us": NASDAQ_EXCHANGES | NYSE_EXCHANGES}[exchanges]
    df = df[df["exchange"].isin(allowed)]
    df = df[df["ticker"].str.fullmatch(r"[A-Z]{1,5}")]
    df = df[(df["dollar_volume"] >= min_dollar_volume) & (df["price"] >= min_price)]
    df = df[~df["name"].str.contains(_SPECIAL, case=False, regex=True)]
    return df.sort_values("market_cap", ascending=False).reset_index(drop=True)
