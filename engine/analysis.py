"""
analysis.py — the IFRS 15 gap, its causes, balances, controls, disclosures and adjusting journals.

Gap = revenue under IFRS 15 (L4) − revenue as booked (L0). Every month's gap is split into causes that add up exactly.

Identity (tested): the cumulative gap to any month-end equals
    agent netting to date + contract assets + unbilled receivables − contract liabilities − advance billings not yet due
    − refund liabilities (returns) − refund liabilities (rebates)
so the month-end adjusting journals are the IFRS 15 balances at that date (posted as reversing entries) plus the month's
agent reclassification (not reversed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import model as M
from . import variable as V
from .io import OVER_TIME_KINDS, SETTLEMENT_KINDS, align_terms

C_AGENT = "Agent: pass-through presented net"
C_ALLOC = "Allocation of price and discounts"
C_RET = "Expected returns (refund liability)"
C_REB = "Volume rebates (refund liability)"
CAUSE_ORDER = [C_AGENT, C_ALLOC, M.TIMING_CAUSE["cutoff"], M.TIMING_CAUSE["bill_and_hold"], M.TIMING_CAUSE["acceptance"],
               M.TIMING_CAUSE["over_time"], M.TIMING_CAUSE["setup"], C_RET, C_REB]
CAUSE_HELP = {
    C_AGENT: "Recharges of third-party services (e.g. freight) where the company does not control the service: only the margin is revenue (IFRS 15.B34–B38).",
    C_ALLOC: "Contract price, including discount lines, re-allocated to each obligation by stand-alone selling price, so revenue moves with the obligation it belongs to (IFRS 15.73–86).",
    M.TIMING_CAUSE["cutoff"]: "Invoiced in one month, control passes in another: e.g. month-end invoices for goods dispatched next month (IFRS 15.38).",
    M.TIMING_CAUSE["bill_and_hold"]: "Invoiced goods still held by the company where not all four IFRS 15.B81 criteria are met: revenue waits for delivery.",
    M.TIMING_CAUSE["acceptance"]: "Contracts with a substantive customer acceptance clause: revenue waits for acceptance (IFRS 15.B83–B86).",
    M.TIMING_CAUSE["over_time"]: "Subscriptions, maintenance, warranties and services recognised over the service period, not when billed (IFRS 15.35).",
    M.TIMING_CAUSE["setup"]: "Set-up or onboarding fees that are not a separate service: recognised with the service they relate to (IFRS 15.B48–B51).",
    C_RET: "Change in the refund liability for returns expected on recent sales (IFRS 15.B20–B27).",
    C_REB: "Change in the refund liability for volume rebates earned but not yet settled (IFRS 15.50–59).",
}


@dataclass
class Analysis:
    model: M.Model
    var: V.VariableResult
    monthly: pd.DataFrame
    causes: pd.DataFrame
    balances: pd.DataFrame
    terms: pd.DataFrame
    warnings: List[str] = field(default_factory=list)

    @property
    def periods(self) -> pd.PeriodIndex:
        return self.model.periods


def _deferred_cost(m: M.Model) -> pd.Series:
    """Cost of goods booked with the invoice but belonging to revenue not yet recognised (positive), less cost of goods
    delivered before they were invoiced (negative), at each month-end."""
    d, P = m.lines, m.periods
    g = d[(d["pattern"] == "point") & d["cost_amount"].notna()]
    out = pd.Series(0.0, index=P)
    if g.empty:
        return out
    ctrl = g["control_date"].dt.to_period("M")
    for i, p in enumerate(P):
        inv_le = g["inv_period"] <= p
        ctrl_gt = ctrl.isna() | (ctrl > p)
        out.iloc[i] = float(g.loc[inv_le & ctrl_gt, "cost_amount"].sum() - g.loc[~inv_le & ~ctrl_gt, "cost_amount"].sum())
    return out


def analyse(reg: pd.DataFrame, terms: pd.DataFrame, ssp_table: Optional[pd.DataFrame], s: M.Settings) -> Analysis:
    terms = align_terms(reg, terms)
    m = M.build(reg, terms, ssp_table, s)
    v = V.run(m, terms)
    P = m.periods
    ml = M.monthly_layers(m)
    rl = v.refund_liability.reindex(P).fillna(0.0)
    rb = v.rebate_liability.reindex(P).fillna(0.0)
    d_rl, d_rb = rl.diff().fillna(rl), rb.diff().fillna(rb)
    ml["L4"] = ml["L3"] - d_rl - d_rb
    c = pd.DataFrame(index=P)
    c[C_AGENT] = ml["L1"] - ml["L0"]
    # L3 − L1 = (L3 − L3a) + (L3a − L1): allocation effect, shown in the month the obligations are satisfied (linked
    # discount lines included), plus timing on invoiced prices split by cause.
    c[C_ALLOC] = ml["L3"] - ml["L3a"]
    tc = M.timing_by_cause(m)
    for key, label in M.TIMING_CAUSE.items():
        c[label] = tc[key] if key in tc.columns else 0.0
    c[C_RET] = -d_rl
    c[C_REB] = -d_rb
    c = c[CAUSE_ORDER]
    bal = M.balances_series(m)
    bal["refund_liability"] = rl
    bal["rebate_liability"] = rb
    bal["return_asset"] = rl * (v.cost_ratio or 0.0)
    bal["deferred_cost"] = _deferred_cost(m)
    w = m.warnings + v.warnings
    if v.cost_ratio is None and float(rl.abs().max() if len(rl) else 0.0) >= 0.5:
        w.append("The register has no cost of sales, so the asset for the right to recover returned goods is not measured "
                 "(it is shown as nil). Add a cost column to measure it.")
    return Analysis(m, v, ml, c, bal, terms, w)


def fy_months(a: Analysis, period: pd.Period) -> List[pd.Period]:
    fy = M.fiscal_year(period, a.model.settings.fy_start_month)
    return [p for p in a.periods if M.fiscal_year(p, a.model.settings.fy_start_month) == fy and p <= period]


def fy_label(a: Analysis, period: pd.Period) -> str:
    fs = a.model.settings.fy_start_month
    fy = M.fiscal_year(period, fs)
    end = M.fy_start_period(fy, fs) + 11
    return f"FY{fy} (year ending {end.to_timestamp(how='end'):%d %B %Y})"


def gap_summary(a: Analysis, period: pd.Period) -> Dict:
    ytd = fy_months(a, period)
    r = {"booked_month": float(a.monthly.loc[period, "L0"]), "ifrs15_month": float(a.monthly.loc[period, "L4"]),
         "booked_ytd": float(a.monthly.loc[ytd, "L0"].sum()), "ifrs15_ytd": float(a.monthly.loc[ytd, "L4"].sum()),
         "causes_month": a.causes.loc[period], "causes_ytd": a.causes.loc[ytd].sum()}
    r["gap_month"] = r["ifrs15_month"] - r["booked_month"]
    r["gap_ytd"] = r["ifrs15_ytd"] - r["booked_ytd"]
    return r


def bridge_steps(a: Analysis, period: pd.Period, ytd: bool = False, materiality: float = 0.0) -> List[tuple]:
    """Booked → causes → IFRS 15. Causes below `materiality` are grouped as 'Other'."""
    g = gap_summary(a, period)
    causes = g["causes_ytd"] if ytd else g["causes_month"]
    steps = [("Revenue as booked", g["booked_ytd"] if ytd else g["booked_month"], "absolute")]
    other = 0.0
    for k, v in causes.items():
        if abs(v) < 0.5:
            continue
        if abs(v) < materiality:
            other += float(v)
        else:
            steps.append((k, float(v), "relative"))
    if abs(other) >= 0.5:
        steps.append(("Other causes", other, "relative"))
    steps.append(("Revenue under IFRS 15", g["ifrs15_ytd"] if ytd else g["ifrs15_month"], "total"))
    return steps


# ── controls ─────────────────────────────────────────────────────────────────
def timing_exceptions(a: Analysis, period: pd.Period, cause: Optional[str] = None) -> pd.DataFrame:
    """Point-in-time lines invoiced in the month whose control passes in another month (or not yet), and lines
    invoiced earlier whose control passes in the month. `cause`: 'cutoff', 'bill_and_hold', 'acceptance' or None."""
    d = a.model.lines
    pt = d[(d["pattern"] == "point") & ~d["kind"].isin(["Setup"])].copy()
    if cause:
        pt = pt[pt["cause"] == cause]
    pt["control_period"] = pt["control_date"].dt.to_period("M")
    out_ = (pt["inv_period"] == period) & (pt["control_period"].isna() | (pt["control_period"] != period))
    in_ = (pt["control_period"] == period) & (pt["inv_period"] != period)
    x = pt[out_ | in_].copy()
    x["effect_on_month"] = np.where(x["inv_period"] == period, -x["L1"], x["L1"])   # invoiced basis, as in the bridge
    x["direction"] = np.where(x["inv_period"] == period, "Invoiced this month, control passes later",
                              "Invoiced earlier, control passes this month")
    x["cause_label"] = x["cause"].map(M.TIMING_CAUSE)
    x["direction"] = np.where(x["inv_period"] == period, "Out: later", "In: invoiced earlier")
    return x.sort_values("effect_on_month")[["invoice_no", "invoice_date", "customer", "item_code", "item_desc", "amount", "L2",
                                             "delivery_date", "acceptance_date", "control_date", "cause", "cause_label", "note",
                                             "direction", "effect_on_month"]]


def cutoff_exceptions(a: Analysis, period: pd.Period) -> pd.DataFrame:
    return timing_exceptions(a, period, "cutoff")


def bill_and_hold(a: Analysis, period: pd.Period) -> pd.DataFrame:
    """Bill-and-hold arrangements open at the month-end: invoiced on or before it, goods held after it (or held
    within the month)."""
    d = a.model.lines
    lk = M.terms_lookup(a.terms)
    end = period.to_timestamp(how="end").normalize()
    rows = []
    g = d[(d["kind"] == "Goods") & (d["invoice_date"] <= end)]
    for r in g.itertuples():
        t = M.line_terms(lk, r.contract_id, r.customer)
        if not t.get("bill_and_hold"):
            continue
        held = pd.isna(r.delivery_date) or (r.delivery_date - r.invoice_date).days > 7
        relevant = pd.isna(r.delivery_date) or r.delivery_date.to_period("M") >= period
        if not (held and relevant):
            continue
        crit = {"Substantive reason": t.get("bh_reason"), "Identified as the customer's": t.get("bh_identified"),
                "Ready for transfer": t.get("bh_ready"), "Cannot be redirected": t.get("bh_no_redirect")}
        rows.append({"invoice_no": r.invoice_no, "invoice_date": r.invoice_date, "customer": r.customer, "item": r.item_desc,
                     "amount": r.amount, "delivery_date": r.delivery_date, **{k: "Yes" if v else "No" for k, v in crit.items()},
                     "outcome": "Recognise when invoiced (all B81 criteria met)" if all(crit.values()) else
                     ("Recognise on delivery (criteria not met)" if pd.notna(r.delivery_date) else
                      "Not recognised until delivered (criteria not met)")})
    return pd.DataFrame(rows)


def acceptance_pending(a: Analysis, period: pd.Period) -> pd.DataFrame:
    """Lines with a substantive acceptance clause invoiced by the month-end and not accepted by then."""
    d = a.model.lines
    end = period.to_timestamp(how="end").normalize()
    x = d[(d["cause"] == "acceptance") & (d["invoice_date"] <= end)
          & (d["control_date"].isna() | (d["control_date"] > end))]
    return x[["invoice_no", "invoice_date", "contract_id", "customer", "item_desc", "L2", "delivery_date", "acceptance_date"]]


def agent_screening(a: Analysis, period: pd.Period) -> pd.DataFrame:
    d = a.model.lines
    pt = d[(d["kind"] == "Pass-through") & (d["inv_period"] == period)]
    low = d[(d["inv_period"] == period) & ~d["kind"].isin(SETTLEMENT_KINDS | {"Pass-through"}) & d["cost_amount"].notna()
            & (d["L2"] > 0) & ((d["L2"] - d["cost_amount"]) / d["L2"] < 0.03)]
    rows = [{"type": "Pass-through", "invoice_no": r.invoice_no, "customer": r.customer, "item": r.item_desc, "billed": r.amount,
             "cost": r.cost_amount, "revenue": r.L1,
             "assessment": ("Agent: the company arranges the carrier's service and does not control it, so only the margin is "
                            "revenue" if a.model.settings.passthrough_agent else "Principal (policy choice): presented gross")}
            for r in pt.itertuples()]
    rows += [{"type": "Below cost" if r.L2 < r.cost_amount else "Low margin", "invoice_no": r.invoice_no,
              "customer": r.customer, "item": r.item_desc, "billed": r.amount, "cost": r.cost_amount, "revenue": r.L2,
              "assessment": ("Allocated revenue below cost: check the pricing and whether the contract is onerous (IAS 37)"
                             if r.L2 < r.cost_amount else
                             "Margin under 3%: confirm the company controls the item before transfer (B37 indicators)")}
             for r in low.itertuples()]
    return pd.DataFrame(rows)


def financing_flags(a: Analysis, period: pd.Period) -> pd.DataFrame:
    t = a.terms
    if t is None or t.empty or "payment_days" not in t:
        return pd.DataFrame()
    long_ = t[t["payment_days"].fillna(0) > 365]
    d = a.model.lines
    rows = []
    for r in long_.itertuples():
        x = d[((d["contract_id"] == r.contract_id) if isinstance(r.contract_id, str) else (d["customer"] == r.customer))
              & (d["inv_period"] <= period) & d["pattern"].isin(["point", "over_time"])]
        if len(x):
            rows.append({"customer": r.customer if isinstance(r.customer, str) else r.contract_id,
                         "payment_terms_days": int(r.payment_days), "billed_to_date": float(x["L1"].sum()),
                         "assessment": "Payment due more than 12 months after transfer: assess a significant financing component "
                                       "(IFRS 15.60–65). Not adjusted in this analysis."})
    long_service = d[d["kind"].isin(OVER_TIME_KINDS) & d["start"].notna() & ((d["end"] - d["start"]).dt.days > 400)
                     & (d["inv_period"] <= period) & (d["amount"] > 0)]
    if len(long_service):
        names = sorted(long_service["customer"].unique())
        rows.append({"customer": ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else ""),
                     "payment_terms_days": None,
                     "billed_to_date": float(long_service["L1"].sum()),
                     "assessment": "Services longer than 12 months paid in advance: assess whether the advance payment contains "
                                   "a significant financing component (IFRS 15.61–63)."})
    return pd.DataFrame(rows)


def data_quality(a: Analysis) -> List[str]:
    d = a.model.lines
    out = []
    n = int((d["amount"] == 0).sum())
    if n:
        out.append(f"{n} line(s) have a zero amount; free items receive a share of the contract price when they have a stand-alone price.")
    fut = d[d["invoice_date"] > pd.Timestamp.today().normalize()]
    if len(fut):
        out.append(f"{len(fut)} line(s) are dated in the future.")
    bad = d[d["kind"].isin(OVER_TIME_KINDS) & d["service_end"].notna() & (d["service_end"] < d["service_start"])]
    if len(bad):
        out.append(f"{len(bad)} subscription line(s) end before they start; they were recognised on the start date.")
    return out


# ── disclosures ──────────────────────────────────────────────────────────────
def disaggregation(a: Analysis, period: pd.Period) -> Dict[str, pd.DataFrame]:
    """IFRS 15.114 / B87–B89: revenue by product line, customer group and timing, for the month and fiscal YTD.
    Returns and rebates are attributed to goods, recognised at a point in time, and to the customer group concerned."""
    d, sch = a.model.lines, a.model.schedule
    ytd = fy_months(a, period)
    po = d[d["pattern"].isin(["point", "over_time", "pass_through"])][["line_id", "kind", "customer_group", "pattern"]]
    s = sch[sch["period"].isin(ytd)].merge(po, on="line_id")
    s = s.assign(product=s["kind"].replace({"Setup": "Set-up & onboarding", "Pass-through": "Freight & recharges (net)"}),
                 timing=np.where(s["pattern"] == "over_time", "Over time", "At a point in time"))[
        ["period", "amount", "product", "customer_group", "timing"]]
    st = d[(d["pattern"] == "settlement") & d["inv_period"].isin(ytd)]
    s2 = pd.DataFrame({"period": st["inv_period"].values, "amount": st["L2"].values,
                       "product": np.where(st["kind"] == "Discount", "Discounts not linked to a sale", "Goods"),
                       "customer_group": st["customer_group"].values, "timing": "At a point in time"})
    rr = []
    rg = a.var.refund_by_group.reindex(a.periods).fillna(0.0)
    for g in rg.columns:
        mv = -(rg[g] - rg[g].shift(1).fillna(0.0))
        rr += [{"period": p, "amount": float(mv[p]), "product": "Goods", "customer_group": g, "timing": "At a point in time"}
               for p in ytd]
    rbc = a.var.rebate_by_customer.reindex(a.periods).fillna(0.0)
    for c in rbc.columns:
        mv = -(rbc[c] - rbc[c].shift(1).fillna(0.0))
        rr += [{"period": p, "amount": float(mv[p]), "product": "Goods", "customer_group": a.var.group_of.get(c, "Other"),
                "timing": "At a point in time"} for p in ytd]
    allr = pd.concat([s, s2, pd.DataFrame(rr)], ignore_index=True)
    out = {}
    for key, col in [("Product / service line", "product"), ("Customer group", "customer_group"), ("Timing of transfer", "timing")]:
        g = allr.groupby([col, "period"])["amount"].sum().unstack(fill_value=0.0)
        tbl = pd.DataFrame({"Month": g[period] if period in g.columns else 0.0, "Fiscal YTD": g.sum(axis=1)})
        tbl = tbl[(tbl["Month"].abs() >= 0.5) | (tbl["Fiscal YTD"].abs() >= 0.5)]
        tbl.loc["Total revenue"] = [tbl["Month"].sum(), tbl["Fiscal YTD"].sum()]
        tbl.index.name = key
        out[key] = tbl.reset_index()
    return out


def deferred_by_reason(a: Analysis, period: pd.Period) -> pd.Series:
    """Revenue invoiced to date but not yet recognised at the month-end, by reason (explains the contract liability)."""
    d, sch = a.model.lines, a.model.schedule
    po = d[d["pattern"].isin(["point", "over_time"]) & (d["inv_period"] <= period)]
    rec = sch[sch["line_id"].isin(po["line_id"]) & (sch["period"] != M.UNKNOWN) & (sch["period"] <= period)] \
        .groupby("line_id")["amount"].sum()
    left = po["L2"] - po["line_id"].map(rec).fillna(0.0).values
    return left.groupby(po["cause"].map(M.TIMING_CAUSE).fillna("Other")).sum().sort_values(ascending=False)


def balances_rollforward(a: Analysis, period: pd.Period) -> pd.DataFrame:
    """IFRS 15.116–118: opening and closing contract balances for the fiscal year to date."""
    ytd = fy_months(a, period)
    open_p = ytd[0] - 1
    b = a.balances
    has_open = open_p in b.index
    get = lambda p, c: float(b.loc[p, c]) if p in b.index else 0.0  # noqa: E731
    rows = [
        ("Contract assets", get(open_p, "contract_asset"), get(period, "contract_asset")),
        ("Receivables: unbilled (right unconditional)", get(open_p, "unbilled_receivable"), get(period, "unbilled_receivable")),
        ("Contract liabilities", get(open_p, "contract_liability"), get(period, "contract_liability")),
        ("Advance invoices not yet due (not receivables)", get(open_p, "billed_not_due"), get(period, "billed_not_due")),
    ]
    t = pd.DataFrame(rows, columns=["Balance", "Opening", "Closing"])
    t["Movement"] = t["Closing"] - t["Opening"]
    # 116(b): revenue recognised in the period that was in the opening contract liability, line by line
    from_open = M.revenue_from_opening_liability(a.model, open_p, period) if has_open else 0.0
    t.attrs.update(revenue_from_opening_liability=from_open, opening_period=open_p, has_opening=has_open)
    return t


def other_balances(a: Analysis, period: pd.Period) -> pd.DataFrame:
    ytd = fy_months(a, period)
    open_p = ytd[0] - 1
    b = a.balances
    get = lambda p, c: float(b.loc[p, c]) if p in b.index else 0.0  # noqa: E731
    rows = [("Refund liability: expected returns", get(open_p, "refund_liability"), get(period, "refund_liability")),
            ("Refund liability: customer rebates", get(open_p, "rebate_liability"), get(period, "rebate_liability")),
            ("Right to recover returned goods (asset)", get(open_p, "return_asset"), get(period, "return_asset")),
            ("Inventory: cost of goods not yet transferred", get(open_p, "deferred_cost"), get(period, "deferred_cost"))]
    t = pd.DataFrame(rows, columns=["Balance", "Opening", "Closing"])
    t["Movement"] = t["Closing"] - t["Opening"]
    return t


def remaining_obligations(a: Analysis, period: pd.Period) -> pd.DataFrame:
    """IFRS 15.120: transaction price allocated to obligations not yet satisfied at the month-end, for contracts invoiced
    to date, plus contracted amounts not yet invoiced when the terms give a Contract Value per contract (order book). With
    the IFRS 15.121(a) expedient, contracts with an original expected duration of one year or less are left out."""
    d, sch = a.model.lines, a.model.schedule
    po = d[d["pattern"].isin(["point", "over_time"]) & (d["inv_period"] <= period)].copy()
    lastd = po["end"].where(po["pattern"] == "over_time", po["control_date"])
    dur = (lastd.groupby(po["contract_id"]).max() - po.groupby("contract_id")["invoice_date"].min()).dt.days
    short = set(dur[dur.notna() & (dur <= 366)].index)
    # the contract's original expected duration from the terms wins; a contract with a stated contract value but no term
    # is treated as longer than a year (its full length is not visible from the invoices raised so far)
    t_ = a.terms
    if t_ is not None and len(t_) and "contract_id" in t_:
        for r in t_.to_dict("records"):
            k = r.get("contract_id")
            if not isinstance(k, str):
                continue
            months = r.get("contract_months")
            if months is not None and pd.notna(months):
                (short.add if float(months) <= 12 else short.discard)(k)
            elif r.get("contract_value") is not None and pd.notna(r.get("contract_value")):
                short.discard(k)
    excluded = 0.0
    fut = sch[sch["line_id"].isin(po["line_id"]) & (sch["period"] > period)].merge(po[["line_id", "contract_id", "pattern"]], on="line_id")
    if a.model.settings.rpo_expedient:
        ex = fut["contract_id"].isin(short)
        excluded = float(fut.loc[ex, "amount"].sum())
        fut = fut[~ex]
    # contracted amounts not yet invoiced (order book), from 'Contract Value' in the terms; the 121(a) expedient applies to
    # it as well when the contract's expected duration (from its invoiced lines) is one year or less
    ob, ob_short = 0.0, 0.0
    t = a.terms
    if t is not None and len(t) and "contract_value" in t:
        cv = t[t["contract_id"].map(lambda v: isinstance(v, str)) & t["contract_value"].notna()]
        if len(cv):
            billed = d[d["pattern"].isin(["point", "over_time", "discount"]) & (d["inv_period"] <= period)] \
                .groupby("contract_id")["L1"].sum()
            for c, v in zip(cv["contract_id"], cv["contract_value"]):
                left = max(0.0, float(v) - float(billed.get(c, 0.0)))
                if a.model.settings.rpo_expedient and c in short:
                    ob_short += left
                else:
                    ob += left
    cols = ["When", "Over time", "At a point in time", "Total"]
    if ob >= 0.5:
        fut = pd.concat([fut, pd.DataFrame({"line_id": [-1], "period": [M.UNKNOWN], "amount": [ob], "contract_id": ["(order book)"],
                                            "pattern": ["order_book"]})], ignore_index=True)
    if fut.empty:
        t = pd.DataFrame(columns=cols)
    else:
        ahead = fut["period"].map(lambda p: (p - period).n)
        fut["When"] = np.select([fut["pattern"] == "order_book", fut["period"] == M.UNKNOWN, ahead <= 12, ahead <= 24],
                                ["Contracted, not yet invoiced (timing to confirm)", "On acceptance or delivery (date not known)",
                                 "Within 12 months", "In 13–24 months"], "After 24 months")
        fut["Type"] = np.where(fut["pattern"] == "over_time", "Over time", "At a point in time")
        fut.loc[fut["pattern"] == "order_book", "Type"] = "Not yet scheduled"
        t = fut.pivot_table(index="When", columns="Type", values="amount", aggfunc="sum").fillna(0.0)
        if "Not yet scheduled" in t.columns:
            cols = ["When", "Over time", "At a point in time", "Not yet scheduled", "Total"]
        for c in cols[1:-1]:
            if c not in t.columns:
                t[c] = 0.0
        t["Total"] = t[cols[1:-1]].sum(axis=1)
        order = ["Within 12 months", "In 13–24 months", "After 24 months", "On acceptance or delivery (date not known)",
                 "Contracted, not yet invoiced (timing to confirm)"]
        t = t.reindex([o for o in order if o in t.index])
        t.loc["Total"] = t.sum()
        t = t.reset_index()[cols]
    t.attrs["excluded_short"] = excluded + ob_short
    t.attrs["order_book"] = ob
    return t


# ── journals ─────────────────────────────────────────────────────────────────
JOURNAL_COLS = ["Debit account", "Credit account", "Amount", "Reverses next month", "Narrative"]


def journals(a: Analysis, period: pd.Period) -> pd.DataFrame:
    """Month-end IFRS 15 adjustments on top of invoice-based books. Balance entries reverse on the first day of next
    month; the agent reclassification is for the month only and does not reverse."""
    b = a.balances.loc[period]
    agent_m = -float(a.causes.loc[period, C_AGENT])
    rows = []

    def add(dr, cr, amt, why, rev=True):
        if abs(amt) >= 0.005:
            if amt < 0:
                dr, cr, amt = cr, dr, -amt
            rows.append({"Debit account": dr, "Credit account": cr, "Amount": round(amt, 2),
                         "Reverses next month": "Yes" if rev else "No", "Narrative": why})

    add("Revenue", "Contract liabilities", b["contract_liability"],
        "Amounts invoiced and due (or paid) for obligations not yet satisfied: subscriptions billed in advance, goods not "
        "yet transferred, set-up fees, allocation to later obligations")
    add("Revenue", "Trade receivables", b["billed_not_due"],
        "Invoices issued ahead of performance and not yet due: not a receivable until due (IFRS 15.106)")
    add("Contract assets", "Revenue", b["contract_asset"], "Revenue recognised for obligations satisfied but not yet invoiced "
        "while other obligations in the contract remain (right conditional)")
    add("Trade receivables (unbilled)", "Revenue", b["unbilled_receivable"], "Revenue recognised and not yet invoiced where "
        "only the passage of time is needed (right unconditional)")
    add("Revenue", "Refund liabilities: returns", b["refund_liability"], "Returns expected on recent sales")
    add("Right to recover returned goods", "Cost of sales", b["return_asset"], "Cost of the goods expected to be returned")
    add("Revenue", "Refund liabilities: rebates", b["rebate_liability"], "Volume rebates earned and not yet settled")
    add("Inventory", "Cost of sales", b["deferred_cost"], "Cost of goods invoiced but not yet transferred (negative: cost of "
        "goods transferred before invoicing)")
    add("Revenue", "Cost of sales", agent_m, "Pass-through recharges for the month presented net (agent)", rev=False)
    return pd.DataFrame(rows, columns=JOURNAL_COLS)


def _revenue_effect(j: pd.DataFrame) -> float:
    if j.empty:
        return 0.0
    dr = j.loc[j["Debit account"] == "Revenue", "Amount"].sum()
    cr = j.loc[j["Credit account"] == "Revenue", "Amount"].sum()
    return float(cr - dr)


def posted_revenue_effect(posted: pd.DataFrame, revenue_codes=("Revenue",)) -> float:
    """Revenue effect of last month's posted reversing entries, from a journal upload file in this app's layout (or any
    file with account, debit, credit and reversal date columns)."""
    if posted is None or posted.empty:
        return 0.0
    cols = {str(c).strip().lower().replace(" ", "_"): c for c in posted.columns}
    acc = posted[cols.get("account", cols.get("gl_account", list(posted.columns)[0]))].astype(str).str.strip()
    code = posted[cols["gl_code"]].astype(str).str.strip() if "gl_code" in cols else acc
    dr = pd.to_numeric(posted[cols.get("debit")], errors="coerce").fillna(0.0) if "debit" in cols else 0.0
    cr = pd.to_numeric(posted[cols.get("credit")], errors="coerce").fillna(0.0) if "credit" in cols else 0.0
    rev = acc.isin(revenue_codes) | code.isin(revenue_codes)
    reverses = posted[cols["reversal_date"]].astype(str).str.strip().replace({"nan": "", "None": ""}) != "" \
        if "reversal_date" in cols else pd.Series(True, index=posted.index)
    return float((cr - dr)[rev & reverses].sum())


def journal_reconciliation(a: Analysis, period: pd.Period, posted_prev: Optional[float] = None) -> pd.DataFrame:
    """Prior month's reversing entries reversed + this month's entries = the month's gap. Last month's entries are
    re-measured from today's data; when last month's posted entries are supplied (`posted_prev`, their revenue effect),
    the difference is shown as a change in last month's estimate."""
    j = journals(a, period)
    rev_now = _revenue_effect(j[j["Reverses next month"] == "Yes"])
    agent = _revenue_effect(j[j["Reverses next month"] == "No"])
    prev = period - 1
    rev_prev = _revenue_effect(journals(a, prev)[lambda x: x["Reverses next month"] == "Yes"]) if prev in a.periods else 0.0
    gap = gap_summary(a, period)["gap_month"]
    rows = [("Reverse last month's balance entries", -rev_prev), ("Post this month's balance entries", rev_now),
            ("Agent reclassification for the month", agent), ("Net effect on revenue", -rev_prev + rev_now + agent),
            ("Gap on the dashboard (IFRS 15 − booked)", gap)]
    t = pd.DataFrame(rows, columns=["Step", "Effect on revenue"])
    t.attrs["ties"] = abs((-rev_prev + rev_now + agent) - gap) < max(0.05 * (len(j) + 1), 1e-9 * abs(gap))
    if posted_prev is not None:
        change = rev_prev - posted_prev
        t = pd.concat([t, pd.DataFrame([("Last month's entries as posted", posted_prev),
                                        ("Last month's entries re-measured today", rev_prev),
                                        ("Change in last month's estimate (new information)", change)],
                                       columns=t.columns)], ignore_index=True)
        t.attrs.update(ties=abs((-rev_prev + rev_now + agent) - gap) < max(0.05 * (len(j) + 1), 1e-9 * abs(gap)),
                       change_in_estimate=change)
    return t


def contract_detail(a: Analysis, contract_id: str, period: Optional[pd.Period] = None) -> pd.DataFrame:
    """A contract's lines with what has been recognised up to the review month (`period`, default the last month)."""
    d = a.model.lines
    x = d[d["contract_id"] == contract_id].copy()
    sch = a.model.schedule
    upto = period if period is not None else a.periods[-1]
    rec = sch[sch["line_id"].isin(x["line_id"]) & (sch["period"] != M.UNKNOWN) & (sch["period"] <= upto)] \
        .groupby("line_id")["amount"].sum()
    x["recognised_to_date"] = x["line_id"].map(rec).fillna(0.0)

    def when(p, s, e, c, cause):
        if p == "over_time":
            return f"Over time, {s:%d %b %Y} – {e:%d %b %Y}"
        if p == "point":
            if pd.notna(c):
                return f"Point in time, {c:%d %b %Y}"
            return "Not yet: awaiting " + ("acceptance" if cause == "acceptance" else "delivery")
        return {"pass_through": "Agent: margin only", "discount": "Discount: allocated to the obligations"}.get(p, "Settlement")
    x["when"] = [when(*v) for v in zip(x["pattern"], x["start"], x["end"], x["control_date"], x["cause"])]
    return x


def contract_balances_at(a: Analysis, period: pd.Period) -> pd.DataFrame:
    b = M.contract_balances(a.model, period)
    cols = ["contract_asset", "unbilled_receivable", "contract_liability", "billed_not_due"]
    b = b[(b[cols].abs() >= 0.5).any(axis=1)]
    return b[["contract_id", "customer", "billed", "recognised"] + cols].sort_values("contract_liability", ascending=False) \
        .reset_index(drop=True)


def contracts_ranked(a: Analysis, period: pd.Period) -> pd.DataFrame:
    d, sch = a.model.lines, a.model.schedule
    ytd = fy_months(a, period)
    po = d[d["pattern"].isin(["point", "over_time", "pass_through", "discount"])]
    booked = po[po["inv_period"].isin(ytd)].groupby("contract_id")["L0"].sum()
    s = sch[sch["line_id"].isin(po["line_id"]) & sch["period"].isin(ytd)].merge(po[["line_id", "contract_id"]], on="line_id")
    ifrs = s.groupby("contract_id")["amount"].sum()
    t = pd.concat([booked.rename("booked_ytd"), ifrs.rename("ifrs15_ytd")], axis=1).fillna(0.0)
    t["effect"] = t["ifrs15_ytd"] - t["booked_ytd"]
    info = po.groupby("contract_id").agg(customer=("customer", "first"), kinds=("kind", lambda k: ", ".join(sorted(set(k)))))
    t = t.join(info)
    t.index.name = "contract_id"
    return t.reindex(t["effect"].abs().sort_values(ascending=False).index).reset_index()


# ── judgements ───────────────────────────────────────────────────────────────
def sensitivity(reg, terms, ssp, base: M.Settings, period: pd.Period, progress=None) -> pd.DataFrame:
    """Revenue and balances under alternative judgements, each changed one at a time."""
    from dataclasses import replace
    variants = [("Current settings", {})]
    variants += [(f"Confidence {c:.0%}", {"confidence": c}) for c in (0.75, 0.95) if abs(c - base.confidence) > 1e-9]
    variants += [(f"Significance {x:.0%}", {"significance": x}) for x in (0.01, 0.05) if abs(x - base.significance) > 1e-9]
    variants += [("Pass-through presented gross (principal)" if base.passthrough_agent else "Pass-through presented net (agent)",
                  {"passthrough_agent": not base.passthrough_agent}),
                 ("Set-up fees distinct" if not base.setup_distinct else "Set-up fees not distinct",
                  {"setup_distinct": not base.setup_distinct}),
                 ("One-off services over time" if not base.services_over_time else "One-off services on completion",
                  {"services_over_time": not base.services_over_time}),
                 ("Discounts to specific obligations (82)" if not base.discount_specific else "Discounts allocated pro rata",
                  {"discount_specific": not base.discount_specific})]
    rows = []
    for k, (name, kw) in enumerate(variants):
        if progress:
            progress(k / len(variants), name)
        an = analyse(reg, terms, ssp, replace(base, **kw))
        g = gap_summary(an, period)
        b = an.balances.loc[period]
        rows.append({"Judgement": name, "IFRS 15 revenue (month)": g["ifrs15_month"], "IFRS 15 revenue (fiscal YTD)": g["ifrs15_ytd"],
                     "Contract liabilities": b["contract_liability"], "Advance invoices not yet due": b["billed_not_due"],
                     "Refund liabilities": b["refund_liability"] + b["rebate_liability"]})
    t = pd.DataFrame(rows)
    for c in t.columns[1:]:
        t[f"Δ {c}"] = t[c] - t[c].iloc[0]
    return t


# ── draft note ───────────────────────────────────────────────────────────────
def draft_note(a: Analysis, period: pd.Period, unit: str, fmt) -> str:
    g = gap_summary(a, period)
    s = a.model.settings
    d = a.model.lines
    rf_t = balances_rollforward(a, period)
    rf_attrs = rf_t.attrs
    rf = rf_t.set_index("Balance")
    ob = other_balances(a, period).set_index("Balance")
    rpo = remaining_obligations(a, period)
    dis = disaggregation(a, period)["Timing of transfer"]
    month_end = period.to_timestamp(how="end")
    kinds = set(d["kind"])
    pt = float(dis.loc[dis.iloc[:, 0] == "At a point in time", "Fiscal YTD"].sum())
    ot = float(dis.loc[dis.iloc[:, 0] == "Over time", "Fiscal YTD"].sum())
    total_rpo = float(rpo.loc[rpo["When"] == "Total", "Total"].sum()) if len(rpo) else 0.0
    within = float(rpo.loc[rpo["When"] == "Within 12 months", "Total"].sum()) if len(rpo) else 0.0
    cl_open, cl_close = rf.loc["Contract liabilities", "Opening"], rf.loc["Contract liabilities", "Closing"]
    ca_close = rf.loc["Contract assets", "Closing"]
    nd_close = rf.loc["Advance invoices not yet due (not receivables)", "Closing"]
    reasons = deferred_by_reason(a, period)
    reasons = reasons[reasons > 0.5].head(3)
    pol = ["Revenue is recognised when, or as, control of goods or services passes to the customer, at the amount the "
           "company expects to be entitled to."]
    goods = "Goods are recognised when control passes to the customer, normally on delivery"
    if (d["cause"] == "acceptance").any():
        goods += ", or on customer acceptance where the contract contains a substantive acceptance clause"
    pol.append(goods + ".")
    if (d["cause"] == "bill_and_hold").any() or "Bill-and-hold" in " ".join(d["note"].astype(str)):
        pol.append("Bill-and-hold arrangements are recognised before delivery only when all the criteria in IFRS 15.B81 are met.")
    ot_k = [k for k, w in [("Subscription", "subscriptions and maintenance contracts"), ("Warranty", "extended warranties")] if k in kinds]
    if ot_k or (d["pattern"] == "over_time").any():
        pol.append("Subscriptions, maintenance contracts, service-type warranties and services performed over a period are "
                   "recognised on a straight-line basis over the service period.")
    if "Setup" in kinds:
        pol.append("Set-up and onboarding fees are " + ("recognised when the set-up is complete, as a distinct service." if s.setup_distinct
                   else "not a separate service and are recognised over the service to which they relate."))
    if "Licence" in kinds:
        pol.append("Licences of software and other intellectual property give the customer a right to use the intellectual "
                   "property as it exists when the licence is granted, and are recognised when the customer can first use the "
                   "licence (IFRS 15.B58–B61). Licences that give a right to access are accounted for as subscriptions.")
    if (d["alloc_method"] != "").any():
        used82 = d["alloc_method"].astype(str).str.contains("82").any()
        pol.append("Where a contract contains more than one performance obligation, the transaction price, including any discount, "
                   "is allocated on the basis of relative stand-alone selling prices"
                   + (", except that a discount is allocated to particular obligations where management has concluded that "
                      "the criteria in IFRS 15.82 are met."
                      if used82 else ".")
                   + " Changes in the transaction price after inception are allocated on the same basis; amounts allocated to "
                     "obligations already satisfied are recognised in the period of the change.")
    if "Pass-through" in kinds:
        pol.append("For freight and other services provided by third parties, the company assesses whether it controls the service "
                   "before it is transferred to the customer; " + ("where it does not, it acts as an agent and recognises only its "
                   "margin." if s.passthrough_agent else "it acts as principal and recognises the gross amount."))
    vc = ("Variable consideration is estimated using the expected value for returns, based on the company's own return history, "
          "and the most likely amount for volume rebates with a single threshold. Estimates are included in the transaction price "
          f"only to the extent that it is highly probable (assessed at a {s.confidence:.0%} confidence level) that a significant "
          f"reversal (above {s.significance:.0%} of the revenue concerned) will not occur.")
    lines_ = [f"Revenue from contracts with customers (draft for review: {fy_label(a, period)}, to {month_end:%d %B %Y})", "",
              "Accounting policy", " ".join(pol), "", vc, "",
              "Disaggregation",
              f"Revenue for the year to date was {fmt(g['ifrs15_ytd'])}: {fmt(pt)} at a point in time and {fmt(ot)} over time "
              "(returns and rebates are deducted from goods recognised at a point in time).", "",
              "Contract balances",
              f"Contract liabilities were {fmt(cl_close)} at {month_end:%d %B %Y} (opening {fmt(cl_open)}) and contract "
              f"assets {fmt(ca_close)}."
              + (f" Invoices of {fmt(nd_close)} issued before the related goods or services were transferred, and not yet due "
                 "for payment, are presented neither as receivables nor as contract liabilities." if nd_close >= 0.5 else "")
              + (" At the month-end, amounts invoiced but not yet recognised as revenue related mainly to: "
                 + "; ".join(f"{k.lower()} {fmt(v)}" for k, v in reasons.items()) + "." if len(reasons) else "")
              + (f" Revenue of {fmt(rf_attrs['revenue_from_opening_liability'])} recognised in the period was included in the "
                 "opening contract liability." if rf_attrs.get("has_opening") and cl_open >= 0.5 else "")
              + " Refund liabilities for expected returns and rebates were "
              f"{fmt(ob.loc['Refund liability: expected returns', 'Closing'] + ob.loc['Refund liability: customer rebates', 'Closing'])}.", "",
              "Remaining performance obligations",
              f"The transaction price allocated to performance obligations not yet satisfied was {fmt(total_rpo)}, of which "
              f"{fmt(within)} is expected to be recognised within 12 months"
              + (f" and {fmt(rpo.attrs.get('order_book', 0.0))} is contracted but not yet invoiced, with timing to be confirmed."
                 if rpo.attrs.get("order_book", 0.0) >= 0.5 else ".")
              + (" As permitted by IFRS 15.121, contracts with an original expected duration of one year or less are not included."
                 if s.rpo_expedient else "")]
    ff = financing_flags(a, period)
    if len(ff):
        lines_ += ["", "Significant financing component",
                   "Some customers pay more than 12 months after transfer, or pay in advance for services longer than 12 months. "
                   "Management should assess whether these contracts contain a significant financing component; no adjustment "
                   "has been made in this analysis."]
    return "\n".join(lines_) + "\n"
