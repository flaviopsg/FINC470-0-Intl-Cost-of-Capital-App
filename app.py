"""
International Cost of Capital App
=================================

Compares a project's cost of equity and WACC in USD and in the currency of a
selected country, using:

  * Damodaran unlevered industry betas (U.S. industries)
  * OECD statutory corporate income tax rates
  * Damodaran BBB (Baa2) default spread for large non-financial firms
  * Trading Economics 10-year government bond yields

Layout of this file
-------------------
  1. Constants, source URLs, clearly-labelled fallback data
  2. Data fetching / parsing (cached; every fetch is wrapped in try/except)
  3. Calculations: ALL FORMULAS LIVE IN `compute_cost_of_capital()`
  4. Streamlit UI
"""

from __future__ import annotations

import datetime as dt
import io
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup

# =============================================================================
# 1. CONSTANTS, SOURCES, FALLBACKS
# =============================================================================

APP_TITLE = "International Cost of Capital App"

BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
OECD_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
BONDS_URL = "https://tradingeconomics.com/bonds"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/vnd.ms-excel,application/vnd.openxmlformats-officedocument."
    "spreadsheetml.sheet,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
}
HTTP_TIMEOUT = 25  # seconds
CACHE_TTL = 6 * 60 * 60  # 6 hours; the "Refresh data" button clears it

US = "United States"
DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = US
DEFAULT_ERP_PCT = 6.5
DEFAULT_DV_PCT = 30.0

# ---- Fallback values: used ONLY when a live fetch fails. Clearly labelled in the UI.
# They are approximate / illustrative and should be replaced via the manual overrides.
FALLBACK_BETAS = {  # illustrative unlevered betas (approximate, NOT live data)
    "Software (System & Application)": 1.20,
    "Software (Internet)": 1.35,
    "Semiconductor": 1.40,
    "Computers/Peripherals": 1.15,
    "Retail (General)": 0.85,
    "Drugs (Pharmaceutical)": 0.95,
    "Utility (General)": 0.35,
    "Auto & Truck": 1.10,
    "Food Processing": 0.55,
    "Telecom. Services": 0.55,
    "Oil/Gas (Integrated)": 0.75,
    "Chemical (Basic)": 0.85,
}
FALLBACK_SPREAD_BBB = 0.0111  # Damodaran, large non-financial Baa2/BBB, Jan-2026 table
FALLBACK_TAX = {  # approximate combined statutory CIT rates (decimal), illustrative
    "United States": 0.258, "United Kingdom": 0.25, "Germany": 0.299, "France": 0.258,
    "Italy": 0.278, "Spain": 0.25, "Netherlands": 0.258, "Belgium": 0.25,
    "Austria": 0.23, "Ireland": 0.125, "Portugal": 0.315, "Greece": 0.22,
    "Finland": 0.20, "Sweden": 0.206, "Norway": 0.22, "Denmark": 0.22,
    "Switzerland": 0.196, "Poland": 0.19, "Czech Republic": 0.21, "Hungary": 0.09,
    "Japan": 0.297, "South Korea": 0.264, "China": 0.25, "India": 0.252,
    "Australia": 0.30, "New Zealand": 0.28, "Canada": 0.262, "Mexico": 0.30,
    "Brazil": 0.34, "Chile": 0.27, "Colombia": 0.35, "Peru": 0.295,
    "South Africa": 0.27, "Israel": 0.23, "Turkey": 0.25, "Indonesia": 0.22,
    "Singapore": 0.17, "Hong Kong": 0.165, "Philippines": 0.25, "Thailand": 0.20,
    "Malaysia": 0.24,
}
FALLBACK_TAX.update({  # more approximate rates so the fallback country list is broad
    "Russia": 0.25, "Lithuania": 0.16, "Romania": 0.16, "Slovakia": 0.21, "Slovenia": 0.22,
    "Iceland": 0.21, "Croatia": 0.18, "Taiwan": 0.20, "Vietnam": 0.20, "Pakistan": 0.29,
    "Zambia": 0.30, "Kenya": 0.30, "Nigeria": 0.30,
})
# 10Y yields (%) read manually from tradingeconomics.com/bonds on 2026-11-16.
# Used ONLY if the live scrape fails (TE serves its table via a bot-protected endpoint).
FALLBACK_YIELDS_DATE = "2026-11-16 snapshot"
FALLBACK_YIELDS = {k: v / 100 for k, v in {
    "United States": 5.1410, "United Kingdom": 5.3653, "Japan": 2.8880, "Australia": 5.1250,
    "Germany": 3.3884, "Brazil": 15.3450, "Russia": 16.7400, "India": 7.3180, "Canada": 3.8200,
    "Italy": 4.4570, "France": 4.9520, "South Africa": 8.7200, "China": 1.6331,
    "Switzerland": 0.5700, "Mexico": 9.2990, "Netherlands": 3.4750, "New Zealand": 4.7740,
    "Portugal": 3.8380, "South Korea": 3.9860, "Spain": 3.9610, "Greece": 4.2950,
    "Turkey": 36.4750, "Finland": 3.7640, "Lithuania": 4.3390, "Norway": 4.3910,
    "Poland": 6.3360, "Romania": 7.1600, "Slovakia": 4.3000, "Slovenia": 3.9390,
    "Sweden": 3.1000, "Hungary": 5.5250, "Iceland": 6.8100, "Ireland": 3.5270,
    "Austria": 3.6260, "Belgium": 4.3870, "Croatia": 3.8670, "Czech Republic": 5.2310,
    "Denmark": 3.1710, "Peru": 6.2414, "Chile": 6.0100, "Colombia": 12.6980,
    "Singapore": 2.3429, "Taiwan": 1.8700, "Thailand": 1.9830, "Vietnam": 4.5730,
    "Hong Kong": 3.9960, "Indonesia": 7.6070, "Israel": 4.1270, "Malaysia": 3.8654,
    "Pakistan": 12.6220, "Philippines": 7.7030, "Zambia": 14.7400, "Kenya": 12.5000,
    "Nigeria": 16.5980,
}.items()}
FRED_DGS10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"  # keyless U.S. 10Y backup

# ---- Country-name harmonisation (OECD names / ISO3 codes vs Trading Economics slugs)
ISO3_TO_NAME = {
    "USA": "United States", "GBR": "United Kingdom", "DEU": "Germany", "FRA": "France",
    "ITA": "Italy", "ESP": "Spain", "NLD": "Netherlands", "BEL": "Belgium", "AUT": "Austria",
    "IRL": "Ireland", "PRT": "Portugal", "GRC": "Greece", "FIN": "Finland", "SWE": "Sweden",
    "NOR": "Norway", "DNK": "Denmark", "CHE": "Switzerland", "POL": "Poland",
    "CZE": "Czech Republic", "HUN": "Hungary", "SVK": "Slovakia", "SVN": "Slovenia",
    "EST": "Estonia", "LVA": "Latvia", "LTU": "Lithuania", "LUX": "Luxembourg",
    "ISL": "Iceland", "JPN": "Japan", "KOR": "South Korea", "CHN": "China", "IND": "India",
    "AUS": "Australia", "NZL": "New Zealand", "CAN": "Canada", "MEX": "Mexico",
    "BRA": "Brazil", "CHL": "Chile", "COL": "Colombia", "PER": "Peru", "ARG": "Argentina",
    "ZAF": "South Africa", "ISR": "Israel", "TUR": "Turkey", "IDN": "Indonesia",
    "SGP": "Singapore", "HKG": "Hong Kong", "PHL": "Philippines", "THA": "Thailand",
    "MYS": "Malaysia", "VNM": "Vietnam", "RUS": "Russia", "ROU": "Romania",
    "BGR": "Bulgaria", "HRV": "Croatia", "SRB": "Serbia", "CYP": "Cyprus", "MLT": "Malta",
    "EGY": "Egypt", "NGA": "Nigeria", "KEN": "Kenya", "PAK": "Pakistan", "BGD": "Bangladesh",
    "LKA": "Sri Lanka", "TWN": "Taiwan", "UKR": "Ukraine", "KAZ": "Kazakhstan",
    "MAR": "Morocco", "SAU": "Saudi Arabia", "ARE": "United Arab Emirates", "QAT": "Qatar",
    "UGA": "Uganda", "ZMB": "Zambia", "NAM": "Namibia", "BWA": "Botswana", "MUS": "Mauritius",
    "JOR": "Jordan", "CRI": "Costa Rica", "URY": "Uruguay",
}
COUNTRY_ALIASES = {  # canonical lower-case key -> canonical lower-case name
    "korea": "south korea", "republic of korea": "south korea",
    "turkiye": "turkey", "czechia": "czech republic", "slovak republic": "slovakia",
    "russian federation": "russia", "viet nam": "vietnam", "chinese taipei": "taiwan",
    "united states of america": "united states", "us": "united states",
    "uk": "united kingdom", "people s republic of china": "china",
}
CURRENCY = {
    "United States": "USD", "United Kingdom": "GBP", "Japan": "JPY", "China": "CNY",
    "India": "INR", "Brazil": "BRL", "Canada": "CAD", "Australia": "AUD", "Mexico": "MXN",
    "South Korea": "KRW", "Switzerland": "CHF", "Sweden": "SEK", "Norway": "NOK",
    "Denmark": "DKK", "Poland": "PLN", "Czech Republic": "CZK", "Hungary": "HUF",
    "Romania": "RON", "Turkey": "TRY", "South Africa": "ZAR", "Chile": "CLP",
    "Colombia": "COP", "Peru": "PEN", "Israel": "ILS", "New Zealand": "NZD",
    "Singapore": "SGD", "Hong Kong": "HKD", "Indonesia": "IDR", "Philippines": "PHP",
    "Thailand": "THB", "Malaysia": "MYR", "Vietnam": "VND", "Russia": "RUB",
    "Iceland": "ISK", "Egypt": "EGP", "Nigeria": "NGN", "Kenya": "KES", "Pakistan": "PKR",
    "Taiwan": "TWD", "Serbia": "RSD", "Ukraine": "UAH", "Kazakhstan": "KZT",
    "Morocco": "MAD", "Bangladesh": "BDT", "Sri Lanka": "LKR",
}
for _euro in ["Germany", "France", "Italy", "Spain", "Netherlands", "Belgium", "Austria",
              "Ireland", "Portugal", "Greece", "Finland", "Slovakia", "Slovenia",
              "Lithuania", "Latvia", "Estonia", "Luxembourg", "Cyprus", "Malta",
              "Croatia", "Bulgaria"]:
    CURRENCY[_euro] = "EUR"


def canon_country(name) -> str:
    """Harmonise country labels from different sources to one display name."""
    raw = str(name).strip()
    if re.fullmatch(r"[A-Z]{3}", raw):
        return ISO3_TO_NAME.get(raw, raw)
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    s = re.sub(r"\(.*?\)", " ", s)          # "China (People's Republic of)" -> "China"
    s = s.split(",")[0]                     # "Hong Kong, China" -> "Hong Kong"
    s = re.sub(r"[^A-Za-z ]", " ", s).lower()
    s = " ".join(s.split())
    s = COUNTRY_ALIASES.get(s, s)
    return s.title()


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return " ".join(str(v).replace("\n", " ").split()).strip().lower()


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _http_get(url: str) -> requests.Response:
    r = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r


def _to_float(v) -> Optional[float]:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _result(source: str, url: str) -> dict:
    return {"source": source, "url": url, "fetched_at": _now_utc(), "ok": False,
            "error": None, "as_of": None, "data": None, "note": ""}


# =============================================================================
# 2. DATA FETCHING & PARSING
# =============================================================================

# ---------------------------------------------------------------- 2a. Betas
def _format_date_cell(v) -> str:
    if isinstance(v, (dt.datetime, dt.date, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%B %d, %Y")
    f = _to_float(v)
    if f is not None and 30000 < f < 70000:  # Excel serial date
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=f)).strftime("%B %d, %Y")
    return str(v).strip()


def _find_update_date(top: pd.DataFrame) -> Optional[str]:
    """Look for a 'Date updated' label (or any date cell) in the title rows."""
    for _, row in top.iterrows():
        cells = row.tolist()
        for j, c in enumerate(cells):
            s = _norm(c)
            if "date" in s or "updated" in s:
                for c2 in cells[j + 1:]:
                    if _norm(c2):
                        return _format_date_cell(c2)
                if ":" in str(c):
                    tail = str(c).split(":", 1)[1].strip()
                    if tail:
                        return tail
    for _, row in top.iterrows():
        for c in row.tolist():
            if isinstance(c, (dt.datetime, dt.date, pd.Timestamp)):
                return _format_date_cell(c)
    return None


def parse_betas_excel(content: bytes) -> tuple[pd.DataFrame, Optional[str], str]:
    """Locate the sheet + header row containing 'Industry Name' and 'Unlevered beta'."""
    xls = pd.ExcelFile(io.BytesIO(content))  # pandas sniffs .xls (xlrd) vs .xlsx (openpyxl)
    sheets = sorted(xls.sheet_names, key=lambda s: 0 if "industry" in s.lower() else 1)
    for sh in sheets:
        raw = pd.read_excel(xls, sheet_name=sh, header=None)
        hdr = None
        for i in range(min(80, len(raw))):
            vals = [_norm(v) for v in raw.iloc[i].tolist()]
            if "industry name" in vals and any("unlevered beta" in v for v in vals):
                hdr = i
                break
        if hdr is None:
            continue
        headers = [_norm(v) for v in raw.iloc[hdr].tolist()]
        ind_col = headers.index("industry name")
        # Prefer the column named exactly "Unlevered beta" (NOT "...corrected for cash")
        exact = [j for j, h in enumerate(headers) if h == "unlevered beta"]
        loose = [j for j, h in enumerate(headers) if "unlevered beta" in h and "cash" not in h]
        cols = exact or loose
        if not cols:
            continue
        df = raw.iloc[hdr + 1:, [ind_col, cols[0]]].copy()
        df.columns = ["Industry", "Unlevered beta"]
        df["Industry"] = df["Industry"].astype(str).str.strip()
        df["Unlevered beta"] = pd.to_numeric(df["Unlevered beta"], errors="coerce")
        df = df[(df["Industry"] != "") & (df["Industry"].str.lower() != "nan")]
        df = df[df["Unlevered beta"].notna()]
        df = df[~df["Industry"].str.lower().str.startswith("total market")]
        df = df.drop_duplicates("Industry").reset_index(drop=True)
        if len(df) < 10:
            continue
        return df, _find_update_date(raw.iloc[:hdr]), sh
    raise ValueError("Could not find an 'Industry Name' / 'Unlevered beta' header in any sheet")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_betas() -> dict:
    res = _result("Damodaran (NYU Stern): Betas by Sector, U.S.", BETAS_URL)
    try:
        r = _http_get(BETAS_URL)
        df, as_of, sheet = parse_betas_excel(r.content)
        res.update(ok=True, data=df, note=f"sheet '{sheet}', column 'Unlevered beta'")
        lm = r.headers.get("Last-Modified")
        res["as_of"] = as_of or (f"file last modified {lm}" if lm else "date not stated in file")
    except Exception as e:  # noqa: BLE001 - never crash on a failed fetch
        res["error"] = f"{type(e).__name__}: {e}"
        res["data"] = pd.DataFrame(
            {"Industry": list(FALLBACK_BETAS), "Unlevered beta": list(FALLBACK_BETAS.values())})
        res["as_of"] = "FALLBACK (illustrative values, not live data)"
    return res


# ---------------------------------------------------------------- 2b. OECD tax rates
_YEAR_RE = re.compile(r"(19|20)\d{2}(\.0)?")


def parse_tax_frame(raw: pd.DataFrame) -> Optional[tuple[dict, str]]:
    """Parse a raw (header=None) table of country -> statutory CIT rate.

    Column choice, in order: a 'Combined' column; else the latest year column;
    else the sum of central + sub-central components; else the single numeric column.
    """
    if raw is None or raw.empty:
        return None
    raw = raw.reset_index(drop=True)
    nrows = len(raw)
    title = " ".join(_norm(v) for v in raw.iloc[: min(8, nrows)].values.ravel())
    hdr = None
    for i in range(min(40, nrows)):
        vals = [_norm(v) for v in raw.iloc[i].tolist()]
        numeric_cells = sum(_to_float(v) is not None for v in vals if v)
        if (any(_YEAR_RE.fullmatch(v) for v in vals) or any("combined" in v for v in vals)
                or any(v in ("jurisdiction", "country", "economy", "countries") for v in vals)
                or (numeric_cells == 0 and sum("central" in v or "rate" in v for v in vals) >= 1
                    and sum(bool(v) for v in vals) >= 2)):
            hdr = i
            break
    headers = [_norm(v) for v in raw.iloc[hdr].tolist()] if hdr is not None else [""] * raw.shape[1]
    body = raw.iloc[(hdr + 1 if hdr is not None else 0):].reset_index(drop=True)
    if body.empty:
        return None

    # Country column = column with the most non-numeric text labels
    def n_text(col):
        return sum(1 for v in col if isinstance(v, str) and _to_float(v) is None and len(v.strip()) >= 3)

    counts = [n_text(body.iloc[:, j]) for j in range(body.shape[1])]
    ccol = int(np.argmax(counts))
    if counts[ccol] < 10:
        return None
    numcols = [j for j in range(body.shape[1]) if j != ccol
               and sum(_to_float(v) is not None for v in body.iloc[:, j]) >= 0.5 * counts[ccol]]
    if not numcols:
        return None

    year_label = None
    years = {j: int(float(headers[j])) for j in numcols if _YEAR_RE.fullmatch(headers[j] or "")}
    combined = [j for j in numcols if "combined" in headers[j]]
    components = [j for j in numcols if any(k in headers[j] for k in ("central", "sub", "surtax", "local"))]
    if combined:
        use, how = [combined[0]], "sum"
    elif years:
        jmax = max(years, key=years.get)
        use, how, year_label = [jmax], "sum", str(years[jmax])
    elif len(components) >= 2:
        use, how = components, "sum"
    else:
        use, how = [numcols[0]], "sum"
    if year_label is None:
        m = re.search(r"\b(20\d{2})\b", title)
        year_label = m.group(1) if m else "latest available"

    vals = body.iloc[:, use].map(_to_float)
    series = vals.sum(axis=1, min_count=1) if how == "sum" else vals.iloc[:, 0]
    finite = series.dropna()
    if finite.empty:
        return None
    scale = 1.0 if finite.max() <= 1.0 else 100.0  # fractions vs percentages
    out = {}
    for name, v in zip(body.iloc[:, ccol], series):
        if v is None or (isinstance(v, float) and math.isnan(v)) or not isinstance(name, str):
            continue
        rate = float(v) / scale
        if 0 <= rate <= 0.6:
            out.setdefault(canon_country(name), rate)
    return (out, year_label) if len(out) >= 15 else None


def _html_tables_as_raw(html: str) -> list[pd.DataFrame]:
    try:
        tables = pd.read_html(io.StringIO(html))
    except (ValueError, ImportError):
        return []
    raws = []
    for t in tables:
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
        raws.append(pd.concat([pd.DataFrame([cols]), pd.DataFrame(t.values)], ignore_index=True))
    return raws


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_tax_rates() -> dict:
    res = _result("OECD Corporate Tax Statistics 2024: statutory CIT rates", OECD_URL)
    try:
        r = _http_get(OECD_URL)
        parsed, how = None, ""
        # (A) an HTML table on the page itself
        for raw in _html_tables_as_raw(r.text):
            parsed = parse_tax_frame(raw)
            if parsed:
                how = "HTML table on the OECD page"
                break
        # (B) the figure's StatLink (Excel file behind 'Figure 2.1: Statutory CIT rates')
        if not parsed:
            links = list(dict.fromkeys(re.findall(r"https?://stat\.link/[A-Za-z0-9]+", r.text)))
            for link in links[:3]:
                try:
                    r2 = _http_get(link)
                    xls = pd.ExcelFile(io.BytesIO(r2.content))
                    for sh in xls.sheet_names:
                        parsed = parse_tax_frame(pd.read_excel(xls, sheet_name=sh, header=None))
                        if parsed:
                            how = f"StatLink Excel {link} (sheet '{sh}')"
                            break
                except Exception:  # noqa: BLE001
                    continue
                if parsed:
                    break
        if not parsed:
            raise ValueError("no parsable tax-rate table or StatLink file found on the OECD page")
        rates, year = parsed
        res.update(ok=True, data=rates, as_of=f"tax year {year}", note=f"parsed from {how}")
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        res["data"] = dict(FALLBACK_TAX)
        res["as_of"] = "FALLBACK (approximate 2024 combined rates, not live data)"
    return res


# ---------------------------------------------------------------- 2c. BBB spread
def parse_ratings_html(html: str) -> tuple[float, Optional[str]]:
    """First Baa2/BBB row = left-hand block 'For large non-financial service firms'."""
    soup = BeautifulSoup(html, "lxml")
    spread = None
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        for j, c in enumerate(cells):
            if re.sub(r"\s", "", c).lower() == "baa2/bbb":
                for c2 in cells[j + 1:]:
                    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", c2)
                    if m:
                        spread = float(m.group(1)) / 100
                        break
                    f = _to_float(c2)
                    if f is not None:
                        spread = f / 100 if f > 0.2 else f
                        break
                break
        if spread is not None:
            break
    if spread is None:
        raise ValueError("Baa2/BBB row not found")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"as of\s+([A-Za-z]+\s+\d{4})", text, flags=re.I)
    return spread, (m.group(1) if m else None)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_bbb_spread() -> dict:
    res = _result("Damodaran (NYU Stern): ratings, interest coverage & default spreads", RATINGS_URL)
    try:
        r = _http_get(RATINGS_URL)
        spread, as_of = parse_ratings_html(r.text)
        res.update(ok=True, data=spread, as_of=f"data as of {as_of}" if as_of else "date not stated",
                   note="Baa2/BBB row, large non-financial service firms table")
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        res["data"] = FALLBACK_SPREAD_BBB
        res["as_of"] = "FALLBACK (Damodaran Jan-2026 value, 1.11%)"
    return res


# ---------------------------------------------------------------- 2d. 10Y yields
def parse_bonds_html(html: str) -> dict:
    """Rows whose link ends in '/<country>/government-bond-yield' are the 10-year bonds.
    The first numeric cell after the name cell is the yield (%); last cell is the quote date."""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=re.compile(r"/government-bond-yield/?$"))
        if a is None:
            continue
        parts = [p for p in a["href"].split("/") if p]
        if len(parts) < 2:
            continue
        name = canon_country(parts[-2].replace("-", " "))
        tds = tr.find_all("td")
        idx = next((i for i, td in enumerate(tds) if td.find("a", href=a["href"])), None)
        if idx is None:
            continue
        y = None
        p_cell = tr.find("td", id="p")  # TE marks the price/yield cell with id="p"
        if p_cell is not None:
            y = _to_float(p_cell.get_text(strip=True))
        if y is None:
            for td in tds[idx + 1:]:
                y = _to_float(td.get_text(strip=True))
                if y is not None:
                    break
        if y is None or not (-5 < y < 100) or name in out:
            continue
        out[name] = {"yield": y / 100, "date": tds[-1].get_text(strip=True) if tds else ""}
    return out


def _fetch_fred_us10y() -> tuple[float, str]:
    """Backup only: latest non-missing FRED DGS10 observation (percent)."""
    r = _http_get(FRED_DGS10_URL)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    last = df.dropna().iloc[-1]
    return float(last["value"]) / 100, str(last["date"])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_yields() -> dict:
    res = _result("Trading Economics: government bond 10Y yields", BONDS_URL)
    try:
        data, errors = {}, []
        # TE renders the table client-side; the same rows are served by '?partial=markets'.
        with requests.Session() as s:
            s.headers.update(HTTP_HEADERS)
            for url in (BONDS_URL, BONDS_URL + "?partial=markets"):
                try:
                    r = s.get(url, timeout=HTTP_TIMEOUT)
                    r.raise_for_status()
                    data = parse_bonds_html(r.text)
                    if len(data) >= 5:
                        break
                    errors.append(f"{url}: HTTP {r.status_code}, {len(data)} rows parsed")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{url}: {type(e).__name__}: {e}")
        if len(data) < 5:
            raise ValueError("; ".join(errors) + " (TE may be blocking automated requests)")
        res.update(ok=True, data=data, as_of=f"quotes fetched {res['fetched_at']}",
                   note="per-country quote date shown with each yield")
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        res["data"] = {k: {"yield": v, "date": FALLBACK_YIELDS_DATE} for k, v in FALLBACK_YIELDS.items()}
        res["as_of"] = f"FALLBACK: Trading Economics {FALLBACK_YIELDS_DATE} (not live)"
        try:  # keep at least the U.S. rate live
            y, d = _fetch_fred_us10y()
            res["data"][US] = {"yield": y, "date": f"FRED DGS10 {d}"}
            res["note"] = f"U.S. 10Y live from FRED DGS10 ({d}): {FRED_DGS10_URL}"
            res["as_of"] += f"; U.S. from FRED DGS10 as of {d}"
        except Exception as e2:  # noqa: BLE001
            res["error"] += f" | FRED backup failed: {type(e2).__name__}"
    return res


# =============================================================================
# 3. CALCULATIONS — every formula is here and numbered
# =============================================================================

@dataclass
class CoCInputs:
    beta_u: float          # unlevered beta
    tax: float             # project tax rate t (decimal)
    erp: float             # equity risk premium (decimal)
    wd: float              # target debt share D/V (decimal, 0 <= wd < 1)
    y_us: float            # U.S. 10Y government yield (decimal)
    y_fc: float            # selected country's 10Y government yield (decimal)
    s_usd: float           # USD BBB default spread (decimal)
    s_fc_override: Optional[float] = None  # foreign BBB spread override (decimal) or None


def compute_cost_of_capital(p: CoCInputs) -> dict:
    """Return every intermediate value. Inputs are decimals (0.065 = 6.5%)."""
    if not (0 <= p.wd < 1):
        raise ValueError("Debt share D/V must be in [0, 1).")
    if p.y_us <= -1 or p.y_fc <= -1:
        raise ValueError("Yields must be greater than -100%.")

    # [F1] Debt-to-equity implied by the debt share:  D/E = (D/V) / (1 - D/V)
    de = p.wd / (1 - p.wd)
    # [F2] Hamada relevering:  beta_L = beta_U * [1 + (1 - t) * D/E]
    beta_l = p.beta_u * (1 + (1 - p.tax) * de)
    # [F3] USD cost of equity (CAPM):  Ke_USD = rf_US + beta_L * ERP
    ke_usd = p.y_us + beta_l * p.erp
    # [F4] USD pre-tax cost of debt:  Kd_USD = rf_US + spread_USD(BBB)
    kd_usd = p.y_us + p.s_usd
    # [F5] USD after-tax cost of debt:  Kd_USD * (1 - t)
    kd_usd_at = kd_usd * (1 - p.tax)
    # [F6] USD WACC:  (E/V) * Ke + (D/V) * Kd * (1 - t),  with E/V = 1 - D/V
    wacc_usd = (1 - p.wd) * ke_usd + p.wd * kd_usd_at

    # [F7] Interest-rate (inflation) differential:  X = (1 + rf_FC) / (1 + rf_US)
    x = (1 + p.y_fc) / (1 + p.y_us)
    # [F8] Foreign-currency cost of equity:  Ke_FC = (1 + Ke_USD) * X - 1
    ke_fc = (1 + ke_usd) * x - 1
    same_rf = p.y_fc == p.y_us  # e.g. U.S. selected: X = 1, avoid floating-point noise
    if same_rf:
        ke_fc = ke_usd
    # [F9] Foreign BBB spread (automatic), consistent with converting Kd_USD:
    #      Kd_FC = (1 + Kd_USD) * X - 1   and   spread_FC = Kd_FC - rf_FC
    #      which simplifies to  spread_FC = spread_USD * (1 + rf_FC) / (1 + rf_US)
    s_fc_auto = p.s_usd if same_rf else (1 + kd_usd) * x - 1 - p.y_fc
    s_fc = s_fc_auto if p.s_fc_override is None else p.s_fc_override
    # [F10] Foreign pre-tax cost of debt:  Kd_FC = rf_FC + spread_FC
    kd_fc = p.y_fc + s_fc
    # [F11] Foreign after-tax cost of debt:  Kd_FC * (1 - t)
    kd_fc_at = kd_fc * (1 - p.tax)
    # [F12] Foreign WACC:  (1 - D/V) * Ke_FC + (D/V) * Kd_FC * (1 - t)
    wacc_fc = (1 - p.wd) * ke_fc + p.wd * kd_fc_at

    # Cross-checks / memo items (not used in the results)
    kd_usd_converted = (1 + kd_usd) * x - 1               # equals kd_fc when spread is automatic
    wacc_usd_converted = (1 + wacc_usd) * x - 1           # direct conversion of USD WACC
    wacc_gap = wacc_fc - wacc_usd_converted               # analytically = (D/V) * t * (1 - X) when auto spread
    ke_fc_additive = p.y_fc + beta_l * p.erp              # alternative convention (README)

    return dict(de=de, beta_l=beta_l, ke_usd=ke_usd, kd_usd=kd_usd, kd_usd_at=kd_usd_at,
                wacc_usd=wacc_usd, x=x, ke_fc=ke_fc, s_fc_auto=s_fc_auto, s_fc=s_fc,
                s_fc_is_override=p.s_fc_override is not None, kd_fc=kd_fc, kd_fc_at=kd_fc_at,
                wacc_fc=wacc_fc, kd_usd_converted=kd_usd_converted,
                wacc_usd_converted=wacc_usd_converted, wacc_gap=wacc_gap,
                wacc_gap_formula=p.wd * p.tax * (1 - x), ke_fc_additive=ke_fc_additive)


def step_table(p: CoCInputs, r: dict) -> pd.DataFrame:
    """Every formula with its numeric substitution, so each number can be traced."""
    f = lambda v: f"{v:.6f}"  # noqa: E731
    rows = [
        ("F1", "Debt-to-equity", "D/E = (D/V) / (1 − D/V)",
         f"{f(p.wd)} / (1 − {f(p.wd)})", r["de"]),
        ("F2", "Levered beta (Hamada)", "βL = βU × [1 + (1 − t) × D/E]",
         f"{f(p.beta_u)} × [1 + (1 − {f(p.tax)}) × {f(r['de'])}]", r["beta_l"]),
        ("F3", "USD cost of equity", "Ke$ = rf_US + βL × ERP",
         f"{f(p.y_us)} + {f(r['beta_l'])} × {f(p.erp)}", r["ke_usd"]),
        ("F4", "USD pre-tax cost of debt", "Kd$ = rf_US + spread_USD",
         f"{f(p.y_us)} + {f(p.s_usd)}", r["kd_usd"]),
        ("F5", "USD after-tax cost of debt", "Kd$ × (1 − t)",
         f"{f(r['kd_usd'])} × (1 − {f(p.tax)})", r["kd_usd_at"]),
        ("F6", "USD WACC", "(1 − D/V) × Ke$ + D/V × Kd$ × (1 − t)",
         f"(1 − {f(p.wd)}) × {f(r['ke_usd'])} + {f(p.wd)} × {f(r['kd_usd_at'])}", r["wacc_usd"]),
        ("F7", "Rate differential", "X = (1 + rf_FC) / (1 + rf_US)",
         f"(1 + {f(p.y_fc)}) / (1 + {f(p.y_us)})", r["x"]),
        ("F8", "Local cost of equity", "Ke_FC = (1 + Ke$) × X − 1",
         f"(1 + {f(r['ke_usd'])}) × {f(r['x'])} − 1", r["ke_fc"]),
        ("F9", "Local BBB spread (automatic)",
         "s_FC = (1 + Kd$) × X − 1 − rf_FC  ( = s_USD × X )",
         f"(1 + {f(r['kd_usd'])}) × {f(r['x'])} − 1 − {f(p.y_fc)}", r["s_fc_auto"]),
        ("F9*", "Local BBB spread used",
         "manual override" if r["s_fc_is_override"] else "= automatic (F9)",
         f(r["s_fc"]), r["s_fc"]),
        ("F10", "Local pre-tax cost of debt", "Kd_FC = rf_FC + s_FC",
         f"{f(p.y_fc)} + {f(r['s_fc'])}", r["kd_fc"]),
        ("F11", "Local after-tax cost of debt", "Kd_FC × (1 − t)",
         f"{f(r['kd_fc'])} × (1 − {f(p.tax)})", r["kd_fc_at"]),
        ("F12", "Local WACC", "(1 − D/V) × Ke_FC + D/V × Kd_FC × (1 − t)",
         f"(1 − {f(p.wd)}) × {f(r['ke_fc'])} + {f(p.wd)} × {f(r['kd_fc_at'])}", r["wacc_fc"]),
        ("Memo", "Check: converted USD Kd", "(1 + Kd$) × X − 1  (= F10 if spread automatic)",
         f"(1 + {f(r['kd_usd'])}) × {f(r['x'])} − 1", r["kd_usd_converted"]),
        ("Memo", "Check: converted USD WACC", "(1 + WACC$) × X − 1",
         f"(1 + {f(r['wacc_usd'])}) × {f(r['x'])} − 1", r["wacc_usd_converted"]),
        ("Memo", "Gap: F12 − converted WACC", "= D/V × t × (1 − X) when spread automatic",
         f"{f(p.wd)} × {f(p.tax)} × (1 − {f(r['x'])}) = {f(r['wacc_gap_formula'])}", r["wacc_gap"]),
        ("Memo", "Alt. convention: additive Ke_FC", "rf_FC + βL × ERP (not used)",
         f"{f(p.y_fc)} + {f(r['beta_l'])} × {f(p.erp)}", r["ke_fc_additive"]),
    ]
    return pd.DataFrame(rows, columns=["Step", "Item", "Formula", "Substitution", "Result (decimal)"])


def all_industry_table(betas: pd.DataFrame, base: CoCInputs) -> pd.DataFrame:
    rows = []
    for ind, bu in zip(betas["Industry"], betas["Unlevered beta"]):
        p = CoCInputs(**{**base.__dict__, "beta_u": float(bu)})
        r = compute_cost_of_capital(p)
        rows.append({"Industry": ind, "Unlevered beta": bu, "Levered beta": r["beta_l"],
                     "Ke USD": r["ke_usd"], "Ke local": r["ke_fc"],
                     "WACC USD": r["wacc_usd"], "WACC local": r["wacc_fc"]})
    return pd.DataFrame(rows)


def validation_messages(p: CoCInputs, r: dict, country: str) -> list[tuple[str, str]]:
    """(level, message) pairs for implausible combinations."""
    msgs = []
    if p.wd > 0.8:
        msgs.append(("warning", f"D/V = {p.wd:.0%} is very high; Hamada beta ({r['beta_l']:.2f}) "
                                "and a BBB spread are unlikely to be consistent at this leverage."))
    if r["beta_l"] > 4:
        msgs.append(("warning", f"Levered beta {r['beta_l']:.2f} is implausibly high."))
    if p.erp == 0:
        msgs.append(("warning", "Equity risk premium is 0%: cost of equity equals the risk-free rate."))
    if r["ke_usd"] <= r["kd_usd"]:
        msgs.append(("warning", "USD cost of equity ≤ USD pre-tax cost of debt — check beta/ERP/spread."))
    if p.wd > 0 and r["ke_fc"] <= r["kd_fc"]:
        msgs.append(("warning", "Local cost of equity ≤ local pre-tax cost of debt — check the local spread override."))
    if p.s_usd > 0.08 or r["s_fc"] > 0.15:
        msgs.append(("warning", "A BBB spread above 8% (USD) / 15% (local) is unusual for investment grade."))
    if p.y_us < 0 or p.y_fc < 0:
        msgs.append(("info", "A negative 10-year yield is being used."))
    if p.y_fc > 0.25:
        msgs.append(("warning", f"{country}'s 10Y yield ({p.y_fc:.1%}) is very high; the differential "
                                "conversion will dominate the local-currency results."))
    if p.tax > 0.5:
        msgs.append(("warning", f"Tax rate {p.tax:.1%} is unusually high."))
    if r["wacc_fc"] <= p.y_fc:
        msgs.append(("warning", "Local WACC is not above the local risk-free rate."))
    return msgs


# =============================================================================
# 4. UI
# =============================================================================

def pct(x: float, d: int = 3) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def override_input(label: str, fetched: float, key: str, lo: float, hi: float,
                   step: float, is_pct: bool = True, help_txt: str = "") -> tuple[float, bool]:
    """Toggle + input. When the toggle is off, the fetched value is shown and used."""
    on = st.toggle(f"Override {label}", key=f"{key}_on", help=help_txt)
    if on:
        default = fetched * 100 if is_pct else fetched
        v = st.number_input(f"{label}{' (%)' if is_pct else ''}", min_value=lo, max_value=hi,
                            value=float(np.clip(default, lo, hi)), step=step, format="%.4f",
                            key=f"{key}_val")
        return (v / 100 if is_pct else v), True
    st.caption(f"Using fetched value: **{pct(fetched) if is_pct else f'{fetched:.4f}'}**")
    return fetched, False


def source_status_row(res: dict) -> dict:
    return {"Source": res["source"], "Status": "✅ live" if res["ok"] else "⚠️ fallback",
            "Data as of": res["as_of"], "Fetched at": res["fetched_at"],
            "URL": res["url"],
            "Detail": " | ".join(x for x in (res["note"], res["error"]) if x)}


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🌍", layout="wide")
    st.title(f"🌍 {APP_TITLE}")
    st.caption("Project cost of equity and WACC in USD and in the selected country's currency, "
               "with every intermediate step shown.")

    # ------------------------------------------------------------- data
    with st.sidebar:
        st.header("Data")
        if st.button("🔄 Refresh data", width="stretch",
                     help="Clear the cache and refetch all four sources"):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Fetching data (betas, tax rates, spreads, yields)…"):
        betas_res, tax_res = fetch_betas(), fetch_tax_rates()
        spread_res, yld_res = fetch_bbb_spread(), fetch_yields()

    for res in (betas_res, tax_res, spread_res, yld_res):
        if not res["ok"]:
            st.warning(f"**{res['source']}** could not be fetched or parsed ({res['error']}). "
                       f"Using clearly labelled fallback values — check them or use the manual overrides.")

    betas: pd.DataFrame = betas_res["data"]
    tax_rates: dict = dict(tax_res["data"])
    yields: dict = dict(yld_res["data"])
    # Guarantee U.S. values exist even if a live source omitted them
    if US not in tax_rates:
        tax_rates[US] = FALLBACK_TAX[US]
        st.warning("U.S. tax rate missing from OECD data; using fallback 25.8% (override available).")
    if US not in yields:
        yields[US] = {"yield": FALLBACK_YIELDS[US], "date": "fallback"}
        st.warning("U.S. 10Y yield missing from bond data; using fallback (override available).")

    countries = sorted(set(tax_rates) & set(yields))
    if US not in countries:
        countries = [US] + countries
    industries = betas["Industry"].tolist()

    # ------------------------------------------------------------- inputs
    with st.sidebar:
        st.header("Project inputs")
        industry = st.selectbox("Industry", industries,
                                index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
        country = st.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY),
                               help="Countries present in BOTH the bond-yield and tax-rate data")
        is_us = country == US
        ccy = CURRENCY.get(country, "LCY")
        lc = f"local {ccy}"  # label for local-currency columns (distinct even when ccy == USD)

        erp_pct = st.number_input("Equity risk premium (%)", 0.0, 20.0, DEFAULT_ERP_PCT, 0.1, format="%.2f")
        dv_pct = st.number_input("Target debt share D/V (%)", 0.0, 95.0, DEFAULT_DV_PCT, 1.0, format="%.2f",
                                 help="Allowed range 0%–95% (D/V = 100% implies infinite D/E).")

        st.subheader("Project tax rate")
        use_country_tax = st.toggle("Use the selected country's tax rate", value=False,
                                    help="Off = U.S. rate (default). On = selected country's rate. "
                                         "The chosen rate applies to BOTH currencies.")

        st.header("Manual overrides")
        fetched_bu = float(betas.loc[betas["Industry"] == industry, "Unlevered beta"].iloc[0])
        beta_u, ov_beta = override_input("unlevered beta", fetched_bu, f"beta_{industry}",
                                         0.0, 5.0, 0.01, is_pct=False)
        tax_us, ov_tax_us = override_input("U.S. corporate tax rate", tax_rates[US], "tax_us",
                                           0.0, 60.0, 0.1)
        if is_us:
            tax_fc, ov_tax_fc = tax_us, ov_tax_us
        else:
            tax_fc, ov_tax_fc = override_input(f"{country} corporate tax rate", tax_rates[country],
                                               f"tax_{country}", 0.0, 60.0, 0.1)
        s_usd, ov_spread = override_input("USD BBB spread", spread_res["data"], "s_usd", 0.0, 20.0, 0.01)
        y_us, ov_yus = override_input("U.S. 10Y yield", yields[US]["yield"], "y_us", -2.0, 50.0, 0.01)
        if is_us:
            y_fc, ov_yfc = y_us, ov_yus
            st.caption("Selected country is the U.S.: local 10Y yield = U.S. 10Y yield.")
        else:
            y_fc, ov_yfc = override_input(f"{country} 10Y yield", yields[country]["yield"],
                                          f"y_{country}", -2.0, 50.0, 0.01)

        tax = tax_fc if use_country_tax else tax_us

        st.subheader(f"{country} BBB spread ({ccy})")
        s_fc_auto_preview = s_usd * (1 + y_fc) / (1 + y_us)  # same as [F9]; shown before full compute
        if is_us:
            s_fc_override = None
            st.caption("U.S. selected: local spread = USD spread (no separate override).")
        else:
            ov_sfc = st.toggle(f"Override {country} BBB spread", key=f"sfc_{country}_on")
            if ov_sfc:
                s_fc_override = st.number_input(f"{country} BBB spread (%)", 0.0, 30.0,
                                                float(round(s_fc_auto_preview * 100, 4)), 0.01,
                                                format="%.4f", key=f"sfc_{country}_val") / 100
            else:
                s_fc_override = None
                st.caption(f"Automatic: s_FC = s_USD × (1 + rf_FC)/(1 + rf_US) = "
                           f"**{pct(s_fc_auto_preview, 4)}**")

    # ------------------------------------------------------------- compute
    p = CoCInputs(beta_u=beta_u, tax=tax, erp=erp_pct / 100, wd=dv_pct / 100,
                  y_us=y_us, y_fc=y_fc, s_usd=s_usd, s_fc_override=s_fc_override)
    try:
        r = compute_cost_of_capital(p)
    except ValueError as e:
        st.error(f"Invalid inputs: {e}")
        st.stop()

    for level, msg in validation_messages(p, r, country):
        (st.warning if level == "warning" else st.info)(msg)

    # ------------------------------------------------------------- headline
    st.subheader(f"{industry} — USD vs {country} ({ccy})")
    if is_us:
        st.info("United States selected: X = 1, so the local-currency results equal the USD results.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cost of equity (USD)", pct(r["ke_usd"], 2))
    c2.metric(f"Cost of equity ({lc})", pct(r["ke_fc"], 2), f"{(r['ke_fc'] - r['ke_usd']) * 100:+.2f} pp")
    c3.metric("WACC (USD)", pct(r["wacc_usd"], 2))
    c4.metric(f"WACC ({lc})", pct(r["wacc_fc"], 2), f"{(r['wacc_fc'] - r['wacc_usd']) * 100:+.2f} pp")

    side = pd.DataFrame({
        "Item": ["Unlevered beta", "D/E", "Levered beta", "Risk-free rate (10Y)", "BBB spread",
                 "Cost of equity", "Pre-tax cost of debt", "After-tax cost of debt", "WACC"],
        "USD": [p.beta_u, r["de"], r["beta_l"], p.y_us, p.s_usd, r["ke_usd"], r["kd_usd"],
                r["kd_usd_at"], r["wacc_usd"]],
        f"{ccy} ({country})": [p.beta_u, r["de"], r["beta_l"], p.y_fc, r["s_fc"], r["ke_fc"],
                               r["kd_fc"], r["kd_fc_at"], r["wacc_fc"]],
    })
    is_rate = [False, False, False, True, True, True, True, True, True]
    disp = side.copy()
    for col in disp.columns[1:]:
        disp[col] = [pct(v, 3) if rate else f"{v:.4f}" for v, rate in zip(side[col], is_rate)]
    st.dataframe(disp, hide_index=True, width="stretch")

    # ------------------------------------------------------------- inputs used
    def src(ov, live):
        return "manual override" if ov else ("fetched" if live else "FALLBACK")

    inputs_df = pd.DataFrame([
        ("Industry", industry, ""),
        ("Selected country", country, ""),
        ("Unlevered beta βU", f"{p.beta_u:.4f}", src(ov_beta, betas_res["ok"])),
        ("Equity risk premium", pct(p.erp, 3), "user input"),
        ("Debt share D/V", pct(p.wd, 2), "user input"),
        ("U.S. tax rate", pct(tax_us, 3), src(ov_tax_us, tax_res["ok"])),
        (f"{country} tax rate", pct(tax_fc, 3), src(ov_tax_fc, tax_res["ok"])),
        ("Project tax rate t (used)", pct(tax, 3), f"switch: {'selected country' if use_country_tax else 'U.S.'}"),
        ("USD BBB spread", pct(p.s_usd, 3), src(ov_spread, spread_res["ok"])),
        ("U.S. 10Y yield", pct(p.y_us, 3),
         src(ov_yus, yld_res["ok"]) + (f" (quote date {yields[US]['date']})" if not ov_yus else "")),
        (f"{country} 10Y yield", pct(p.y_fc, 3),
         src(ov_yfc, yld_res["ok"]) + (f" (quote date {yields[country]['date']})" if not ov_yfc else "")),
        (f"{country} BBB spread", pct(r["s_fc"], 4), "manual override" if r["s_fc_is_override"] else "automatic [F9]"),
    ], columns=["Input", "Value", "Source"])

    tab1, tab2, tab3, tab4 = st.tabs(["Step-by-step calculation", "Inputs used",
                                      "All-industry comparison", "Assumptions & methodology"])
    steps = step_table(p, r)
    with tab1:
        st.caption("Each row shows the formula, the exact numbers substituted (decimals), and the result. "
                   "Formula IDs [F1]–[F12] match comments in `compute_cost_of_capital()` in app.py.")
        sd = steps.copy()
        sd["Result (%, where a rate)"] = [
            "" if s in ("F1", "F2", "F7") else pct(v, 4) for s, v in zip(steps["Step"], steps["Result (decimal)"])]
        sd["Result (decimal)"] = sd["Result (decimal)"].map(lambda v: f"{v:.6f}")
        st.dataframe(sd, hide_index=True, width="stretch")
    with tab2:
        st.dataframe(inputs_df, hide_index=True, width="stretch")

    # ------------------------------------------------------------- all industries
    with tab3:
        st.caption("Uses each industry's fetched unlevered beta with all other current settings "
                   "(tax, D/V, ERP, yields, spreads). The unlevered-beta override applies only to "
                   f"the selected industry, so it is reflected in the row for **{industry}** only.")
        ind_df = all_industry_table(betas, p)
        ind_df.loc[ind_df["Industry"] == industry, ["Unlevered beta", "Levered beta", "Ke USD", "Ke local",
                                                    "WACC USD", "WACC local"]] = [
            p.beta_u, r["beta_l"], r["ke_usd"], r["ke_fc"], r["wacc_usd"], r["wacc_fc"]]
        ind_df = ind_df.rename(columns={"Ke local": f"Ke {lc}", "WACC local": f"WACC {lc}"})
        sort_by = st.selectbox("Sort / chart by", ["WACC USD", f"WACC {lc}", "Ke USD", f"Ke {lc}",
                                                   "Unlevered beta", "Industry"])
        ind_df = ind_df.sort_values(sort_by, ascending=(sort_by == "Industry")).reset_index(drop=True)
        fmt = {c: "{:.3%}" for c in ind_df.columns if c.startswith(("Ke", "WACC"))}
        fmt.update({"Unlevered beta": "{:.4f}", "Levered beta": "{:.4f}"})
        st.dataframe(ind_df.style.format(fmt), hide_index=True, width="stretch", height=420)

        metric = st.radio("Chart metric", ["Cost of equity", "WACC"], horizontal=True)
        a, b = ("Ke USD", f"Ke {lc}") if metric == "Cost of equity" else ("WACC USD", f"WACC {lc}")
        cd = ind_df.sort_values(a)
        fig = go.Figure()
        fig.add_bar(y=cd["Industry"], x=cd[a] * 100, name=f"{metric} USD", orientation="h")
        if not is_us:
            fig.add_bar(y=cd["Industry"], x=cd[b] * 100, name=f"{metric} {lc}", orientation="h")
        fig.update_layout(barmode="group", height=max(450, 18 * len(cd) * (1 if is_us else 1.6)),
                          xaxis_title=f"{metric} (%)", yaxis_title=None,
                          margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.02))
        st.plotly_chart(fig, width="stretch")
        st.download_button("⬇️ Download all-industry table (CSV)", ind_df.to_csv(index=False).encode(),
                           f"all_industries_{country.replace(' ', '_')}.csv", "text/csv")

    # ------------------------------------------------------------- methodology
    with tab4:
        st.markdown(f"""
**Framework.** Costs of capital are first estimated in USD with the CAPM using U.S. inputs, then
converted to {country}'s currency using the differential between the two 10-year government yields
(a proxy for the expected inflation differential, i.e. the International Fisher relation).

**Assumptions**
- Unlevered betas are Damodaran's U.S. industry averages (column *Unlevered beta*, not the cash-corrected one).
- Debt is assumed to be rated BBB (Baa2); the USD spread is Damodaran's spread for large non-financial firms.
- 10-year government yields are the risk-free rates. For countries with default risk, the local yield also
  embeds a sovereign default spread; it is not stripped out here.
- One project tax rate *t* (switch: U.S. rate or the selected country's statutory rate), one debt share D/V and one
  equity risk premium are applied to both currencies. **Currently t = {pct(tax, 2)}** ({'selected country' if use_country_tax else 'U.S.'} rate).
- Hamada assumes debt beta = 0 and a constant D/E.
""")
        st.markdown("**Formulas**")
        st.latex(r"[F1]\;\; D/E = \frac{D/V}{1-D/V} \qquad [F2]\;\; \beta_L = \beta_U\,[1+(1-t)\,D/E]")
        st.latex(r"[F3]\;\; K_e^{\$} = r_f^{US} + \beta_L\,ERP \qquad [F4]\;\; K_d^{\$} = r_f^{US} + s_{USD} \qquad [F5]\;\; K_d^{\$}(1-t)")
        st.latex(r"[F6]\;\; WACC^{\$} = (1-D/V)\,K_e^{\$} + D/V\,K_d^{\$}(1-t)")
        st.latex(r"[F7]\;\; X = \frac{1+r_f^{FC}}{1+r_f^{US}} \qquad [F8]\;\; K_e^{FC} = (1+K_e^{\$})\,X - 1")
        st.latex(r"[F9]\;\; s_{FC} = (1+K_d^{\$})\,X - 1 - r_f^{FC} = s_{USD}\,\frac{1+r_f^{FC}}{1+r_f^{US}}")
        st.latex(r"[F10]\;\; K_d^{FC} = r_f^{FC} + s_{FC} \qquad [F11]\;\; K_d^{FC}(1-t)")
        st.latex(r"[F12]\;\; WACC^{FC} = (1-D/V)\,K_e^{FC} + D/V\,K_d^{FC}(1-t)")
        st.markdown(r"""
With the automatic spread, $K_d^{FC} = (1+K_d^{\$})X-1$, so the local pre-tax cost of debt is exactly the converted USD
cost of debt. Note that $WACC^{FC}$ (built from converted components) differs from directly converting $WACC^{\$}$ by
$D/V \cdot t \cdot (1-X)$, because the tax shield applies to the whole local nominal rate. Both are shown in the
step table. When the U.S. is selected, $X=1$ and the two currencies coincide.
""")
        st.markdown("**Data sources**")
        st.dataframe(pd.DataFrame([source_status_row(x) for x in (betas_res, tax_res, spread_res, yld_res)]),
                     hide_index=True, width="stretch",
                     column_config={"URL": st.column_config.LinkColumn("URL")})

    # ------------------------------------------------------------- sources + downloads
    with st.expander("📡 Data sources, dates and fetch status", expanded=False):
        for res in (betas_res, tax_res, spread_res, yld_res):
            icon = "✅" if res["ok"] else "⚠️"
            st.markdown(f"{icon} **{res['source']}** — [{res['url']}]({res['url']})  \n"
                        f"Data as of: *{res['as_of']}* · fetched {res['fetched_at']}"
                        + (f" · {res['note']}" if res["note"] else "")
                        + (f"  \nError: `{res['error']}`" if res["error"] else ""))

    results_csv = pd.concat([
        inputs_df.assign(Section="Inputs").rename(columns={"Input": "Item"})[["Section", "Item", "Value", "Source"]],
        side.set_axis(["Item", "USD (decimal)", f"Local {ccy} (decimal)"], axis=1).assign(Section="Results"),
    ], ignore_index=True)[["Section", "Item", "Value", "Source", "USD (decimal)", f"Local {ccy} (decimal)"]]
    st.download_button("⬇️ Download current results (CSV)", results_csv.to_csv(index=False).encode(),
                       f"cost_of_capital_{industry[:30].replace(' ', '_')}_{country.replace(' ', '_')}.csv",
                       "text/csv")
    st.download_button("⬇️ Download step-by-step calculation (CSV)", steps.to_csv(index=False).encode(),
                       "calculation_steps.csv", "text/csv")


if __name__ == "__main__":
    main()
