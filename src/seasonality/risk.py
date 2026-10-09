"""매수 주의 신호: 최근 뉴스 · 공시(SEC) · 애널리스트 · 재무 · 업황 · 주가 흐름.

flag 한 개 = {"cat": 분류, "level": high|mid|low, "label": 짧은 이름, "detail": 설명, "url": 출처(있으면)}
종목 위험도 = 가장 높은 flag 의 level (없으면 "none").
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import zlib
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

CATEGORIES = {
    "dilution": "증자·희석", "listing": "상장 유지", "news": "악재 뉴스", "legal": "소송·조사",
    "analyst": "애널리스트", "earnings": "실적 임박", "finance": "재무", "sector": "업황",
    "price": "주가 흐름", "pump": "급등락", "short": "공매도", "liquidity": "거래량",
}
LEVEL_ORDER = {"none": 0, "low": 1, "mid": 2, "high": 3}
UA = "Mozilla/5.0 (stock-seasonality daily-watch; +https://github.com/Kenk2n/stock-seasonality)"
# SEC 은 연락처(이메일 형식)가 들어간 User-Agent 만 받는다. 워크플로에서 SEC_USER_AGENT 로 넘긴다.
SEC_UA = os.environ.get("SEC_USER_AGENT") or "stock-seasonality daily-watch noreply@users.noreply.github.com"

# ---------------------------------------------------------------- 뉴스 키워드
# (정규식, 분류, 위험도, 설명). 제목을 소문자로 바꿔 검사한다.
NEWS_RULES: list[tuple[str, str, str, str]] = [
    (r"(registered direct|public|underwritten|secondary|equity|stock|share|unit|warrants?|follow-on|best efforts)\s+offering"
     r"|(prices|pricing|announces|proposed|closes|closing of|launches)\b.{0,60}\boffering"
     r"|private placement|at-the-market|\batm (program|offering|facility)|shelf registration|dilution",
     "dilution", "high", "유상증자·희석"),
    (r"reverse (stock )?split|share consolidation", "dilution", "high", "주식 병합 (보통 증자·상장 유지용)"),
    (r"convertible (senior )?notes?|convertible preferred|equity line|purchase agreement with", "dilution", "mid", "전환사채·지분 매각 약정"),
    (r"bankrupt|chapter 11|going concern|insolven|restructuring support agreement|forbearance", "listing", "high", "파산·존속 의문"),
    (r"delist|nasdaq (notice|notification|deficiency)|minimum bid price|non-?compliance|deficiency (notice|letter)"
     r"|trading (halt|suspen)", "listing", "high", "상장 유지 문제·거래 정지"),
    (r"short (seller|report)|hindenburg|muddy waters|culper|spruce point|grizzly research|wolfpack", "legal", "high", "공매도 리포트"),
    (r"\bfraud|sec (charges|probe|investigation)|subpoena|department of justice|\bdoj\b|indict", "legal", "high", "사기·당국 조사"),
    (r"class action|securities (fraud )?(lawsuit|litigation)|investigation (alert|on behalf)|shareholder (alert|investigation)",
     "legal", "mid", "집단소송·조사 공지"),
    (r"downgrade", "analyst", "mid", "투자의견 하향"),
    (r"(cuts|lowers|lowered|slashes|withdraws|suspends) (its |full[- ]year |annual )?(guidance|outlook|forecast)"
     r"|profit warning|guidance cut", "news", "mid", "실적 전망 하향"),
    (r"miss(es|ed)? (estimates|expectations|forecasts)|below (estimates|expectations)|disappointing", "news", "mid", "실적 기대 미달"),
    (r"(ceo|cfo|chief executive|chief financial).{0,40}(resign|steps? down|depart|ousted|fired|leaves)", "news", "mid", "경영진 사임"),
    (r"\brecall\b|fda (rejects|declines)|complete response letter|clinical hold|(trial|study) (fail|halt)|fails? to meet",
     "news", "mid", "제품·임상 악재"),
    (r"layoffs?|job cuts|workforce reduction", "news", "low", "구조조정"),
    (r"default(s|ed)? on|missed (interest|debt) payment|covenant", "finance", "high", "채무 불이행 위험"),
]
_COMPILED = [(re.compile(p), cat, lvl, why) for p, cat, lvl, why in NEWS_RULES]


def classify_headline(title: str) -> list[tuple[str, str, str]]:
    t = (title or "").lower()
    return [(cat, lvl, why) for rx, cat, lvl, why in _COMPILED if rx.search(t)]


def _get(url: str, ua: str = UA, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            enc = (r.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:  # 거절 사유가 본문에 있으면 로그로 남긴다
        detail = e.read()[:300].decode("utf-8", "replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} {url} {' '.join(detail.split())[:200]}") from None
    if enc == "gzip":
        body = gzip.decompress(body)
    elif enc == "deflate":
        body = zlib.decompress(body)
    return body


def _cached_json(path: Path, max_age_h: float, fetch):
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return json.loads(path.read_text())
    try:
        data = fetch()
    except Exception as e:  # 네트워크 실패 시 오래된 캐시라도 쓴다
        log.warning("%s 받기 실패 (%s)", path.name, e)
        return json.loads(path.read_text()) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def parse_rss(xml_bytes: bytes) -> list[dict]:
    out = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            continue
        out.append({"date": dt.strftime("%Y-%m-%d %H:%M"), "title": (it.findtext("title") or "").strip(),
                    "url": (it.findtext("link") or "").strip()})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def load_news(ticker: str, cache_dir: Path = Path("data/news"), max_age_h: float = 10) -> list[dict]:
    """야후 파이낸스 RSS 헤드라인 (최근 20개 정도)."""
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    return _cached_json(Path(cache_dir) / f"{ticker}.json", max_age_h, lambda: parse_rss(_get(url))) or []


def news_flags(news: list[dict], asof: pd.Timestamp, recent_days: int = 14, dilution_days: int = 180) -> list[dict]:
    flags, seen = [], set()
    dil = []
    for n in news:
        d = pd.Timestamp(n["date"])
        age = (pd.Timestamp(asof) - d).days
        for cat, lvl, why in classify_headline(n["title"]):
            if cat == "dilution" and age <= dilution_days:
                dil.append((age, lvl, why, n))
            if age > recent_days or (cat, why) in seen:
                continue
            if cat == "dilution":
                continue
            seen.add((cat, why))
            flags.append({"cat": cat, "level": lvl, "label": why, "detail": f"{d:%m/%d} {n['title']}", "url": n["url"]})
    if dil:
        dil.sort(key=lambda x: x[0])
        age, lvl, why, n = dil[0]
        cnt = len({x[3]["title"][:40] for x in dil if x[1] == "high"})
        level = lvl if age <= 30 else "mid"
        label = why if cnt <= 1 else f"{why} (최근 6개월 {cnt}건)"
        flags.append({"cat": "dilution", "level": level, "label": label,
                      "detail": f"{pd.Timestamp(n['date']):%m/%d} {n['title']}", "url": n["url"]})
    return flags


# ---------------------------------------------------------------- SEC 공시
SEC_FORMS = {
    "S-1": ("dilution", "high", "증권신고서(S-1) 제출"), "S-3": ("dilution", "mid", "증자용 일괄신고(S-3) 제출"),
    "F-1": ("dilution", "high", "증권신고서(F-1) 제출"), "F-3": ("dilution", "mid", "증자용 일괄신고(F-3) 제출"),
    "424B1": ("dilution", "high", "증자 가격 확정 공시(424B)"), "424B2": ("dilution", "high", "증자 가격 확정 공시(424B)"),
    "424B3": ("dilution", "mid", "증권 매도 공시(424B3)"), "424B4": ("dilution", "high", "증자 가격 확정 공시(424B)"),
    "424B5": ("dilution", "high", "증자 가격 확정 공시(424B)"), "424B7": ("dilution", "mid", "증권 매도 공시(424B7)"),
    "NT 10-K": ("listing", "high", "연간보고서 제출 지연"), "NT 10-Q": ("listing", "high", "분기보고서 제출 지연"),
    "NT 20-F": ("listing", "high", "연간보고서 제출 지연"), "25-NSE": ("listing", "high", "상장폐지 통지"),
}
SEC_8K_ITEMS = {
    "3.01": ("listing", "high", "상장 유지 기준 미달 통지 (8-K 3.01)"),
    "4.02": ("finance", "high", "과거 재무제표 신뢰 불가 (8-K 4.02)"),
    "4.01": ("finance", "mid", "감사인 교체 (8-K 4.01)"),
    "1.03": ("listing", "high", "파산 신청 (8-K 1.03)"),
    "2.04": ("finance", "high", "채무 조기상환 사유 발생 (8-K 2.04)"),
    "5.02": ("news", "low", "임원 변경 (8-K 5.02)"),
    "3.02": ("dilution", "mid", "미등록 주식 발행 (8-K 3.02)"),
}


def load_sec_cik_map(cache_dir: Path = Path("data/sec")) -> dict[str, int]:
    data = _cached_json(Path(cache_dir) / "company_tickers.json", 24 * 7,
                        lambda: json.loads(_get("https://www.sec.gov/files/company_tickers.json", SEC_UA)))
    if not data:
        return {}
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def load_sec_filings(ticker: str, cik_map: dict[str, int], cache_dir: Path = Path("data/sec")) -> list[dict]:
    cik = cik_map.get(ticker.upper())
    if not cik:
        return []

    def fetch():
        time.sleep(0.15)  # SEC 요청 제한(초당 10회) 안쪽
        j = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", SEC_UA))
        return parse_sec_recent(j, cik)

    return _cached_json(Path(cache_dir) / f"{ticker.upper()}.json", 12, fetch) or []


def parse_sec_recent(j: dict, cik: int, limit: int = 60) -> list[dict]:
    r = j.get("filings", {}).get("recent", {})
    out = []
    for i in range(min(limit, len(r.get("form", [])))):
        acc = r["accessionNumber"][i]
        out.append({
            "form": r["form"][i], "date": r["filingDate"][i], "items": (r.get("items") or [""] * (i + 1))[i] or "",
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{r['primaryDocument'][i]}",
        })
    return out


def sec_flags(filings: list[dict], asof: pd.Timestamp, days: int = 45) -> list[dict]:
    flags, seen = [], set()
    for f in filings:
        age = (pd.Timestamp(asof) - pd.Timestamp(f["date"])).days
        if age > days or age < -1:
            continue
        hits = []
        base = f["form"].replace("/A", "")
        if base in SEC_FORMS:
            hits.append(SEC_FORMS[base])
        if base in ("8-K", "6-K"):
            for it in str(f["items"]).split(","):
                if it.strip() in SEC_8K_ITEMS:
                    hits.append(SEC_8K_ITEMS[it.strip()])
        for cat, lvl, why in hits:
            if why in seen:
                continue
            seen.add(why)
            flags.append({"cat": cat, "level": lvl, "label": why, "detail": f"{f['date']} SEC {f['form']}", "url": f["url"]})
    return flags


# ---------------------------------------------------------------- 애널리스트
def load_analyst_actions(ticker: str, cache_dir: Path = Path("data/analyst"), max_age_h: float = 20) -> list[dict]:
    def fetch():
        import yfinance as yf

        ud = yf.Ticker(ticker).upgrades_downgrades
        if ud is None or ud.empty:
            return []
        ud = ud.reset_index().head(40)
        return [{"date": pd.Timestamp(r["GradeDate"]).strftime("%Y-%m-%d"), "firm": str(r.get("Firm", "")),
                 "action": str(r.get("Action", "")), "to": str(r.get("ToGrade", "")), "from": str(r.get("FromGrade", "")),
                 "pt_action": str(r.get("priceTargetAction", "")),
                 "pt": float(r["currentPriceTarget"]) if pd.notna(r.get("currentPriceTarget")) else None,
                 "pt_prior": float(r["priorPriceTarget"]) if pd.notna(r.get("priorPriceTarget")) else None}
                for _, r in ud.iterrows()]

    return _cached_json(Path(cache_dir) / f"{ticker}.json", max_age_h, fetch) or []


def analyst_flags(actions: list[dict], asof: pd.Timestamp, days: int = 30) -> list[dict]:
    recent = [a for a in actions if 0 <= (pd.Timestamp(asof) - pd.Timestamp(a["date"])).days <= days]
    flags = []
    downs = [a for a in recent if a["action"].lower() == "down"]
    if downs:
        a = downs[0]
        flags.append({"cat": "analyst", "level": "mid", "label": f"투자의견 하향 {len(downs)}건",
                      "detail": f"{a['date'][5:]} {a['firm']}: {a['from']} → {a['to']}"})
    cuts = [a for a in recent if a["pt_action"].lower() == "lowers"]
    if len(cuts) >= 2:
        flags.append({"cat": "analyst", "level": "low", "label": f"목표가 하향 {len(cuts)}건",
                      "detail": ", ".join(f"{a['firm']} ${a['pt_prior']:.0f}→${a['pt']:.0f}" for a in cuts[:3]
                                          if a["pt"] and a["pt_prior"])})
    return flags


# ---------------------------------------------------------------- 업황 (업종 ETF)
SECTOR_ETF = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV", "Energy": "XLE", "Industrials": "XLI",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Utilities": "XLU", "Basic Materials": "XLB",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}
# 산업 이름에 들어 있으면 더 좁은 산업 ETF 를 쓴다
INDUSTRY_ETF = [
    ("Biotechnology", "XBI"), ("Semiconductor", "SMH"), ("Banks", "KRE"), ("Oil & Gas E&P", "XOP"),
    ("Oil & Gas", "XLE"), ("Airlines", "JETS"), ("Solar", "TAN"), ("Software", "IGV"), ("Internet Retail", "XRT"),
    ("Specialty Retail", "XRT"), ("Apparel Retail", "XRT"), ("Residential Construction", "XHB"), ("Gold", "GDX"),
    ("Silver", "SIL"), ("Medical Devices", "IHI"), ("Drug Manufacturers", "XPH"), ("Insurance", "KIE"),
    ("Aerospace", "ITA"), ("Restaurants", "PEJ"), ("Travel", "PEJ"), ("Lodging", "PEJ"), ("Casinos", "BJK"),
    ("Coal", "XME"), ("Steel", "SLX"), ("Copper", "COPX"), ("Uranium", "URA"), ("Marine Shipping", "BOAT"),
    ("Capital Markets", "KCE"), ("Asset Management", "KCE"), ("Utilities", "XLU"), ("REIT", "XLRE"),
]
ETF_NAMES = {
    "XLK": "기술", "XLF": "금융", "XLV": "헬스케어", "XLE": "에너지", "XLI": "산업재", "XLY": "경기소비재",
    "XLP": "필수소비재", "XLU": "유틸리티", "XLB": "소재", "XLRE": "부동산", "XLC": "커뮤니케이션", "XBI": "바이오",
    "SMH": "반도체", "KRE": "지역은행", "XOP": "석유·가스 탐사", "JETS": "항공", "TAN": "태양광", "IGV": "소프트웨어",
    "XRT": "소매", "XHB": "주택건설", "GDX": "금광", "SIL": "은광", "IHI": "의료기기", "XPH": "제약", "KIE": "보험",
    "ITA": "항공우주·방산", "PEJ": "레저·여행", "BJK": "카지노", "XME": "금속·광업", "SLX": "철강", "COPX": "구리",
    "URA": "우라늄", "BOAT": "해운", "KCE": "증권·자산운용", "SPY": "S&P 500",
}


def etf_for(sector: str | None, industry: str | None) -> str | None:
    for key, etf in INDUSTRY_ETF:
        if industry and key.lower() in industry.lower():
            return etf
    return SECTOR_ETF.get(sector or "")


def all_etfs() -> list[str]:
    return sorted(set(SECTOR_ETF.values()) | {e for _, e in INDUSTRY_ETF} | {"SPY"})


def sector_table(closes: dict[str, pd.Series]) -> pd.DataFrame:
    """ETF 별 1·3개월 수익률, S&P 500 대비, 200일선 위/아래."""
    spy = closes.get("SPY")
    rows = []
    for etf, c in closes.items():
        c = c.dropna()
        if len(c) < 210:
            continue
        r1, r3 = c.iloc[-1] / c.iloc[-22] - 1, c.iloc[-1] / c.iloc[-64] - 1
        s3 = spy.dropna().iloc[-1] / spy.dropna().iloc[-64] - 1 if spy is not None else 0.0
        rows.append({"etf": etf, "name": ETF_NAMES.get(etf, etf), "ret1m": r1, "ret3m": r3, "vs_spy3m": r3 - s3,
                     "above200": bool(c.iloc[-1] > c.tail(200).mean())})
    return pd.DataFrame(rows).set_index("etf") if rows else pd.DataFrame()


def sector_flags(etf: str | None, table: pd.DataFrame) -> list[dict]:
    if not etf or etf not in table.index or etf == "SPY":
        return []
    r = table.loc[etf]
    below, falling = not r["above200"], r["ret3m"] < 0
    # 시장보다 덜 오른 것만으로는 표시하지 않는다 (S&P 500 이 강할 때는 대부분 업종이 뒤처짐)
    if below and falling:
        level = "mid"
    elif below or (falling and r["vs_spy3m"] < -0.08):
        level = "low"
    else:
        return []
    detail = (f"{r['name']}({etf}) 3개월 {r['ret3m'] * 100:+.1f}%, S&P 500 대비 {r['vs_spy3m'] * 100:+.1f}%p, "
              f"200일선 {'위' if r['above200'] else '아래'}")
    return [{"cat": "sector", "level": level, "label": f"{r['name']} 업종 약세", "detail": detail}]


# ---------------------------------------------------------------- 재무·주가·기타
def finance_flags(p: dict, price: float) -> list[dict]:
    flags = []
    cash, fcf = p.get("total_cash"), p.get("free_cashflow")
    if fcf is not None and cash is not None and fcf < 0:
        runway = cash / -fcf
        if runway < 1:
            flags.append({"cat": "finance", "level": "high", "label": "현금 1년치 미만",
                          "detail": f"현금 ${cash / 1e6:,.0f}M, 연간 현금 소진 ${-fcf / 1e6:,.0f}M → 증자 가능성 큼"})
        elif runway < 2:
            flags.append({"cat": "finance", "level": "mid", "label": "현금 2년치 미만",
                          "detail": f"현금 ${cash / 1e6:,.0f}M, 연간 현금 소진 ${-fcf / 1e6:,.0f}M"})
    pm = p.get("profit_margins")
    if pm is not None and pm < -0.5:
        flags.append({"cat": "finance", "level": "mid", "label": "큰 적자", "detail": f"순이익률 {pm * 100:.0f}%"})
    de = p.get("debt_to_equity")
    if de is not None and de > 300:
        flags.append({"cat": "finance", "level": "mid", "label": "부채 과다", "detail": f"부채비율 {de:.0f}%"})
    cr = p.get("current_ratio")
    if cr is not None and cr < 1:
        flags.append({"cat": "finance", "level": "low", "label": "유동비율 1 미만", "detail": f"유동비율 {cr:.2f}"})
    rec = (p.get("recommendation") or "").lower()
    if rec in ("sell", "underperform", "strong_sell"):
        flags.append({"cat": "analyst", "level": "mid", "label": "애널리스트 평균 '매도'", "detail": rec})
    tm, na = p.get("target_mean"), p.get("n_analysts") or 0
    if tm and na >= 3 and tm < price * 0.9:
        flags.append({"cat": "analyst", "level": "low", "label": "목표가 < 현재가",
                      "detail": f"평균 목표가 ${tm:,.2f} ({tm / price - 1:+.0%}, {na}명)"})
    sf = p.get("short_float")
    if sf is not None and np.isfinite(sf):
        if sf >= 0.30:
            flags.append({"cat": "short", "level": "mid", "label": f"공매도 {sf * 100:.0f}%", "detail": "유통주식 대비 공매도 30% 이상"})
        elif sf >= 0.15:
            flags.append({"cat": "short", "level": "low", "label": f"공매도 {sf * 100:.0f}%", "detail": "유통주식 대비 공매도 15% 이상"})
    return flags


def price_flags(df: pd.DataFrame, pos: float | None = None, next_earnings: pd.Timestamp | None = None,
                asof: pd.Timestamp | None = None, dollar_volume: float | None = None) -> list[dict]:
    c = df["Close"].dropna()
    price = float(c.iloc[-1])
    asof = pd.Timestamp(asof or c.index[-1])
    flags = []
    if price < 1:
        flags.append({"cat": "listing", "level": "high", "label": "주가 $1 미만", "detail": "30거래일 이어지면 나스닥 상장 유지 기준 미달"})
    elif price < 3:
        flags.append({"cat": "price", "level": "low", "label": "저가주", "detail": f"${price:.2f}, 변동성·증자 위험 큼"})
    if len(c) > 64:
        r3 = price / c.iloc[-64] - 1
        if r3 < -0.3:
            flags.append({"cat": "price", "level": "mid", "label": f"3개월 {r3 * 100:+.0f}% 급락", "detail": "떨어지는 칼날일 수 있음"})
    if pos is not None and np.isfinite(pos):
        if pos < -0.15:
            flags.append({"cat": "price", "level": "high", "label": "박스 이탈", "detail": f"박스 하단보다 크게 아래 (위치 {pos * 100:.0f}%)"})
        elif pos < 0:
            flags.append({"cat": "price", "level": "mid", "label": "박스 하단 아래", "detail": f"위치 {pos * 100:.0f}%, 더 밀리면 박스가 깨짐"})
    if len(c) > 25:
        r = c.pct_change().tail(20)
        run = price / c.iloc[-21] - 1
        if r.max() > 0.5 or run > 1.0:
            flags.append({"cat": "pump", "level": "high", "label": "최근 급등",
                          "detail": f"20일 {run * 100:+.0f}%, 하루 최대 {r.max() * 100:+.0f}% → 급락·덤핑 위험"})
        elif r.min() < -0.4:
            flags.append({"cat": "pump", "level": "high", "label": "최근 폭락", "detail": f"하루 {r.min() * 100:.0f}% 하락"})
        elif r.std() > 0.08:
            flags.append({"cat": "pump", "level": "mid", "label": "변동성 매우 큼", "detail": f"하루 변동 표준편차 {r.std() * 100:.0f}%"})
    if next_earnings is not None and pd.notna(next_earnings):
        dd = (pd.Timestamp(next_earnings) - asof).days
        if 0 <= dd <= 7:
            flags.append({"cat": "earnings", "level": "mid", "label": f"실적 D-{dd}", "detail": f"{pd.Timestamp(next_earnings):%m/%d} 발표, 크게 출렁일 수 있음"})
        elif 0 <= dd <= 14:
            flags.append({"cat": "earnings", "level": "low", "label": f"실적 D-{dd}", "detail": f"{pd.Timestamp(next_earnings):%m/%d} 발표"})
    if dollar_volume is not None and dollar_volume < 1e6:
        flags.append({"cat": "liquidity", "level": "mid", "label": "거래대금 적음", "detail": f"하루 약 ${dollar_volume / 1e3:,.0f}K, 원하는 가격에 사고팔기 어려움"})
    return flags


def summarize(flags: list[dict]) -> dict:
    # 애널리스트 하향은 뉴스 제목과 애널리스트 기록 양쪽에서 잡히므로 기록 쪽(건수 포함)만 남긴다
    if any(f["label"].startswith("투자의견 하향 ") for f in flags):
        flags = [f for f in flags if f["label"] != "투자의견 하향"]
    level = max((f["level"] for f in flags), key=lambda x: LEVEL_ORDER[x], default="none")
    flags = sorted(flags, key=lambda f: -LEVEL_ORDER[f["level"]])
    return {"level": level, "cats": sorted({f["cat"] for f in flags}), "flags": flags,
            "n_high": sum(f["level"] == "high" for f in flags), "n_mid": sum(f["level"] == "mid" for f in flags)}
