"""입력 데이터: KREAM 시세 CSV, 해외 몰 가격 CSV, 환율, 해외 몰 검색 링크."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# CSV 머리글은 영문·한글 모두 받는다
KREAM_COLUMNS = {
    "product_id": ["product_id", "id", "상품번호"],
    "name": ["name", "상품명", "이름"],
    "brand": ["brand", "브랜드"],
    "model_no": ["model_no", "style_code", "sku", "모델번호"],
    "category": ["category", "카테고리"],
    "size": ["size", "사이즈"],
    "release_price": ["release_price", "발매가"],
    "last_price": ["last_price", "최근거래가", "최근 거래가"],
    "lowest_ask": ["lowest_ask", "즉시구매가"],
    "highest_bid": ["highest_bid", "즉시판매가"],
    "trades": ["trades", "거래량", "거래수", "체결수"],
    "wishes": ["wishes", "관심", "관심수"],
}
OFFER_COLUMNS = {
    "model_no": ["model_no", "style_code", "sku", "모델번호"],
    "size": ["size", "사이즈"],
    "store": ["store", "몰", "쇼핑몰", "판매처"],
    "currency": ["currency", "통화"],
    "price": ["price", "가격", "판매가"],
    "local_shipping": ["local_shipping", "현지배송비"],
    "intl_shipping_usd": ["intl_shipping_usd", "국제배송비"],
    "url": ["url", "링크"],
}
KREAM_NUMERIC = ["release_price", "last_price", "lowest_ask", "highest_bid", "trades", "wishes"]
OFFER_NUMERIC = ["price", "local_shipping", "intl_shipping_usd"]

# 1 단위 통화당 원. 실시간 환율을 못 받을 때만 쓰는 값이라 앱에서 고쳐 쓴다
FALLBACK_FX = {"KRW": 1.0, "USD": 1400.0, "EUR": 1600.0, "GBP": 1850.0, "JPY": 9.3, "CNY": 195.0, "HKD": 180.0}

SEARCH_SITES = {
    "StockX": "https://stockx.com/search?s={q}",
    "GOAT": "https://www.goat.com/search?query={q}",
    "eBay": "https://www.ebay.com/sch/i.html?_nkw={q}",
    "Google쇼핑": "https://www.google.com/search?tbm=shop&q={q}",
    "Nike US": "https://www.nike.com/w?q={q}",
    "SSENSE": "https://www.ssense.com/en-us/search?q={q}",
    "Farfetch": "https://www.farfetch.com/shopping/search/items.aspx?q={q}",
    "SNKRDUNK(일본)": "https://snkrdunk.com/search?keywords={q}",
    "Rakuten(일본)": "https://search.rakuten.co.jp/search/mall/{q}/",
}


def parse_number(value) -> float:
    """'129,000원', '1.2만', '$110' 같은 값을 숫자로. 못 읽으면 NaN."""
    if value is None:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    s = str(value).strip().replace(",", "")
    mult = 1.0
    if s.endswith("만"):
        mult, s = 10_000.0, s[:-1]
    elif s.endswith("천"):
        mult, s = 1_000.0, s[:-1]
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) * mult if m else np.nan


def normalize_model(value) -> str:
    """모델번호 비교용 키: 대문자, 영숫자만 ('dd1391-100' == 'DD1391 100')."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return re.sub(r"[^0-9A-Z]", "", str(value).upper())


def _rename(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    lookup = {alias.lower(): col for col, aliases in spec.items() for alias in aliases}
    df = df.rename(columns={c: lookup.get(str(c).strip().lower(), c) for c in df.columns})
    for col in spec:
        if col not in df.columns:
            df[col] = np.nan
    return df


def _clean_size(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip().fillna("")
    return out.str.replace(r"\.0$", "", regex=True)


def prepare_kream(df: pd.DataFrame) -> pd.DataFrame:
    df = _rename(df.copy(), KREAM_COLUMNS)
    for col in KREAM_NUMERIC:
        df[col] = df[col].map(parse_number)
    df["size"] = _clean_size(df["size"])
    df["model_key"] = df["model_no"].map(normalize_model)
    df["name"] = df["name"].fillna(df["model_no"]).astype(str)
    return df


def prepare_offers(df: pd.DataFrame) -> pd.DataFrame:
    df = _rename(df.copy(), OFFER_COLUMNS)
    for col in OFFER_NUMERIC:
        df[col] = df[col].map(parse_number)
    df["local_shipping"] = df["local_shipping"].fillna(0.0)
    df["currency"] = df["currency"].fillna("USD").astype(str).str.upper().str.strip()
    df["size"] = _clean_size(df["size"])
    df["store"] = df["store"].fillna("").astype(str)
    df["model_key"] = df["model_no"].map(normalize_model)
    return df.dropna(subset=["price"]).loc[lambda d: d["model_key"] != ""].reset_index(drop=True)


def load_kream_csv(path_or_buffer) -> pd.DataFrame:
    return prepare_kream(pd.read_csv(path_or_buffer))


def load_offers_csv(path_or_buffer) -> pd.DataFrame:
    return prepare_offers(pd.read_csv(path_or_buffer))


def sample_kream() -> pd.DataFrame:
    return load_kream_csv(EXAMPLES / "kream_sample.csv")


def sample_offers() -> pd.DataFrame:
    return load_offers_csv(EXAMPLES / "overseas_sample.csv")


def fetch_fx(timeout: float = 10.0) -> dict:
    """1 단위 통화당 원 (open.er-api.com, 키 불필요). 실패하면 예외."""
    with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=timeout) as resp:
        rates = json.load(resp)["rates"]
    krw = rates["KRW"]
    return {cur: krw / rates[cur] for cur in FALLBACK_FX if cur in rates}


def get_fx() -> tuple[dict, bool]:
    """(환율, 실시간 여부)."""
    try:
        return {**FALLBACK_FX, **fetch_fx()}, True
    except Exception:
        return dict(FALLBACK_FX), False


def search_links(model_no: str, name: str = "") -> dict:
    q = urllib.parse.quote_plus(str(model_no or name).strip())
    return {site: url.format(q=q) for site, url in SEARCH_SITES.items()}
