"""
sample_data.py — a fictional company's sales register, contract terms and price list for the demo.

Cedar Panels Ltd (fictional) manufactures laminated and veneered boards, sells fittings, installs fit-out
projects, and runs a dealer portal sold as an annual subscription. July 2023 – March 2026, fiscal year
July–June, PKR, amounts net of sales tax.

The books recognise every invoice line as revenue on the invoice date (a common invoice-based policy).
Planted IFRS 15 issues, concentrated in the review month (March 2026):
  • month-end invoices for goods dispatched in April (cut-off)
  • a bill-and-hold invoice whose goods are not separately identified (criteria not met)
  • annual portal subscriptions and maintenance contracts billed upfront (should be deferred)
  • onboarding fees recognised upfront although not a distinct service
  • freight recharged at cost and booked gross (the company is an agent for the carrier)
  • project bundles sold at a package discount (allocation by stand-alone selling price)
  • a distributor on course to reach its annual rebate tier with nothing accrued
  • product returns booked only when credit notes are issued, a month after the sale
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

RNG = np.random.default_rng(15)
START, END = pd.Timestamp("2023-07-01"), pd.Timestamp("2026-03-31")
MONTHS = pd.period_range(START, END, freq="M")
REVIEW = pd.Period("2026-03", "M")

PRODUCTS = {  # code: (description, type, list price, unit cost)
    "LB-18": ("Laminated Board 18mm", "Goods", 6500, 4300),
    "PB-16": ("Particle Board 16mm", "Goods", 4200, 2900),
    "VN-01": ("Veneered Panel Walnut", "Goods", 9800, 6300),
    "HW-KIT": ("Hardware Fittings Kit", "Goods", 1200, 700),
    "INST": ("Installation Service (per project)", "Service", 450000, 280000),
    "DSN": ("Design & Site Survey Fee", "Setup", 150000, 60000),
    "AMC": ("Annual Maintenance Contract (12 months)", "Subscription", 240000, 90000),
    "PRT-SUB": ("Dealer Portal Subscription (12 months)", "Subscription", 180000, 40000),
    "PRT-ONB": ("Dealer Portal Onboarding Fee", "Setup", 60000, 20000),
    "EXW-2": ("Extended Warranty 24 months", "Warranty", 85000, 25000),
    "FRT": ("Freight Recharge (third-party carrier)", "Pass-through", 0, 0),
}
SEAS = np.array([1.00, 1.02, 1.05, 1.08, 1.10, 1.04, 0.92, 0.95, 1.06, 1.00, 0.94, 0.88])  # Jul..Jun


def _season(p: pd.Period) -> float:
    return float(SEAS[(p.month - 7) % 12])


def _days(p: pd.Period) -> int:
    return p.days_in_month


def build():
    global RNG
    RNG = np.random.default_rng(15)          # same data on every call
    rows = []
    inv_no = [100000]

    def new_inv():
        inv_no[0] += 1
        return f"INV-{inv_no[0]}"

    def add(inv, date, contract, cust, group, code, qty, price=None, delivery=None, s_start=None, s_end=None,
            acceptance=None, cost=None, ref=None, amount=None, desc=None, typ=None):
        d, t, lp, uc = PRODUCTS.get(code, (desc or code, typ or "Goods", 0, 0))
        price = lp if price is None else price
        amt = round(qty * price, 0) if amount is None else round(amount, 0)
        rows.append({
            "Invoice No": inv, "Invoice Date": date.date(), "Contract ID": contract, "Customer": cust, "Customer Group": group,
            "Item Code": code, "Item Description": desc or d, "Item Type": typ or t, "Quantity": qty, "Unit Price": price,
            "Net Amount": amt, "Delivery Date": delivery.date() if delivery is not None else None,
            "Service Start": s_start.date() if s_start is not None else None, "Service End": s_end.date() if s_end is not None else None,
            "Acceptance Date": acceptance.date() if acceptance is not None else None,
            "Cost Amount": round(cost, 0) if cost is not None else (round(qty * uc, 0) if uc else None), "Reference Invoice": ref,
        })
        return amt

    distributors = {"Prime Build Distributors": 1.0, "Northern Timber Traders": 0.8, "Coastal Hardware Co": 0.55,
                    "Metro Furnishing Supply": 0.42}
    retailers = [f"Retail Outlet {c}" for c in "ABCDEFGH"]
    subs_customers = [f"Dealer {n}" for n in ["Faisal", "Hyder", "Ravi", "Indus", "Margalla", "Kohinoor", "Saddar", "Gulberg",
                                               "Clifton", "Chenab", "Jhelum", "Potohar"]]
    sales_by_month_cust = {}

    for i, p in enumerate(MONTHS):
        g = 1.0 + 0.09 * i / 12               # ~9% annual growth
        s = _season(p)
        last = p.to_timestamp(how="end").normalize()

        # distributors: weekly invoices, goods
        for cust, size in distributors.items():
            for w in range(4):
                day = min(3 + w * 7 + int(RNG.integers(0, 4)), _days(p))
                date = pd.Timestamp(year=p.year, month=p.month, day=day)
                inv = new_inv()
                for code, base in [("LB-18", 260), ("PB-16", 320), ("VN-01", 60), ("HW-KIT", 300)]:
                    q = max(1, int(base * size * g * s * RNG.normal(1, 0.12)))
                    amt = add(inv, date, inv, cust, "Distributor", code, q, delivery=date + pd.Timedelta(days=int(RNG.integers(0, 3))))
                    sales_by_month_cust[(p, cust)] = sales_by_month_cust.get((p, cust), 0) + amt
                    if RNG.random() < 0.5:
                        add(inv, date, inv, cust, "Distributor", "FRT", 1, price=round(amt * 0.025, -2), amount=round(amt * 0.025, -2),
                            delivery=date, cost=round(amt * 0.025, -2))

        # retail outlets: goods with return rights
        for cust in retailers:
            for k in range(2):
                day = min(5 + k * 12 + int(RNG.integers(0, 5)), _days(p))
                date = pd.Timestamp(year=p.year, month=p.month, day=day)
                inv = new_inv()
                for code, base in [("LB-18", 22), ("VN-01", 9), ("HW-KIT", 40)]:
                    q = max(1, int(base * g * s * RNG.normal(1, 0.2)))
                    add(inv, date, inv, cust, "Retail", code, q, delivery=date + pd.Timedelta(days=int(RNG.integers(0, 4))))

        # normal month-end cut-off noise: a few last-day invoices delivered next month
        for _ in range(2):
            cust = list(distributors)[int(RNG.integers(0, 4))]
            inv = new_inv()
            q = int(120 * RNG.uniform(0.6, 1.4))
            add(inv, last, inv, cust, "Distributor", "LB-18", q, delivery=last + pd.Timedelta(days=int(RNG.integers(1, 4))))

        # projects: bundle of boards + installation + design fee, discounted package
        if i % 2 == 0:
            cust = ["Skyline Developers", "Harbour Hotels", "Greenfield Offices"][i % 3]
            date = pd.Timestamp(year=p.year, month=p.month, day=int(RNG.integers(8, 20)))
            inv = new_inv(); con = f"PRJ-{p.strftime('%y%m')}"
            q1, q2 = int(900 * RNG.uniform(0.8, 1.2)), int(300 * RNG.uniform(0.8, 1.2))
            # package price: boards at list, installation at 40% off, design and site survey included free of charge
            done = date + pd.Timedelta(days=int(RNG.integers(25, 50)))
            acc = done + pd.Timedelta(days=7) if cust == "Skyline Developers" else None   # Skyline contracts need sign-off
            add(inv, date, con, cust, "Project", "LB-18", q1, delivery=date + pd.Timedelta(days=5), acceptance=acc)
            add(inv, date, con, cust, "Project", "VN-01", q2, delivery=date + pd.Timedelta(days=5), acceptance=acc)
            add(inv, date, con, cust, "Project", "INST", 1, price=270000, delivery=done, acceptance=acc)
            add(inv, date, con, cust, "Project", "DSN", 1, price=0, delivery=date, acceptance=acc)

        # dealer portal subscriptions (annual, billed upfront) + onboarding for new dealers; AMCs
        n_new = 1 + (1 if RNG.random() < 0.35 else 0)
        for k in range(n_new):
            cust = subs_customers[(i * 2 + k) % len(subs_customers)]
            date = pd.Timestamp(year=p.year, month=p.month, day=int(RNG.integers(2, 26)))
            inv = new_inv(); con = f"SUB-{inv[4:]}"
            start = date + pd.Timedelta(days=int(RNG.integers(0, 10)))
            end = start + pd.DateOffset(years=1) - pd.Timedelta(days=1)
            add(inv, date, con, cust, "Corporate", "PRT-SUB", 1, s_start=start, s_end=end)
            if i < 12 or RNG.random() < 0.5:
                add(inv, date, con, cust, "Corporate", "PRT-ONB", 1, delivery=start)
        if i % 3 == 1:
            cust = ["Harbour Hotels", "Greenfield Offices", "Skyline Developers"][i % 3]
            date = pd.Timestamp(year=p.year, month=p.month, day=int(RNG.integers(2, 26)))
            inv = new_inv()
            add(inv, date, f"AMC-{inv[4:]}", cust, "Project", "AMC", 1, s_start=date, s_end=date + pd.DateOffset(years=1) - pd.Timedelta(days=1))
        if i % 4 == 2:
            date = pd.Timestamp(year=p.year, month=p.month, day=int(RNG.integers(2, 26)))
            inv = new_inv()
            add(inv, date, inv, "Retail Outlet C", "Retail", "EXW-2", 2, s_start=date, s_end=date + pd.DateOffset(years=2) - pd.Timedelta(days=1))

    reg = pd.DataFrame(rows)

    # returns: retail ~3.5% and distributors ~1% of the month's goods sales, credited the following month
    goods = reg[reg["Item Type"] == "Goods"].copy()
    goods["month"] = pd.to_datetime(goods["Invoice Date"]).dt.to_period("M")
    ret_rows = []
    for (m, cust, grp), gdf in goods.groupby(["month", "Customer", "Customer Group"]):
        if grp not in ("Retail", "Distributor"):
            continue
        rate = RNG.normal(0.035, 0.012) if grp == "Retail" else RNG.normal(0.010, 0.004)
        rate = max(rate, 0.0)
        val = gdf["Net Amount"].sum() * rate
        if val < 1000 or m + 1 > MONTHS[-1]:
            continue
        nm = m + 1
        date = pd.Timestamp(year=nm.year, month=nm.month, day=int(RNG.integers(5, 25)))
        src = gdf.iloc[0]
        ret_rows.append({"Invoice No": f"CN-{len(ret_rows) + 5001}", "Invoice Date": date.date(), "Contract ID": src["Contract ID"],
                         "Customer": cust, "Customer Group": grp, "Item Code": "RET", "Item Description": "Sales return credit note",
                         "Item Type": "Return", "Quantity": -1, "Unit Price": round(val, 0), "Net Amount": -round(val, 0),
                         "Delivery Date": None, "Service Start": None, "Service End": None, "Acceptance Date": None,
                         "Cost Amount": -round(val * 0.66, 0), "Reference Invoice": src["Invoice No"]})
    reg = pd.concat([reg, pd.DataFrame(ret_rows)], ignore_index=True)

    terms = pd.DataFrame([
        {"Customer": "Prime Build Distributors", "Rebate Type": "Tiered", "Rebate %": 3.0, "Rebate Threshold": 210_000_000,
         "Return Rights": "Yes", "Acceptance Required": "No", "Bill and Hold": "No", "Payment Terms Days": 60},
        {"Customer": "Northern Timber Traders", "Rebate Type": "Tiered", "Rebate %": 2.5, "Rebate Threshold": 200_000_000,
         "Return Rights": "Yes", "Acceptance Required": "No", "Bill and Hold": "No", "Payment Terms Days": 60},
        {"Customer": "Coastal Hardware Co", "Rebate Type": "Flat", "Rebate %": 1.5, "Rebate Threshold": None,
         "Return Rights": "Yes", "Acceptance Required": "No", "Bill and Hold": "No", "Payment Terms Days": 45},
        {"Customer": "Metro Furnishing Supply", "Rebate Type": "Tiered", "Rebate %": 2.0, "Rebate Threshold": 118_000_000,
         "Return Rights": "Yes", "Acceptance Required": "No", "Bill and Hold": "No", "Payment Terms Days": 45},
        *[{"Customer": r, "Rebate Type": "None", "Rebate %": None, "Rebate Threshold": None, "Return Rights": "Yes",
           "Acceptance Required": "No", "Bill and Hold": "No", "Payment Terms Days": 30} for r in [f"Retail Outlet {c}" for c in "ABCDEFGH"]],
        {"Customer": "Skyline Developers", "Rebate Type": "None", "Return Rights": "No", "Acceptance Required": "Yes",
         "Acceptance Formality": "No", "Bill and Hold": "No", "Payment Terms Days": 90},
        # contract-specific terms take precedence over the customer row: a bill-and-hold order with no acceptance clause
        {"Customer": "Skyline Developers", "Contract ID": "PRJ-BH-2603", "Rebate Type": "None", "Return Rights": "No",
         "Acceptance Required": "No", "Bill and Hold": "Yes", "BH Substantive Reason": "Yes", "BH Separately Identified": "No",
         "BH Ready for Transfer": "Yes", "BH Cannot Redirect": "No", "Payment Terms Days": 90},
        {"Customer": "Harbour Hotels", "Rebate Type": "None", "Return Rights": "No", "Acceptance Required": "No",
         "Bill and Hold": "No", "Payment Terms Days": 90},
        {"Customer": "Greenfield Offices", "Rebate Type": "None", "Return Rights": "No", "Acceptance Required": "No",
         "Bill and Hold": "No", "Payment Terms Days": 420},
    ])
    # an 18-month framework agreement: the latest Harbour Hotels project has a second phase not yet invoiced (order book)
    hh = reg[(reg["Customer"] == "Harbour Hotels") & reg["Contract ID"].astype(str).str.startswith("PRJ-")]
    if len(hh):
        last = hh.sort_values("Invoice Date")["Contract ID"].iloc[-1]
        billed = float(hh.loc[hh["Contract ID"] == last, "Net Amount"].sum())
        terms = pd.concat([terms, pd.DataFrame([{"Customer": "Harbour Hotels", "Contract ID": last, "Rebate Type": "None",
                                                  "Return Rights": "No", "Acceptance Required": "No", "Bill and Hold": "No",
                                                  "Payment Terms Days": 90, "Contract Value": round(billed + 18_500_000, -3),
                                                  "Contract Term": "18 months"}])], ignore_index=True)
    lead = ["Customer", "Contract ID"]
    terms = terms[lead + [c for c in terms.columns if c not in lead]]

    # rebates settled after each fiscal year: credit notes issued in August for the year just ended (books hit August)
    reg["_d"] = pd.to_datetime(reg["Invoice Date"])
    fy = reg["_d"].map(lambda d: d.year + (1 if d.month >= 7 else 0))
    reb_rows = []
    for ti, t in terms.iterrows():
        if t["Rebate Type"] not in ("Tiered", "Flat"):
            continue
        for y in (2024, 2025):
            sales = reg[(reg["Customer"] == t["Customer"]) & (fy == y) & reg["Item Type"].isin(["Goods"])]["Net Amount"].sum()
            if t["Rebate Type"] == "Flat" or sales >= t["Rebate Threshold"]:
                val = round(sales * t["Rebate %"] / 100, 0)
                date = pd.Timestamp(year=y, month=8, day=10)
                reb_rows.append({"Invoice No": f"RB-{y}-{ti}", "Invoice Date": date.date(), "Contract ID": f"RB-{y}-{ti}",
                                 "Customer": t["Customer"], "Customer Group": "Distributor", "Item Code": "REB",
                                 "Item Description": f"Volume rebate FY{y}", "Item Type": "Rebate", "Quantity": -1, "Unit Price": val,
                                 "Net Amount": -val, "Delivery Date": None, "Service Start": None, "Service End": None,
                                 "Acceptance Date": None, "Cost Amount": None, "Reference Invoice": None})
    reg = pd.concat([reg.drop(columns="_d"), pd.DataFrame(reb_rows)], ignore_index=True)

    # ── planted March 2026 issues ────────────────────────────────────────────
    r_last = pd.Timestamp("2026-03-31")
    for k, (cust, q) in enumerate([("Prime Build Distributors", 1400), ("Northern Timber Traders", 900), ("Metro Furnishing Supply", 650)]):
        inv = new_inv()
        a = PRODUCTS["LB-18"]
        reg = pd.concat([reg, pd.DataFrame([{
            "Invoice No": inv, "Invoice Date": r_last.date(), "Contract ID": inv, "Customer": cust, "Customer Group": "Distributor",
            "Item Code": "LB-18", "Item Description": a[0], "Item Type": "Goods", "Quantity": q, "Unit Price": a[2],
            "Net Amount": q * a[2], "Delivery Date": (r_last + pd.Timedelta(days=3 + k)).date(), "Service Start": None,
            "Service End": None, "Acceptance Date": None, "Cost Amount": q * a[3], "Reference Invoice": None}])], ignore_index=True)
    inv = new_inv()
    reg = pd.concat([reg, pd.DataFrame([{
        "Invoice No": inv, "Invoice Date": pd.Timestamp("2026-03-26").date(), "Contract ID": "PRJ-BH-2603", "Customer": "Skyline Developers",
        "Customer Group": "Project", "Item Code": "VN-01", "Item Description": "Veneered Panel Walnut (bill-and-hold, held at our warehouse)",
        "Item Type": "Goods", "Quantity": 1500, "Unit Price": 9800, "Net Amount": 1500 * 9800,
        "Delivery Date": None, "Service Start": None, "Service End": None, "Acceptance Date": None,
        "Cost Amount": 1500 * 6300, "Reference Invoice": None}])], ignore_index=True)
    # a burst of upfront annual subscriptions and AMCs in March
    for k, cust in enumerate(["Dealer Chenab", "Dealer Jhelum", "Dealer Potohar", "Dealer Ravi"]):
        inv = new_inv(); date = pd.Timestamp("2026-03-0%d" % (3 + k))
        start = date
        reg = pd.concat([reg, pd.DataFrame([
            {"Invoice No": inv, "Invoice Date": date.date(), "Contract ID": f"SUB-{inv[4:]}", "Customer": cust, "Customer Group": "Corporate",
             "Item Code": "PRT-SUB", "Item Description": PRODUCTS["PRT-SUB"][0], "Item Type": "Subscription", "Quantity": 3,
             "Unit Price": 180000, "Net Amount": 540000, "Delivery Date": None, "Service Start": start.date(),
             "Service End": (start + pd.DateOffset(years=1) - pd.Timedelta(days=1)).date(), "Acceptance Date": None,
             "Cost Amount": None, "Reference Invoice": None},
            {"Invoice No": inv, "Invoice Date": date.date(), "Contract ID": f"SUB-{inv[4:]}", "Customer": cust, "Customer Group": "Corporate",
             "Item Code": "PRT-ONB", "Item Description": PRODUCTS["PRT-ONB"][0], "Item Type": "Setup", "Quantity": 1, "Unit Price": 60000,
             "Net Amount": 60000, "Delivery Date": start.date(), "Service Start": None, "Service End": None, "Acceptance Date": None,
             "Cost Amount": None, "Reference Invoice": None}])], ignore_index=True)

    reg = reg.sort_values(["Invoice Date", "Invoice No"]).reset_index(drop=True)
    for c in ["Invoice Date", "Delivery Date", "Service Start", "Service End", "Acceptance Date"]:
        reg[c] = pd.to_datetime(reg[c])
    ssp = pd.DataFrame([{"Item Code": c, "Stand-alone Selling Price": v[2]} for c, v in PRODUCTS.items()
                        if v[2] and c in ("INST", "DSN", "AMC", "PRT-SUB", "EXW-2")])
    return reg, terms, ssp


def write(out_dir: str = ".") -> None:
    reg, terms, ssp = build()
    with pd.ExcelWriter(os.path.join(out_dir, "sample_sales_register.xlsx"), engine="xlsxwriter") as xw:
        reg.to_excel(xw, sheet_name="Sales Register", index=False)
    with pd.ExcelWriter(os.path.join(out_dir, "sample_contract_terms.xlsx"), engine="xlsxwriter") as xw:
        terms.to_excel(xw, sheet_name="Contract Terms", index=False)
        ssp.to_excel(xw, sheet_name="Stand-alone Prices", index=False)
        wb = xw.book
        ws = xw.sheets["Contract Terms"]
        head = wb.add_format({"bold": True, "bg_color": "#1E293B", "font_color": "white", "text_wrap": True, "valign": "top"})
        for i, c in enumerate(terms.columns):
            ws.write(0, i, c, head)
            ws.set_column(i, i, 24 if c in ("Customer", "Contract ID") else 15)
        ws.set_row(0, 32)
        ws.freeze_panes(1, 2)
        n = len(terms) + 200
        for i, c in enumerate(terms.columns):
            if c in ("Return Rights", "Acceptance Required", "Acceptance Formality", "Bill and Hold", "BH Substantive Reason",
                     "BH Separately Identified", "BH Ready for Transfer", "BH Cannot Redirect"):
                ws.data_validation(1, i, n, i, {"validate": "list", "source": ["Yes", "No"]})
            if c == "Rebate Type":
                ws.data_validation(1, i, n, i, {"validate": "list", "source": ["None", "Flat", "Tiered"]})
        xw.sheets["Stand-alone Prices"].set_column(0, 1, 26)
        guide = wb.add_worksheet("Instructions")
        bold = wb.add_format({"bold": True, "font_size": 12})
        wrap = wb.add_format({"text_wrap": True, "valign": "top"})
        guide.set_column(0, 0, 28)
        guide.set_column(1, 1, 95)
        rows = [
            ("How to use this file", ""),
            ("One row per…", "customer (leave Contract ID blank) or per contract (fill Contract ID). A contract row applies only "
                             "to that contract and overrides the customer row for it."),
            ("Customer", "Exactly as in the sales register (capitals and spacing do not matter)."),
            ("Contract ID", "Optional. The order, project or contract number used in the register."),
            ("Rebate Type", "None, Flat (a % of all purchases) or Tiered (a % of all purchases once the year's purchases reach the "
                            "threshold). Measured per customer."),
            ("Rebate %", "3 or 3% means 3%."),
            ("Rebate Threshold", "Purchases for the fiscal year needed to earn a tiered rebate, e.g. 200000000 or 'PKR 200m'."),
            ("Return Rights", "Yes if the customer may return goods. Expected returns are then provided from history."),
            ("Acceptance Required", "Yes if revenue must wait for customer sign-off (a substantive acceptance clause)."),
            ("Acceptance Formality", "Yes if the acceptance is only a formality (the goods are known to meet the specification)."),
            ("Bill and Hold", "Yes if goods are invoiced but held by you at the customer's request. Then answer the four criteria:"),
            ("BH Substantive Reason", "The customer asked for the arrangement for a genuine reason."),
            ("BH Separately Identified", "The goods are set aside and labelled as the customer's."),
            ("BH Ready for Transfer", "The goods are ready to be shipped now."),
            ("BH Cannot Redirect", "You cannot use the goods or send them to another customer."),
            ("Payment Terms Days", "30, 'Net 45', '2 months' or 'Advance'. Decides when an invoice becomes a receivable."),
            ("Contract Value", "Optional. Total contract price, so amounts not yet invoiced are included in remaining obligations."),
            ("Contract Term", "Optional. Original expected duration, e.g. 36 or '3 years'. Used for the one-year expedient."),
            ("Stand-alone Prices sheet", "Optional. Item Code and the price you sell it for on its own. Used to allocate bundle "
                                         "prices and discounts."),
        ]
        for i, (k, v) in enumerate(rows):
            guide.write(i, 0, k, bold if i == 0 or not v else wb.add_format({"bold": True, "valign": "top"}))
            guide.write(i, 1, v, wrap)
        guide.activate()
        guide.set_first_sheet()


if __name__ == "__main__":
    r, t, s = build()
    write(os.path.dirname(os.path.abspath(__file__)))
    print(r.shape, t.shape, s.shape)
    print(r.groupby(r["Invoice Date"].dt.to_period("M"))["Net Amount"].sum().tail(6))
