"""KREAM 인기·프리미엄 점수와 해외 몰 가격 비교."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .costs import CostSettings, landed_cost, normalize_category, profit

# 점수 가중치: 거래량(잘 팔리는지) · 프리미엄(발매가보다 얼마나 비싼지) · 관심수(인기)
DEFAULT_WEIGHTS = {"trades": 0.40, "premium": 0.35, "wishes": 0.25}

SALE_BASIS = {
    "bid": "즉시판매가 (바로 팔 수 있는 값, 보수적)",
    "last": "최근 거래가",
    "ask": "즉시구매가 − 1,000원 (판매 입찰로 최저가 경쟁)",
}


def sale_price(row: pd.Series, basis: str = "bid") -> float:
    """KREAM 에서 받을 것으로 보는 판매가. 원하는 값이 없으면 보수적인 순서로 대체."""
    ask = row.get("lowest_ask")
    candidates = {
        "bid": row.get("highest_bid"),
        "last": row.get("last_price"),
        "ask": ask - 1_000 if pd.notna(ask) else np.nan,
    }
    order = {"bid": ["bid", "last", "ask"], "last": ["last", "bid", "ask"], "ask": ["ask", "last", "bid"]}[basis]
    for key in order:
        if pd.notna(candidates[key]) and candidates[key] > 0:
            return float(candidates[key])
    return np.nan


def _rank(s: pd.Series) -> pd.Series:
    """0~1 백분위. 값이 없으면 중간(0.5)."""
    if s.notna().sum() == 0:
        return pd.Series(0.5, index=s.index)
    return s.rank(pct=True).fillna(0.5)


def score_items(kream: pd.DataFrame, weights: dict | None = None) -> pd.DataFrame:
    """상품(+사이즈)별 프리미엄과 인기 점수(0~100)."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    df = kream.copy()
    market = df["last_price"].fillna(df["lowest_ask"])
    df["premium"] = market - df["release_price"]
    df["premium_pct"] = df["premium"] / df["release_price"]
    total = sum(w.values()) or 1.0
    df["score"] = 100 * (w["trades"] * _rank(np.log1p(df["trades"]))
                         + w["premium"] * _rank(df["premium_pct"])
                         + w["wishes"] * _rank(np.log1p(df["wishes"]))) / total
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def pick_candidates(scored: pd.DataFrame, min_trades: float = 0, min_premium_pct: float = 0.0,
                    top: int | None = None) -> pd.DataFrame:
    """거래량·프리미엄 기준을 넘는 상품. 거래량·발매가가 비어 있으면 그 조건은 건너뛴다."""
    keep = (scored["trades"].isna() | (scored["trades"] >= min_trades)) & \
           (scored["premium_pct"].isna() | (scored["premium_pct"] >= min_premium_pct))
    out = scored[keep]
    return out.head(top) if top else out


def compare(kream: pd.DataFrame, offers: pd.DataFrame, fx: dict, settings: CostSettings,
            basis: str = "bid") -> pd.DataFrame:
    """KREAM 상품 × 해외 가격 모든 조합의 손익. 모델번호가 같고 사이즈가 같거나 해외 쪽 사이즈가 비어 있으면 매칭."""
    if kream.empty or offers.empty:
        return pd.DataFrame()
    pairs = kream.merge(offers.rename_axis("offer_idx").reset_index(), on="model_key", suffixes=("", "_offer"))
    pairs = pairs[(pairs["size_offer"] == "") | (pairs["size"] == "") | (pairs["size"] == pairs["size_offer"])]
    rows = []
    for _, r in pairs.iterrows():
        sp = sale_price(r, basis)
        if pd.isna(sp) or r["currency"] not in fx:
            continue
        intl = r["intl_shipping_usd"] if pd.notna(r["intl_shipping_usd"]) else None
        cost = landed_cost(r["price"], r["currency"], r["category"], fx, settings,
                           local_shipping=r["local_shipping"], intl_shipping_usd=intl)
        p = profit(sp, cost, settings)
        rows.append({
            "product_id": r.get("product_id"), "name": r["name"], "brand": r.get("brand"),
            "model_no": r["model_no"], "category": normalize_category(r["category"]), "size": r["size"],
            "score": r.get("score", np.nan), "premium_pct": r.get("premium_pct", np.nan),
            "trades": r.get("trades"), "offer_idx": r["offer_idx"], "store": r["store"], "currency": r["currency"],
            "offer_price": r["price"], "url": r.get("url"), "sale_price": sp,
            "landed_cost": cost["total"], "tax": cost["duty"] + cost["excise"] + cost["education_tax"] + cost["vat"],
            "kream_fee": p["kream_fee"], "profit": p["profit"], "margin": p["margin"], "roi": p["roi"],
            "breakeven_price": p["breakeven_price"],
            "safety": (sp - p["breakeven_price"]) / sp,
        })
    return pd.DataFrame(rows).sort_values("profit", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def best_offers(comparison: pd.DataFrame) -> pd.DataFrame:
    """상품·사이즈마다 가장 이익이 큰 해외 구매처 하나."""
    if comparison.empty:
        return comparison
    best = comparison.loc[comparison.groupby(["model_no", "size"], dropna=False)["profit"].idxmax()]
    return best.sort_values("profit", ascending=False).reset_index(drop=True)
