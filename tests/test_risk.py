import numpy as np
import pandas as pd

from seasonality import risk

RSS = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>China SXT Pharmaceuticals Announces $12 Million Registered Direct Offering With Significant Dilution</title>
<link>https://example.com/a</link><pubDate>Thu, 08 Oct 2026 14:44:09 +0000</pubDate></item>
<item><title>China SXT Pharmaceuticals, Inc. Announces Share Consolidation</title>
<link>https://example.com/b</link><pubDate>Wed, 05 Aug 2026 13:00:00 +0000</pubDate></item>
<item><title>China SXT Pharmaceuticals Inc. Announces $9 Million Registered Direct Offering</title>
<link>https://example.com/c</link><pubDate>Thu, 23 Jul 2026 12:45:00 +0000</pubDate></item>
<item><title>Analyst downgrade hits shares</title>
<link>https://example.com/d</link><pubDate>Tue, 06 Oct 2026 12:45:00 +0000</pubDate></item>
<item><title>Old CEO resigns story</title>
<link>https://example.com/e</link><pubDate>Tue, 06 Jan 2026 12:45:00 +0000</pubDate></item>
</channel></rss>"""


def test_classify_headline():
    cats = lambda t: {c for c, _, _ in risk.classify_headline(t)}
    assert cats("XYZ Announces $12 Million Registered Direct Offering") == {"dilution"}
    assert cats("XYZ prices upsized public offering of common stock") == {"dilution"}
    assert cats("Company expands its product offering in Europe") == set()
    assert cats("Analyst upgrades XYZ to Buy") == set()
    assert "listing" in cats("XYZ receives Nasdaq notice regarding minimum bid price")
    assert "legal" in cats("Hindenburg Research publishes short report on XYZ")
    assert "news" in cats("XYZ lowers full-year guidance")


def test_news_flags_from_rss():
    news = risk.parse_rss(RSS)
    assert news[0]["date"].startswith("2026-10-08") and len(news) == 5
    fl = risk.news_flags(news, pd.Timestamp("2026-10-09"))
    dil = [f for f in fl if f["cat"] == "dilution"]
    assert len(dil) == 1 and dil[0]["level"] == "high" and "3건" in dil[0]["label"]
    assert any(f["cat"] == "analyst" for f in fl)
    assert not any("CEO" in f["detail"] for f in fl)  # 14일 넘은 일반 뉴스는 제외
    s = risk.summarize(fl)
    assert s["level"] == "high" and s["flags"][0]["level"] == "high"


def test_sec_flags():
    j = {"filings": {"recent": {
        "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003", "0001-25-000004"],
        "filingDate": ["2026-10-07", "2026-10-01", "2026-09-30", "2026-01-05"],
        "form": ["424B5", "8-K", "10-Q", "S-1"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"],
        "items": ["", "3.01,9.01", "", ""]}}}
    filings = risk.parse_sec_recent(j, 123)
    assert filings[0]["url"].endswith("/123/000126000001/a.htm")
    fl = risk.sec_flags(filings, pd.Timestamp("2026-10-09"))
    labels = {f["label"] for f in fl}
    assert any("424B" in x for x in labels) and any("3.01" in x for x in labels)
    assert not any("S-1" in x for x in labels)  # 45일 지난 공시는 제외


def test_finance_price_sector_flags():
    fl = risk.finance_flags({"total_cash": 5e6, "free_cashflow": -10e6, "short_float": 0.35,
                             "target_mean": 5.0, "n_analysts": 4}, price=8.0)
    cats = {(f["cat"], f["level"]) for f in fl}
    assert ("finance", "high") in cats and ("short", "mid") in cats and ("analyst", "low") in cats

    idx = pd.bdate_range("2026-01-01", periods=120)
    c = np.r_[np.full(110, 2.0), np.linspace(2.0, 6.0, 10)]
    df = pd.DataFrame({"Close": c}, index=idx)
    fl = risk.price_flags(df, pos=-0.3, next_earnings=idx[-1] + pd.Timedelta(days=5), dollar_volume=5e5)
    cats = {f["cat"] for f in fl}
    assert {"pump", "price", "earnings", "liquidity"} <= cats

    closes = {"SPY": pd.Series(np.linspace(100, 120, 260)), "XBI": pd.Series(np.linspace(120, 90, 260)),
              "XLU": pd.Series(np.linspace(100, 104, 260))}
    tab = risk.sector_table(closes)
    assert risk.etf_for("Healthcare", "Biotechnology") == "XBI"
    sf = risk.sector_flags("XBI", tab)
    assert sf and sf[0]["level"] == "mid"
    assert risk.sector_flags("XLU", tab) == []  # 시장보다 덜 올랐을 뿐 오르는 중이면 표시 안 함
    assert risk.sector_flags("SPY", tab) == []


def test_summarize_dedupes_downgrade():
    fl = [{"cat": "analyst", "level": "mid", "label": "투자의견 하향", "detail": "news"},
          {"cat": "analyst", "level": "mid", "label": "투자의견 하향 2건", "detail": "UBS"}]
    s = risk.summarize(fl)
    assert [f["label"] for f in s["flags"]] == ["투자의견 하향 2건"]
