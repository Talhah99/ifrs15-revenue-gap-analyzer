"""
IFRS 15 Revenue Gap Analyzer
============================
Upload an invoice-level sales register (and, optionally, contract terms and stand-alone prices). The app applies
the IFRS 15 five-step model to every line and shows, month by month, how revenue under IFRS 15 differs from
revenue as booked on invoices — and why: cut-off, bill-and-hold, customer acceptance, subscriptions, set-up fees,
allocation of bundle discounts, principal-versus-agent, expected returns and volume rebates.

It then produces the month-end adjusting journals, the contract-balance roll-forward, remaining performance
obligations, disaggregated revenue and a draft accounting-policy note.

Run:  streamlit run app.py
"""

from __future__ import annotations

import html
import io
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import streamlit as st

from engine import analysis as A
from engine import charts
from engine import io as rio
from engine.exports import excel_pack, schedule_csv
from engine.io import SETTLEMENT_KINDS
from engine import model as M
from engine.model import Settings, fiscal_year
from styles import CSS

APP_DIR = os.path.dirname(os.path.abspath(__file__))
st.set_page_config(page_title="IFRS 15 Revenue Gap Analyzer", page_icon="🧾", layout="wide", initial_sidebar_state="auto")
st.markdown(CSS, unsafe_allow_html=True)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
NOT_IN_FILE = "— not in file —"
esc = html.escape

DEFAULTS = {"raw_reg": None, "reg_name": None, "reg_id": None, "raw_terms": None, "terms_name": None, "terms_id": None,
            "raw_ssp": None, "data": None, "terms": None, "ssp": None, "load_warnings": [], "workflow_step": 1,
            "review_period": None, "fy_start": 7, "unit": "PKR", "company": "", "confidence": 0.90,
            "significance": 0.02, "agent": True, "setup_distinct": False, "setup_months": 0, "services_over_time": False,
            "discount_specific": False, "payment_days": 30, "rpo_expedient": True, "undelivered": False, "materiality": 0.0,
            "data_source": None, "kind_overrides": {}, "sens": None, "gl_codes": {}, "prepared_by": "", "reviewed_by": "",
            "review_notes": "", "posted_prev": None, "mapping_cur": {}, "confirm_demo": False}
TAB_NAMES = ["📂 Data", "🔗 Mapping", "📊 Dashboard", "📄 Contracts", "🎯 Returns & rebates", "🛡️ Controls", "📚 Disclosures",
             "🧮 Journals", "⚖️ Judgements", "📤 Export"]
SETTING_KEYS = ["fy_start", "unit", "company", "confidence", "significance", "agent", "setup_distinct", "setup_months",
                "services_over_time", "discount_specific", "payment_days", "rpo_expedient", "undelivered", "gl_codes",
                "kind_overrides"]
RESULT_KEYS = ["sens", "xlsx", "csv", "bundle", "posted_prev"]
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v
for k, v in st.session_state.pop("_pending", {}).items():
    st.session_state[k] = v


def queue(**kw):
    p = st.session_state.get("_pending", {})
    p.update(kw)
    st.session_state["_pending"] = p


def go(tab: str):
    """Button callback: open another tab."""
    st.session_state["main_tabs"] = tab


def show_alert(msg: str, kind: str = "info"):
    st.markdown(f'<div class="alert-{kind}">{msg}</div>', unsafe_allow_html=True)


def read_error(e: Exception, what: str = "the file") -> str:
    """Plain-language message for a file that could not be read; the technical detail goes last, in small print."""
    t = f"{type(e).__name__}: {e}".lower()
    if "no columns to parse" in t or "emptydataerror" in t:
        msg = f"{what.capitalize()} is empty."
    elif any(k in t for k in ("format cannot be determined", "zip file", "badzipfile", "xlrderror", "unsupported format",
                              "not a valid", "invalid file")):
        msg = (f"{what.capitalize()} does not look like a valid Excel file. Open it in Excel and save it as .xlsx "
               "(or as CSV), then upload it again.")
    elif any(k in t for k in ("unicode", "codec", "tokeniz", "new-line character", "parsererror", "expected")):
        msg = (f"{what.capitalize()} could not be read as CSV text. Save it from Excel as .xlsx or \"CSV UTF-8\", "
               "then upload it again.")
    else:
        msg = f"{what.capitalize()} could not be read. Save it as .xlsx or CSV and try again."
    return f'{esc(msg)}<br><span style="font-size:.85em;opacity:.7">Detail: {esc(str(e))[:160]}</span>'


def kpi_html(items, cls: str = "") -> str:
    cells = "".join(f'<div class="kpi {c}"><div class="kl">{esc(str(l))}</div><div class="kv" title="{esc(str(v))}">{esc(str(v))}</div>'
                    f'<div class="ks" title="{esc(str(s))}">{esc(str(s))}</div></div>' for l, v, s, c in items)
    return f'<div class="kpi-grid {cls}">{cells}</div>'


def money(v, unit: Optional[str] = None) -> str:
    unit = st.session_state.unit if unit is None else unit
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    a = abs(float(v))
    for div, suf in [(1e9, "bn"), (1e6, "m"), (1e3, "k")]:
        if round(a / div, 1) >= 1:
            s = f"{a / div:,.1f}" + suf
            break
    else:
        s = f"{a:,.0f}"
    return f"{'-' if v < 0 and s not in ('0', '0.0') else ''}{unit + ' ' if unit else ''}{s}"


def acct(v) -> str:
    if v is None or pd.isna(v):
        return "–"
    v = round(float(v))
    return f"({abs(v):,.0f})" if v < 0 else f"{v:,.0f}"


def mlabel(p) -> str:
    return pd.Period(p, "M").strftime("%b %Y")


def sample_bytes(fname: str) -> Optional[bytes]:
    path = os.path.join(APP_DIR, fname)
    if not os.path.isfile(path):
        try:
            import sample_data
            sample_data.write(APP_DIR)
        except Exception:
            return None
    with open(path, "rb") as f:
        return f.read()


def read_terms_file(name: str, data: bytes):
    """Terms workbook: the first sheet is contract terms; a sheet named like 'price'/'SSP' holds stand-alone prices."""
    terms, _ = rio.read_table(name, data)
    ssp = None
    if not name.lower().endswith((".csv", ".txt")):
        xl = pd.ExcelFile(io.BytesIO(data))
        for sh in xl.sheet_names[1:]:
            if any(w in sh.lower() for w in ["price", "ssp", "stand"]):
                ssp, _ = rio.read_table(name, data, sheet=sh)
    return terms, ssp


@st.cache_data(show_spinner=False, max_entries=8)
def cached_analysis(data: pd.DataFrame, terms: pd.DataFrame, ssp: pd.DataFrame, s: tuple) -> A.Analysis:
    return A.analyse(data, terms, ssp, Settings(**dict(s)))


def settings_tuple():
    ss = st.session_state
    return (("fy_start_month", int(ss.fy_start)), ("confidence", float(ss.confidence)), ("significance", float(ss.significance)),
            ("passthrough_agent", bool(ss.agent)), ("setup_distinct", bool(ss.setup_distinct)),
            ("setup_months", int(ss.setup_months)), ("services_over_time", bool(ss.services_over_time)),
            ("discount_specific", bool(ss.discount_specific)), ("default_payment_days", int(ss.payment_days)),
            ("rpo_expedient", bool(ss.rpo_expedient)), ("undelivered_if_no_date", bool(ss.undelivered)))


def type_key(df: pd.DataFrame, it_col: Optional[str], desc_col: Optional[str]) -> pd.Series:
    src = df[it_col] if it_col else df[desc_col]
    return src.astype(object).where(src.notna(), "").map(lambda v: str(v).strip())


def validate(raw: pd.DataFrame, mapping: Dict, overrides: Dict) -> rio.Loaded:
    """Normalise the register, then apply the category overrides the user edited. A negative line keeps its settlement
    category, and a negative line moved to Goods becomes a return (credit note)."""
    res = rio.normalise_register(raw, mapping)
    if res.ok and overrides:
        d = res.data
        new = d["type_key"].map(overrides)
        hit = new.notna() & ~((d["amount"] < 0) & d["kind"].isin(SETTLEMENT_KINDS))
        d.loc[hit, "kind"] = new[hit]
        d.loc[hit & (d["amount"] < 0) & (d["kind"] == "Goods"), "kind"] = "Return"
    return res


def clear_results(**extra):
    """Forget everything computed from the previous data (a new file or the demo replaces it)."""
    for k in RESULT_KEYS:
        st.session_state[k] = None
    st.session_state.update(**extra)


def start_over():
    for k, v in DEFAULTS.items():
        st.session_state[k] = v
    for k in ["xlsx", "csv", "bundle", "reg_upl", "terms_upl"]:
        st.session_state.pop(k, None)
    st.session_state["main_tabs"] = TAB_NAMES[0]


def profile_json() -> bytes:
    import json
    return json.dumps({"app": "ifrs15-gap-analyzer", "version": 1,
                       "settings": {k: st.session_state.get(k) for k in SETTING_KEYS},
                       "mapping": st.session_state.get("mapping_cur") or {}}, indent=2, default=str).encode("utf-8")


def load_profile(data: bytes) -> str:
    import json
    try:
        p = json.loads(data.decode("utf-8"))
        assert p.get("app") == "ifrs15-gap-analyzer"
    except Exception:
        return "This is not a settings profile saved by this app."
    queue(**{k: v for k, v in (p.get("settings") or {}).items() if k in SETTING_KEYS}, mapping_cur=p.get("mapping") or {})
    return ""


# ── demo ───────────────────────────────────────────────────────────────────────
if st.session_state.pop("autoload_demo", False):
    reg_b, terms_b = sample_bytes("sample_sales_register.xlsx"), sample_bytes("sample_contract_terms.xlsx")
    raw, _ = rio.read_table("sample_sales_register.xlsx", reg_b)
    rt, rs = read_terms_file("sample_contract_terms.xlsx", terms_b)
    res = validate(raw, rio.guess_mapping(list(raw.columns)), {})
    if res.ok:
        T = rio.normalise_terms(rt)
        clear_results()
        st.session_state.update(raw_reg=raw, reg_name="sample_sales_register.xlsx", reg_id="demo", raw_terms=rt, raw_ssp=rs,
                                terms_name="sample_contract_terms.xlsx", terms_id="demo", data=res.data,
                                terms=T, ssp=rio.normalise_ssp(rs), load_warnings=res.warnings + rio.terms_warnings(res.data, T),
                                workflow_step=3, review_period=str(res.data["invoice_date"].max().to_period("M")), fy_start=7,
                                unit="PKR", company="Cedar Panels Ltd (demo)", data_source="Demo data", kind_overrides={},
                                confirm_demo=False, main_tabs=TAB_NAMES[2])


def demo_button(key: str, primary: bool = True):
    """Load the demo; asks first when the user's own data is loaded."""
    own = st.session_state.data is not None and st.session_state.data_source != "Demo data"
    if own and not st.session_state.confirm_demo:
        if st.button("⚡ Load the demo", key=key, width="stretch"):
            st.session_state.confirm_demo = True
            st.rerun()
    elif own:
        st.warning("This replaces your data with the demo.")
        c1, c2 = st.columns(2)
        if c1.button("Replace with demo", key=key + "_yes", type="primary", width="stretch"):
            st.session_state["autoload_demo"] = True
            st.rerun()
        if c2.button("Cancel", key=key + "_no", width="stretch"):
            st.session_state.confirm_demo = False
            st.rerun()
    elif st.button("⚡ Load the demo & run", key=key, width="stretch", type="primary" if primary else "secondary"):
        st.session_state["autoload_demo"] = True
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Header
# ══════════════════════════════════════════════════════════════════════════════
has_data = st.session_state.data is not None
if not has_data:
    st.markdown("""
<div class="app-header"><div class="header-inner">
  <div class="header-icon">🧾</div>
  <div class="header-text"><h1>IFRS 15 Revenue Gap Analyzer</h1>
  <p>Invoices are not revenue. See, month by month, how much of what you booked IFRS 15 recognises, why the rest moves,
  and the journals and disclosures that close the gap.</p></div>
  <div class="header-badges"><span class="badge badge-version">v1.1</span></div>
</div></div>""", unsafe_allow_html=True)
    c = st.columns(4)
    cards = [
        ("1", "Revenue follows control", "Goods when control passes (delivery or acceptance), subscriptions over the service "
         "period, set-up fees with their service. Month-end invoices for goods delivered next month are moved, provided the "
         "register carries delivery dates."),
        ("2", "Bundles, agents, bill-and-hold", "Contract prices and discounts allocated by stand-alone price, recharges you "
         "do not control shown net, and bill-and-hold tested against all four IFRS 15 criteria."),
        ("3", "The constraint, measured", "Returns from your own history and a simulated chance of each customer reaching "
         "its rebate tier, tested against how significant a reversal would be."),
        ("4", "Journals that tie out", "Month-end entries in debit/credit form that reconcile to the gap, contract balances, "
         "remaining obligations, disaggregation and a draft note: one close file."),
    ]
    for col, (n, t, d) in zip(c, cards):
        col.markdown(f'<div class="how-card"><span class="how-num">{n}</span><h4>{t}</h4><p>{d}</p></div>', unsafe_allow_html=True)
    st.markdown("")
    r1, r2, r3 = st.columns([1.2, 1, 1])
    with r1:
        demo_button("demo_top")
    with r2:
        b = sample_bytes("sample_sales_register.xlsx")
        if b:
            st.download_button("🧾 Sample sales register", b, "sample_sales_register.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
                               help="Fictional manufacturer, July 2023 – March 2026, invoice lines net of sales tax. Use it as a template.")
    with r3:
        b = sample_bytes("sample_contract_terms.xlsx")
        if b:
            st.download_button("📑 Sample terms & prices", b, "sample_contract_terms.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
                               help="Includes an Instructions sheet explaining every column.")
    cur = st.session_state.workflow_step
    steps = [("1", "Upload", "Sales register & terms"), ("2", "Map & validate", "Columns and item types"),
             ("3", "Review", "Gap, journals, disclosures")]
    parts = []
    for i, (num, name, desc) in enumerate(steps):
        s_ = i + 1
        cc = "done" if s_ < cur else ("active" if s_ == cur else "pending")
        nc = "done-text" if s_ < cur else ("active-text" if s_ == cur else "")
        parts.append(f'<div class="step-node"><div class="step-circle {cc}">{"✓" if s_ < cur else num}</div><div class="step-label">'
                     f'<div class="step-name {nc}">{name}</div><div class="step-desc">{desc}</div></div></div>')
        if i < 2:
            parts.append(f'<div class="step-connector {"done" if s_ < cur else ""}"></div>')
    st.markdown(f'<div class="stepper">{"".join(parts)}</div>', unsafe_allow_html=True)
else:
    comp = st.session_state.company.strip() or "Company name not set"
    st.markdown(f"""
<div class="slimbar"><div class="slim-title">🧾 IFRS 15 Revenue Gap Analyzer</div>
  <div class="slim-meta"><span class="slim-co">{esc(comp)}</span><span class="slim-sep">·</span>
  <span>Review {mlabel(st.session_state.review_period)}</span><span class="slim-sep">·</span>
  <span>{esc(st.session_state.data_source or "")}</span><span class="slim-sep">·</span>
  <span>{len(st.session_state.data):,} lines</span></div></div>""", unsafe_allow_html=True)
    if not st.session_state.company.strip():
        show_alert("Add the <strong>company name</strong> in the sidebar: it heads the Excel pack and the draft note.", "warning")

# ══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### ⚙️ Review settings")
    data = st.session_state.data
    if data is not None:
        ps = pd.period_range(data["invoice_date"].min().to_period("M"), data["invoice_date"].max().to_period("M"), freq="M")
        labels = [str(p) for p in ps][::-1]
        if st.session_state.review_period not in labels:
            st.session_state.review_period = labels[0]
        st.selectbox("Review month", labels, key="review_period", format_func=lambda s: pd.Period(s, "M").strftime("%B %Y"))
    st.text_input("Company", key="company", max_chars=80, placeholder="Your company name")
    c1, c2 = st.columns(2)
    c1.text_input("Currency", key="unit", max_chars=6)
    c2.selectbox("Fiscal year starts", list(range(1, 13)), key="fy_start", format_func=lambda m: MONTHS[m - 1][:3])
    st.markdown('<div class="sidebar-divider"></div><div class="sidebar-section-label">Judgements</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.selectbox("Certainty needed", [0.75, 0.80, 0.85, 0.90, 0.95], key="confidence", format_func=lambda v: f"{v:.0%}",
                 help="How sure you must be that revenue will not have to be reversed before you include an estimate "
                      "(\"highly probable\", IFRS 15.56). 90% is common.")
    c2.selectbox("Reversal that matters", [0.005, 0.01, 0.02, 0.05, 0.10], key="significance", format_func=lambda v: f"{v:.1%}",
                 help="A possible reversal smaller than this share of the revenue concerned is not significant, so it does "
                      "not hold revenue back (IFRS 15.57).")
    st.radio("Freight and other recharges", [True, False], key="agent",
             format_func=lambda v: "We arrange them: show net" if v else "We provide them: show gross",
             help="Agent or principal (IFRS 15.B34–B38): the test is whether you control the service before it is transferred "
                  "(primary responsibility, inventory risk, pricing discretion), not whether it is billed at cost.")
    st.radio("Set-up and onboarding fees", [False, True], key="setup_distinct",
             format_func=lambda v: "A separate service: on completion" if v else "Part of the service: spread with it",
             help="IFRS 15.B48–B51: most set-up activities do not transfer a service on their own.")
    with st.expander("More judgements"):
        st.number_input("Spread set-up fees over at least (months; 0 = the service term)", 0, 120, key="setup_months", step=6,
                        help="When the fee lets the customer renew without paying it again (a material right, B49), spread it "
                             "over the expected customer relationship.")
        st.toggle("Recognise one-off services as the work is done", key="services_over_time",
                  help="Installation and similar services from start to completion (IFRS 15.35(b)/(c)), instead of on completion.")
        st.toggle("Give discounts only to items sold below list", key="discount_specific",
                  help="Where items are always sold at their stand-alone price, the discount goes to the others (IFRS 15.82). "
                       "Use only where you have concluded the 82 criteria are met.")
        st.toggle("Goods with no delivery date: not delivered yet", key="undelivered",
                  help="Off: a blank delivery date is read as delivery on the invoice date. On: such goods wait until a "
                       "delivery date appears (safer at the month-end close).")
        st.number_input("Payment terms when none are given (days)", 0, 720, key="payment_days", step=15,
                        help="Decides when an invoice becomes a receivable (IFRS 15.105–108).")
        st.toggle("Leave contracts of a year or less out of remaining obligations", key="rpo_expedient",
                  help="The practical expedient in IFRS 15.121(a). A contract term in the terms file decides the length.")
    st.caption("Judgements apply to every contract and are stated in the draft note and the Excel pack. The ⚖️ Judgements tab "
               "shows what each one is worth.")
    with st.expander("💾 Save or load my settings"):
        st.caption("Save your company, currency, fiscal year, judgements, category choices, GL codes and column mapping, "
                   "and load them next month.")
        st.download_button("Save settings profile", profile_json(), "ifrs15_settings_profile.json", "application/json",
                           width="stretch")
        pf = st.file_uploader("Load a settings profile", type=["json"], key="profile_upl", label_visibility="collapsed")
        if pf is not None and st.session_state.get("profile_id") != pf.file_id:
            st.session_state["profile_id"] = pf.file_id
            err = load_profile(pf.getvalue())
            if err:
                st.error(err)
            else:
                st.rerun()
    if has_data:
        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        st.button("↺ Start over", on_click=start_over, width="stretch",
                  help="Clear the data and settings and go back to the start.")

U = lambda: st.session_state.unit or ""  # noqa: E731

tabs = st.tabs(TAB_NAMES, key="main_tabs", on_change="rerun")
ACTIVE = st.session_state.get("main_tabs") or TAB_NAMES[0]


def show(i: int) -> bool:
    """Only the open tab is computed and drawn, so every click stays fast."""
    return ACTIVE == TAB_NAMES[i]


# ── DATA ───────────────────────────────────────────────────────────────────────
with tabs[0]:
  if show(0):
    flash = st.session_state.pop("flash", None)
    if flash:
        show_alert(flash[0], flash[1])
    st.markdown('<div class="section-title">📂 Upload the sales register</div>', unsafe_allow_html=True)
    left, right = st.columns([1.3, 1])
    with left:
        f = st.file_uploader("Sales register (Excel or CSV): one row per invoice line, net of sales tax",
                             type=["xlsx", "xls", "csv", "txt"], key="reg_upl")
        if f is not None and f.file_id != st.session_state.reg_id:
            try:
                df, notes = rio.read_table(f.name, f.getvalue())
                if df.empty:
                    show_alert(f"<strong>{esc(f.name)}</strong> has column headings but no data rows.", "error")
                else:
                    clear_results(data=None, raw_reg=df, reg_name=f.name, reg_id=f.file_id, workflow_step=2,
                                  load_warnings=[], data_source=None)
                    if st.session_state.terms_id == "demo":
                        st.session_state.update(raw_terms=None, raw_ssp=None, terms_name=None, terms_id=None)
                    st.session_state["reg_notes"] = list(notes)
                    extra = {"company": ""} if st.session_state.company.strip() == "Cedar Panels Ltd (demo)" else {}
                    queue(**extra)
                    st.rerun()
            except Exception as e:
                show_alert(read_error(e, "the sales register"), "error")
        ft = st.file_uploader("Contract terms (optional): rebates, returns, acceptance, bill-and-hold, payment terms, contract "
                              "value. A second sheet may hold stand-alone prices. The sample has an Instructions sheet.",
                              type=["xlsx", "xls", "csv"], key="terms_upl")
        if ft is not None and ft.file_id != st.session_state.terms_id:
            try:
                t, sp = read_terms_file(ft.name, ft.getvalue())
                st.session_state.update(raw_terms=t, raw_ssp=sp, terms_name=ft.name, terms_id=ft.file_id)
                if st.session_state.data is not None:
                    clear_results(data=None, workflow_step=2)
                show_alert(f"✅ Terms <strong>{esc(ft.name)}</strong> loaded ({len(t)} rows"
                           + (f", {len(sp)} stand-alone prices" if sp is not None else "")
                           + "). Validate in <strong>Mapping</strong> to apply them.", "success")
            except Exception as e:
                show_alert(read_error(e, "the terms file"), "error")
        elif ft is None and st.session_state.terms_id not in (None, "demo"):
            st.session_state.update(raw_terms=None, raw_ssp=None, terms_name=None, terms_id=None)
        if st.session_state.raw_terms is not None:
            rec, ign = rio.terms_columns_report(st.session_state.raw_terms)
            with st.expander(f"How the terms file was read: {len(rec)} column(s) understood"
                             + (f", {len(ign)} ignored" if ign else ""), expanded=bool(ign)):
                st.dataframe(rec, hide_index=True, width="stretch")
                if ign:
                    show_alert("Ignored (not recognised): <strong>" + esc(", ".join(ign)) + "</strong>. Rename them to the "
                               "headings in the sample terms file if they matter.", "warning")
        if st.session_state.raw_reg is not None and st.session_state.data is None:
            show_alert(f"✅ <strong>{esc(st.session_state.reg_name or '')}</strong> is loaded "
                       f"({len(st.session_state.raw_reg):,} lines). Add the optional contract terms above if you have them, "
                       "then check the columns."
                       + "".join(f"<br>{esc(n)}" for n in st.session_state.get("reg_notes", [])), "success")
            st.button("Next: check columns →", on_click=go, args=(TAB_NAMES[1],), type="primary", key="go_map")
        if st.session_state.data is not None:
            st.markdown("")
            demo_button("demo_data", primary=False)
    with right:
        st.markdown("**Sales register columns** (* required)")
        st.dataframe(pd.DataFrame({
            "Column": ["Invoice Date *", "Customer *", "Net Amount *", "Invoice No / Contract ID", "Item Code / Description / Type",
                       "Quantity, Unit Price", "Delivery Date", "Service Start / End", "Acceptance Date", "Cost Amount",
                       "Reference Invoice"],
            "Used for": ["Month booked", "Terms, rebates, returns", "Revenue as booked", "Grouping lines into contracts",
                         "Goods, service, subscription, set-up, recharge, discount, return…", "Stand-alone prices",
                         "When control passes (goods)", "Recognition over time", "Contracts with acceptance clauses",
                         "Agent netting, deferred cost, returns asset", "Credit notes that cancel a service period"]}),
            hide_index=True, width="stretch", height=430,
            column_config={"Column": st.column_config.TextColumn(width="medium"), "Used for": st.column_config.TextColumn(width="large")})
        st.caption("Dates: Excel dates, ISO, dd/mm/yyyy or mm/dd/yyyy (decided once per file) and SAP-style yyyymmdd. Amounts: "
                   "thousand separators, decimal commas, (brackets), trailing minus, currency codes and CR/DR.")
    if st.session_state.raw_reg is not None:
        with st.expander(f"Preview: {st.session_state.reg_name}"):
            st.dataframe(st.session_state.raw_reg.head(25), width="stretch")

# ── MAPPING ────────────────────────────────────────────────────────────────────
with tabs[1]:
  if show(1):
    flash = st.session_state.pop("flash", None)
    if flash:
        show_alert(flash[0], flash[1])
    raw = st.session_state.raw_reg
    if raw is None:
        st.markdown('<div class="empty-state"><div class="empty-state-icon">🔗</div><h3>No file yet</h3>'
                    '<p>Upload a sales register in <b>Data</b>, or load the demo.</p></div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="section-title">🔗 Columns in {esc(st.session_state.reg_name or "")}</div>',
                    unsafe_allow_html=True)
        guess = rio.guess_mapping(list(raw.columns))
        saved = st.session_state.mapping_cur or {}
        opts = [NOT_IN_FILE] + [str(c) for c in raw.columns]
        nice = {"invoice_no": "Invoice number", "invoice_date": "Invoice date", "contract_id": "Contract / order",
                "customer": "Customer", "customer_group": "Customer group", "item_code": "Item code", "item_desc": "Item description",
                "item_type": "Item type", "qty": "Quantity", "unit_price": "Unit price", "amount": "Net amount",
                "delivery_date": "Delivery / completion date", "service_start": "Service start", "service_end": "Service end",
                "acceptance_date": "Acceptance date", "cost_amount": "Cost amount", "ref_invoice": "Reference invoice"}
        mapping = {}
        fields = list(rio.FIELDS)
        rid = st.session_state.reg_id or "x"
        for i in range(0, len(fields), 4):
            cols = st.columns(4)
            for j, fld in enumerate(fields[i:i + 4]):
                g = saved.get(fld) if saved.get(fld) in opts else (str(guess.get(fld)) if guess.get(fld) is not None else None)
                if fld in saved and saved[fld] is None:
                    g = None
                sel = cols[j].selectbox(f"{nice[fld]}{' *' if fld in rio.REQUIRED else ''}", opts,
                                        index=opts.index(g) if g in opts else 0, key=f"map_{fld}_{rid}")
                mapping[fld] = None if sel == NOT_IN_FILE else sel
        st.session_state.mapping_cur = mapping

        # item types → IFRS 15 category (editable)
        st.markdown('<div class="section-title">🏷️ How each item type is treated</div>', unsafe_allow_html=True)
        if mapping.get("item_type") or mapping.get("item_desc"):
            key = type_key(raw, mapping.get("item_type"), mapping.get("item_desc"))
            desc = raw[mapping["item_desc"]].astype(object).where(raw[mapping["item_desc"]].notna(), "").map(str) \
                if mapping.get("item_desc") else key
            vals = pd.DataFrame({"key": key, "desc": desc}).drop_duplicates("key").head(200).reset_index(drop=True)
            ov = st.session_state.kind_overrides
            inferred = [rio.infer_kind(k if mapping.get("item_type") else None, d) for k, d in zip(vals["key"], vals["desc"])]
            vals["IFRS 15 category"] = [ov.get(k, i) for k, i in zip(vals["key"], inferred)]
            ed = st.data_editor(vals.rename(columns={"key": "Item type in file", "desc": "Example description"}), hide_index=True,
                                width="stretch", height=min(42 + 35 * len(vals), 380), num_rows="fixed",
                                key=f"kind_editor_{rid}", disabled=["Item type in file", "Example description"],
                                column_config={"IFRS 15 category": st.column_config.SelectboxColumn(options=rio.KINDS, required=True)})
            st.caption("Goods and Licence: a point in time. Service: on completion, or over its service period when it has one. "
                       "Subscription and Warranty (service-type): over the service period. Setup: with the service it belongs to unless "
                       "distinct. Pass-through: net if agent. Discount: allocated across the contract's obligations. Return / Rebate: "
                       "settlements, replaced by estimates where terms allow. Negative goods lines are returns; a negative line keeps a "
                       "return, rebate or discount category whatever you choose here.")
            overrides = {k: v for k, v, i in zip(ed["Item type in file"], ed["IFRS 15 category"], inferred) if v != i}
        else:
            overrides = {}
            st.caption("Map an item type or description column so each line can be classified.")

        if st.button("✅ Validate & run analysis", type="primary"):
            res = validate(raw, mapping, overrides)
            if not res.ok:
                for e in res.errors:
                    show_alert(esc(e), "error")
                for w_ in res.warnings:
                    show_alert(esc(w_), "warning")
            else:
                T = rio.normalise_terms(st.session_state.raw_terms)
                clear_results()
                queue(data=res.data, terms=T, ssp=rio.normalise_ssp(st.session_state.raw_ssp),
                      load_warnings=res.warnings + rio.terms_warnings(res.data, T), workflow_step=3,
                      review_period=str(res.data["invoice_date"].max().to_period("M")),
                      data_source=st.session_state.reg_name, kind_overrides=overrides, main_tabs=TAB_NAMES[2])
                st.rerun()
        if st.session_state.data is not None and st.session_state.data_source in (st.session_state.reg_name, "Demo data"):
            d = st.session_state.data
            st.markdown('<div class="section-title">🧪 What was read</div>', unsafe_allow_html=True)
            st.markdown(kpi_html([
                ("Invoice lines", f"{len(d):,}", f"{d['invoice_no'].nunique():,} invoices", "indigo"),
                ("Customers", f"{d['customer'].nunique():,}", f"{d['contract_id'].nunique():,} contracts", "teal"),
                ("Period", f"{d['invoice_date'].min():%b %y} – {d['invoice_date'].max():%b %y}",
                 f"{d['invoice_date'].dt.to_period('M').nunique()} months", "indigo"),
                ("Net amount", money(float(d["amount"].sum())), "total of all lines: tie this to your ledger", "green"),
            ]), unsafe_allow_html=True)
            kc = d["kind"].value_counts()
            st.caption("Lines by category: " + " · ".join(f"{k} {v:,}" for k, v in kc.items()))
            for w in st.session_state.load_warnings:
                show_alert(esc(w), "warning")
            st.button("📊 Open the Dashboard", on_click=go, args=(TAB_NAMES[2],), type="primary")


# ── helpers ────────────────────────────────────────────────────────────────────
def need_data() -> bool:
    if st.session_state.data is None:
        st.markdown('<div class="empty-state"><div class="empty-state-icon">🧾</div><h3>No analysis yet</h3>'
                    '<p>Upload and validate a sales register in <b>Data</b>, or load the demo.</p></div>', unsafe_allow_html=True)
        return False
    return True


def ctx():
    with st.spinner("Applying IFRS 15 to every invoice line…"):
        a = cached_analysis(st.session_state.data, st.session_state.terms if st.session_state.terms is not None else rio.normalise_terms(None),
                            st.session_state.ssp if st.session_state.ssp is not None else rio.normalise_ssp(None), settings_tuple())
    P = pd.Period(st.session_state.review_period, "M")
    return a, P, P.strftime("%B %Y")


def text_table(df: pd.DataFrame, money_cols: List[str], pinned: Optional[str] = None, height=None, widths=None):
    v = df.copy()
    v.attrs = {}
    for c in money_cols:
        if c in v:
            v[c] = v[c].map(acct)
    cfg = {c: st.column_config.TextColumn(c, alignment="right") for c in money_cols if c in v}
    if pinned:
        cfg[pinned] = st.column_config.TextColumn(pinned, pinned=True)
    for c, w in (widths or {}).items():
        if c in v:
            cfg[c] = st.column_config.TextColumn(c, width=w, pinned=(c == pinned) or None,
                                                 alignment="right" if c in money_cols else None)
    kw = {"height": height} if height else {}
    st.dataframe(v, hide_index=True, width="stretch", column_config=cfg, **kw)


# ── DASHBOARD ──────────────────────────────────────────────────────────────────
with tabs[2]:
    if show(2) and need_data():
        a, P, label = ctx()
        g = A.gap_summary(a, P)
        bal = a.balances.loc[P]
        rpo = A.remaining_obligations(a, P)
        rpo_total = float(rpo.loc[rpo["When"] == "Total", "Total"].sum()) if len(rpo) else 0.0
        te = A.timing_exceptions(a, P)
        st.markdown(f'<div class="breadcrumb"><span class="breadcrumb-cat">{esc(st.session_state.data_source or "")}</span>'
                    f'<span class="breadcrumb-arrow">›</span><span class="breadcrumb-title">IFRS 15 review · {label} · '
                    f'{esc(A.fy_label(a, P))}</span></div>', unsafe_allow_html=True)
        pct = g["gap_month"] / g["booked_month"] if g["booked_month"] else np.nan
        prev_gap = float(a.monthly.loc[P - 1, "L4"] - a.monthly.loc[P - 1, "L0"]) if (P - 1) in a.monthly.index else None
        st.markdown(kpi_html([
            ("Revenue as booked", money(g["booked_month"]), f"fiscal YTD {money(g['booked_ytd'])}", "indigo"),
            ("Revenue under IFRS 15", money(g["ifrs15_month"]), f"fiscal YTD {money(g['ifrs15_ytd'])}", "teal"),
            ("Gap this month", money(g["gap_month"]),
             (f"{pct:+.1%} of booked" if np.isfinite(pct) else "IFRS 15 − booked")
             + (f" · last month {money(prev_gap)}" if prev_gap is not None else ""),
             "red" if abs(g["gap_month"]) >= 0.5 else "green"),
            ("Gap fiscal YTD", money(g["gap_ytd"]), "IFRS 15 − booked", "red" if abs(g["gap_ytd"]) >= 0.5 else "green"),
        ]), unsafe_allow_html=True)

        # ── what needs attention this close, largest first ──────────────────
        items = []
        by_cause = te.groupby("cause")["effect_on_month"].agg(["count", "sum"]) if len(te) else pd.DataFrame()
        for k, title, why in [
                ("cutoff", "Cut-off", "invoiced in one month, delivered in another"),
                ("bill_and_hold", "Bill-and-hold", "invoiced but held: the B81 criteria are not all met"),
                ("acceptance", "Customer acceptance", "waiting for the customer's sign-off")]:
            if k in by_cause.index and abs(by_cause.loc[k, "sum"]) >= 0.5:
                n = int(by_cause.loc[k, "count"])
                items.append((abs(by_cause.loc[k, "sum"]), "🔴" if by_cause.loc[k, "sum"] < 0 else "🟢",
                              f"{title}: {n} line{'s' if n != 1 else ''}", float(by_cause.loc[k, "sum"]), why, TAB_NAMES[5]))
        cm = g["causes_month"]
        for c_, title, why, tab in [
                (M.TIMING_CAUSE["over_time"], "Subscriptions & services billed ahead", "deferred over the service period", 3),
                (M.TIMING_CAUSE["setup"], "Set-up fees", "spread with the service they belong to", 3),
                (A.C_ALLOC, "Bundle and discount allocation", "price moved between obligations", 3),
                (A.C_REB, "Volume rebates", "change in the rebate liability", 4),
                (A.C_RET, "Expected returns", "change in the refund liability", 4),
                (A.C_AGENT, "Recharges shown net", "only the margin is revenue", 5)]:
            v = float(cm.get(c_, 0.0))
            if abs(v) >= 0.5:
                items.append((abs(v), "🔴" if v < 0 else "🟢", title, v, why, TAB_NAMES[tab]))
        rt_ = a.var.rebate_table
        if not rt_.empty:
            near = rt_[(rt_["period"] == P) & rt_["basis"].astype(str).str.startswith("Constraint")]
            for r_ in near.itertuples():
                items.append((abs(r_.liability), "🟠", f"Rebate judgement: {r_.customer}", -float(r_.liability),
                              f"{r_.probability:.0%} chance of reaching the tier: accrued under the constraint", TAB_NAMES[4]))
        ff_ = A.financing_flags(a, P)
        if len(ff_):
            items.append((0.0, "🟠", "Financing component to assess", None, f"{len(ff_)} item(s) with long payment terms or "
                          "long prepaid services", TAB_NAMES[5]))
        nw = len(st.session_state.load_warnings) + len(a.warnings)
        if nw:
            items.append((-1.0, "⚪", "Data and estimation notes", None, f"{nw} note(s) to read before signing off", TAB_NAMES[5]))
        items.sort(key=lambda x: -x[0])
        st.markdown('<div class="section-title">🎯 This close: what needs your attention</div>', unsafe_allow_html=True)
        if not items:
            show_alert("✅ Nothing to review: revenue as booked already matches IFRS 15 this month.", "success")
        for n_, (_, icon, title, amt, why, tab) in enumerate(items[:7]):
            c1, c2, c3 = st.columns([5.2, 1.6, 1.2], vertical_alignment="center")
            c1.markdown(f'<div class="att-row"><span class="att-ic">{icon}</span><div><div class="att-t">{esc(title)}</div>'
                        f'<div class="att-w">{esc(why)}</div></div></div>', unsafe_allow_html=True)
            c2.markdown(f'<div class="att-amt {"neg" if (amt or 0) < 0 else ""}">'
                        f'{money(amt) if amt is not None else ""}</div>', unsafe_allow_html=True)
            c3.button("Open →", key=f"att_{n_}", on_click=go, args=(tab,), width="stretch",
                      help=f"Open {tab.split(' ', 1)[1]}")
        if len(items) > 7:
            st.caption(f"{len(items) - 7} smaller item(s) not shown; see Controls and Journals.")

        st.markdown(kpi_html([
            ("Contract liabilities", money(bal["contract_liability"]),
             f"+ {money(bal['billed_not_due'])} invoiced ahead, not yet due", "amber"),
            ("Contract assets & unbilled", money(bal["contract_asset"] + bal["unbilled_receivable"]),
             f"assets {money(bal['contract_asset'])} · unbilled {money(bal['unbilled_receivable'])}", "teal"),
            ("Refund liabilities", money(bal["refund_liability"] + bal["rebate_liability"]),
             f"returns {money(bal['refund_liability'])} · rebates {money(bal['rebate_liability'])}", "amber"),
            ("Remaining obligations", money(rpo_total), "price allocated, not yet delivered", "indigo"),
        ]), unsafe_allow_html=True)

        # tie-out strip: cumulative gap = balances, and journals = this month's gap
        cum_gap = float((a.monthly["L4"] - a.monthly["L0"]).loc[:P].sum())
        agent_cum = float(a.causes[A.C_AGENT].loc[:P].sum())
        sum_bal = (agent_cum + bal["contract_asset"] + bal["unbilled_receivable"] - bal["contract_liability"] - bal["billed_not_due"]
                   - bal["refund_liability"] - bal["rebate_liability"])
        rec = A.journal_reconciliation(a, P)
        ok1 = abs(cum_gap - sum_bal) < 1
        ok2 = bool(rec.attrs.get("ties"))
        ok3 = abs(float(g["causes_month"].sum()) - g["gap_month"]) < 1
        st.markdown(
            '<div class="tieout">'
            f'<div class="tie {"ok" if ok3 else "bad"}"><span>{"✓" if ok3 else "✗"}</span><div>Causes add up to the month\'s gap '
            f'<b>{money(g["gap_month"])}</b></div></div>'
            f'<div class="tie {"ok" if ok1 else "bad"}"><span>{"✓" if ok1 else "✗"}</span><div>Cumulative gap <b>{money(cum_gap)}</b> = '
            f'IFRS 15 balances <b>{money(sum_bal)}</b></div></div>'
            f'<div class="tie {"ok" if ok2 else "bad"}"><span>{"✓" if ok2 else "✗"}</span><div>Journals: reverse last month + post '
            f'this month = <b>{money(float(rec.iloc[3, 1]))}</b></div></div></div>', unsafe_allow_html=True)

        c1, c2 = st.columns([1.35, 1])
        with c1:
            b1, b2 = st.columns([1.2, 1])
            basis = b1.radio("Show the bridge for", ["month", "ytd"], horizontal=True, key="bridge_basis",
                             format_func=lambda s: {"month": "The review month", "ytd": "Fiscal year to date"}[s])
            mat = b2.number_input(f"Group causes below ({U()})", min_value=0.0, step=100000.0, key="materiality", format="%.0f",
                                  help="Causes smaller than this are shown together as 'Other causes'.")
            st.plotly_chart(charts.fig_bridge(A.bridge_steps(a, P, basis == "ytd", mat), U(),
                                              f"From revenue as booked to IFRS 15 · {label if basis == 'month' else 'fiscal YTD'}"),
                            width="stretch")
        with c2:
            st.plotly_chart(charts.fig_balances(a.balances, P, U()), width="stretch")
        st.plotly_chart(charts.fig_booked_vs_ifrs(a.monthly, P, U()), width="stretch")
        st.plotly_chart(charts.fig_gap_by_cause(a.causes, P, U()), width="stretch")
        st.caption("Negative = revenue booked earlier than IFRS 15 allows (moved to a later month, or an increase in a refund "
                   "liability); positive = revenue that IFRS 15 recognises this month although it was invoiced earlier, or a "
                   "release of returns and rebates.")

# ── CONTRACTS ──────────────────────────────────────────────────────────────────
with tabs[3]:
    if show(3) and need_data():
        a, P, label = ctx()
        st.markdown('<div class="section-title">📄 Contract walkthrough: the five steps</div>', unsafe_allow_html=True)
        rk = A.contracts_ranked(a, P)
        rk = rk[rk["effect"].abs() >= 0.5]
        if rk.empty:
            show_alert("No contract has an IFRS 15 effect in the fiscal year to date.", "info")
        else:
            st.caption("Contracts ranked by their IFRS 15 effect in the fiscal year to date.")
            text_table(rk.head(200).rename(columns={"contract_id": "Contract", "customer": "Customer", "kinds": "Includes",
                                                    "booked_ytd": "Booked YTD", "ifrs15_ytd": "IFRS 15 YTD", "effect": "Effect"})
                       [["Contract", "Customer", "Includes", "Booked YTD", "IFRS 15 YTD", "Effect"]],
                       ["Booked YTD", "IFRS 15 YTD", "Effect"], pinned="Contract", height=260)
            opts = list(rk["contract_id"].head(200))
            if st.session_state.get("contract_pick") not in opts:
                st.session_state["contract_pick"] = opts[0]
            cust_of = dict(zip(rk["contract_id"], rk["customer"]))
            cid = st.selectbox("Contract", opts, key="contract_pick", format_func=lambda c: f"{c} · {cust_of.get(c, '')}")
            det = A.contract_detail(a, cid, P)
            cust = det["customer"].iloc[0]
            from engine.model import line_terms, terms_lookup
            t = line_terms(terms_lookup(a.terms), cid, cust)
            terms_txt = [k for k, v in [("rebates", t.get("rebate_type") in ("Flat", "Tiered")), ("return rights", t.get("return_rights")),
                                       ("acceptance clause" + (" (a formality)" if t.get("acceptance_formality") else ""),
                                        t.get("acceptance_required")), ("bill-and-hold", t.get("bill_and_hold")),
                                       (f"payment {t.get('payment_days', 0) or 0:.0f} days",
                                        t.get("payment_days") is not None and pd.notna(t.get("payment_days")))] if v]
            src = "contract-specific terms" if cid in terms_lookup(a.terms)["contract"] else "the customer's terms"
            po = det[det["pattern"].isin(["point", "over_time"])]
            disc = det[det["pattern"] == "discount"]
            c1, c2 = st.columns([1, 1.4])
            with c1:
                st.markdown(f"**Step 1 · Contract** {esc(cid)} with **{esc(cust)}**, invoiced "
                            f"{det['invoice_date'].min():%d %b %Y}" + (f"; {src}: {', '.join(terms_txt)}." if terms_txt else "."))
                st.markdown(f"**Step 2 · Performance obligations:** {len(po)} — " +
                            ", ".join(f"{esc(r.item_desc)} ({esc(r.kind.lower())})" for r in po.itertuples()))
                tp = po["L1"].sum() + disc["L1"].sum()
                st.markdown(f"**Step 3 · Transaction price:** {money(tp)}"
                            + (f", after a discount of {money(-disc['L1'].sum())}" if len(disc) else "")
                            + (". Variable consideration for returns and rebates is estimated across the customer's sales."
                               if any(k in " ".join(terms_txt) for k in ("rebates", "return")) else "."))
                am = po.loc[po["alloc_method"] != "", "alloc_method"]
                alloc = am.iloc[0] if len(am) else ""
                st.markdown("**Step 4 · Allocation:** " + (f"{alloc}: the price is re-allocated across the obligations." if alloc else
                                                          "invoiced prices are used (one obligation, all satisfied together, or "
                                                          "invoiced prices within tolerance of stand-alone prices)."))
                st.markdown("**Step 5 · Recognition:** shown per obligation in the table and the chart.")
            with c2:
                st.plotly_chart(charts.fig_contract(det, a.model.schedule, U()), width="stretch")
            v = det[["item_desc", "kind", "L0", "ssp_line", "L2", "recognised_to_date", "when", "note"]].assign(
                item_desc=det["item_desc"].astype(str)).rename(columns={
                "item_desc": "Item", "kind": "Category", "qty": "Qty", "L0": "Invoiced", "ssp_line": "Stand-alone price",
                "L2": "Allocated", "when": "When recognised", "note": "Why", "recognised_to_date": "Recognised to date"})
            text_table(v, ["Invoiced", "Stand-alone price", "Allocated", "Recognised to date"], pinned="Item",
                       widths={"Item": "medium", "When recognised": "medium", "Why": "medium"})

# ── RETURNS & REBATES ──────────────────────────────────────────────────────────
with tabs[4]:
    if show(4) and need_data():
        a, P, label = ctx()
        conf = a.model.settings.confidence
        st.markdown('<div class="section-title">🎯 Expected returns: the constraint, measured</div>', unsafe_allow_html=True)
        rr = a.var.return_rates
        if rr.empty:
            show_alert("No customers with return rights or a return history were found.", "info")
        else:
            groups = sorted(rr["group"].unique())
            if st.session_state.get("ret_group") not in groups:
                st.session_state["ret_group"] = groups[0]
            gsel = st.radio("Customer group", groups, horizontal=True, key="ret_group")
            row = rr[(rr["group"] == gsel) & (rr["period"] == P)]
            if len(row):
                row = row.iloc[0]
                st.markdown(kpi_html([
                    ("Expected return rate", f"{row['expected_rate']:.2%}", "expected value of closed months (B23)", "teal"),
                    ("Rate used", f"{row['constrained_rate']:.2%}",
                     "constraint applied" if row["constrained_rate"] > row["expected_rate"] + 1e-12 else
                     "no significant reversal risk: expected value", "indigo"),
                    ("Goods revenue this month", money(row["sales"]), f"{gsel}", "indigo"),
                    ("Refund liability", money(float(a.var.refund_by_group.loc[P, gsel]) if gsel in a.var.refund_by_group else 0.0),
                     f"open return window: {a.var.return_window} month(s)", "amber"),
                ]), unsafe_allow_html=True)
            c1, c2 = st.columns([1.4, 1])
            with c1:
                st.plotly_chart(charts.fig_return_rates(rr, gsel, P), width="stretch")
            with c2:
                st.plotly_chart(charts.fig_rate_distribution(rr, gsel, P, conf), width="stretch")
            sig_ = a.model.settings.significance
            st.caption(f"Each month's provision uses only returns received before that month, on sale months whose return "
                       "window had closed (no hindsight). The estimate is the expected value. The constraint adds to it only the "
                       f"part of the {conf:.0%}-level rate that exceeds the expected rate by more than {sig_:.1%} (the reversal "
                       "that matters): rate used = the higher of the expected rate and the "
                       f"{conf:.0%}-level rate less {sig_:.1%}. The chart shows actual rates only for months whose return window "
                       "has closed. Unused provision is released when the window closes.")

        st.markdown('<div class="section-title">🎚️ Volume rebates: probability of reaching the tier</div>', unsafe_allow_html=True)
        rt = a.var.rebate_table
        if rt.empty:
            show_alert("No rebate terms were provided. Add Rebate Type, Rebate % and Rebate Threshold per customer in the terms file.", "info")
        else:
            fy = fiscal_year(P, a.model.settings.fy_start_month)
            now = rt[(rt["period"] == P) & (rt["fiscal_year"] == fy)]
            if now.empty:
                show_alert(f"No rebate customer bought in {label}.", "info")
            if len(now):
                v = now.assign(**{"Probability of tier": now["probability"].map(lambda p: "–" if pd.isna(p) else f"{p:.0%}"),
                                  "Rebate": [f"{p:.1%} " + ("flat" if k == "Flat" else f"above {money(t)}")
                                             for p, k, t in zip(now["rebate_pct"], now["rebate_type"], now["threshold"])],
                                  "Most likely": now["most_likely_rate"].map(lambda r: f"{r:.1%}"),
                                  "Accrued at": now["accrual_rate"].map(lambda r: f"{r:.1%}")})
                text_table(v.rename(columns={"customer": "Customer", "ytd_purchases": "Purchases YTD", "accrued": "Accrued",
                                             "settled": "Settled", "liability": "Liability", "basis": "Basis"})
                           [["Customer", "Rebate", "Purchases YTD", "Probability of tier", "Most likely", "Accrued at", "Liability",
                             "Basis"]],
                           ["Purchases YTD", "Liability"], pinned="Customer", widths={"Basis": "large", "Rebate": "medium"})
                tiered = list(now[now["rebate_type"] == "Tiered"]["customer"])
                if tiered:
                    if st.session_state.get("reb_cust") not in tiered:
                        st.session_state["reb_cust"] = tiered[0]
                    csel = st.selectbox("Tiered customer", tiered, key="reb_cust")
                    r_ = now.set_index("customer").loc[csel]
                    c1, c2 = st.columns(2)
                    with c1:
                        sim = a.var.rebate_sims.get((csel, fy, P))
                        if sim is not None:
                            st.plotly_chart(charts.fig_rebate_sim(sim, r_["threshold"], r_["ytd_purchases"], U(), csel), width="stretch")
                        else:
                            show_alert("The year is complete or no months remain to simulate.", "info")
                    with c2:
                        st.plotly_chart(charts.fig_rebate_path(rt, csel, fy, conf, P), width="stretch")
                    p = r_["probability"]
                    decision = ("accrued on all purchases to date" if r_["accrual_rate"] > 0 else "not accrued")
                    n_sim = len(sim) if sim is not None else 0     # the full simulation, not a sample of it
                    show_alert(f"<strong>{esc(csel)}</strong>: {p:.0%} probability of reaching {money(r_['threshold'])} this year "
                               f"(purchases to date {money(r_['ytd_purchases'])}). The rebate is {decision}. {esc(str(r_['basis']))}."
                               + (f" The remaining months are simulated {n_sim:,} times from the customer's own monthly purchases, "
                                  "scaled for this year's growth." if n_sim else ""), "info")
            st.caption("Rebate credit notes settle the liability first-in, first-out against earlier accruals, instead of reducing "
                       "the month's revenue.")

# ── CONTROLS ───────────────────────────────────────────────────────────────────
with tabs[5]:
    if show(5) and need_data():
        a, P, label = ctx()
        st.markdown(f'<div class="section-title">✂️ Timing exceptions · {label}</div>', unsafe_allow_html=True)
        te = A.timing_exceptions(a, P)
        if te.empty:
            show_alert("✅ Every point-in-time line invoiced this month transferred control this month, and no earlier invoice "
                       "transferred control this month.", "success")
        else:
            late = te[te["effect_on_month"] < 0]
            show_alert(f"{'❌' if len(late) else '⚠️'} {len(late)} line(s) invoiced in {label} transfer control in a later month or not "
                       f"yet ({money(-late['effect_on_month'].sum())}); {len(te) - len(late)} line(s) invoiced earlier transfer "
                       f"control this month ({money(te[te['effect_on_month'] > 0]['effect_on_month'].sum())}).",
                       "error" if len(late) else "warning")
            causes_present = ["All"] + [M_ for M_ in ["Cut-off: control passes in another month", "Bill-and-hold criteria not met",
                                                      "Customer acceptance"] if M_ in set(te["cause_label"])]
            pick = st.radio("Cause", causes_present, horizontal=True, key="te_cause")
            te_view = te if pick == "All" else te[te["cause_label"] == pick]
            text_table(te_view.assign(invoice_date=te_view["invoice_date"].dt.strftime("%d %b %y"),
                                   control_date=te_view["control_date"].map(lambda x: x.strftime("%d %b %y") if pd.notna(x) else "not yet"),
                                   cause_label=te_view["cause_label"].map(lambda c: charts.SHORT.get(c, c)))
                       .rename(columns={"invoice_no": "Invoice", "invoice_date": "Invoiced on", "customer": "Customer",
                                        "item_desc": "Item", "amount": "Invoiced", "L2": "Allocated", "control_date": "Control",
                                        "cause_label": "Cause", "effect_on_month": "Effect", "direction": "Direction",
                                        "note": "Why"})
                       [["Invoice", "Invoiced on", "Customer", "Item", "Invoiced", "Allocated", "Control", "Cause", "Effect",
                         "Why"]],
                       ["Invoiced", "Allocated", "Effect"], pinned="Invoice",
                       widths={"Why": "large", "Item": "medium", "Customer": "medium", "Direction": "small", "Cause": "small"})
            st.caption("Effect = the invoiced amount moved out of the month (negative: control passes later) or into it (positive: "
                       "invoiced earlier), as in the bridge. Where the allocated price "
                       "differs, the difference is shown under Allocation.")
        st.markdown(f'<div class="section-title">📦 Bill-and-hold: IFRS 15.B81 criteria · open at {label}</div>', unsafe_allow_html=True)
        bh = A.bill_and_hold(a, P)
        if bh.empty:
            st.caption("No bill-and-hold arrangements open at this month-end (goods held more than 7 days after invoicing for "
                       "contracts with bill-and-hold terms).")
        else:
            bh2 = bh.assign(invoice_date=bh["invoice_date"].dt.strftime("%d %b %y"),
                            delivery_date=bh["delivery_date"].map(lambda x: x.strftime("%d %b %y") if pd.notna(x) else "not yet"))
            text_table(bh2.rename(columns={"invoice_no": "Invoice", "customer": "Customer", "item": "Item", "amount": "Amount",
                                           "invoice_date": "Invoiced", "delivery_date": "Delivered", "outcome": "Outcome",
                                           "Substantive reason": "Reason", "Identified as the customer's": "Identified",
                                           "Ready for transfer": "Ready", "Cannot be redirected": "No redirect"})
                       [["Invoice", "Customer", "Item", "Amount", "Invoiced", "Delivered", "Reason", "Identified", "Ready",
                         "No redirect", "Outcome"]],
                       ["Amount"], pinned="Invoice", widths={"Outcome": "large", "Item": "medium", "Reason": "small",
                                                             "Identified": "small", "Ready": "small", "No redirect": "small"})
            st.caption("B81 criteria: a substantive reason for the arrangement · the goods are identified separately as the "
                       "customer's · ready for physical transfer · cannot be used or sent to another customer.")
        st.markdown(f'<div class="section-title">✍️ Customer acceptance · pending at {label}</div>', unsafe_allow_html=True)
        ap = A.acceptance_pending(a, P)
        if ap.empty:
            st.caption("No lines awaiting a substantive customer acceptance at this month-end.")
        else:
            show_alert(f"{len(ap)} line(s) need acceptance that is not evidenced by the month-end ({money(ap['L2'].sum())}): no "
                       "revenue is recognised until acceptance, unless it is only a formality (IFRS 15.B83–B86; set "
                       "'Acceptance Formality' in the terms).", "warning")
            text_table(ap.assign(invoice_date=ap["invoice_date"].dt.strftime("%d %b %Y"),
                                 delivery_date=ap["delivery_date"].map(lambda x: x.strftime("%d %b %Y") if pd.notna(x) else "–"),
                                 acceptance_date=ap["acceptance_date"].map(lambda x: x.strftime("%d %b %Y") if pd.notna(x) else "–"))
                       .rename(columns={"invoice_no": "Invoice", "invoice_date": "Invoiced", "contract_id": "Contract",
                                        "customer": "Customer", "item_desc": "Item", "L2": "Amount", "delivery_date": "Delivered",
                                        "acceptance_date": "Accepted"}), ["Amount"], pinned="Invoice")
        st.markdown(f'<div class="section-title">🤝 Principal vs agent · {label}</div>', unsafe_allow_html=True)
        ag = A.agent_screening(a, P)
        if ag.empty:
            st.caption("No pass-through recharges or near-zero-margin lines this month.")
        else:
            pt_ = ag[ag["type"] == "Pass-through"]
            if len(pt_):
                show_alert(f"Pass-through recharges billed {money(pt_['billed'].sum())}; revenue recognised "
                           f"{money(pt_['revenue'].sum())} "
                           + ("(agent: margin only)." if a.model.settings.passthrough_agent else "(principal: gross)."), "info")
            text_table(ag.rename(columns={"type": "Type", "invoice_no": "Invoice", "customer": "Customer", "item": "Item",
                                          "billed": "Billed", "cost": "Cost", "revenue": "Revenue recognised",
                                          "assessment": "Assessment"}),
                       ["Billed", "Cost", "Revenue recognised"], pinned="Invoice", height=320, widths={"Assessment": "large"})
        st.markdown('<div class="section-title">⏳ Significant financing component</div>', unsafe_allow_html=True)
        ff = A.financing_flags(a, P)
        if ff.empty:
            st.caption("No customer has payment terms beyond 12 months and no service longer than 12 months is paid in advance "
                       "(practical expedient, IFRS 15.63).")
        else:
            text_table(ff.rename(columns={"customer": "Customer", "payment_terms_days": "Payment terms (days)",
                                          "billed_to_date": "Billed to date", "assessment": "Assessment"}),
                       ["Billed to date"], widths={"Assessment": "large"})
        msgs = st.session_state.load_warnings + a.warnings + A.data_quality(a)
        if msgs:
            st.markdown('<div class="section-title">🧪 Data and estimation notes</div>', unsafe_allow_html=True)
            for w_ in dict.fromkeys(msgs):
                show_alert(esc(w_), "warning")

# ── DISCLOSURES ────────────────────────────────────────────────────────────────
with tabs[6]:
    if show(6) and need_data():
        a, P, label = ctx()
        fy = fiscal_year(P, a.model.settings.fy_start_month)
        st.markdown(f'<div class="section-title">📚 Disaggregation of revenue · FY{fy} to {label} (IFRS 15.114)</div>', unsafe_allow_html=True)
        dis = A.disaggregation(a, P)
        items = list(dis.items())
        for row in (items[:2], items[2:]):
            cols = st.columns(2)
            for col, (k, t) in zip(cols, row):
                with col:
                    st.markdown(f"**{k}**")
                    text_table(t.rename(columns={k: "Line"}), ["Month", "Fiscal YTD"], widths={"Line": "medium"})
        st.markdown('<div class="section-title">🔄 Contract balances (IFRS 15.116)</div>', unsafe_allow_html=True)
        rf = A.balances_rollforward(a, P)
        if not rf.attrs["has_opening"]:
            show_alert("The data starts in this fiscal year, so opening balances are shown as zero.", "warning")
        text_table(pd.concat([rf, A.other_balances(a, P)], ignore_index=True), ["Opening", "Closing", "Movement"], pinned="Balance",
                   widths={"Balance": "large"})
        st.caption(f"IFRS 15.116(b): revenue recognised in FY{fy} to date that was included in the opening contract liability, "
                   f"measured contract by contract: {money(rf.attrs['revenue_from_opening_liability'])}. Invoices issued ahead of "
                   "performance are contract liabilities once due (or paid); before that they are neither a liability nor a "
                   "receivable (IFRS 15.106, 108).")
        why = A.deferred_by_reason(a, P)
        why = why[why.abs() >= 0.5]
        if len(why):
            st.caption("IFRS 15.118, invoiced but not yet recognised at the month-end: "
                       + "; ".join(f"{esc(k)} {money(v)}" for k, v in why.items()) + ".")
        st.markdown('<div class="section-title">⏭️ Remaining performance obligations (IFRS 15.120)</div>', unsafe_allow_html=True)
        rpo = A.remaining_obligations(a, P)
        c1, c2 = st.columns([1, 1.2])
        with c1:
            text_table(rpo, [c for c in rpo.columns if c != "When"], pinned="When", widths={"When": "medium"})
            ex = rpo.attrs.get("excluded_short", 0.0)
            st.caption("Contracts invoiced to date (orders not yet invoiced are not in a sales register). "
                       + (f"Contracts with an original expected duration of one year or less are left out under the practical "
                          f"expedient in IFRS 15.121(a): {money(ex)}." if a.model.settings.rpo_expedient else
                          "The 121(a) expedient is off: all contracts are included."))
        with c2:
            if len(rpo):
                st.plotly_chart(charts.fig_rpo(rpo, U()), width="stretch")
        st.markdown('<div class="section-title">📝 Draft note</div>', unsafe_allow_html=True)
        note = A.draft_note(a, P, U(), money)
        st.text_area("Draft accounting policy and disclosure note (edit before use)", note, height=360)
        from engine.exports import note_docx
        st.download_button("⬇️ Download the draft note (Word, with the disclosure tables)",
                           note_docx(a, P, U(), st.session_state.company.strip() or "Company"), f"ifrs15_note_{P}.docx",
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

# ── JOURNALS ───────────────────────────────────────────────────────────────────
JOURNAL_ACCOUNTS = ["Revenue", "Contract liabilities", "Trade receivables", "Contract assets", "Trade receivables (unbilled)",
                    "Refund liabilities: returns", "Refund liabilities: rebates", "Right to recover returned goods",
                    "Inventory", "Cost of sales"]


def journal_csv(a, P) -> bytes:
    from engine.exports import erp_lines
    up = erp_lines(a, P, U(), st.session_state.gl_codes)
    up["Posting date"] = pd.to_datetime(up["Posting date"]).dt.strftime("%Y-%m-%d")
    up["Reversal date"] = pd.to_datetime(up["Reversal date"]).dt.strftime("%Y-%m-%d")
    return up.rename(columns=lambda c: c.lower().replace(" ", "_")).to_csv(index=False).encode("utf-8-sig")


with tabs[7]:
    if show(7) and need_data():
        a, P, label = ctx()
        codes = st.session_state.gl_codes or {}
        st.markdown(f'<div class="section-title">🧮 Month-end adjusting journals · {label}</div>', unsafe_allow_html=True)
        j = A.journals(a, P)
        if j.empty:
            show_alert("No adjustments are needed: the books already match IFRS 15 at this month-end.", "success")
        else:
            nxt = (P + 1).to_timestamp().strftime("%d %b %Y")
            rows = []
            for n, e in enumerate(j.itertuples(index=False), start=1):
                rows.append({"#": str(n), "GL code": codes.get(e[0], ""), "Account": e[0], "Debit": e[2], "Credit": np.nan,
                             "Reverses": nxt if e[3] == "Yes" else "No", "Narrative": e[4]})
                rows.append({"#": "", "GL code": codes.get(e[1], ""), "Account": "    " + e[1], "Debit": np.nan, "Credit": e[2],
                             "Reverses": "", "Narrative": ""})
            jl = pd.DataFrame(rows)
            jl = pd.concat([jl, pd.DataFrame([{"#": "", "GL code": "", "Account": "Total", "Debit": jl["Debit"].sum(),
                                               "Credit": jl["Credit"].sum(), "Reverses": "", "Narrative": ""}])], ignore_index=True)
            if not any(codes.values()):
                jl = jl.drop(columns="GL code")
            text_table(jl, ["Debit", "Credit"], widths={"Narrative": "large", "Account": "medium", "#": "small"},
                       height=min(38 + 35 * len(jl), 560))
            st.caption(f"Balance entries are posted at {P.to_timestamp(how='end'):%d %b %Y} and reverse on {nxt}; the agent "
                       "reclassification is for the month only and does not reverse. Debits equal credits: "
                       f"{acct(jl['Debit'].iloc[-1])} = {acct(jl['Credit'].iloc[-1])}.")
            c1, c2 = st.columns([1, 1])
            c1.download_button("⬇️ Journal upload (CSV)", journal_csv(a, P), f"ifrs15_journals_{P}.csv", "text/csv",
                               width="stretch", type="primary")
            with c2.popover("🏷️ Set GL account codes", width="stretch"):
                st.caption("Your chart-of-accounts codes. They go into the journal upload, the Excel pack and your saved "
                           "settings profile, so you set them once.")
                gl = st.data_editor(pd.DataFrame({"Account": JOURNAL_ACCOUNTS,
                                                  "GL code": [codes.get(x, "") for x in JOURNAL_ACCOUNTS]}),
                                    hide_index=True, disabled=["Account"], key="gl_editor", width="stretch")
                if st.button("Save codes", key="save_gl", type="primary"):
                    queue(gl_codes={r["Account"]: str(r["GL code"]).strip() for r in gl.to_dict("records")
                                    if str(r["GL code"] or "").strip()})
                    st.rerun()

        st.markdown('<div class="section-title">🔁 Reconciliation to the gap</div>', unsafe_allow_html=True)
        up_prev = st.file_uploader("Optional: last month's journal upload as posted (the CSV this app produced). The "
                                   "difference from today's re-measurement is shown as a change in estimate.",
                                   type=["csv"], key="posted_upl")
        posted = None
        if up_prev is not None:
            try:
                pdf = pd.read_csv(io.BytesIO(up_prev.getvalue()), encoding="utf-8-sig")
                posted = A.posted_revenue_effect(pdf, ("Revenue",) + ((codes["Revenue"],) if codes.get("Revenue") else ()))
            except Exception as e:
                show_alert(read_error(e, "that file"), "error")
        rec = A.journal_reconciliation(a, P, posted)
        c1, c2 = st.columns([1.2, 1])
        with c1:
            text_table(rec, ["Effect on revenue"], widths={"Step": "large"})
        with c2:
            if rec.attrs.get("ties"):
                show_alert("✅ Reversing last month's entries and posting this month's changes revenue by exactly the month's "
                           "gap. Last month's entries are re-measured from today's data"
                           + ("; load what you actually posted to see any change in estimate." if posted is None else "."),
                           "success")
            else:
                show_alert("❌ The journals do not reconcile to the gap. Please report this with the data that produced it.",
                           "error")
            if posted is not None:
                ch = rec.attrs.get("change_in_estimate", 0.0)
                show_alert(f"Change in last month's estimate: <strong>{money(ch)}</strong>. "
                           + ("New information since last close (late credit notes, delivery or acceptance dates, revised "
                              "estimates) moved last month's balances; it is recognised this month."
                              if abs(ch) >= 0.5 else "Last month's posted entries still hold."),
                           "warning" if abs(ch) >= 0.5 else "info")
        g = A.gap_summary(a, P)
        eff = pd.DataFrame({"Cause": list(g["causes_month"].index), "Effect on revenue this month": g["causes_month"].values,
                            "Fiscal YTD": g["causes_ytd"].values})
        eff = eff[(eff["Effect on revenue this month"].abs() >= 0.5) | (eff["Fiscal YTD"].abs() >= 0.5)]
        eff = pd.concat([eff, pd.DataFrame([{"Cause": "Total gap", "Effect on revenue this month": g["gap_month"],
                                             "Fiscal YTD": g["gap_ytd"]}])], ignore_index=True)
        st.markdown('<div class="section-title">📈 Effect on revenue by cause</div>', unsafe_allow_html=True)
        text_table(eff, ["Effect on revenue this month", "Fiscal YTD"], widths={"Cause": "large"})

# ── JUDGEMENTS ─────────────────────────────────────────────────────────────────
with tabs[8]:
    if show(8) and need_data():
        a, P, label = ctx()
        st.markdown(f'<div class="section-title">⚖️ What each judgement is worth · {label}</div>', unsafe_allow_html=True)
        st.caption("Each judgement is changed on its own and the whole analysis is re-run. The Δ columns show the effect against "
                   "the current settings: the figures to discuss with the auditor before they ask.")
        key_ = (settings_tuple(), str(P), len(st.session_state.data))
        if st.session_state.sens is None or st.session_state.sens[0] != key_:
            st.caption("Takes about 10 to 40 seconds, depending on the size of the register. Stay on this tab until it finishes: switching tabs "
                       "stops the run.")
            if st.button("▶ Run the sensitivity analysis", type="primary", key="run_sens"):
                bar = st.progress(0.0, text="Re-running IFRS 15 under each alternative judgement…")
                tbl = A.sensitivity(st.session_state.data, st.session_state.terms if st.session_state.terms is not None
                                    else rio.normalise_terms(None), st.session_state.ssp if st.session_state.ssp is not None
                                    else rio.normalise_ssp(None), Settings(**dict(settings_tuple())), P,
                                    progress=lambda f, n: bar.progress(min(f, 1.0), text=f"Re-running: {n}"))
                st.session_state.sens = (key_, tbl)
                st.rerun()
        if st.session_state.sens is not None and st.session_state.sens[0] == key_:
            tbl = st.session_state.sens[1]
            base = tbl.iloc[0]
            show_alert(f"Current settings: IFRS 15 revenue {money(base['IFRS 15 revenue (month)'])} for {label}, "
                       f"{money(base['IFRS 15 revenue (fiscal YTD)'])} for the fiscal year to date.", "info")
            d_ = tbl.iloc[1:][["Judgement", "Δ IFRS 15 revenue (month)", "Δ IFRS 15 revenue (fiscal YTD)", "Δ Contract liabilities",
                               "Δ Refund liabilities"]]
            moved = (d_.iloc[:, 1:].abs() >= 0.5).any(axis=1)
            if moved.any():
                text_table(d_[moved], [c for c in d_.columns if c != "Judgement"], widths={"Judgement": "large"})
            if (~moved).any():
                st.caption("No effect this month: " + "; ".join(d_.loc[~moved, "Judgement"]) + ".")
            big = d_.reindex(d_["Δ IFRS 15 revenue (fiscal YTD)"].abs().sort_values(ascending=False).index).head(1)
            if len(big) and abs(float(big.iloc[0]["Δ IFRS 15 revenue (fiscal YTD)"])) >= 0.5:
                show_alert(f"The most sensitive judgement is <strong>{esc(big.iloc[0]['Judgement'])}</strong>: it would change fiscal "
                           f"YTD revenue by {money(float(big.iloc[0]['Δ IFRS 15 revenue (fiscal YTD)']))}.", "warning")
            st.caption("The table is also added to the Excel pack and the close file.")

# ── EXPORT ─────────────────────────────────────────────────────────────────────
with tabs[9]:
    if show(9) and need_data():
        a, P, label = ctx()
        from engine.exports import close_bundle, note_docx
        st.markdown(f'<div class="section-title">📤 Close file · {label}</div>', unsafe_allow_html=True)
        sset = a.model.settings
        sett = {"Review month": label, "Fiscal year": A.fy_label(a, P),
                "Fiscal year starts": MONTHS[sset.fy_start_month - 1],
                "Certainty needed (highly probable)": f"{sset.confidence:.0%}",
                "Reversal that matters (significant)": f"{sset.significance:.1%} of the revenue concerned",
                "Freight and other recharges": "Agent (net)" if sset.passthrough_agent else "Principal (gross)",
                "Set-up fees": "Distinct" if sset.setup_distinct else (
                    f"Not distinct: over at least {sset.setup_months} months" if sset.setup_months else "Not distinct: with the service"),
                "One-off services": "Over time (start to completion)" if sset.services_over_time else "On completion",
                "Discounts": "To specific obligations (82, management's conclusion)" if sset.discount_specific
                else "Pro rata to stand-alone prices",
                "Goods with no delivery date": "Not yet delivered" if sset.undelivered_if_no_date else "Delivered on the invoice date",
                "Default payment terms": f"{sset.default_payment_days} days",
                "Remaining obligations": "121(a) expedient applied" if sset.rpo_expedient else "All contracts",
                "Data source": st.session_state.data_source or "", "Lines analysed": f"{len(a.model.lines):,}"}
        sens = st.session_state.sens[1] if st.session_state.sens is not None and st.session_state.sens[0][:2] == \
            (settings_tuple(), str(P)) else None
        comp = st.session_state.company.strip() or "Company"
        with st.container(border=True):
            st.markdown("**Review and sign-off** (printed on the Excel pack's summary)")
            c1, c2 = st.columns(2)
            c1.text_input("Prepared by", key="prepared_by", placeholder="Name and date")
            c2.text_input("Reviewed by", key="reviewed_by", placeholder="Name and date")
            st.text_area("Notes for the reviewer or auditor", key="review_notes", height=80,
                         placeholder="e.g. Bill-and-hold order PRJ-BH-2603 confirmed with sales; no change to rebate tiers.")
        signoff = {"Prepared by": st.session_state.prepared_by, "Reviewed by": st.session_state.reviewed_by,
                   "Notes": st.session_state.review_notes}
        xkey = (str(P), settings_tuple(), comp, U(), sens is not None, len(a.model.lines),
                tuple(sorted((st.session_state.gl_codes or {}).items())), tuple(signoff.values()))
        if st.session_state.get("bundle") is None or st.session_state.bundle[0] != xkey:
            with st.spinner("Preparing the close file…"):
                st.session_state.bundle = (xkey, close_bundle(a, P, U(), comp, sett, sens, st.session_state.gl_codes, signoff),
                                           excel_pack(a, P, U(), comp, sett, sens, st.session_state.gl_codes, signoff),
                                           note_docx(a, P, U(), comp), schedule_csv(a, P))
        _, zbytes, xbytes, dbytes, cbytes = st.session_state.bundle
        stem = "".join(ch if ch.isalnum() else "_" for ch in comp).strip("_") or "company"
        st.download_button("⬇️ Download the whole close file (.zip): Excel pack, schedule, journals, draft note",
                           zbytes, f"{stem}_IFRS15_close_{P}.zip", "application/zip", type="primary", width="stretch")
        st.caption("Or download a single file:")
        c1, c2, c3, c4 = st.columns(4)
        c1.download_button("📊 Excel review pack", xbytes, f"{stem}_IFRS15_review_{P}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
        c2.download_button("📝 Draft note (.docx)", dbytes, f"{stem}_IFRS15_note_{P}.docx",
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document", width="stretch")
        c3.download_button("🧾 Revenue schedule (CSV)", cbytes, f"{stem}_IFRS15_schedule_{P}.csv", "text/csv", width="stretch")
        c4.download_button("🏦 Journal upload (CSV)", journal_csv(a, P), f"{stem}_IFRS15_journals_{P}.csv", "text/csv",
                           width="stretch")
        st.markdown('<div class="export-card" style="min-height:0"><div class="export-card-body"><b>The Excel pack</b> has the bridge with live '
                    'totals and check cells, the tie-out of the gap to the balances, the journals in debit/credit form with '
                    'their reconciliation, an ERP upload sheet, the gap by month, contract balances and roll-forward, remaining '
                    'obligations, disaggregation, returns, rebates, timing exceptions, bill-and-hold, acceptance, '
                    'principal-vs-agent, allocation, stand-alone prices'
                    + (", the judgement sensitivity" if sens is not None else "")
                    + ', the draft note and your sign-off.</div></div>', unsafe_allow_html=True)
        if sens is None:
            st.button("Add the judgement sensitivity to the pack → ⚖️ Judgements", on_click=go, args=(TAB_NAMES[8],))

st.markdown('<div class="app-footer">IFRS 15 Revenue Gap Analyzer · built with Python, Streamlit and Plotly · by '
            '<strong>Muhammad Talha Baig</strong> · demo data is fictional · a review aid, not a substitute for professional judgement</div>',
            unsafe_allow_html=True)
