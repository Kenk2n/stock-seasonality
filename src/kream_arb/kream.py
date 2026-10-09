"""KREAM 상품 페이지에서 시세 읽기 (실험 기능).

KREAM 은 공개 API 가 없어서 웹 페이지(HTML)에 들어 있는 상품 정보(JSON-LD)와 화면 글자를 읽는다.
사이트 구조가 바뀌면 일부 값이 비거나 실패할 수 있다 — 그때는 CSV 로 넣는다.
요청 사이에 쉬는 시간을 두고, 받은 페이지는 data/kream/ 에 하루 동안 저장해 같은 페이지를 다시 받지 않는다.
이용약관을 확인하고 개인 분석 용도로만, 적은 양만 쓸 것.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
import urllib.request
from pathlib import Path

import pandas as pd

from .data import parse_number, prepare_kream

BASE = "https://kream.co.kr"
CACHE = Path(__file__).resolve().parents[2] / "data" / "kream"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# 화면 글자에서 찾는 항목: (열 이름, 정규식)
_TEXT_PATTERNS = [
    ("model_no", r"모델번호\s*([A-Z0-9][A-Z0-9\-/ ]{2,30}?)\s*(?:출시일|대표|컬러|발매가|$)"),
    ("release_price", r"발매가\s*(?:\([^)]*\)\s*)?([\d,]+)\s*원"),
    ("last_price", r"최근\s*거래가\s*([\d,]+)\s*원"),
    ("lowest_ask", r"즉시\s*구매가\s*([\d,]+)\s*원"),
    ("highest_bid", r"즉시\s*판매가\s*([\d,]+)\s*원"),
    ("wishes", r"관심(?:상품)?\s*([\d,.]+\s*[만천]?)"),
    ("trades", r"거래\s*([\d,.]+\s*[만천]?)\s*건"),
]


def fetch(url: str, timeout: float = 15.0, max_age_hours: float = 24.0) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (re.sub(r"[^0-9A-Za-z]+", "_", url.replace(BASE, "")).strip("_")[:120] + ".html")
    if key.exists() and time.time() - key.stat().st_mtime < max_age_hours * 3600:
        return key.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    key.write_text(text, encoding="utf-8")
    return text


def _json_ld_products(page: str) -> list[dict]:
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, flags=re.S):
        try:
            data = json.loads(html_lib.unescape(block))
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Product":
                out.append(item)
    return out


def _visible_text(page: str) -> str:
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", page)))


def parse_product_page(page: str, product_id: str | int | None = None) -> dict:
    """상품 페이지 HTML → 시세 dict. 못 찾은 값은 None."""
    row = {"product_id": product_id, "name": None, "brand": None, "model_no": None, "category": None,
           "size": "", "release_price": None, "last_price": None, "lowest_ask": None, "highest_bid": None,
           "trades": None, "wishes": None}
    for item in _json_ld_products(page):
        row["name"] = row["name"] or item.get("name")
        brand = item.get("brand")
        row["brand"] = row["brand"] or (brand.get("name") if isinstance(brand, dict) else brand)
        row["model_no"] = row["model_no"] or item.get("sku") or item.get("mpn")
        row["category"] = row["category"] or item.get("category")
        offers = item.get("offers") or {}
        offers = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(offers, dict):
            row["lowest_ask"] = row["lowest_ask"] or parse_number(offers.get("lowPrice") or offers.get("price"))
    text = _visible_text(page)
    for col, pattern in _TEXT_PATTERNS:
        if row[col] in (None, "") or (isinstance(row[col], float) and row[col] != row[col]):
            m = re.search(pattern, text)
            if m:
                row[col] = m.group(1).strip() if col == "model_no" else parse_number(m.group(1))
    if not row["name"]:
        m = re.search(r"<title>(.*?)</title>", page, flags=re.S)
        row["name"] = html_lib.unescape(m.group(1)).split("|")[0].strip() if m else None
    return row


def discover_product_ids(list_url: str, limit: int = 50) -> list[str]:
    """검색·랭킹 페이지(예: https://kream.co.kr/search?sort=popular)에 보이는 상품 번호."""
    ids = list(dict.fromkeys(re.findall(r"/products/(\d+)", fetch(list_url))))
    return ids[:limit]


def fetch_products(product_ids: list, delay: float = 1.5, progress=None) -> tuple[pd.DataFrame, list[str]]:
    """상품 번호들의 시세. (표, 실패 메시지 목록)."""
    rows, errors = [], []
    for i, pid in enumerate(product_ids):
        pid = str(pid).strip()
        if not pid:
            continue
        try:
            rows.append(parse_product_page(fetch(f"{BASE}/products/{pid}"), pid))
        except Exception as e:  # 네트워크·차단 등
            errors.append(f"{pid}: {e}")
        if progress:
            progress(i + 1, len(product_ids))
        if i + 1 < len(product_ids):
            time.sleep(delay)
    return (prepare_kream(pd.DataFrame(rows)) if rows else pd.DataFrame()), errors
