"""exports.py — the audit-ready IFRS 15 review pack (Excel), an ERP journal upload file and the revenue schedule (CSV).

Every sheet uses readable headings, real dates, print settings (A4, fit to width, repeated header row) and, where a
figure can be recomputed in Excel, a live formula with a check cell that reads OK or CHECK.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd
from xlsxwriter.utility import xl_rowcol_to_cell as rc

from . import analysis as A
from . import model as M

HEAD = {
    "invoice_no": "Invoice no", "invoice_date": "Invoice date", "contract_id": "Contract", "customer": "Customer",
    "customer_group": "Customer group", "item_code": "Item", "item_desc": "Description", "item": "Description",
    "kind": "Category", "qty": "Quantity", "amount": "Invoiced amount", "L2": "Allocated price", "L1": "Invoiced (net of agent)",
    "delivery_date": "Delivered", "acceptance_date": "Accepted", "control_date": "Control passes", "cause_label": "Cause",
    "note": "Basis", "direction": "Direction", "effect_on_month": "Effect on the month", "outcome": "Outcome",
    "billed": "Billed to date", "recognised": "Recognised to date", "contract_asset": "Contract asset",
    "unbilled_receivable": "Unbilled receivable", "contract_liability": "Contract liability",
    "billed_not_due": "Invoiced, not yet due", "type": "Type", "cost": "Cost", "revenue": "Revenue recognised",
    "assessment": "Assessment", "payment_terms_days": "Payment terms (days)", "billed_to_date": "Billed to date",
    "booked_ytd": "Booked (fiscal YTD)", "ifrs15_ytd": "IFRS 15 (fiscal YTD)", "effect": "Effect", "kinds": "Categories",
}


def _readable(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: HEAD.get(c, c) for c in df.columns})


class _Book:
    def __init__(self, xw: pd.ExcelWriter, company: str, label: str):
        self.wb, self.company, self.label = xw.book, company, label
        wb = self.wb
        base = {"font_name": "Calibri", "font_size": 10, "valign": "top"}
        self.base = base
        self.h = wb.add_format({**base, "bold": True, "font_color": "white", "bg_color": "#1E293B", "text_wrap": True,
                                "valign": "vcenter", "indent": 1})
        self.h_num = wb.add_format({**base, "bold": True, "font_color": "white", "bg_color": "#1E293B", "text_wrap": True,
                                    "valign": "vcenter", "align": "right"})
        self.num = wb.add_format({**base, "num_format": "#,##0;[Red](#,##0);-"})
        self.num2 = wb.add_format({**base, "num_format": "#,##0.00;[Red](#,##0.00);-"})
        self.pct = wb.add_format({**base, "num_format": "0.00%"})
        self.date = wb.add_format({**base, "num_format": "dd mmm yyyy", "align": "center"})
        self.month = wb.add_format({**base, "num_format": "mmm yyyy", "align": "center"})
        self.txt = wb.add_format({**base, "text_wrap": True, "indent": 1})
        self.plain = wb.add_format({**base, "indent": 1})
        self.center = wb.add_format({**base, "align": "center"})
        self.bnum = wb.add_format({**base, "bold": True, "num_format": "#,##0;[Red](#,##0);-"})
        self.btxt = wb.add_format({**base, "bold": True, "indent": 1})
        self.bold = wb.add_format({**base, "bold": True, "top": 1})
        self.bold_num = wb.add_format({**base, "bold": True, "num_format": "#,##0;[Red](#,##0);-", "top": 1})
        self.title = wb.add_format({"bold": True, "font_size": 16, "font_color": "#0F172A", "font_name": "Calibri"})
        self.sub = wb.add_format({"font_size": 10, "font_color": "#64748B", "font_name": "Calibri", "text_wrap": True})
        self.lab = wb.add_format({"bold": True, "font_size": 11, "font_color": "#334155", "font_name": "Calibri", "bottom": 1})
        self.ok = wb.add_format({**base, "bold": True, "font_color": "#047857", "align": "center"})
        green = {"type": "cell", "criteria": "==", "value": '"OK"', "format": wb.add_format({"font_color": "#047857", "bold": True})}
        red = {"type": "cell", "criteria": "==", "value": '"CHECK"', "format": wb.add_format({"font_color": "#B91C1C", "bold": True,
                                                                                            "bg_color": "#FEE2E2"})}
        self.cf = (green, red)

    def setup(self, s, landscape=True, header_rows=0, wide=False):
        if landscape:
            s.set_landscape()
        s.set_paper(8 if wide else 9)
        s.fit_to_pages(1, 0)
        s.set_margins(left=0.4, right=0.4, top=0.6, bottom=0.6)
        s.set_footer(f"&L{self.company} · IFRS 15 review · {self.label} · &A&RPage &P of &N")
        if header_rows:
            s.repeat_rows(0, header_rows - 1)

    def check(self, s, row, col, formula):
        s.write_formula(row, col, formula, self.ok)
        for f in self.cf:
            s.conditional_format(row, col, row, col, f)

    def table(self, name, df: pd.DataFrame, widths: Optional[Dict] = None, money=(), pcts=(), wrap=(), months=(), total=None,
              intro: Optional[str] = None, filt: bool = True, center=()):
        """Write a typed table. `total`: list of columns to total with a SUM formula. Returns (sheet, first data row,
        last data row, column index map)."""
        s = self.wb.add_worksheet(name[:31])
        r0 = 0
        if intro:
            s.merge_range(0, 0, 0, max(len(df.columns) - 1, 3), intro, self.sub)
            s.set_row(0, 30)
            r0 = 2
        cols = list(df.columns)
        lines = 1
        for i, c in enumerate(cols):
            numeric = c in money or c in pcts or pd.api.types.is_numeric_dtype(df[c])
            s.write(r0, i, str(c), self.h_num if numeric else self.h)
            w = (widths or {}).get(c)
            if w is None:
                w = 34 if c in wrap else (12 if c in months else 16)
            s.set_column(i, i, w)
            lines = max(lines, int(np.ceil(len(str(c)) * 1.1 / max(w - 2, 4))))
        s.set_row(r0, 15 * lines + 8)
        for ri, row in enumerate(df.itertuples(index=False), start=r0 + 1):
            is_total = any(isinstance(v, str) and v.strip().lower().startswith("total") for v in row[:2])
            for ci, (c, v) in enumerate(zip(cols, row)):
                if is_total and isinstance(v, (int, float, np.integer, np.floating)) and np.isfinite(v):
                    s.write_number(ri, ci, float(v), self.bnum)
                    continue
                if is_total and isinstance(v, str):
                    s.write(ri, ci, v, self.btxt)
                    continue
                if v is None or (isinstance(v, float) and not np.isfinite(v)) or v is pd.NaT:
                    s.write_blank(ri, ci, None, self.plain)
                elif isinstance(v, pd.Period):
                    s.write_datetime(ri, ci, v.to_timestamp().to_pydatetime(), self.month)
                elif isinstance(v, (pd.Timestamp, datetime)):
                    s.write_datetime(ri, ci, pd.Timestamp(v).to_pydatetime(), self.month if c in months else self.date)
                elif c in center:
                    s.write(ri, ci, v if isinstance(v, str) else (int(v) if isinstance(v, (int, float, np.integer, np.floating))
                                                                  and float(v).is_integer() else v), self.center)
                elif isinstance(v, (bool, np.bool_)):
                    s.write(ri, ci, "Yes" if v else "No", self.plain)
                elif isinstance(v, (int, float, np.integer, np.floating)):
                    fmt = self.pct if c in pcts else (self.num if c in money else self.num2 if isinstance(v, float) and
                                                      abs(v) < 1000 and v != int(v) else self.num)
                    s.write_number(ri, ci, float(v), fmt)
                else:
                    s.write(ri, ci, str(v), self.txt if c in wrap else self.plain)
        r1 = r0 + max(len(df), 1)
        if total and len(df):
            s.write(r1 + 1, 0, "Total", self.bold)
            for c in total:
                ci = cols.index(c)
                s.write_formula(r1 + 1, ci, f"=SUBTOTAL(9,{rc(r0 + 1, ci)}:{rc(r1, ci)})", self.bold_num)
        s.freeze_panes(r0 + 1, 0)
        if filt:
            s.autofilter(r0, 0, r1, max(len(cols) - 1, 0))
        self.setup(s, header_rows=r0 + 1, wide=sum((widths or {}).get(c, 16) for c in cols) > 190)
        return s, r0 + 1, r1, {c: i for i, c in enumerate(cols)}


def erp_lines(a: A.Analysis, period: pd.Period, unit: str, codes: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """The month's entries one debit or credit per line, with GL codes when a mapping is given."""
    j = A.journals(a, period)
    month_end = period.to_timestamp(how="end").normalize()
    codes = codes or {}
    up = []
    for n, e in enumerate(j.itertuples(index=False), start=1):
        ref = f"IFRS15-{period.strftime('%Y%m')}-{n:02d}"
        rev = (period + 1).to_timestamp() if e[3] == "Yes" else None
        for acct, dr, cr in [(e[0], e[2], 0.0), (e[1], 0.0, e[2])]:
            up.append({"Journal ref": ref, "Posting date": month_end, "Reversal date": rev, "GL code": codes.get(acct, ""),
                       "Account": acct, "Debit": dr, "Credit": cr, "Currency": unit, "Description": str(e[4])})
    return pd.DataFrame(up, columns=["Journal ref", "Posting date", "Reversal date", "GL code", "Account", "Debit", "Credit",
                                     "Currency", "Description"])


def excel_pack(a: A.Analysis, period: pd.Period, unit: str, company: str, settings: Dict,
               sensitivity: Optional[pd.DataFrame] = None, codes: Optional[Dict[str, str]] = None,
               signoff: Optional[Dict[str, str]] = None) -> bytes:
    buf = io.BytesIO()
    label = period.strftime("%B %Y")
    g = A.gap_summary(a, period)
    month_end = period.to_timestamp(how="end").normalize()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        B = _Book(xw, company, label)
        wb = B.wb

        # ── Summary: the bridge with live totals and checks ─────────────────
        ws = wb.add_worksheet("Summary")
        ws.hide_gridlines(2)
        ws.write("A1", "IFRS 15 Revenue Gap Review", B.title)
        ws.write("A2", f"{company} · {label} · {A.fy_label(a, period)} · amounts in {unit} · generated "
                       f"{datetime.now():%d %b %Y %H:%M}", B.sub)
        r = 3
        for i, t in enumerate(["Revenue bridge", "Month", "Fiscal YTD"]):
            ws.write(r, i, t, B.lab)
        r += 1
        top = r
        ws.write(r, 0, "Revenue as booked (invoice basis)", B.plain)
        ws.write_number(r, 1, g["booked_month"], B.num); ws.write_number(r, 2, g["booked_ytd"], B.num); r += 1
        for k in A.CAUSE_ORDER:
            ws.write(r, 0, k, B.plain)
            ws.write_number(r, 1, float(g["causes_month"][k]), B.num)
            ws.write_number(r, 2, float(g["causes_ytd"][k]), B.num)
            r += 1
        last = r - 1
        ws.write(r, 0, "Revenue under IFRS 15", B.bold)
        for c in (1, 2):
            ws.write_formula(r, c, f"=SUM({rc(top, c)}:{rc(last, c)})", B.bold_num,
                             g["ifrs15_month"] if c == 1 else g["ifrs15_ytd"])
        ifrs_row = r
        r += 1
        ws.write(r, 0, "Gap (IFRS 15 − booked)", B.bold)
        for c in (1, 2):
            ws.write_formula(r, c, f"={rc(ifrs_row, c)}-{rc(top, c)}", B.bold_num, g["gap_month"] if c == 1 else g["gap_ytd"])
        r += 1
        ws.write(r, 0, "Check: IFRS 15 revenue per the engine", B.plain)
        ws.write_number(r, 1, g["ifrs15_month"], B.num); ws.write_number(r, 2, g["ifrs15_ytd"], B.num)
        ws.write(r, 3, "", B.plain)
        B.check(ws, r, 3, f'=IF(AND(ABS({rc(r, 1)}-{rc(ifrs_row, 1)})<1,ABS({rc(r, 2)}-{rc(ifrs_row, 2)})<1),"OK","CHECK")')
        r += 2

        # tie-out: cumulative gap = balances
        bal = a.balances.loc[period]
        cum_gap = float((a.monthly["L4"] - a.monthly["L0"]).loc[:period].sum())
        agent_cum = float(a.causes[A.C_AGENT].loc[:period].sum())
        ws.write(r, 0, f"Tie-out at {month_end:%d %b %Y}: cumulative gap = IFRS 15 balances", B.lab)
        ws.write(r, 1, "Amount", B.lab); ws.write(r, 2, "", B.lab); r += 1
        t0 = r
        items = [("Agent netting to date (presented net)", agent_cum),
                 ("+ Contract assets", float(bal["contract_asset"])),
                 ("+ Unbilled receivables", float(bal["unbilled_receivable"])),
                 ("− Contract liabilities", -float(bal["contract_liability"])),
                 ("− Invoices issued, not yet due (IFRS 15.106)", -float(bal["billed_not_due"])),
                 ("− Refund liability: returns", -float(bal["refund_liability"])),
                 ("− Refund liability: rebates", -float(bal["rebate_liability"]))]
        for k, v in items:
            ws.write(r, 0, k, B.plain); ws.write_number(r, 1, v, B.num); r += 1
        ws.write(r, 0, "Sum of balances", B.bold)
        ws.write_formula(r, 1, f"=SUM({rc(t0, 1)}:{rc(r - 1, 1)})", B.bold_num, sum(v for _, v in items))
        sum_row = r; r += 1
        ws.write(r, 0, f"Cumulative gap since {a.periods[0].strftime('%b %Y')}", B.plain)
        ws.write_number(r, 1, cum_gap, B.num)
        B.check(ws, r, 2, f'=IF(ABS({rc(r, 1)}-{rc(sum_row, 1)})<1,"OK","CHECK")')
        r += 2

        if signoff and any(str(v).strip() for v in signoff.values()):
            ws.write(r, 0, "Review and sign-off", B.lab); ws.write(r, 1, "", B.lab); ws.write(r, 2, "", B.lab); r += 1
            for k, v in signoff.items():
                ws.write(r, 0, str(k), B.plain); ws.write(r, 1, str(v) if str(v).strip() else "—", B.plain); r += 1
            r += 1
        ws.write(r, 0, "Settings and judgements", B.lab); ws.write(r, 1, "", B.lab); ws.write(r, 2, "", B.lab); r += 1
        for k, v in settings.items():
            ws.write(r, 0, str(k), B.plain); ws.write(r, 1, str(v), B.plain); r += 1
        r += 1
        ws.write(r, 0, "Warnings", B.lab); ws.write(r, 1, "", B.lab); ws.write(r, 2, "", B.lab); r += 1
        for w in (a.warnings + A.data_quality(a)) or ["None"]:
            ws.merge_range(r, 0, r, 3, w, B.txt); ws.set_row(r, 28); r += 1
        ws.set_column(0, 0, 58); ws.set_column(1, 2, 18); ws.set_column(3, 3, 10)
        B.setup(ws, landscape=False)

        # ── Journals: Dr/Cr lines, totals, balance check, reconciliation ─────
        j = A.journals(a, period)
        jl = []
        for n, e in enumerate(j.itertuples(index=False), start=1):
            jl.append({"Entry": n, "Account": e[0], "Debit": e[2], "Credit": None, "Reverses on": e[3], "Narrative": e[4]})
            jl.append({"Entry": n, "Account": e[1], "Debit": None, "Credit": e[2], "Reverses on": e[3], "Narrative": ""})
        jdf = pd.DataFrame(jl, columns=["Entry", "Account", "Debit", "Credit", "Reverses on", "Narrative"])
        nxt = (period + 1).to_timestamp().strftime("%d %b %Y")
        jdf["Reverses on"] = jdf["Reverses on"].map({"Yes": nxt, "No": "Does not reverse"})
        s, f0, f1, ci = B.table("Journals", jdf, {"Entry": 7, "Account": 36, "Debit": 16, "Credit": 16, "Reverses on": 16,
                                                   "Narrative": 70}, money=("Debit", "Credit"), wrap=("Narrative",),
                                center=("Entry", "Reverses on"),
                                total=["Debit", "Credit"],
                                intro=f"Month-end IFRS 15 adjustments at {month_end:%d %B %Y}, on top of invoice-based books. "
                                      f"Balance entries reverse on {nxt}; the agent reclassification is for the month only.")
        if len(jdf):
            tr = f1 + 1
            s.write(tr + 1, 0, "Debits = credits", B.plain)
            B.check(s, tr + 1, ci["Debit"], f'=IF(ABS({rc(tr, ci["Debit"])}-{rc(tr, ci["Credit"])})<0.5,"OK","CHECK")')
        rec = A.journal_reconciliation(a, period)
        rr0 = f1 + 4
        s.write(rr0, 1, "Reconciliation to the month's gap", B.lab); s.write(rr0, 2, "Effect on revenue", B.lab)
        for i, row in enumerate(rec.itertuples(index=False), start=1):
            fmt = B.bold_num if i >= 4 else B.num
            if i == 4:
                s.write(rr0 + i, 1, row[0], B.bold)
                s.write_formula(rr0 + i, 2, f"=SUM({rc(rr0 + 1, 2)}:{rc(rr0 + 3, 2)})", fmt, float(row[1]))
            else:
                s.write(rr0 + i, 1, row[0], B.bold if i == 5 else B.plain)
                s.write_number(rr0 + i, 2, float(row[1]), fmt)
        B.check(s, rr0 + 5, 3, f'=IF(ABS({rc(rr0 + 4, 2)}-{rc(rr0 + 5, 2)})<2,"OK","CHECK")')

        # ── ERP upload layout ───────────────────────────────────────────────
        updf = erp_lines(a, period, unit, codes)
        B.table("ERP upload", updf, {"Journal ref": 18, "GL code": 12, "Account": 34, "Description": 70}, money=("Debit", "Credit"),
                wrap=("Description",), filt=False, center=("Currency",))

        # ── Gap by month: real dates, live gap, check column ────────────────
        mo = a.monthly.join(a.causes)
        mo = mo[mo.index <= period].reset_index().rename(columns={"index": "Month", "period": "Month", "L0": "Revenue as booked",
                                                                  "L4": "Revenue under IFRS 15"})
        mo = mo[["Month", "Revenue as booked"] + A.CAUSE_ORDER + ["Revenue under IFRS 15"]]
        mo["Gap"] = 0.0
        mo["Check"] = ""
        s, f0, f1, ci = B.table("Gap by month", mo, {**{c: 15 for c in mo.columns}, "Month": 11, "Check": 8},
                                money=[c for c in mo.columns if c not in ("Month", "Check")], months=("Month",),
                                intro="Each month: revenue as booked, each IFRS 15 adjustment and revenue under IFRS 15. "
                                      "Gap = IFRS 15 − booked (formula); Check = the adjustments add up to the gap.")
        cb, cf = ci[A.CAUSE_ORDER[0]], ci[A.CAUSE_ORDER[-1]]
        for i in range(f0, f1 + 1):
            s.write_formula(i, ci["Gap"], f"={rc(i, ci['Revenue under IFRS 15'])}-{rc(i, ci['Revenue as booked'])}", B.num)
            s.write_formula(i, ci["Check"], f'=IF(ABS(SUM({rc(i, cb)}:{rc(i, cf)})-{rc(i, ci["Gap"])})<1,"OK","CHECK")', B.ok)
        for f in B.cf:
            s.conditional_format(f0, ci["Check"], f1, ci["Check"], f)

        # ── balances ────────────────────────────────────────────────────────
        rf = A.balances_rollforward(a, period)
        ob = A.other_balances(a, period)
        both = pd.concat([rf, ob], ignore_index=True)
        both["Movement"] = 0.0
        s, f0, f1, ci = B.table("Contract balances", both, {"Balance": 50}, money=("Opening", "Closing", "Movement"),
                                intro=f"IFRS 15.116–118: opening ({rf.attrs.get('opening_period', '')}) and closing "
                                      f"({month_end:%d %b %Y}) balances. Revenue recognised from the opening contract liability: "
                                      f"{rf.attrs.get('revenue_from_opening_liability', 0):,.0f}.")
        for i in range(f0, f1 + 1):
            s.write_formula(i, ci["Movement"], f"={rc(i, ci['Closing'])}-{rc(i, ci['Opening'])}", B.num)
        cbd = _readable(A.contract_balances_at(a, period))
        B.table("Balances by contract", cbd, {"Customer": 30}, money=[c for c in cbd.columns if c not in ("Contract", "Customer")],
                total=[c for c in cbd.columns if c not in ("Contract", "Customer")],
                intro=f"Contract balances at {month_end:%d %B %Y}, contract by contract (IFRS 15.105–108).")
        rpo = A.remaining_obligations(a, period)
        intro = ("IFRS 15.120: price allocated to obligations not yet satisfied, for contracts invoiced to date.")
        if rpo.attrs.get("excluded_short"):
            intro += f" Excluded under the 121(a) expedient (contracts of one year or less): {rpo.attrs['excluded_short']:,.0f}."
        B.table("Remaining obligations", rpo, {"When": 42}, money=[c for c in rpo.columns if c != "When"], intro=intro)
        dis = A.disaggregation(a, period)
        dd = pd.concat([t.rename(columns={k: "Line"}).assign(Analysis=k) for k, t in dis.items()])[["Analysis", "Line", "Month",
                                                                                                      "Fiscal YTD"]]
        B.table("Disaggregation", dd, {"Analysis": 24, "Line": 36}, money=("Month", "Fiscal YTD"),
                intro="IFRS 15.114 and B87–B89. Returns and rebates are deducted from goods recognised at a point in time.")

        # ── variable consideration ──────────────────────────────────────────
        rr = a.var.return_rates
        if not rr.empty:
            rr = rr[rr["period"] <= period].copy()
            rr.loc[~rr["window_closed"].astype(bool) | (rr["period"] > period - a.var.return_window), "actual_rate"] = np.nan
            rr = rr.rename(columns={
                "group": "Customer group", "period": "Sale month", "sales": "Goods revenue", "returns": "Returns",
                "actual_rate": "Actual rate", "expected_rate": "Expected value rate", "rate_at_confidence": "Rate at confidence",
                "constrained_rate": "Rate used", "history_months": "History (months)", "window_closed": "Return window closed"})
            B.table("Returns", rr, {"Sale month": 11}, money=("Goods revenue", "Returns"), months=("Sale month",),
                    pcts=("Actual rate", "Expected value rate", "Rate at confidence", "Rate used"),
                    intro="Expected value (B23) from the company's own closed return history, no later information; the "
                          "constraint applies when a reversal above the significance level is not highly improbable.")
        rt = a.var.rebate_table
        if not rt.empty:
            rt = rt[rt["period"] == period].drop(columns=["period", "expected_value_rate"]).rename(columns={
                "customer": "Customer", "fiscal_year": "Fiscal year", "rebate_type": "Type", "rebate_pct": "Rebate %",
                "threshold": "Threshold", "ytd_purchases": "Purchases (YTD)", "probability": "Probability of tier",
                "most_likely_rate": "Most likely rate", "accrual_rate": "Rate accrued", "basis": "Basis",
                "expected_value_rate": "Expected value rate", "accrued": "Accrued", "settled": "Settled", "liability": "Liability"})
            B.table("Rebates", rt, {"Customer": 26, "Basis": 40, "Fiscal year": 9, "Type": 9, "Rebate %": 9},
                    money=("Threshold", "Purchases (YTD)", "Accrued", "Settled", "Liability"),
                    pcts=("Rebate %", "Probability of tier", "Most likely rate", "Rate accrued"), center=("Fiscal year", "Type"),
                    wrap=("Basis",), total=["Liability"],
                    intro="Most likely amount (53(b)) for single-tier rebates, then the constraint. Settlements are matched "
                          "first-in, first-out against earlier accruals.")

        # ── controls ────────────────────────────────────────────────────────
        te = A.timing_exceptions(a, period).drop(columns=["cause", "item_code", "acceptance_date"])
        if len(te):
            te = _readable(te)
            B.table("Timing exceptions", te, {"Invoice no": 13, "Invoice date": 12, "Customer": 22, "Description": 28, "Basis": 36,
                                              "Direction": 16, "Cause": 26, "Delivered": 12, "Control passes": 12},
                    money=("Invoiced amount", "Allocated price", "Effect on the month"), wrap=("Basis",),
                    total=["Effect on the month"],
                    intro="Point-in-time lines whose control passes in a different month from the invoice: cut-off, "
                          "bill-and-hold and customer acceptance.")
        bh = A.bill_and_hold(a, period)
        if len(bh):
            B.table("Bill-and-hold", _readable(bh), {"Customer": 24, "Description": 34, "Outcome": 40}, money=("Invoiced amount",),
                    intro="IFRS 15.B79–B82: revenue before delivery only when all four criteria are met.")
        ap = A.acceptance_pending(a, period)
        if len(ap):
            B.table("Acceptance pending", _readable(ap), {"Customer": 26, "Description": 34}, money=("Allocated price",),
                    total=["Allocated price"])
        ag = A.agent_screening(a, period)
        if len(ag):
            B.table("Principal vs agent", _readable(ag), {"Customer": 26, "Description": 30, "Assessment": 60},
                    money=("Billed to date", "Cost", "Revenue recognised"), wrap=("Assessment",),
                    intro=f"IFRS 15.B34–B38: pass-through and low-margin lines invoiced in {label}.")
        ff = A.financing_flags(a, period)
        if len(ff):
            B.table("Financing component", _readable(ff), {"Customer": 30, "Assessment": 70}, money=("Billed to date",),
                    wrap=("Assessment",), intro="IFRS 15.60–65: contracts to assess for a significant financing component.")
        al = a.model.lines[a.model.lines["alloc_method"] != ""][["contract_id", "customer", "item_code", "item_desc", "qty", "L1",
                                                                 "ssp_line", "L2", "alloc_method"]]
        if len(al):
            al = al.rename(columns={"L1": "Invoiced", "ssp_line": "Stand-alone price", "L2": "Allocated", "alloc_method": "Method"})
            B.table("Allocation", _readable(al), {"Description": 30, "Customer": 24, "Method": 44, "Quantity": 9},
                    money=("Invoiced", "Stand-alone price", "Allocated"), total=["Invoiced", "Allocated"], wrap=("Method",),
                    intro="IFRS 15.73–86: contracts whose price was re-allocated across their obligations.")
        sp = a.model.ssp.rename(columns={"item_code": "Item", "ssp_unit": "Stand-alone price (unit)", "ssp_source": "Basis"})
        B.table("Stand-alone prices", sp, {"Basis": 60}, money=("Stand-alone price (unit)",), wrap=("Basis",),
                intro="IFRS 15.76–80: the basis of each stand-alone selling price used in the allocation.")
        if sensitivity is not None and len(sensitivity):
            B.table("Judgements", sensitivity, {"Judgement": 44}, money=[c for c in sensitivity.columns if c != "Judgement"],
                    intro="Each judgement changed on its own; Δ columns show the effect against the current settings.")

        note = A.draft_note(a, period, unit, lambda v: f"{unit} {v:,.0f}")
        wsn = wb.add_worksheet("Draft note")
        wsn.set_column(0, 0, 95, B.txt)
        head = wb.add_format({"bold": True, "font_size": 11, "font_color": "#334155", "font_name": "Calibri", "valign": "top"})
        for i, para in enumerate(note.split("\n")):
            wsn.write(i, 0, para, head if para and (i == 0 or len(para) < 40) else B.txt)
        B.setup(wsn, landscape=False)
    return buf.getvalue()


def schedule_csv(a: A.Analysis, period: pd.Period) -> bytes:
    """Revenue schedule by line and month (to the review month and beyond), with the line's invoiced and allocated
    price given once, plus the monthly variable-consideration movements."""
    d = a.model.lines
    sch = a.model.schedule.copy()
    sch["Month"] = sch["period"].map(lambda p: "Not yet known" if p == M.UNKNOWN else p.strftime("%Y-%m"))
    info = d.set_index("line_id")[["invoice_no", "invoice_date", "contract_id", "customer", "item_code", "kind", "pattern", "L1",
                                   "L2", "alloc_method"]]
    x = sch.join(info, on="line_id").sort_values(["line_id", "period"], key=lambda s: s.map(str) if s.name == "period" else s)
    first = ~x["line_id"].duplicated()
    x.loc[~first, ["L1", "L2"]] = np.nan
    out = pd.DataFrame({"Line": x["line_id"], "Invoice no": x["invoice_no"], "Invoice date": x["invoice_date"].dt.strftime("%Y-%m-%d"),
                        "Contract": x["contract_id"], "Customer": x["customer"], "Item": x["item_code"], "Category": x["kind"],
                        "Recognition": x["pattern"].map({"point": "Point in time", "over_time": "Over time",
                                                         "pass_through": "Agent (net)", "discount": "Discount (allocated)",
                                                         "settlement": "Settlement"}),
                        "Invoiced (net of agent)": x["L1"].round(2), "Allocated price": x["L2"].round(2),
                        "Allocation method": x["alloc_method"], "Revenue month": x["Month"],
                        "Revenue recognised": x["amount"].round(2)})
    vc = []
    for col, nm in [("refund_liability", "Expected returns: change in refund liability"),
                    ("rebate_liability", "Volume rebates: change in refund liability")]:
        s_ = a.balances[col]
        mv = -(s_ - s_.shift(1).fillna(0.0))
        vc += [{"Line": "", "Category": "Variable consideration", "Recognition": nm, "Revenue month": p.strftime("%Y-%m"),
                "Revenue recognised": round(float(v), 2)} for p, v in mv.items() if abs(v) >= 0.005]
    out = pd.concat([out, pd.DataFrame(vc)], ignore_index=True)
    return out.to_csv(index=False).encode("utf-8-sig")


def note_docx(a: A.Analysis, period: pd.Period, unit: str, company: str) -> bytes:
    """The draft note as a Word document, with the disaggregation, contract balances and remaining-obligation tables."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    note = A.draft_note(a, period, unit, lambda v: f"{unit} {v:,.0f}")
    paras = note.split("\n")
    doc.add_heading(f"{company}: {paras[0]}", level=1)

    def table(df: pd.DataFrame, money_cols):
        t = doc.add_table(rows=1, cols=len(df.columns))
        t.style = "Light Grid Accent 1"
        for i, c in enumerate(df.columns):
            t.rows[0].cells[i].text = str(c)
        for row in df.itertuples(index=False):
            cells = t.add_row().cells
            for i, (c, v) in enumerate(zip(df.columns, row)):
                cells[i].text = (f"{v:,.0f}" if c in money_cols and isinstance(v, (int, float, np.floating)) and np.isfinite(v)
                                 else ("" if v is None or (isinstance(v, float) and not np.isfinite(v)) else str(v)))

    for p in paras[1:]:
        if not p.strip():
            continue
        if len(p) < 40:
            doc.add_heading(p, level=2)
            if p == "Disaggregation":
                for k, t in A.disaggregation(a, period).items():
                    doc.add_paragraph(k, style="Caption")
                    table(t.rename(columns={k: "Line"}), ("Month", "Fiscal YTD"))
            if p == "Contract balances":
                rf = pd.concat([A.balances_rollforward(a, period), A.other_balances(a, period)], ignore_index=True)
                table(rf, ("Opening", "Closing", "Movement"))
            if p == "Remaining performance obligations":
                rpo = A.remaining_obligations(a, period)
                if len(rpo):
                    table(rpo, [c for c in rpo.columns if c != "When"])
        else:
            doc.add_paragraph(p)
    doc.add_paragraph("Draft for review. Generated by the IFRS 15 Revenue Gap Analyzer; edit before use.", style="Caption")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def close_bundle(a: A.Analysis, period: pd.Period, unit: str, company: str, settings: Dict,
                 sensitivity: Optional[pd.DataFrame] = None, codes: Optional[Dict[str, str]] = None,
                 signoff: Optional[Dict[str, str]] = None) -> bytes:
    """One zip for the close file: Excel pack, revenue schedule, journal upload and the draft note (.docx)."""
    import re as _re
    import zipfile

    stem = _re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_") or "company"
    tag = f"{stem}_{period.strftime('%Y-%m')}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{tag}_IFRS15_review_pack.xlsx", excel_pack(a, period, unit, company, settings, sensitivity, codes, signoff))
        z.writestr(f"{tag}_revenue_schedule.csv", schedule_csv(a, period))
        z.writestr(f"{tag}_journal_upload.csv", erp_lines(a, period, unit, codes).assign(
            **{"Posting date": lambda x: pd.to_datetime(x["Posting date"]).dt.strftime("%Y-%m-%d"),
               "Reversal date": lambda x: pd.to_datetime(x["Reversal date"]).dt.strftime("%Y-%m-%d")})
                   .to_csv(index=False).encode("utf-8-sig"))
        z.writestr(f"{tag}_draft_note.docx", note_docx(a, period, unit, company))
    return buf.getvalue()
