"""
io.py — read and normalise the sales register, contract terms and stand-alone selling prices.

Canonical sales register (one row per invoice line, amounts net of sales tax):
    invoice_no, invoice_date, contract_id, customer, customer_group, item_code, item_desc, item_type, kind,
    qty, unit_price, amount, delivery_date, service_start, service_end, acceptance_date, cost_amount, ref_invoice

`kind` is the IFRS 15 category used by the engine:
    Goods, Service, Subscription, Setup, Licence, Warranty, Pass-through, Return, Rebate, Discount
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

FIELDS: Dict[str, List[str]] = {
    "invoice_no": ["invoice no", "invoice number", "invoice", "billing document", "bill no", "document no", "doc no", "inv no",
                   "voucher no", "vch no", "voucher number", "document number", "num", "invoice id"],
    "invoice_date": ["invoice date", "billing date", "document date", "posting date", "date", "inv date"],
    "contract_id": ["contract id", "contract", "contract no", "sales order", "order no", "so number", "project", "agreement"],
    "customer": ["customer", "customer name", "sold to", "sold-to party", "client", "party", "buyer", "name", "contact",
                 "party name", "account name", "client name", "customer/vendor name"],
    "customer_group": ["customer group", "segment", "channel", "customer type", "customer category"],
    "item_code": ["item code", "material", "sku", "product code", "item", "material code", "product id"],
    "item_desc": ["item description", "description", "material description", "product", "product name", "item name",
                  "particulars", "memo", "memo/description", "product/service", "line description"],
    "item_type": ["item type", "type", "category", "product type", "line type", "revenue type"],
    "qty": ["quantity", "qty", "units", "billed quantity"],
    "unit_price": ["unit price", "price", "rate", "net price"],
    "amount": ["net amount", "amount", "net value", "value", "line amount", "revenue", "sales value", "net sales", "total",
               "line total", "net total", "taxable value"],
    "delivery_date": ["delivery date", "dispatch date", "goods issue date", "shipment date", "completion date", "transfer date",
                      "pgi date"],
    "service_start": ["service start", "start date", "period start", "subscription start", "contract start"],
    "service_end": ["service end", "end date", "period end", "subscription end", "contract end"],
    "acceptance_date": ["acceptance date", "customer acceptance", "sign-off date", "accepted on"],
    "cost_amount": ["cost amount", "cost", "cost of sales", "cogs", "standard cost"],
    "ref_invoice": ["reference invoice", "reference", "original invoice", "ref invoice", "reference document"],
}
REQUIRED = ["invoice_date", "customer", "amount"]
DATE_FIELDS = ["invoice_date", "delivery_date", "service_start", "service_end", "acceptance_date"]
KINDS = ["Goods", "Service", "Subscription", "Setup", "Licence", "Warranty", "Pass-through", "Return", "Rebate", "Discount"]
POINT_KINDS = {"Goods", "Service", "Licence"}
OVER_TIME_KINDS = {"Subscription", "Warranty"}
SETTLEMENT_KINDS = {"Return", "Rebate", "Discount"}
_NULLS = {"", "nan", "none", "null", "nat", "<na>", "n/a", "#n/a", "-"}
_ALL = {s for v in FIELDS.values() for s in v}

TERM_FIELDS: Dict[str, List[str]] = {
    "customer": ["customer", "customer name", "client", "sold to"],
    "contract_id": ["contract id", "contract", "contract no"],
    "rebate_type": ["rebate type", "rebate"],
    "rebate_pct": ["rebate %", "rebate pct", "rebate rate", "rebate percent"],
    "rebate_threshold": ["rebate threshold", "threshold", "rebate tier", "tier threshold"],
    "return_rights": ["return rights", "right of return", "returns allowed"],
    "acceptance_required": ["acceptance required", "customer acceptance required", "acceptance clause"],
    "acceptance_formality": ["acceptance formality", "acceptance is a formality", "formality only", "acceptance formality only"],
    "bill_and_hold": ["bill and hold", "bill & hold", "bill-and-hold"],
    "bh_reason": ["bh substantive reason", "substantive reason"],
    "bh_identified": ["bh separately identified", "separately identified"],
    "bh_ready": ["bh ready for transfer", "ready for transfer", "ready for physical transfer"],
    "bh_no_redirect": ["bh cannot redirect", "cannot redirect", "cannot use or direct"],
    "payment_days": ["payment terms days", "payment terms", "credit days", "payment days"],
    "contract_value": ["contract value", "total contract value", "tcv", "order value", "contract amount"],
    "contract_months": ["contract term months", "contract term", "term months", "contract months", "duration months",
                        "contract duration"],
}


def _clean(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/%& ]", " ", str(s).strip().lower())).strip()


_AVOID = {"invoice_no": ["date"], "contract_id": ["date", "start", "end", "value", "amount", "term", "months"], "customer": ["group", "type", "segment"],
          "item_code": ["description", "type", "name"], "item_desc": ["type", "code"], "amount": ["cost", "tax"],
          "invoice_date": ["no", "number"], "unit_price": ["stand"], "cost_amount": ["date"],
          "rebate_type": ["%", "pct", "rate", "percent", "threshold", "tier"], "rebate_pct": ["type", "threshold"],
          "acceptance_required": ["formality", "date"], "payment_days": ["rebate"]}


def guess_mapping(columns: List[str], fields: Dict[str, List[str]] = FIELDS) -> Dict[str, Optional[str]]:
    """Exact synonym matches first (for every field), then the longest synonym contained in a heading."""
    cleaned = {c: _clean(c) for c in columns}
    out: Dict[str, Optional[str]] = {f: None for f in fields}
    used = set()
    for f, syns in fields.items():
        hit = next((c for c, cc in cleaned.items() if c not in used and cc in syns), None)
        if hit is not None:
            out[f] = hit
            used.add(hit)
    for f, syns in fields.items():
        if out[f] is not None:
            continue
        best, score = None, 0
        for c, cc in cleaned.items():
            if c in used or any(w in cc.split() for w in _AVOID.get(f, [])):
                continue
            sc = max([len(s) for s in syns if len(s) > 3 and s in cc] or [0])
            if sc > score:
                best, score = c, sc
        out[f] = best
        if best is not None:
            used.add(best)
    return out


def read_table(name: str, data: bytes, sheet=0) -> Tuple[pd.DataFrame, List[str]]:
    """Read CSV/TXT/XLSX/XLS; detects delimiter, encoding and report title rows above the header."""
    notes: List[str] = []
    if name.lower().endswith((".csv", ".txt")):
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            encs = ("utf-16",)
        elif data[:400].count(b"\x00") > len(data[:400]) // 4:           # UTF-16 without a byte-order mark
            encs = ("utf-16-le",) if data[1:2] == b"\x00" else ("utf-16-be",)
        else:
            encs = ("utf-8-sig", "cp1252", "latin-1")
        text = None
        for enc in encs:
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        lines = text.splitlines()
        try:
            delim = csv.Sniffer().sniff("\n".join(lines[:50]), delimiters=",;\t|").delimiter
        except csv.Error:
            delim = max([",", ";", "\t", "|"], key=lambda d: sum(l.count(d) for l in lines[:50]))
        probe = pd.DataFrame(list(csv.reader(lines[:40], delimiter=delim)))
        k = _header_row(probe)
        df = pd.read_csv(io.StringIO(text), sep=delim, skiprows=k, header=0, engine="python", dtype=str)
    else:
        probe = pd.read_excel(io.BytesIO(data), header=None, nrows=40, sheet_name=sheet)
        k = _header_row(probe)
        df = pd.read_excel(io.BytesIO(data), header=k, sheet_name=sheet)
    if k:
        notes.append(f"{k} title row(s) above the column headings were skipped.")
    return df.dropna(how="all").dropna(axis=1, how="all"), notes


def _header_row(df: pd.DataFrame) -> int:
    for i in range(min(30, len(df))):
        cells = [_clean(x) for x in df.iloc[i].tolist() if isinstance(x, str) and x.strip()]
        if sum(c in _ALL or c in {s for v in TERM_FIELDS.values() for s in v} for c in cells) >= 2:
            return i
    return 0


_CCY = (r"(?:PKR|INR|RS\.?|USD|EUR|GBP|AED|SAR|QAR|OMR|KWD|BHD|CAD|AUD|NZD|SGD|HKD|JPY|CNY|ZAR|NGN|KES|BDT|LKR|MYR|"
        r"IDR|PHP|THB|TRY|CHF|SEK|NOK|DKK|\$|€|£|₹|¥|₨)")


def _decimal_sep(core: pd.Series, three_decimal: bool = False) -> str:
    """',' when the column is written with decimal commas, else '.'. Only unambiguous values vote: 1.234,56 · 12,5 ·
    1.234.567 for commas; 1,234.56 · 12.5 · 1,234,567 for points. Values such as 1.250 or 1,250 could be either (a
    three-decimal currency or a thousands separator) and do not vote; with no votes the point is the decimal."""
    comma = (core.str.match(r"^\d{1,3}(\.\d{3})+,\d+$") | core.str.match(r"^\d+,\d{1,2}$")
             | core.str.match(r"^\d{1,3}(\.\d{3}){2,}$") | core.str.match(r"^\d+,\d{4,}$"))
    dot = (core.str.match(r"^\d{1,3}(,\d{3})+\.\d+$") | core.str.match(r"^\d+\.\d{1,2}$")
           | core.str.match(r"^\d{1,3}(,\d{3}){2,}$") | core.str.match(r"^\d+\.\d{4,}$") | core.str.match(r"^\d{4,}\.\d+$"))
    if int(comma.sum()) or int(dot.sum()):
        return "," if int(comma.sum()) > int(dot.sum()) else "."
    # nothing unambiguous: 1.250-style values are thousands (European whole numbers) unless the amounts are in a
    # three-decimal currency (KWD, BHD, OMR, JOD, TND, LYD, IQD)
    amb_dot = core.str.match(r"^\d{1,3}\.\d{3}$")
    return "." if three_decimal or not amb_dot.any() else ","


_THREE_DEC = r"\b(?:KWD|BHD|OMR|JOD|TND|LYD|IQD)\b"


def parse_amounts(series: pd.Series, decimal: Optional[str] = None) -> Tuple[pd.Series, int]:
    """Text amounts → floats. Handles thousands separators (1,234 / 1.234 / 1'234 / 1,00,000), decimal commas (decided
    for the whole column), (brackets), leading or trailing minus, currency codes and symbols anywhere around the number
    ($-1,000, PKR(200), 500 USD), and CR/DR markers with or without a space: a marker used on a minority of lines (or the
    less common of the two) means a negative amount."""
    if pd.api.types.is_bool_dtype(series):
        return series.astype(float), 0
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype(float), 0
    s = series.astype("string").str.strip()
    blank = (s.isna() | s.str.lower().isin(_NULLS)).fillna(True).astype(bool)
    t = s.fillna("").str.upper().str.replace("\u00a0", " ", regex=False).str.strip()
    t = t.str.replace(_CCY, "", regex=True).str.strip()
    cr = (t.str.contains(r"[\d)]\s*CR\.?$", regex=True) | t.str.contains(r"^CR\.?\s*[\d(-]", regex=True)).fillna(False).astype(bool)
    dr = (t.str.contains(r"[\d)]\s*DR\.?$", regex=True) | t.str.contains(r"^DR\.?\s*[\d(-]", regex=True)).fillna(False).astype(bool)
    t = t.str.replace(r"\s*(?:CR|DR)\.?$", "", regex=True).str.replace(r"^(?:CR|DR)\.?\s*", "", regex=True)
    t2 = t.str.replace(r"\s", "", regex=True)
    neg = (t2.str.startswith("-") | t2.str.endswith("-") | (t2.str.contains("(", regex=False) & t2.str.contains(")", regex=False)))
    core = t2.str.replace(r"[()'’+-]", "", regex=True)
    if decimal is None:
        decimal = _decimal_sep(core[~blank], bool(s.fillna("").str.upper().str.contains(_THREE_DEC, regex=True).any()))
    if decimal == ",":
        core = core.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        core = core.str.replace(",", "", regex=False)
    v = pd.to_numeric(core, errors="coerce")
    bad = int((v.isna() & ~blank).sum())
    neg = neg.fillna(False).astype(bool)
    n = int((~blank).sum())
    ncr, ndr = int(cr.sum()), int(dr.sum())
    if ncr and ndr:
        neg = neg ^ (cr if ncr < ndr else dr)
    elif ncr and ncr < 0.5 * n:
        neg = neg ^ cr
    elif ndr and ndr < 0.5 * n:
        neg = neg ^ dr
    v[neg] *= -1
    v[blank] = np.nan
    return v.astype(float), bad


def _dayfirst_info(values) -> Tuple[bool, bool]:
    """(day-first?, decided from the data?) for dd/mm vs mm/dd text dates."""
    a, b, ambiguous = set(), set(), False
    for x in values:
        m = re.match(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", str(x))
        if m:
            p, q = int(m.group(1)), int(m.group(2))
            a.add(p); b.add(q)
            ambiguous |= p != q
    if any(v > 12 for v in a):
        return True, True
    if any(v > 12 for v in b):
        return False, True
    return True, not ambiguous


def _dayfirst(values) -> bool:
    return _dayfirst_info(values)[0]


def date_order(*columns: pd.Series, info: bool = False):
    """Day-first (True) or month-first, decided once for a whole file from all its date columns. With info=True, also
    whether the data decided it (False when every date could be read either way)."""
    vals = []
    for c in columns:
        if c is None or pd.api.types.is_datetime64_any_dtype(c):
            continue
        vals.extend(pd.Series(c).dropna().astype(str).unique()[:500].tolist())
    r = _dayfirst_info(vals)
    return r if info else r[0]


MIN_YEAR, MAX_YEAR = 1900, 2261     # pandas nanosecond range; later dates (9999-12-31 'open-ended') are treated as blank


def _fit(x) -> pd.Series:
    """Datetimes in any unit → datetime64[ns]; years outside the supported range become NaT."""
    x = pd.to_datetime(x, errors="coerce")
    if getattr(x.dt, "tz", None) is not None:
        x = x.dt.tz_localize(None)
    x = x.where(x.dt.year.between(MIN_YEAR, MAX_YEAR))
    return x.astype("datetime64[ns]")


def _year_of(v) -> Optional[int]:
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", str(v))
    return int(m.group(1)) if m else None


def parse_dates(series: pd.Series, dayfirst: Optional[bool] = None) -> Tuple[pd.Series, int]:
    """Dates from Excel dates, Excel serial numbers, SAP-style YYYYMMDD, ISO, or dd/mm/yyyy and mm/dd/yyyy (the order is
    passed in for the whole file, or detected from this column). Dates after 2261 (e.g. SAP's 31.12.9999 'valid to') are
    treated as open-ended and left blank; their count is in out.attrs['open_ended']."""
    s = pd.Series(series)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if pd.api.types.is_datetime64_any_dtype(s):
        far = int((s.dt.year > MAX_YEAR).sum())
        out = _fit(s).dt.normalize()
        out.attrs["open_ended"] = far
        return out, 0
    s = s.astype(object)
    isdt = s.map(lambda x: isinstance(x, (pd.Timestamp, np.datetime64)) or (hasattr(x, "year") and hasattr(x, "month")))
    far = 0
    if isdt.any():
        yrs = s[isdt].map(lambda x: pd.Timestamp(x).year if isinstance(x, np.datetime64) else x.year)
        ok = yrs.between(MIN_YEAR, MAX_YEAR)
        far += int((yrs > MAX_YEAR).sum())
        good = s[isdt][ok]
        if len(good):
            out[good.index] = _fit(pd.Series([pd.Timestamp(x) for x in good], index=good.index))
    txt_all = s.map(lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) else str(x).strip())
    txt_all = txt_all.str.replace(r"\.0$", "", regex=True)
    blank = txt_all.str.lower().isin(_NULLS) & ~isdt
    yr = txt_all.map(_year_of)
    too_far = ~isdt & ~blank & (yr.map(lambda y: y is not None and y > MAX_YEAR)
                                | txt_all.str.match(r"^(2[3-9]|[3-9]\d)\d{2}(0[1-9]|1[0-2])\d{2}$"))
    far += int(too_far.sum())
    num = pd.to_numeric(txt_all.where(~isdt & ~too_far), errors="coerce")
    ymd = txt_all.str.match(r"^(19|20|21|22)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$") & ~isdt & ~too_far
    if ymd.any():
        out[ymd] = _fit(pd.to_datetime(txt_all[ymd], format="%Y%m%d", errors="coerce"))
    serial = num.between(20000, 80000) & ~ymd & ~isdt
    if serial.any():
        out[serial] = _fit(pd.Timestamp("1899-12-30") + pd.to_timedelta(num[serial], unit="D"))
    rest = ~serial & ~ymd & ~isdt & ~blank & ~too_far
    if rest.any():
        txt = txt_all[rest]
        iso = txt.str.match(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
        if iso.any():                                   # year first: never day-first
            parts = txt[iso].str.extract(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})").astype(int)
            out[iso[iso].index] = _fit(pd.to_datetime(parts.set_axis(["year", "month", "day"], axis=1), errors="coerce"))
        oth = txt[~iso]
        if len(oth):
            df = _dayfirst(oth.unique()[:500]) if dayfirst is None else dayfirst
            out[oth.index] = _fit(pd.to_datetime(oth, errors="coerce", dayfirst=df, format="mixed"))
    bad = int((out.isna() & rest).sum())
    out = out.dt.normalize()
    out.attrs["open_ended"] = far
    return out, bad


_KIND_RULES = [
    ("Return", r"\breturns?\b|credit note|\bcn\b"),
    ("Rebate", r"rebate|volume incentive|loyalty credit"),
    ("Discount", r"\bdiscount|price adjustment|trade allowance"),
    ("Pass-through", r"pass[- ]?through|recharge|reimburs|freight|courier|carriage|shipping charge|media spend|ad spend|"
                     r"disbursement"),
    ("Setup", r"\bset[- ]?up\b|onboarding|implementation fee|activation|joining fee|installation fee|connection fee"),
    ("Warranty", r"extended warranty|service warranty|warranty plan|service plan"),
    ("Subscription", r"subscription|maintenance|\bamc\b|support contract|annual support|retainer|\bsaas\b|hosting|"
                     r"membership"),
    ("Licence", r"licen[cs]e|software key"),
    ("Service", r"\bservices?\b|installation|consult|training|repair|commissioning|design"),
    ("Goods", r"goods|product|board|panel|material|\bkit\b|stock|hardware|equipment|merchandise"),
]


def infer_kind(item_type, item_desc) -> str:
    """IFRS 15 category from the item type, or the description when the type is blank or unrecognised."""
    for txt in (item_type, item_desc):
        t = str(txt or "").strip().lower()
        if not t or t in _NULLS:
            continue
        for k in KINDS:
            if t == k.lower():
                return k
        for k, pat in _KIND_RULES:
            if re.search(pat, t):
                return k
    return "Goods"


def _clean_key(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    return None if s.lower() in _NULLS else re.sub(r"\.0$", "", s)


@dataclass
class Loaded:
    data: pd.DataFrame
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.data.empty


def normalise_register(raw: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> Loaded:
    w: List[str] = []
    if raw is None or raw.empty:
        return Loaded(pd.DataFrame(), errors=["The file has column headings but no data rows."])
    missing = [f for f in REQUIRED if not mapping.get(f)]
    if missing:
        lab = {"invoice_date": "Invoice date", "customer": "Customer", "amount": "Net amount"}
        return Loaded(pd.DataFrame(), errors=["Choose the column for: " + ", ".join(lab.get(f, f) for f in missing)
                                              + " (in the Mapping tab)."])
    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    out = pd.DataFrame(index=df.index)
    for f in FIELDS:
        col = mapping.get(f)
        out[f] = df[col] if col and col in df.columns else None
    # the key the category screen uses (item type, or the description when no type column is mapped)
    src = out["item_type"] if mapping.get("item_type") else out["item_desc"]
    out["type_key"] = src.astype(object).where(src.notna(), "").map(lambda v: str(v).strip())
    for f in ["invoice_no", "contract_id", "customer", "customer_group", "item_code", "ref_invoice"]:
        out[f] = pd.Series([_clean_key(v) for v in out[f]], index=out.index, dtype=object)
    out["customer"] = out["customer"].map(lambda v: re.sub(r"\s+", " ", v) if isinstance(v, str) else v)
    n_blank = int((out["customer"].isna() & df[mapping["amount"]].notna() & df[mapping["invoice_date"]].notna()).sum())
    if n_blank:
        w.append(f"{n_blank} line(s) with an amount but no customer were ignored.")
    out = out[out["customer"].notna()].copy()
    # one spelling per customer: names that differ only in capitals or spacing are the same customer
    ck = out["customer"].str.casefold()
    canon = out.groupby(ck)["customer"].agg(lambda x: x.value_counts().index[0])
    variants = out.groupby(ck)["customer"].nunique()
    if (variants > 1).any():
        ex = [canon[k] for k in variants[variants > 1].index[:3]]
        w.append(f"{int((variants > 1).sum())} customer name(s) appear with different capitals or spacing and were merged "
                 f"(e.g. {', '.join(ex)}).")
    out["customer"] = ck.map(canon)
    tot = pd.Series(False, index=out.index)
    for f in ["customer", "invoice_no"]:
        tot |= out[f].fillna("").str.lower().str.contains(_TOTAL_RX, regex=True)
    if mapping.get("invoice_no"):            # a real customer called "Total ..." still has an invoice number
        tot &= out["invoice_no"].isna() | out["invoice_no"].str.lower().str.contains(_TOTAL_RX, regex=True)
    if tot.any():
        w.append(f"{int(tot.sum())} total / subtotal row(s) were ignored.")
        out = out[~tot]
    order, decided = date_order(*[out[f] for f in DATE_FIELDS], info=True)
    has_text_dates = any(out[f].astype(str).str.match(r"^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}").any() for f in DATE_FIELDS
                         if not pd.api.types.is_datetime64_any_dtype(out[f]))
    if has_text_dates and not decided:
        w.append("Every text date in the file can be read either way (day ≤ 12), so day/month order was assumed "
                 "(dd/mm/yyyy). If the file is mm/dd/yyyy, convert the dates to Excel dates or ISO (yyyy-mm-dd).")
    for f in DATE_FIELDS:
        parsed, bad = parse_dates(out[f], dayfirst=order)
        far = parsed.attrs.get("open_ended", 0)
        out[f] = parsed
        if bad:
            w.append(f"{bad} {f.replace('_', ' ')} value(s) could not be read as dates and were left blank.")
        if far:
            w.append(f"{far} {f.replace('_', ' ')} value(s) after the year 2261 (e.g. 31.12.9999) were treated as open-ended "
                     "and left blank.")
    nod = out["invoice_date"].isna()
    if nod.any():
        w.append(f"{int(nod.sum())} line(s) with no readable invoice date were ignored.")
        out = out[~nod]
    dec = None
    if not pd.api.types.is_numeric_dtype(out["amount"]):
        a = out["amount"].astype("string").str.upper().str.replace(_CCY, "", regex=True) \
            .str.replace(r"(?:CR|DR)\.?$", "", regex=True).str.replace(r"[()\s'’+-]", "", regex=True)
        dec = _decimal_sep(a.dropna(), bool(out["amount"].astype("string").fillna("").str.upper()
                                            .str.contains(_THREE_DEC, regex=True).any()))
    for f in ["amount", "qty", "unit_price", "cost_amount"]:
        out[f], bad = parse_amounts(out[f], decimal=dec)
        if bad and f != "amount":
            w.append(f"{bad} {f.replace('_', ' ')} value(s) could not be read as numbers and were left blank.")
        if bad and f == "amount":
            return Loaded(pd.DataFrame(), w, [f"{bad} amount(s) could not be read as numbers. Check the Net Amount column."])
    need_amt = out["amount"].isna() & out["qty"].notna() & out["unit_price"].notna()
    out.loc[need_amt, "amount"] = out.loc[need_amt, "qty"] * out.loc[need_amt, "unit_price"]
    out["amount"] = out["amount"].fillna(0.0)
    desc = out["item_desc"].map(_clean_key)
    desc = desc.where(desc.notna(), out["item_code"]).where(lambda x: x.notna(), out["item_type"].map(_clean_key))
    out["item_desc"] = desc.fillna("(no description)").astype(str)
    out["kind"] = [infer_kind(t, d) for t, d in zip(out["item_type"], out["item_desc"])]
    neg_goods = (out["amount"] < 0) & (out["kind"] == "Goods")
    if neg_goods.any():
        out.loc[neg_goods, "kind"] = "Return"
        w.append(f"{int(neg_goods.sum())} negative goods line(s) were treated as returns (credit notes).")
    neg_other = (out["amount"] < 0) & ~out["kind"].isin(SETTLEMENT_KINDS | {"Goods", "Return"})
    if neg_other.any():
        w.append(f"{int(neg_other.sum())} negative service, subscription or other line(s) keep their category and reduce "
                 "revenue for that obligation (a credit with a reference invoice cancels the rest of its service period).")
    out["invoice_no"] = out["invoice_no"].fillna(pd.Series([f"LINE-{i}" for i in out.index], index=out.index))
    # a credit note with no contract belongs to the contract of the invoice it references
    base = out["contract_id"].fillna(out["invoice_no"])
    inv2con = dict(zip(out.loc[out["amount"] >= 0, "invoice_no"][::-1], base[out["amount"] >= 0][::-1]))
    from_ref = out["contract_id"].isna() & out["ref_invoice"].isin(inv2con)
    out["contract_id"] = base.where(~from_ref, out["ref_invoice"].map(inv2con))
    out["customer_group"] = out["customer_group"].fillna("All customers")
    out["item_code"] = out["item_code"].fillna(out["item_desc"].astype(str).str[:30])
    out["qty"] = out["qty"].fillna(1.0)
    out["line_id"] = np.arange(len(out))
    no_inv = out["invoice_no"].str.startswith("LINE-")
    key = ["invoice_date", "customer", "item_code", "qty", "amount"] if no_inv.all() else \
        ["invoice_no", "invoice_date", "item_code", "qty", "amount"]
    dup = out.duplicated(subset=key, keep=False) & (no_inv.all() | ~no_inv)
    if dup.any():
        w.append(f"{int(dup.sum())} line(s) look like duplicates (same {'customer' if no_inv.all() else 'invoice'}, date, "
                 "item, quantity and amount). They are kept; check the export did not repeat rows.")
    if (out["kind"].isin(OVER_TIME_KINDS) & (out["service_start"].isna() | out["service_end"].isna())).any():
        n = int((out["kind"].isin(OVER_TIME_KINDS) & (out["service_start"].isna() | out["service_end"].isna())).sum())
        w.append(f"{n} subscription / warranty line(s) have no service period; a 12-month period from the invoice date is assumed.")
    pts = out["kind"].isin(["Goods"]) & out["delivery_date"].isna()
    if pts.any():
        w.append(f"{int(pts.sum())} goods line(s) have no delivery date; unless the contract terms say otherwise (bill-and-hold, acceptance), the invoice date is taken as the date control passes (change this under More judgements).")
    if out.empty:
        return Loaded(out, w, ["No usable lines were found."])
    return Loaded(out.reset_index(drop=True), w)


_TOTAL_RX = r"^(?:sub ?|grand |overall )?(?:total|result)s?\b|\b(?:sub ?total|grand total|overall result)\b|\s(?:total|result)$"


def _yes(v) -> bool:
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return bool(np.isfinite(v) and v == 1)
    return str(v).strip().lower() in {"y", "yes", "true", "1", "1.0", "x", "✓", "✔", "required", "applies"}


_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "mio": 1e6, "million": 1e6, "bn": 1e9, "b": 1e9,
         "billion": 1e9, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7, "crores": 1e7}


def parse_term_number(series: pd.Series, scale: bool = True) -> Tuple[pd.Series, pd.Series]:
    """Numbers written as text in contract terms: '3%', '2.5 %', 'PKR 200m', '1.5 crore', 'Net 30', '45 days'.
    Returns (value, had_percent_sign)."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype(float), pd.Series(False, index=series.index)
    vals, pct = [], []
    for x in series:
        if x is None or (isinstance(x, float) and np.isnan(x)) or str(x).strip().lower() in _NULLS:
            vals.append(np.nan); pct.append(False); continue
        if isinstance(x, (int, float, np.integer, np.floating)):
            vals.append(float(x)); pct.append(False); continue
        t = str(x).strip().lower().replace("\u00a0", " ")
        m = re.search(r"([-+]?\d[\d,']*(?:\.\d+)?)\s*(%|[a-z]+)?", t)
        if not m:
            vals.append(np.nan); pct.append(False); continue
        v = float(re.sub(r"[,']", "", m.group(1)))
        suf = m.group(2) or ""
        if suf == "%":
            pct.append(True)
        else:
            pct.append(False)
            if scale:
                v *= _MULT.get(suf, 1.0)
        vals.append(v)
    return pd.Series(vals, index=series.index, dtype=float), pd.Series(pct, index=series.index)


def _rebate_pct(col: pd.Series) -> pd.Series:
    """Rebate rates → fractions. Text is a percentage ('3', '3%', '0.5' → 0.5%). Numbers typed in cells share one scale
    for the column: percentages if any is 1 or more (3 → 3%), otherwise fractions (0.03 → 3%)."""
    is_num = col.map(lambda v: isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool))
    nums = pd.to_numeric(col.where(is_num), errors="coerce")
    num_pct = bool(nums.notna().any() and nums.max() >= 1)
    txt, _ = parse_term_number(col.where(~is_num).astype(object), scale=False)
    return pd.Series(np.where(is_num, nums / (100 if num_pct else 1), txt / 100), index=col.index, dtype=float)


def _payment_days(col: pd.Series) -> pd.Series:
    """Payment terms → days: 'Net 30', '45 days', '2 months' (60), 'Advance' / 'prepaid' / 'due on receipt' / 'COD' (0)."""
    out = []
    for v in col:
        if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip().lower() in _NULLS:
            out.append(np.nan); continue
        if isinstance(v, (int, float, np.integer, np.floating)):
            out.append(float(v)); continue
        t = str(v).strip().lower()
        if re.search(r"advance|prepa|upfront|up-front|in advance|\bcia\b|\bcod\b|on receipt|immediate|cash", t):
            out.append(0.0); continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*(months?|mths?|mos?|weeks?|wks?|days?|d)?\b", t)
        if not m:
            out.append(np.nan); continue
        n, u = float(m.group(1)), (m.group(2) or "d")
        out.append(n * 30 if u.startswith("m") else n * 7 if u.startswith("w") else n)
    return pd.Series(out, index=col.index, dtype=float)


def _term_months(col: pd.Series) -> pd.Series:
    """Contract term → months: 36, '36 months', '3 years', '3 yrs', '18m'."""
    out = []
    for v in col:
        if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip().lower() in _NULLS:
            out.append(np.nan); continue
        if isinstance(v, (int, float, np.integer, np.floating)):
            out.append(float(v)); continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*(years?|yrs?|y\b|months?|mths?|mos?|m\b)?", str(v).lower())
        if not m:
            out.append(np.nan); continue
        n, u = float(m.group(1)), (m.group(2) or "m")
        out.append(n * 12 if u.startswith("y") else n)
    return pd.Series(out, index=col.index, dtype=float)


def align_terms(reg: pd.DataFrame, terms: pd.DataFrame) -> pd.DataFrame:
    """Rename terms customers to the register's spelling when they differ only in capitals or spacing."""
    if terms is None or terms.empty or reg is None or reg.empty:
        return terms
    canon = {str(c).casefold(): c for c in reg["customer"].dropna().unique()}
    t = terms.copy()
    t["customer"] = [canon.get(re.sub(r"\s+", " ", c).strip().casefold(), c) if isinstance(c, str) else c for c in t["customer"]]
    return t


def terms_warnings(reg: pd.DataFrame, terms: pd.DataFrame) -> List[str]:
    """Terms rows that match nothing in the register (usually a spelling difference in the customer name)."""
    w: List[str] = []
    if terms is None or terms.empty or reg is None or reg.empty:
        return w
    terms = align_terms(reg, terms)
    cu = set(reg["customer"].dropna())
    co = set(reg["contract_id"].dropna())
    ok = lambda v: isinstance(v, str) and v != ""  # noqa: E731
    miss_cu = sorted({c for c, k in zip(terms["customer"], terms["contract_id"]) if ok(c) and not ok(k) and c not in cu})
    miss_co = sorted({k for k in terms["contract_id"] if ok(k) and k not in co})
    if miss_cu:
        w.append(f"Contract terms for {len(miss_cu)} customer(s) match no customer in the sales register: "
                 + ", ".join(miss_cu[:5]) + (" …" if len(miss_cu) > 5 else "") + ". Check the spelling.")
    if miss_co:
        w.append(f"Contract terms for {len(miss_co)} contract id(s) match no contract in the sales register: "
                 + ", ".join(miss_co[:5]) + (" …" if len(miss_co) > 5 else "") + ".")
    return w


TERM_LABELS = {"customer": "Customer", "contract_id": "Contract ID", "rebate_type": "Rebate type", "rebate_pct": "Rebate %",
               "rebate_threshold": "Rebate threshold", "return_rights": "Return rights",
               "acceptance_required": "Acceptance required", "acceptance_formality": "Acceptance is a formality",
               "bill_and_hold": "Bill-and-hold", "bh_reason": "B-a-H: substantive reason",
               "bh_identified": "B-a-H: separately identified", "bh_ready": "B-a-H: ready for transfer",
               "bh_no_redirect": "B-a-H: cannot redirect", "payment_days": "Payment terms",
               "contract_value": "Contract value", "contract_months": "Contract term"}


def terms_columns_report(raw: Optional[pd.DataFrame]) -> Tuple[pd.DataFrame, List[str]]:
    """Which headings of a terms file were understood (and as what), and which were ignored."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Heading in your file", "Read as"]), []
    m = guess_mapping(list(raw.columns), TERM_FIELDS)
    used = {v: k for k, v in m.items() if v}
    rec = pd.DataFrame([(str(c), TERM_LABELS[used[c]]) for c in raw.columns if c in used],
                       columns=["Heading in your file", "Read as"])
    return rec, [str(c) for c in raw.columns if c not in used]


def normalise_terms(raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = list(TERM_FIELDS)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)
    m = guess_mapping(list(raw.columns), TERM_FIELDS)
    t = pd.DataFrame({f: (raw[m[f]] if m.get(f) else None) for f in cols})
    for f in ["customer", "contract_id"]:
        t[f] = pd.Series([_clean_key(v) for v in t[f]], index=t.index, dtype=object)
    t = t[t["customer"].notna() | t["contract_id"].notna()].copy()
    t["rebate_type"] = t["rebate_type"].map(lambda v: str(v).strip().title() if _clean_key(v) else "None")
    t.loc[~t["rebate_type"].isin(["Tiered", "Flat"]), "rebate_type"] = "None"
    t["rebate_pct"] = _rebate_pct(t["rebate_pct"])
    t["rebate_threshold"] = parse_term_number(t["rebate_threshold"], scale=True)[0]
    t["payment_days"] = _payment_days(t["payment_days"])
    t["contract_value"] = parse_term_number(t["contract_value"], scale=True)[0]
    t["contract_months"] = _term_months(t["contract_months"])
    for f in ["return_rights", "acceptance_required", "acceptance_formality", "bill_and_hold", "bh_reason",
              "bh_identified", "bh_ready", "bh_no_redirect"]:
        t[f] = t[f].map(_yes)
    return t.reset_index(drop=True)


def normalise_ssp(raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["item_code", "ssp"])
    cols = list(raw.columns)
    code = guess_mapping(cols, {"item_code": FIELDS["item_code"]})["item_code"]
    price = next((c for c in cols if re.search(r"stand|ssp|selling price|price", _clean(c)) and c != code), None)
    if not code or not price:
        return pd.DataFrame(columns=["item_code", "ssp"])
    out = pd.DataFrame({"item_code": raw[code].map(_clean_key), "ssp": parse_amounts(raw[price])[0]})
    return out.dropna().drop_duplicates("item_code", keep="last").reset_index(drop=True)
