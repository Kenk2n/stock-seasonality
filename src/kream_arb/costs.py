"""해외 구매 → 정식 수입신고 → KREAM 판매까지의 비용과 손익.

자가사용 목록통관(미국 $200 / 그 외 $150 면세)으로 들여온 물건을 되파는 것은 관세법 위반이므로
면세 한도는 계산에 쓰지 않는다. 항상 관세·부가세를 낸 정식 수입을 가정한다.

세금 계산 순서 (관세청 기준):
  과세가격 = (물품가 + 현지 세금 + 현지 배송비 + 국제 운송료) × 환율
  관세     = 과세가격 × 관세율
  개별소비세 = (과세가격 + 관세 − 200만원) × 20%   (가방·시계 등, 200만원 초과분만)
  교육세   = 개별소비세 × 30%
  부가세   = (과세가격 + 관세 + 개별소비세 + 교육세) × 10%
"""

from __future__ import annotations

from dataclasses import dataclass, field

CATEGORIES = ["신발", "의류", "가방", "시계", "액세서리", "테크", "컬렉터블", "기타"]

# 일반 수입 기본 관세율 (원산지 FTA 적용 시 0% 가능 — 대부분 베트남·중국 생산이라 해당 없음)
DEFAULT_DUTY = {"신발": 0.13, "의류": 0.13, "가방": 0.08, "시계": 0.08, "액세서리": 0.08,
                "테크": 0.08, "컬렉터블": 0.08, "기타": 0.08}

# 배대지 → 한국 국제 운송료 (USD, 1개 기준 대략값)
DEFAULT_INTL_SHIPPING_USD = {"신발": 25.0, "의류": 15.0, "가방": 25.0, "시계": 15.0, "액세서리": 12.0,
                             "테크": 20.0, "컬렉터블": 20.0, "기타": 20.0}

# 개별소비세 대상 (200만원 초과분 과세)
EXCISE_CATEGORIES = {"가방", "시계"}
EXCISE_THRESHOLD_KRW = 2_000_000
EXCISE_RATE = 0.20
EDUCATION_TAX_RATE = 0.30
VAT_RATE = 0.10

_ALIASES = {
    "신발": ["신발", "스니커즈", "sneakers", "sneaker", "shoes", "shoe", "footwear"],
    "의류": ["의류", "옷", "apparel", "clothing", "clothes", "top", "bottom", "outer", "상의", "하의", "아우터"],
    "가방": ["가방", "bag", "bags"],
    "시계": ["시계", "watch", "watches"],
    "액세서리": ["액세서리", "acc", "accessory", "accessories", "모자", "cap", "hat", "지갑", "wallet"],
    "테크": ["테크", "tech", "electronics", "전자", "전자기기"],
    "컬렉터블": ["컬렉터블", "collectible", "collectibles", "피규어", "figure", "toy", "toys", "토이"],
}
_ALIAS_LOOKUP = {a.lower(): cat for cat, names in _ALIASES.items() for a in names}


def normalize_category(value) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "기타"
    return _ALIAS_LOOKUP.get(str(value).strip().lower(), "기타")


@dataclass
class CostSettings:
    """비용 가정. 모든 금액은 원(KRW), 비율은 0~1."""

    business: bool = False  # True: 일반과세 사업자 (수입 부가세 환급, 판매가에서 부가세 납부)
    kream_fee_rate: float = 0.066  # KREAM 판매 수수료 (부가세 포함). 등급별로 다르니 확인 필요
    kream_inbound_krw: float = 3_000  # KREAM 검수센터로 보내는 택배비
    card_fee_rate: float = 0.012  # 해외 결제 수수료 (브랜드 + 카드사)
    broker_fee_krw: float = 20_000  # 정식 수입신고 관세사·통관 수수료 (건당)
    bundle_size: int = 1  # 한 번에 묶어 통관하는 수량 (관세사 수수료를 나눠 냄)
    sales_tax_rate: float = 0.0  # 현지 판매세 (미국 무세주 배대지면 0)
    duty_rates: dict = field(default_factory=lambda: dict(DEFAULT_DUTY))
    intl_shipping_usd: dict = field(default_factory=lambda: dict(DEFAULT_INTL_SHIPPING_USD))


def landed_cost(price: float, currency: str, category: str, fx: dict, settings: CostSettings,
                local_shipping: float = 0.0, intl_shipping_usd: float | None = None) -> dict:
    """해외 판매가(현지 통화)에서 한국 도착까지 총비용(원). 항목별 내역을 dict 로 반환."""
    cat = normalize_category(category)
    rate = fx[currency.upper()]
    goods = price * (1 + settings.sales_tax_rate) * rate
    local_ship = (local_shipping or 0.0) * rate
    if intl_shipping_usd is None:
        intl_shipping_usd = settings.intl_shipping_usd.get(cat, DEFAULT_INTL_SHIPPING_USD["기타"])
    intl_ship = intl_shipping_usd * fx["USD"]
    card_fee = (goods + local_ship + intl_ship) * settings.card_fee_rate

    customs_value = goods + local_ship + intl_ship
    duty = customs_value * settings.duty_rates.get(cat, DEFAULT_DUTY["기타"])
    excise = 0.0
    if cat in EXCISE_CATEGORIES:
        excise = max(0.0, customs_value + duty - EXCISE_THRESHOLD_KRW) * EXCISE_RATE
    edu_tax = excise * EDUCATION_TAX_RATE
    vat = (customs_value + duty + excise + edu_tax) * VAT_RATE
    broker = settings.broker_fee_krw / max(1, settings.bundle_size)

    total = goods + local_ship + intl_ship + card_fee + duty + excise + edu_tax + vat + broker
    return {"goods": goods, "local_shipping": local_ship, "intl_shipping": intl_ship, "card_fee": card_fee,
            "customs_value": customs_value, "duty": duty, "excise": excise, "education_tax": edu_tax,
            "vat": vat, "broker": broker, "total": total}


def _cost_basis(cost: dict, settings: CostSettings) -> float:
    """손익 계산에 쓰는 원가. 사업자는 수입 부가세 환급, 관세사 수수료의 부가세도 공제."""
    if not settings.business:
        return cost["total"]
    return cost["total"] - cost["vat"] - cost["broker"] + cost["broker"] / (1 + VAT_RATE)


def profit(sale_price: float, cost: dict, settings: CostSettings) -> dict:
    """KREAM 판매가(원)와 landed_cost 결과로 순이익·마진·ROI·손익분기 판매가."""
    fee = sale_price * settings.kream_fee_rate
    proceeds = sale_price - fee - settings.kream_inbound_krw
    basis = _cost_basis(cost, settings)
    if settings.business:
        proceeds /= 1 + VAT_RATE  # 판매가·수수료·택배비 모두 부가세 포함 금액
    net = proceeds - basis
    return {"sale_price": sale_price, "kream_fee": fee, "proceeds": proceeds, "cost_basis": basis,
            "profit": net, "margin": net / sale_price if sale_price else float("nan"),
            "roi": net / basis if basis else float("nan"),
            "breakeven_price": breakeven_price(cost, settings)}


def breakeven_price(cost: dict, settings: CostSettings) -> float:
    """이 가격 이상으로 KREAM 에서 팔려야 손해가 안 나는 판매가(원)."""
    basis = _cost_basis(cost, settings)
    if settings.business:
        basis *= 1 + VAT_RATE
    return (basis + settings.kream_inbound_krw) / (1 - settings.kream_fee_rate)
