"""Tests for the IFRS 15 engine. Run: pytest -q"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sample_data  # noqa: E402
from engine import analysis as A  # noqa: E402
from engine import io as rio  # noqa: E402
from engine import model as M  # noqa: E402
from engine.exports import excel_pack  # noqa: E402

REVIEW = pd.Period("2026-03", "M")


@pytest.fixture(scope="module")
def demo():
    reg, terms, ssp = sample_data.build()
    L = rio.normalise_register(reg, rio.guess_mapping(list(reg.columns)))
    assert L.ok, L.errors
    T, S = rio.normalise_terms(terms), rio.normalise_ssp(ssp)
    return L.data, T, S, A.analyse(L.data, T, S, M.Settings())


def _reg(rows):
    df = pd.DataFrame(rows)
    L = rio.normalise_register(df, rio.guess_mapping(list(df.columns)))
    assert L.ok, L.errors
    return L.data


# ── identities ───────────────────────────────────────────────────────────────
def test_causes_sum_to_gap(demo):
    a = demo[3]
    assert np.allclose(a.causes.sum(axis=1), a.monthly["L4"] - a.monthly["L0"], atol=1e-4)


def test_cumulative_gap_equals_balances(demo):
    a = demo[3]
    cum = (a.monthly["L4"] - a.monthly["L0"]).cumsum()
    b = a.balances
    agent = a.causes[A.C_AGENT].cumsum()
    assert np.allclose(cum, agent + b["contract_asset"] + b["unbilled_receivable"] - b["contract_liability"] - b["billed_not_due"]
                       - b["refund_liability"] - b["rebate_liability"], atol=1e-3)


def test_allocation_preserves_contract_price(demo):
    d = demo[3].model.lines
    po = d[d["pattern"].isin(["point", "over_time", "discount"])]
    g = po.groupby("contract_id")[["L1", "L2"]].sum()
    assert np.allclose(g["L1"], g["L2"])


def test_schedule_totals_equal_allocated(demo):
    m = demo[3].model
    tot = m.schedule.groupby("line_id")["amount"].sum()
    assert np.allclose(tot.reindex(m.lines["line_id"]).fillna(0.0).values, m.lines["L2"].values)


# ── planted issues in the demo ───────────────────────────────────────────────
def test_planted_cutoff_and_bill_and_hold(demo):
    a = demo[3]
    c = a.causes.loc[REVIEW]
    assert c[M.TIMING_CAUSE["cutoff"]] < -18e6
    assert c[M.TIMING_CAUSE["bill_and_hold"]] == pytest.approx(-14.7e6)
    co = A.cutoff_exceptions(a, REVIEW)
    assert {"Prime Build Distributors", "Northern Timber Traders", "Metro Furnishing Supply"} <= set(co["customer"])
    bh = A.bill_and_hold(a, REVIEW)
    assert (bh["outcome"].str.contains("criteria not met")).all() and len(bh) == 1
    assert A.bill_and_hold(a, pd.Period("2026-01", "M")).empty   # filtered to the review month
    # contract-level terms override the customer row (Skyline needs acceptance, the bill-and-hold order does not)
    bhl = a.model.lines[a.model.lines["contract_id"] == "PRJ-BH-2603"].iloc[0]
    assert bhl["cause"] == "bill_and_hold" and pd.isna(bhl["control_date"])


def test_subscriptions_and_setup_deferred(demo):
    a = demo[3]
    c = a.causes.loc[REVIEW]
    assert c[M.TIMING_CAUSE["over_time"]] < 0 and c[M.TIMING_CAUSE["setup"]] < 0
    d = a.model.lines
    sub = d[(d["kind"] == "Subscription") & (d["inv_period"] == REVIEW)].iloc[0]
    s = a.model.schedule[a.model.schedule["line_id"] == sub["line_id"]]
    assert len(s) >= 12 and s["amount"].sum() == pytest.approx(sub["L2"])


def test_agent_netting(demo):
    a = demo[3]
    d = a.model.lines
    pt = d[d["kind"] == "Pass-through"]
    assert np.allclose(pt["L1"], 0) and a.causes.loc[REVIEW, A.C_AGENT] < 0


def test_bundle_allocation_moves_revenue_to_services(demo):
    d = demo[3].model.lines
    prj = d[d["alloc_method"] == "Relative stand-alone selling price"]
    assert len(prj)
    inst = prj[prj["item_code"] == "INST"]
    dsn = prj[prj["item_code"] == "DSN"]
    assert (inst["L2"] > inst["L1"]).all() and (dsn["L2"] > 0).all()


def test_rebate_probabilities(demo):
    t = demo[3].var.rebate_table
    now = t[(t["period"] == REVIEW) & (t["fiscal_year"] == 2026)].set_index("customer")
    assert now.loc["Prime Build Distributors", "probability"] > 0.8 and now.loc["Prime Build Distributors", "accrual_rate"] == 0.03
    assert now.loc["Metro Furnishing Supply", "probability"] < 0.1 and now.loc["Metro Furnishing Supply", "accrual_rate"] == 0
    # most likely amount is no rebate, but a 10%+ chance of a significant reversal → constrained (rebate accrued)
    nt = now.loc["Northern Timber Traders"]
    assert 0.1 < nt["probability"] < 0.5 and nt["most_likely_rate"] == 0 and nt["accrual_rate"] == 0.025
    sims = demo[3].var.rebate_sims
    assert ("Prime Build Distributors", 2026, REVIEW) in sims
    assert now.loc["Coastal Hardware Co", "accrual_rate"] == 0.015


def test_return_rates_no_hindsight(demo):
    rr = demo[3].var.return_rates
    retail = rr[rr["group"] == "Retail"].set_index("period")
    assert 0.025 < retail.loc[REVIEW, "expected_rate"] < 0.05
    assert retail.loc[REVIEW, "constrained_rate"] >= retail.loc[REVIEW, "expected_rate"]
    # changing a future month's returns must not change an earlier month's rate
    d, T, S, a = demo
    d2 = d.copy()
    late = (d2["kind"] == "Return") & (d2["invoice_date"] >= "2026-02-01")
    d2.loc[late, "amount"] *= 5
    a2 = A.analyse(d2, T, S, M.Settings())
    r2 = a2.var.return_rates[a2.var.return_rates["group"] == "Retail"].set_index("period")
    assert r2.loc[pd.Period("2025-12", "M"), "constrained_rate"] == pytest.approx(retail.loc[pd.Period("2025-12", "M"), "constrained_rate"])


def test_rpo_and_rollforward(demo):
    a = demo[3]
    rpo = A.remaining_obligations(a, REVIEW)
    tot = float(rpo.loc[rpo["When"] == "Total", "Total"].iloc[0])
    assert tot >= 14.7e6        # the bill-and-hold order awaits delivery
    assert "On acceptance or delivery (date not known)" in set(rpo["When"])
    a2 = A.analyse(demo[0], demo[1], demo[2], M.Settings(rpo_expedient=False))
    rpo2 = A.remaining_obligations(a2, REVIEW)
    assert float(rpo2.loc[rpo2["When"] == "Total", "Total"].iloc[0]) > tot
    rf = A.balances_rollforward(a, REVIEW).set_index("Balance")
    assert rf.loc["Contract liabilities", "Closing"] > rf.loc["Contract liabilities", "Opening"]
    assert rf.attrs["revenue_from_opening_liability"] > 0


def test_disaggregation_totals(demo):
    a = demo[3]
    g = A.gap_summary(a, REVIEW)
    for t in A.disaggregation(a, REVIEW).values():
        tot = t[t.iloc[:, 0] == "Total revenue"]
        assert float(tot["Fiscal YTD"].iloc[0]) == pytest.approx(g["ifrs15_ytd"], rel=1e-9)
        assert float(tot["Month"].iloc[0]) == pytest.approx(g["ifrs15_month"], rel=1e-9)


def test_journals_match_balances(demo):
    a = demo[3]
    j = A.journals(a, REVIEW)
    cl = j[j["Credit account"].str.startswith("Contract liabilities")]["Amount"].sum()
    assert cl == pytest.approx(round(a.balances.loc[REVIEW, "contract_liability"]), abs=1)
    assert set(j.loc[j["Narrative"].str.contains("Pass-through"), "Reverses next month"]) == {"No"}


def test_journal_reconciliation_ties_every_month(demo):
    a = demo[3]
    for p in a.periods:
        assert A.journal_reconciliation(a, p).attrs["ties"], p


def test_excel_pack(demo):
    b = excel_pack(demo[3], REVIEW, "PKR", "Demo", {"x": 1})
    xl = pd.ExcelFile(pd.io.common.BytesIO(b))
    assert {"Summary", "Journals", "Gap by month", "Contract balances", "Remaining obligations", "Rebates", "Draft note"} <= set(xl.sheet_names)
    j = pd.read_excel(xl, "Journals", header=None)
    assert j.astype(str).apply(lambda c: c.str.contains("Debit")).any().any()
    assert "ERP upload" in xl.sheet_names and "Timing exceptions" in xl.sheet_names


def test_excel_formulas_recalculate_to_ok(demo, tmp_path):
    """Recalculate the pack in LibreOffice (when installed) and check every check cell reads OK."""
    import shutil
    import subprocess
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        pytest.skip("LibreOffice not installed")
    import openpyxl
    src = tmp_path / "pack.xlsx"
    src.write_bytes(excel_pack(demo[3], REVIEW, "PKR", "Demo", {"x": 1}))
    out = tmp_path / "out"
    prof = tmp_path / "lo_profile" / "user"
    prof.mkdir(parents=True)
    (prof / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse">'
        '<value>0</value></prop></item></oor:items>')
    subprocess.run([soffice, f"-env:UserInstallation=file://{tmp_path / 'lo_profile'}", "--headless", "--calc",
                    "--convert-to", "xlsx", "--outdir", str(out), str(src)], check=True, capture_output=True, timeout=180)
    wb = openpyxl.load_workbook(out / "pack.xlsx", data_only=True)
    checks = [c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value in ("OK", "CHECK")]
    assert len(checks) > 30 and set(checks) == {"OK"}


def test_schedule_csv(demo):
    from engine.exports import schedule_csv
    df = pd.read_csv(pd.io.common.BytesIO(schedule_csv(demo[3], REVIEW)), encoding="utf-8-sig")
    lines = df[df["Category"] != "Variable consideration"]
    assert lines["Allocated price"].sum() == pytest.approx(demo[3].model.lines.loc[
        demo[3].model.lines["line_id"].isin(lines["Line"].astype(int)), "L2"].sum(), rel=1e-9)
    assert (df["Category"] == "Variable consideration").any()


# ── unit cases ───────────────────────────────────────────────────────────────
def test_bill_and_hold_criteria_met_recognises_at_invoice():
    reg = _reg([{"Invoice Date": "2025-01-28", "Customer": "A", "Item Type": "Goods", "Net Amount": 100.0,
                 "Delivery Date": "2025-03-15", "Invoice No": "1"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Bill and Hold": "Yes", "BH Substantive Reason": "Y",
                                           "BH Separately Identified": "Y", "BH Ready for Transfer": "Y", "BH Cannot Redirect": "Y"}]))
    a = A.analyse(reg, T, None, M.Settings())
    assert a.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx(100.0)
    T2 = T.assign(bh_identified=False)
    a2 = A.analyse(reg, T2, None, M.Settings())
    assert a2.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx(0.0)


def test_acceptance_without_date_is_not_recognised():
    reg = _reg([{"Invoice Date": "2025-01-10", "Customer": "A", "Item Type": "Goods", "Net Amount": 50.0, "Delivery Date": "2025-01-12"},
                {"Invoice Date": "2025-02-10", "Customer": "B", "Item Type": "Goods", "Net Amount": 10.0, "Delivery Date": "2025-02-10"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Acceptance Required": "Yes"}]))
    a = A.analyse(reg, T, None, M.Settings())
    assert a.monthly["L4"].sum() == pytest.approx(10.0)
    rpo = A.remaining_obligations(a, pd.Period("2025-02", "M"))
    assert "On acceptance or delivery (date not known)" in set(rpo["When"])


def test_setup_fee_follows_subscription():
    reg = _reg([{"Invoice Date": "2025-01-01", "Contract ID": "C1", "Customer": "A", "Item Type": "Subscription", "Net Amount": 1200.0,
                 "Service Start": "2025-01-01", "Service End": "2025-12-31"},
                {"Invoice Date": "2025-01-01", "Contract ID": "C1", "Customer": "A", "Item Type": "Setup fee", "Net Amount": 120.0}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    assert a.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx((1200 + 120) * 31 / 365, rel=1e-6)
    a2 = A.analyse(reg, rio.normalise_terms(None), None, M.Settings(setup_distinct=True))
    assert a2.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx(1200 * 31 / 365 + 120, rel=1e-6)


def test_residual_allocation():
    reg = _reg([{"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "HW", "Item Type": "Goods",
                 "Quantity": 1, "Net Amount": 900.0, "Delivery Date": "2025-01-06"},
                {"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "SVC", "Item Type": "Subscription",
                 "Quantity": 1, "Net Amount": 100.0, "Service Start": "2025-02-01", "Service End": "2026-01-31"}])
    ssp = rio.normalise_ssp(pd.DataFrame({"Item Code": ["HW"], "Stand-alone Selling Price": [700.0]}))
    a = A.analyse(reg, rio.normalise_terms(None), ssp, M.Settings())
    d = a.model.lines.set_index("item_code")
    assert d.loc["HW", "L2"] == pytest.approx(700.0) and d.loc["SVC", "L2"] == pytest.approx(300.0)
    assert d.loc["HW", "alloc_method"].startswith("Residual approach")


def test_kind_inference_and_dates():
    assert rio.infer_kind(None, "Freight recharge") == "Pass-through"
    assert rio.infer_kind("", "Annual maintenance contract") == "Subscription"
    assert rio.infer_kind("Onboarding", None) == "Setup"
    assert rio.infer_kind(None, "Sales return credit note") == "Return"
    assert rio.infer_kind(None, "Interior design") == "Service"
    assert rio.infer_kind(None, "Annual dinner hamper") == "Goods"
    d, bad = rio.parse_dates(pd.Series(["31/01/2025", "15/02/2025", None]))
    assert d.iloc[0] == pd.Timestamp("2025-01-31") and bad == 0
    d2, _ = rio.parse_dates(pd.Series(["01/31/2025", "02/15/2025"]))
    assert d2.iloc[1] == pd.Timestamp("2025-02-15")


def test_csv_register_round_trip(demo):
    reg, terms, ssp = sample_data.build()
    data = reg.to_csv(index=False, sep=";").encode("cp1252", errors="replace")
    df, _ = rio.read_table("reg.csv", data)
    L = rio.normalise_register(df, rio.guess_mapping(list(df.columns)))
    assert L.ok and len(L.data) == len(demo[0]) and L.data["amount"].sum() == pytest.approx(demo[0]["amount"].sum())


# ── round 2 audit fixes ──────────────────────────────────────────────────────
def test_acceptance_after_bill_and_hold_and_formality():
    rows = [{"Invoice Date": "2025-01-20", "Contract ID": "K", "Customer": "A", "Item Type": "Goods", "Net Amount": 100.0,
             "Delivery Date": None, "Acceptance Date": "2025-03-05"},
            {"Invoice Date": "2025-03-31", "Contract ID": "Z", "Customer": "Z", "Item Type": "Goods", "Net Amount": 0.0}]
    reg = _reg(rows)
    base = {"Customer": "A", "Bill and Hold": "Yes", "BH Substantive Reason": "Y", "BH Separately Identified": "Y",
            "BH Ready for Transfer": "Y", "BH Cannot Redirect": "Y", "Acceptance Required": "Yes"}
    a = A.analyse(reg, rio.normalise_terms(pd.DataFrame([base])), None, M.Settings())
    assert a.monthly.loc[pd.Period("2025-03", "M"), "L4"] == pytest.approx(100.0)       # later of B81 and acceptance
    a2 = A.analyse(reg, rio.normalise_terms(pd.DataFrame([{**base, "Acceptance Formality": "Yes"}])), None, M.Settings())
    assert a2.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx(100.0)      # formality only (B84)


def test_contract_terms_take_precedence():
    reg = _reg([{"Invoice Date": "2025-01-10", "Contract ID": "K1", "Customer": "A", "Item Type": "Goods", "Net Amount": 50.0,
                 "Delivery Date": "2025-01-11"},
                {"Invoice Date": "2025-01-10", "Contract ID": "K2", "Customer": "A", "Item Type": "Goods", "Net Amount": 70.0,
                 "Delivery Date": "2025-01-11"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Acceptance Required": "Yes"},
                                          {"Customer": "A", "Contract ID": "K2", "Acceptance Required": "No"}]))
    a = A.analyse(reg, T, None, M.Settings())
    assert a.monthly["L4"].sum() == pytest.approx(70.0)


def test_discount_line_allocated_across_obligations():
    reg = _reg([{"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "HW", "Item Type": "Goods",
                 "Quantity": 1, "Net Amount": 1000.0, "Delivery Date": "2025-01-06"},
                {"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "SUP", "Item Type": "Subscription",
                 "Quantity": 1, "Net Amount": 1000.0, "Service Start": "2025-01-01", "Service End": "2025-12-31"},
                {"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "DSC", "Item Type": "Discount",
                 "Quantity": 1, "Net Amount": -200.0}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    d = a.model.lines.set_index("item_code")
    assert d.loc["HW", "L2"] == pytest.approx(900.0) and d.loc["SUP", "L2"] == pytest.approx(900.0)
    assert d.loc["DSC", "L2"] == 0 and d.loc["DSC", "pattern"] == "discount"
    assert np.allclose(a.causes.sum(axis=1), a.monthly["L4"] - a.monthly["L0"])


def test_discount_specific_option():
    reg = _reg([{"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "HW", "Item Type": "Goods",
                 "Quantity": 1, "Net Amount": 1000.0, "Delivery Date": "2025-01-06"},
                {"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "SUP", "Item Type": "Subscription",
                 "Quantity": 1, "Net Amount": 800.0, "Service Start": "2025-01-01", "Service End": "2025-12-31"}])
    ssp = rio.normalise_ssp(pd.DataFrame({"Item Code": ["HW", "SUP"], "Stand-alone Selling Price": [1000.0, 1000.0]}))
    a = A.analyse(reg, rio.normalise_terms(None), ssp, M.Settings(discount_specific=True))
    d = a.model.lines.set_index("item_code")
    assert d.loc["HW", "L2"] == pytest.approx(1000.0) and d.loc["SUP", "L2"] == pytest.approx(800.0)
    a2 = A.analyse(reg, rio.normalise_terms(None), ssp, M.Settings())
    assert a2.model.lines.set_index("item_code").loc["HW", "L2"] == pytest.approx(900.0)


def test_service_with_period_and_one_off_option():
    reg = _reg([{"Invoice Date": "2025-01-01", "Customer": "A", "Item Type": "Service", "Net Amount": 310.0,
                 "Service Start": "2025-01-01", "Service End": "2025-01-31", "Contract ID": "S1"},
                {"Invoice Date": "2025-01-01", "Customer": "B", "Item Type": "Service", "Net Amount": 590.0,
                 "Delivery Date": "2025-02-28", "Contract ID": "S2"}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    assert a.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx(310.0)
    a2 = A.analyse(reg, rio.normalise_terms(None), None, M.Settings(services_over_time=True))
    assert a2.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx(310.0 + 590.0 * 31 / 59, rel=1e-6)


def test_credit_on_subscription_keeps_kind_and_cancels_rest():
    reg = _reg([{"Invoice No": "S1", "Invoice Date": "2025-01-01", "Customer": "A", "Item Type": "Subscription", "Net Amount": 1200.0,
                 "Service Start": "2025-01-01", "Service End": "2025-12-31", "Contract ID": "C"},
                {"Invoice No": "CN1", "Invoice Date": "2025-07-01", "Customer": "A", "Item Type": "Subscription", "Net Amount": -600.0,
                 "Reference Invoice": "S1", "Contract ID": "C"},
                {"Invoice No": "Z", "Invoice Date": "2025-12-31", "Customer": "Z", "Item Type": "Goods", "Net Amount": 0.0}])
    assert set(reg.loc[reg["customer"] == "A", "kind"]) == {"Subscription"}
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    assert a.monthly["L4"].sum() == pytest.approx(600.0, rel=1e-6)
    assert a.monthly.loc[pd.Period("2025-08", "M"), "L4"] == pytest.approx(1200 * 31 / 365 - 600 * 31 / 184, rel=1e-6)


def test_revenue_before_first_invoice_month_kept():
    reg = _reg([{"Invoice Date": "2025-02-03", "Customer": "A", "Item Type": "Goods", "Net Amount": 100.0, "Delivery Date": "2025-01-30"}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    assert a.periods[0] == pd.Period("2025-01", "M")
    assert a.balances.loc[pd.Period("2025-01", "M"), "unbilled_receivable"] == pytest.approx(100.0)


def test_payment_terms_split_receivable_from_contract_liability():
    reg = _reg([{"Invoice Date": "2025-01-31", "Customer": "A", "Item Type": "Goods", "Net Amount": 100.0, "Delivery Date": "2025-03-10",
                 "Contract ID": "K"},
                {"Invoice Date": "2025-03-31", "Customer": "Z", "Item Type": "Goods", "Net Amount": 0.0, "Contract ID": "Z"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Payment Terms": "Net 15"}]))
    a = A.analyse(reg, T, None, M.Settings())
    b = a.balances
    assert b.loc[pd.Period("2025-01", "M"), "billed_not_due"] == pytest.approx(100.0)
    assert b.loc[pd.Period("2025-01", "M"), "contract_liability"] == pytest.approx(0.0)
    assert b.loc[pd.Period("2025-02", "M"), "contract_liability"] == pytest.approx(100.0)


def test_deferred_cost_of_sales():
    reg = _reg([{"Invoice Date": "2025-01-31", "Customer": "A", "Item Type": "Goods", "Net Amount": 100.0, "Cost Amount": 60.0,
                 "Delivery Date": "2025-02-02"},
                {"Invoice Date": "2025-02-28", "Customer": "Z", "Item Type": "Goods", "Net Amount": 0.0}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    assert a.balances.loc[pd.Period("2025-01", "M"), "deferred_cost"] == pytest.approx(60.0)
    assert a.balances.loc[pd.Period("2025-02", "M"), "deferred_cost"] == pytest.approx(0.0)


def test_sensitivity_runs(demo):
    reg, T, S, _ = demo
    t = A.sensitivity(reg, T, S, M.Settings(), REVIEW)
    assert t.iloc[0]["Judgement"] == "Current settings" and len(t) >= 6
    gross = t[t["Judgement"].str.contains("gross")].iloc[0]
    assert gross["Δ IFRS 15 revenue (month)"] > 0


# ── data robustness ──────────────────────────────────────────────────────────
def test_amount_formats():
    v, bad = rio.parse_amounts(pd.Series(["1.234,56", "12,5", "(1.000,00)"]))
    assert v.tolist() == [1234.56, 12.5, -1000.0] and bad == 0
    v, bad = rio.parse_amounts(pd.Series(["1,234.56", "PKR 1,00,000", "1,000-", "₹ 500", "INR 20"]))
    assert v.tolist() == [1234.56, 100000.0, -1000.0, 500.0, 20.0] and bad == 0
    v, _ = rio.parse_amounts(pd.Series(["500 CR", "200 DR", "300 CR"]))
    assert v.tolist() == [500.0, -200.0, 300.0]


def test_date_formats():
    d, bad = rio.parse_dates(pd.Series(["20260331", "2026-03-31", 46112, None]))
    assert d.iloc[0] == d.iloc[1] == pd.Timestamp("2026-03-31") and d.iloc[2] == pd.Timestamp("2026-03-31") and bad == 0
    # the day/month order is decided once for the file: 03/04 is 3 April because another column shows 13/04
    reg = _reg([{"Invoice Date": "03/04/2025", "Customer": "A", "Net Amount": 1, "Delivery Date": "13/04/2025"}])
    assert reg.loc[0, "invoice_date"] == pd.Timestamp("2025-04-03")


def test_terms_text_values_and_flags():
    T = rio.normalise_terms(pd.DataFrame({"Customer": ["A", "B", "C"], "Rebate Type": ["Tiered", "Flat", "Tiered"],
                                          "Rebate %": [1, 2, "0.5%"], "Rebate Threshold": ["PKR 200m", "1.5 crore", None],
                                          "Payment Terms": ["Net 30", "45 days", 60], "Acceptance Required": [1, 0, True]}))
    assert T["rebate_pct"].tolist() == pytest.approx([0.01, 0.02, 0.005])
    assert T["rebate_threshold"].iloc[0] == 200e6 and T["rebate_threshold"].iloc[1] == 15e6
    assert T["payment_days"].tolist() == [30, 45, 60] and T["acceptance_required"].tolist() == [True, False, True]


def test_register_cleanup():
    reg = _reg([{"Invoice No": "1", "Invoice Date": "2025-01-05", "Customer": "A", "Net Amount": 10.0, "Item Type": "Service"},
                {"Invoice No": "2", "Invoice Date": "2025-01-06", "Customer": "A", "Net Amount": -4.0, "Item Type": "Service"},
                {"Invoice No": "3", "Invoice Date": "2025-01-06", "Customer": "A", "Net Amount": -5.0, "Item Type": "Goods"},
                {"Invoice No": None, "Invoice Date": "2025-01-31", "Customer": "Grand Total", "Net Amount": 1.0},
                {"Invoice No": "4", "Invoice Date": "2025-01-07", "Customer": "Total Parco", "Net Amount": 3.0}])
    assert reg["kind"].tolist() == ["Service", "Service", "Return", "Goods"]
    assert "Total Parco" in set(reg["customer"]) and reg["item_desc"].notna().all()


def test_terms_warnings():
    reg = _reg([{"Invoice Date": "2025-01-05", "Customer": "Alpha Ltd", "Net Amount": 10.0}])
    w = rio.terms_warnings(reg, rio.normalise_terms(pd.DataFrame({"Customer": ["Alpha Limited"], "Rebate %": [2]})))
    assert w and "Alpha Limited" in w[0]


def test_terms_from_excel_round_trip(tmp_path):
    """The terms workbook read from disk (blank Contract ID cells) must behave like the in-memory terms."""
    sample_data.write(str(tmp_path))
    raw, _ = rio.read_table("t.xlsx", (tmp_path / "sample_contract_terms.xlsx").read_bytes())
    T = rio.normalise_terms(raw)
    assert all(v is None or isinstance(v, str) for v in T["contract_id"])
    lk = M.terms_lookup(T)
    assert M.line_terms(lk, "PRJ-BH-2603", "Skyline Developers")["bill_and_hold"]
    assert M.line_terms(lk, "PRJ-2401", "Skyline Developers")["acceptance_required"]
    reg_raw, _ = rio.read_table("r.xlsx", (tmp_path / "sample_sales_register.xlsx").read_bytes())
    L = rio.normalise_register(reg_raw, rio.guess_mapping(list(reg_raw.columns)))
    assert rio.terms_warnings(L.data, T) == []


# ── round 2 regressions ──────────────────────────────────────────────────────
Z = {"Customer": "Z", "Item Type": "Goods", "Net Amount": 0.0, "Contract ID": "ZZ"}   # filler to extend the month range


def test_credit_note_in_bundle_is_not_allocated():
    reg = _reg([{"Invoice No": "1", "Invoice Date": "2025-01-10", "Contract ID": "K", "Customer": "A", "Item Code": "HW",
                 "Item Type": "Goods", "Net Amount": 1000.0, "Delivery Date": "2025-01-10"},
                {"Invoice No": "1", "Invoice Date": "2025-01-10", "Contract ID": "K", "Customer": "A", "Item Code": "INS",
                 "Item Type": "Service", "Net Amount": 500.0, "Delivery Date": "2025-02-10"},
                {"Invoice No": "2", "Invoice Date": "2025-03-05", "Contract ID": "K", "Customer": "A", "Item Code": "INS",
                 "Item Type": "Service", "Net Amount": -50.0, "Delivery Date": "2025-03-05"}])
    ssp = rio.normalise_ssp(pd.DataFrame({"Item Code": ["HW", "INS"], "Stand-alone Selling Price": [900.0, 500.0]}))
    a = A.analyse(reg, rio.normalise_terms(None), ssp, M.Settings())
    d = a.model.lines
    assert d.loc[d["amount"] < 0, "L2"].iloc[0] == pytest.approx(-50.0)
    assert a.monthly["L4"].sum() == pytest.approx(1450.0)
    assert not any("above the contract price" in w for w in a.warnings)


def test_contract_asset_when_other_obligations_remain():
    rows = [{"Invoice No": "H", "Invoice Date": "2025-01-31", "Contract ID": "K", "Customer": "A", "Item Code": "HW",
             "Item Type": "Goods", "Net Amount": 300.0, "Delivery Date": "2025-01-05"}]
    rows += [{"Invoice No": f"S{m}", "Invoice Date": f"2025-{m:02d}-28", "Contract ID": "K", "Customer": "A", "Item Code": "SUP",
              "Item Type": "Subscription", "Net Amount": 100.0, "Service Start": f"2025-{m:02d}-01",
              "Service End": f"2025-{m:02d}-28"} for m in (1, 2, 3)]
    ssp = rio.normalise_ssp(pd.DataFrame({"Item Code": ["HW", "SUP"], "Stand-alone Selling Price": [600.0, 100.0]}))
    a = A.analyse(_reg(rows), rio.normalise_terms(None), ssp, M.Settings())
    b = a.balances.loc[pd.Period("2025-01", "M")]
    assert b["contract_asset"] > 0 and b["unbilled_receivable"] == pytest.approx(0.0)


def test_single_obligation_billed_in_arrears_is_a_receivable():
    reg = _reg([{"Invoice Date": "2025-02-03", "Customer": "A", "Item Type": "Goods", "Net Amount": 100.0,
                 "Delivery Date": "2025-01-30", "Contract ID": "K"}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    assert a.balances.loc[pd.Period("2025-01", "M"), "unbilled_receivable"] == pytest.approx(100.0)


def test_later_discount_is_a_catch_up_not_a_restatement():
    reg = _reg([{"Invoice No": "1", "Invoice Date": "2025-01-10", "Contract ID": "K", "Customer": "A", "Item Code": "HW",
                 "Item Type": "Goods", "Net Amount": 1000.0, "Delivery Date": "2025-01-10"},
                {"Invoice No": "1", "Invoice Date": "2025-01-10", "Contract ID": "K", "Customer": "A", "Item Code": "SUP",
                 "Item Type": "Subscription", "Net Amount": 1200.0, "Service Start": "2025-01-01", "Service End": "2025-12-31"},
                {"Invoice No": "2", "Invoice Date": "2025-03-15", "Contract ID": "K", "Customer": "A", "Item Code": "DSC",
                 "Item Type": "Discount", "Net Amount": -220.0}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    jan = a.monthly.loc[pd.Period("2025-01", "M"), "L4"]
    assert jan == pytest.approx(1000 + 1200 * 31 / 365, rel=1e-6)          # January is not restated
    assert np.allclose(a.causes.sum(axis=1), a.monthly["L4"] - a.monthly["L0"])
    sch = a.model.schedule.groupby("line_id")["amount"].sum()
    assert sch.sum() == pytest.approx(1000 + 1200 - 220)


def test_not_yet_due_is_tested_line_by_line():
    reg = _reg([{"Invoice No": "S", "Invoice Date": "2025-01-01", "Contract ID": "K", "Customer": "A", "Item Code": "SUP",
                 "Item Type": "Subscription", "Net Amount": 1200.0, "Service Start": "2025-01-01", "Service End": "2025-12-31"},
                {"Invoice No": "G", "Invoice Date": "2025-03-20", "Contract ID": "K", "Customer": "A", "Item Code": "HW",
                 "Item Type": "Goods", "Net Amount": 5000.0, "Delivery Date": "2025-03-21"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Payment Terms": "Net 30"}]))
    a = A.analyse(reg, T, None, M.Settings())
    b = a.balances.loc[pd.Period("2025-03", "M")]
    assert b["billed_not_due"] == pytest.approx(0.0, abs=1e-6)
    assert b["contract_liability"] > 800


def test_rebate_paid_in_instalments_after_year_end():
    rows = [{"Invoice Date": f"{y}-{m:02d}-15", "Customer": "R", "Item Type": "Goods", "Net Amount": 10000.0,
             "Delivery Date": f"{y}-{m:02d}-15"} for y, m in [(2024, 7), (2024, 10), (2025, 1), (2025, 4), (2025, 6),
                                                              (2025, 7), (2025, 8), (2025, 9), (2025, 10)]]
    rows += [{"Invoice Date": "2025-08-10", "Customer": "R", "Item Type": "Rebate", "Net Amount": -750.0},
             {"Invoice Date": "2025-09-10", "Customer": "R", "Item Type": "Rebate", "Net Amount": -750.0}]
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "R", "Rebate Type": "Flat", "Rebate %": 3}]))
    a = A.analyse(_reg(rows), T, None, M.Settings())
    rt = a.var.rebate_table
    fy26 = rt[(rt["fiscal_year"] == 2026) & (rt["period"] == pd.Period("2025-10", "M"))].iloc[0]
    assert fy26["settled"] == 0 and fy26["liability"] == pytest.approx(0.03 * 40000)


def test_setup_relationship_is_at_least_the_service_term():
    reg = _reg([{"Invoice Date": "2025-01-01", "Contract ID": "C", "Customer": "A", "Item Type": "Subscription",
                 "Net Amount": 2400.0, "Service Start": "2025-01-01", "Service End": "2026-12-31"},
                {"Invoice Date": "2025-01-01", "Contract ID": "C", "Customer": "A", "Item Type": "Setup", "Net Amount": 240.0}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings(setup_months=6))
    st_ = a.model.lines[a.model.lines["kind"] == "Setup"].iloc[0]
    assert st_["end"] == pd.Timestamp("2026-12-31") and st_["alloc_method"] == ""


def test_licence_recognised_on_delivery_even_if_invoiced_later():
    reg = _reg([{"Invoice Date": "2025-04-05", "Customer": "A", "Item Type": "Licence", "Net Amount": 100.0,
                 "Delivery Date": "2025-03-10"}])
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    assert a.monthly.loc[pd.Period("2025-03", "M"), "L4"] == pytest.approx(100.0)


def test_116b_measured_line_by_line():
    reg = _reg([{"Invoice No": "1", "Invoice Date": "2025-05-10", "Contract ID": "K", "Customer": "A", "Item Code": "A",
                 "Item Type": "Goods", "Net Amount": 1000.0, "Delivery Date": "2025-05-12"},
                {"Invoice No": "2", "Invoice Date": "2025-08-10", "Contract ID": "K", "Customer": "A", "Item Code": "B",
                 "Item Type": "Goods", "Net Amount": 800.0, "Delivery Date": "2025-08-11"},
                {**Z, "Invoice Date": "2025-09-30"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Contract ID": "K", "Acceptance Required": "Yes",
                                           "Payment Terms": 0}]))
    a = A.analyse(reg, T, None, M.Settings())
    rf = A.balances_rollforward(a, pd.Period("2025-09", "M"))
    assert rf.attrs["revenue_from_opening_liability"] == pytest.approx(0.0)


def test_degenerate_registers_do_not_crash():
    for rows in ([{"Invoice Date": "2025-01-01", "Customer": "A", "Net Amount": -1000.0},
                  {"Invoice Date": "2025-02-01", "Customer": "B", "Net Amount": -2000.0}],
                 [{"Invoice Date": "2025-01-01", "Customer": "A", "Item Type": "Freight", "Net Amount": 50.0}],
                 [{"Invoice Date": "2025-01-01", "Customer": "A", "Item Type": "Subscription", "Net Amount": 1200.0,
                   "Service Start": "2025-01-01", "Service End": "9999-12-31"}]):
        reg = _reg(rows)
        a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
        p = a.periods[-1]
        for f in ["timing_exceptions", "bill_and_hold", "acceptance_pending", "agent_screening", "balances_rollforward",
                  "remaining_obligations", "journals", "journal_reconciliation", "contract_balances_at", "contracts_ranked",
                  "disaggregation", "financing_flags", "deferred_by_reason", "other_balances"]:
            getattr(A, f)(a, p)
        A.draft_note(a, p, "PKR", str)
        excel_pack(a, p, "PKR", "x", {})


def test_credit_note_joins_the_referenced_contract_and_period():
    reg = _reg([{"Invoice No": "S1", "Invoice Date": "2025-01-01", "Customer": "A", "Item Type": "Subscription",
                 "Net Amount": 12000.0, "Service Start": "2025-01-01", "Service End": "2025-12-31"},
                {"Invoice No": "CN1", "Invoice Date": "2025-03-01", "Customer": "A", "Item Type": "Subscription",
                 "Net Amount": -3000.0, "Reference Invoice": "S1"},
                {"Invoice No": "CN2", "Invoice Date": "2025-04-15", "Customer": "A", "Item Type": "Subscription",
                 "Net Amount": -600.0}])
    assert reg.loc[reg["invoice_no"] == "CN1", "contract_id"].iloc[0] == "S1"
    a = A.analyse(reg, rio.normalise_terms(None), None, M.Settings())
    d = a.model.lines.set_index("invoice_no")
    assert d.loc["CN1", "end"] == pd.Timestamp("2025-12-31")


def test_customer_names_are_merged_and_terms_aligned():
    reg = _reg([{"Invoice Date": "2025-01-05", "Customer": "Acme Ltd ", "Net Amount": 1000.0},
                {"Invoice Date": "2025-01-06", "Customer": "ACME LTD", "Net Amount": 1000.0},
                {"Invoice Date": "2025-01-07", "Customer": "acme  ltd", "Net Amount": 1000.0},
                {"Invoice Date": "2025-01-08", "Customer": "Acme Ltd", "Net Amount": 3000.0}])
    assert reg["customer"].nunique() == 1
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "acme ltd", "Rebate Type": "Flat", "Rebate %": "3%"}]))
    a = A.analyse(reg, T, None, M.Settings())
    assert a.balances["rebate_liability"].iloc[-1] == pytest.approx(180.0)


def test_rebate_pct_mixed_text_and_numbers():
    T = rio.normalise_terms(pd.DataFrame({"Customer": ["A", "B", "C"], "Rebate %": ["1", 0.02, "0.5"]}))
    assert T["rebate_pct"].tolist() == pytest.approx([0.01, 0.02, 0.005])


def test_payment_terms_words():
    T = rio.normalise_terms(pd.DataFrame({"Customer": ["A", "B", "C"], "Payment Terms": ["2 months", "Advance", "Net 45"]}))
    assert T["payment_days"].tolist() == [60, 0, 45]


def test_journals_tie_in_every_month_of_random_registers():
    rng = np.random.default_rng(2)
    kinds = ["Goods", "Service", "Subscription", "Setup", "Freight", "Discount", "Return"]
    rows = []
    for i in range(300):
        k = kinds[rng.integers(len(kinds))]
        dt = pd.Timestamp("2024-01-01") + pd.Timedelta(days=int(rng.integers(0, 500)))
        amt = round(float(rng.uniform(10, 5000)), 2) * (-1 if k in ("Discount", "Return") else 1)
        rows.append({"Invoice No": f"I{i // 3}", "Invoice Date": dt.strftime("%Y-%m-%d"), "Contract ID": f"K{i // 4}",
                     "Customer": f"C{i % 7}", "Item Code": f"{k[:3]}{i % 5}", "Item Type": k, "Net Amount": amt,
                     "Delivery Date": (dt + pd.Timedelta(days=int(rng.integers(-5, 40)))).strftime("%Y-%m-%d")})
    a = A.analyse(_reg(rows), rio.normalise_terms(None), None, M.Settings())
    assert np.allclose(a.causes.sum(axis=1), a.monthly["L4"] - a.monthly["L0"], atol=1e-6)
    for p in a.periods:
        assert A.journal_reconciliation(a, p).attrs["ties"], p


def test_order_book_in_remaining_obligations():
    reg = _reg([{"Invoice Date": "2025-01-01", "Contract ID": "K", "Customer": "A", "Item Type": "Subscription",
                 "Net Amount": 1200.0, "Service Start": "2025-01-01", "Service End": "2026-12-31"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Contract ID": "K", "Contract Value": "PKR 5,000"}]))
    rpo = A.remaining_obligations(A.analyse(reg, T, None, M.Settings(rpo_expedient=False)), pd.Period("2025-01", "M"))
    assert float(rpo.loc[rpo["When"] == "Total", "Not yet scheduled"].iloc[0]) == pytest.approx(3800.0)
    assert float(rpo.loc[rpo["When"] == "Total", "Total"].iloc[0]) == pytest.approx(5000 - 1200 * 31 / 730, rel=1e-6)


# ── round 3 regressions ──────────────────────────────────────────────────────
def test_open_ended_date_warning_and_empty_file():
    df = pd.DataFrame([{"Invoice Date": "2025-01-01", "Customer": "A", "Item Type": "Subscription", "Net Amount": 1200.0,
                        "Service Start": "2025-01-01", "Service End": "31.12.9999"}])
    L = rio.normalise_register(df, rio.guess_mapping(list(df.columns)))
    assert any("open-ended" in w for w in L.warnings)
    e = rio.normalise_register(pd.DataFrame(columns=["Invoice Date", "Customer", "Net Amount"]),
                               {"invoice_date": "Invoice Date", "customer": "Customer", "amount": "Net Amount"})
    assert "no data rows" in e.errors[0]


def test_cr_prefix():
    v, bad = rio.parse_amounts(pd.Series(["CR 500", "1,000", "2,000", "300"]))
    assert bad == 0 and v.tolist() == [-500.0, 1000.0, 2000.0, 300.0]


def test_order_book_respects_expedient():
    reg = _reg([{"Invoice Date": "2025-01-10", "Contract ID": "K", "Customer": "A", "Item Type": "Goods", "Net Amount": 2000.0,
                 "Delivery Date": "2025-01-10"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Contract ID": "K", "Contract Value": 10000, "Contract Term": "6 months"}]))
    rpo = A.remaining_obligations(A.analyse(reg, T, None, M.Settings()), pd.Period("2025-01", "M"))
    assert rpo.attrs["order_book"] == 0 and rpo.attrs["excluded_short"] == pytest.approx(8000.0)
    T2 = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Contract ID": "K", "Contract Value": 10000}]))
    rpo2 = A.remaining_obligations(A.analyse(reg, T2, None, M.Settings()), pd.Period("2025-01", "M"))
    assert rpo2.attrs["order_book"] == pytest.approx(8000.0)          # no term given: not assumed to be short


def test_small_balances_still_journalled():
    reg = _reg([{"Invoice Date": "2025-01-10", "Customer": "A", "Item Type": "Goods", "Net Amount": 10.0}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Rebate Type": "Flat", "Rebate %": 2}]))
    a = A.analyse(reg, T, None, M.Settings())
    assert A.journal_reconciliation(a, pd.Period("2025-01", "M")).attrs["ties"]


def test_contract_row_rebate_applies_when_customer_row_has_none():
    reg = _reg([{"Invoice Date": "2025-01-10", "Contract ID": "K", "Customer": "A", "Item Type": "Goods", "Net Amount": 1000.0}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Payment Terms": 30},
                                          {"Customer": "A", "Contract ID": "K", "Rebate Type": "Flat", "Rebate %": 5}]))
    a = A.analyse(reg, T, None, M.Settings())
    assert a.balances["rebate_liability"].iloc[-1] == pytest.approx(50.0)
    assert any("contract row" in w for w in a.warnings)


# ── round 4: claims audit ────────────────────────────────────────────────────
def test_contract_terms_do_not_leak_to_other_contracts():
    reg = _reg([{"Invoice Date": "2025-01-10", "Contract ID": "K1", "Customer": "A", "Item Type": "Goods", "Net Amount": 100.0,
                 "Delivery Date": "2025-01-11"},
                {"Invoice Date": "2025-01-10", "Contract ID": "K2", "Customer": "A", "Item Type": "Goods", "Net Amount": 5000.0,
                 "Delivery Date": "2025-01-11"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Contract ID": "K1", "Acceptance Required": "Yes"}]))
    a = A.analyse(reg, T, None, M.Settings())
    assert a.monthly.loc[pd.Period("2025-01", "M"), "L4"] == pytest.approx(5000.0)


def test_reallocation_checked_line_by_line():
    reg = _reg([{"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "HW", "Item Type": "Goods",
                 "Net Amount": 1000.0, "Delivery Date": "2025-01-05"},
                {"Invoice Date": "2025-01-05", "Contract ID": "K", "Customer": "A", "Item Code": "MNT", "Item Type": "Subscription",
                 "Net Amount": 200.0, "Service Start": "2025-01-01", "Service End": "2025-12-31"}])
    ssp = rio.normalise_ssp(pd.DataFrame({"Item Code": ["HW", "MNT"], "Stand-alone Selling Price": [600.0, 600.0]}))
    d = A.analyse(reg, rio.normalise_terms(None), ssp, M.Settings()).model.lines.set_index("item_code")
    assert d.loc["HW", "L2"] == pytest.approx(600.0) and d.loc["MNT", "L2"] == pytest.approx(600.0)


def test_return_rates_use_only_returns_received_before_the_month(demo):
    reg, T, S, a = demo
    late = pd.DataFrame([{**reg[reg["kind"] == "Return"].iloc[0].to_dict()}])
    sale = reg[(reg["customer_group"] == "Retail") & (reg["kind"] == "Goods") & (reg["inv_period"] == pd.Period("2024-09", "M"))
               if "inv_period" in reg else (reg["customer_group"] == "Retail") & (reg["kind"] == "Goods")
               & (reg["invoice_date"].dt.to_period("M") == pd.Period("2024-09", "M"))].iloc[0]
    late.loc[0, ["invoice_date", "amount", "ref_invoice", "customer", "customer_group", "invoice_no", "line_id"]] = \
        [pd.Timestamp("2026-03-20"), -5e6, sale["invoice_no"], sale["customer"], "Retail", "LATE-1", reg["line_id"].max() + 1]
    a2 = A.analyse(pd.concat([reg, late], ignore_index=True), T, S, M.Settings())
    r1 = a.var.return_rates.set_index(["group", "period"])
    r2 = a2.var.return_rates.set_index(["group", "period"])
    k = ("Retail", pd.Period("2025-06", "M"))
    assert r2.loc[k, "expected_rate"] == pytest.approx(r1.loc[k, "expected_rate"])


def test_rebate_simulation_counts_months_without_purchases():
    rows = [{"Invoice Date": f"{y}-{m:02d}-15", "Customer": "R", "Item Type": "Goods", "Net Amount": 150.0,
             "Delivery Date": f"{y}-{m:02d}-15"} for y, m in [(2024, 7), (2024, 9), (2024, 11), (2025, 1), (2025, 3), (2025, 5),
                                                              (2025, 7), (2025, 9)]]
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "R", "Rebate Type": "Tiered", "Rebate %": 5, "Rebate Threshold": 1000}]))
    a = A.analyse(_reg(rows), T, None, M.Settings())
    rt = a.var.rebate_table
    p = rt[(rt["fiscal_year"] == 2026) & (rt["period"] == pd.Period("2025-09", "M"))].iloc[0]["probability"]
    assert p < 0.5


def test_long_contract_billed_annually_is_not_excluded_by_121a():
    reg = _reg([{"Invoice Date": "2025-01-01", "Contract ID": "K", "Customer": "A", "Item Type": "Subscription",
                 "Net Amount": 1200.0, "Service Start": "2025-01-01", "Service End": "2025-12-31"}])
    T = rio.normalise_terms(pd.DataFrame([{"Customer": "A", "Contract ID": "K", "Contract Value": 3600, "Contract Term": "3 years"}]))
    rpo = A.remaining_obligations(A.analyse(reg, T, None, M.Settings()), pd.Period("2025-01", "M"))
    tot = float(rpo.loc[rpo["When"] == "Total", "Total"].iloc[0])
    assert tot == pytest.approx(3600 - 1200 * 31 / 365, rel=1e-6) and rpo.attrs["excluded_short"] == 0


def test_three_decimal_currency_amounts():
    v, bad = rio.parse_amounts(pd.Series(["KWD 1.250", "KWD 10.750", "KWD 2.000"]))
    assert bad == 0 and v.tolist() == pytest.approx([1.25, 10.75, 2.0])
    v, _ = rio.parse_amounts(pd.Series(["1.234,56", "1.250"]))
    assert v.tolist() == pytest.approx([1234.56, 1250.0])


def test_utf16_csv_without_bom():
    df = pd.DataFrame({"Invoice Date": ["2025-01-05"], "Customer": ["A"], "Net Amount": ["100"]})
    data = df.to_csv(index=False, sep="\t").encode("utf-16-le")
    raw, _ = rio.read_table("x.txt", data)
    assert list(raw.columns) == ["Invoice Date", "Customer", "Net Amount"]
