"""
variable.py — variable consideration: expected returns and customer rebates.

Two steps, as IFRS 15 requires:

1. ESTIMATE (IFRS 15.53)
   • Returns, a large portfolio of similar sales: the EXPECTED VALUE (B23) — the return rate measured from the company's
     own history, by customer group, using only sale months whose return window had closed before the month being
     provided for (no hindsight).
   • Tiered volume rebates, a single threshold: the MOST LIKELY AMOUNT — the customer's remaining purchases for the year
     are simulated by bootstrapping its own monthly purchases (scaled for this year's growth); the rebate is the most
     likely amount when the probability of reaching the tier is at least 50%.
   • Flat rebates: certain, accrued on every sale.

2. CONSTRAIN (IFRS 15.56–57): include revenue only to the extent it is HIGHLY PROBABLE that a SIGNIFICANT reversal will not
   occur. Two user settings make that test explicit: the confidence level (e.g. 90%) and the significance threshold
   (e.g. 2% of the revenue concerned).
   • Returns: at the confidence level the return rate could be q instead of the expected e. If the extra reversal (q − e)
     exceeds the significance threshold, the provision is raised to q − threshold; otherwise the expected value stands.
   • Rebates: when the most likely amount is nil but the probability of reaching the tier is at least 1 − confidence AND
     the rebate rate itself is significant, the rebate is accrued — recognising the full price would risk a significant
     reversal.

Settlement: rebate credit notes reduce the year named in the credit note (e.g. "FY2025", "FY2024-25"), otherwise the
oldest finished year still open, otherwise the current year. A finished year closes when its accrual is fully settled
(any excess is a true-up to revenue) or when the settlement window ends (any unpaid balance is released).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .model import UNKNOWN, Model, customer_terms, fiscal_year, fy_start_period, terms_lookup


@dataclass
class VariableResult:
    refund_by_group: pd.DataFrame
    rebate_by_customer: pd.DataFrame
    return_rates: pd.DataFrame
    rebate_table: pd.DataFrame
    rebate_sims: Dict = field(default_factory=dict)      # (customer, fy, period) → simulated year totals
    return_window: int = 1
    cost_ratio: Optional[float] = None
    group_of: Dict = field(default_factory=dict)          # customer → customer group
    warnings: List[str] = field(default_factory=list)

    @property
    def refund_liability(self) -> pd.Series:
        return self.refund_by_group.sum(axis=1)

    @property
    def rebate_liability(self) -> pd.Series:
        return self.rebate_by_customer.sum(axis=1)


def _goods_recognised(m: Model) -> pd.DataFrame:
    g = m.lines[m.lines["kind"] == "Goods"][["line_id", "customer", "customer_group"]]
    s = m.schedule[(m.schedule["period"] != UNKNOWN)].merge(g, on="line_id")
    s = s[s["period"] <= m.periods[-1]]
    return s.groupby(["customer", "customer_group", "period"], as_index=False)["amount"].sum()


def _quantile(hist: np.ndarray, conf: float) -> float:
    if len(hist) >= 12:
        return float(np.quantile(hist, conf))
    return float(np.mean(hist) + NormalDist().inv_cdf(conf) * (np.std(hist, ddof=1) if len(hist) > 1 else 0.0))


def returns(m: Model, terms: pd.DataFrame) -> Dict:
    d, P, s = m.lines, m.periods, m.settings
    lk = terms_lookup(terms)
    warns: List[str] = []
    gr = _goods_recognised(m)
    ret = d[d["kind"] == "Return"].copy()
    ret["R"] = -ret["amount"]
    has_hist = set(ret["customer"])

    def eligible(c):
        t = customer_terms(lk, c)
        if t:
            return bool(t.get("return_rights"))
        rows = [r for r in lk["contract"].values() if r.get("customer") == c]
        return any(bool(r.get("return_rights")) for r in rows) if rows else c in has_hist
    elig = {c for c in d["customer"].unique() if eligible(c)}
    inv_month = d.drop_duplicates("invoice_no").set_index("invoice_no")["inv_period"]
    ret["sale_period"] = ret["ref_invoice"].map(inv_month)
    matched = ret["sale_period"].notna()
    lags = [(a - b).n for a, b in zip(ret.loc[matched, "inv_period"], ret.loc[matched, "sale_period"]) if (a - b).n >= 0]
    lag = int(np.median(lags)) if lags else 1
    window = int(np.clip(np.ceil(np.quantile(lags, 0.95)) if lags else 1, 1, 6))
    if len(ret) and matched.mean() < 0.5:
        warns.append(f"Most credit notes have no reference invoice, so returns are matched to sales {lag} month(s) earlier.")
    ret.loc[~matched, "sale_period"] = ret.loc[~matched, "inv_period"] - lag
    ret = ret[ret["customer"].isin(elig)]
    gr = gr[gr["customer"].isin(elig)]
    groups = sorted(gr["customer_group"].unique())
    rates, liab = [], pd.DataFrame(index=P)
    nP = len(P)
    for g in groups:
        sales = gr[gr["customer_group"] == g].groupby("period")["amount"].sum().reindex(P).fillna(0.0).to_numpy()
        rg = ret[(ret["customer_group"] == g) & ret["sale_period"].isin(P)]
        si = np.array([(x - P[0]).n for x in rg["sale_period"]], dtype=int)
        ri = np.clip(np.array([(x - P[0]).n for x in rg["inv_period"]], dtype=int), 0, nP - 1)
        RM = np.zeros((nP, nP))                                   # returns by sale month × month received
        np.add.at(RM, (si, ri), rg["R"].to_numpy())
        r_by_sale = RM.sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            actual = np.where(sales > 0, r_by_sale / np.where(sales > 0, sales, 1), np.nan)
        RMc = RM.cumsum(1)                                        # returns received up to each month, per sale month
        e_, q_, used, nh = [], [], [], []
        for i in range(nP):
            closed = [j for j in range(i) if j <= i - window and sales[j] > 0][-24:]   # windows closed before month i
            # no hindsight: only returns received before month i count, even for sale months long closed
            known = RMc[closed, i - 1] if (i > 0 and closed) else np.zeros(0)
            hist = known / sales[closed] if len(closed) else np.zeros(0)
            hist = hist[np.isfinite(hist)]
            if len(hist) >= 3:
                e = float(known.sum() / sales[closed].sum())
                q = max(_quantile(hist, s.confidence), e)
                u = max(e, q - s.significance)
            else:
                e = q = u = np.nan
            e_.append(e); q_.append(q); used.append(u); nh.append(len(hist))
        df = pd.DataFrame({"group": g, "period": P, "sales": sales, "returns": r_by_sale, "actual_rate": actual,
                           "expected_rate": e_, "rate_at_confidence": q_, "constrained_rate": used, "history_months": nh})
        df["window_closed"] = [i <= nP - 1 - window for i in range(nP)]
        first = df["constrained_rate"].first_valid_index()
        if first is None:
            warns.append(f"{g}: not enough return history for an estimate; returns reduce revenue as credit notes are issued.")
            df[["expected_rate", "rate_at_confidence", "constrained_rate"]] = 0.0
        elif first > 0:
            warns.append(f"{g}: the first {first} month(s) have no closed return history, so the earliest estimate is applied to "
                         "them (the only use of later information).")
            df[["expected_rate", "rate_at_confidence", "constrained_rate"]] = df[["expected_rate", "rate_at_confidence",
                                                                                  "constrained_rate"]].bfill()
        rates.append(df)
        prov = df["constrained_rate"].to_numpy() * sales
        cumrec = RM.cumsum(1)                                     # returns received up to each month, per sale month
        L = np.zeros(nP)
        for i in range(nP):
            for j in range(max(0, i - window + 1), i + 1):       # sale months whose return window is still open
                L[i] += max(prov[j] - cumrec[j, i], 0.0)
        liab[g] = L
    rr = pd.concat(rates, ignore_index=True) if rates else pd.DataFrame()
    costed = d[(d["kind"] == "Return") & d["cost_amount"].notna() & (d["amount"] != 0)]
    ratio = abs(float(costed["cost_amount"].sum() / costed["amount"].sum())) if len(costed) and costed["amount"].sum() else None
    if ratio is None:
        gl = d[(d["kind"] == "Goods") & d["cost_amount"].notna() & (d["amount"] > 0)]
        ratio = abs(float(gl["cost_amount"].sum() / gl["amount"].sum())) if len(gl) else None
    if ratio is not None:
        ratio = min(ratio, 1.0)
    return {"liability": liab, "rates": rr, "window": window, "cost_ratio": ratio, "warnings": warns}


SETTLEMENT_WINDOW = 6     # months after the year end in which rebates for that year are expected to be settled


def _named_fy(text: str) -> Optional[int]:
    """The fiscal year a rebate credit note names: 'FY2025', 'FY25', 'FY 2024-25' and 'FY2024/2025' all mean the year
    ending in 2025 (years are labelled by the calendar year in which they end)."""
    m = re.search(r"FY\s?'?(\d{2,4})(?:\s?[-/–]\s?(\d{2,4}))?", text, re.I)
    if not m:
        return None
    y = m.group(2) or m.group(1)
    y = int(y)
    return y + 2000 if y < 100 else y


def _simulate(ytd, hist, remaining, growth, n, rng):
    if remaining <= 0 or len(hist) == 0:
        return np.full(n, ytd)
    return ytd + (rng.choice(hist, size=(n, remaining), replace=True) * growth).sum(axis=1)


def rebates(m: Model, terms: pd.DataFrame, seed: int = 15) -> Dict:
    P, s = m.periods, m.settings
    fy0, conf, sig = s.fy_start_month, s.confidence, s.significance
    lk = terms_lookup(terms)
    gr = _goods_recognised(m)
    d = m.lines
    warns: List[str] = []
    reb = d[d["kind"] == "Rebate"].copy()
    reb["S"] = -reb["amount"]
    def rterms(c):
        """The customer's rebate terms: its own row when it has a rebate, else a contract row for it that has one."""
        t = customer_terms(lk, c)
        if t.get("rebate_type") in ("Flat", "Tiered"):
            return t
        return next((r for r in lk["contract"].values() if r.get("customer") == c and r.get("rebate_type") in ("Flat", "Tiered")),
                    t)
    from_contract = [c for c in d["customer"].unique() if customer_terms(lk, c).get("rebate_type") not in ("Flat", "Tiered")
                     and rterms(c).get("rebate_type") in ("Flat", "Tiered")]
    if from_contract:
        warns.append(f"Rebate terms given on a contract row for {', '.join(map(str, from_contract[:4]))} are applied to all of "
                     "that customer's purchases (rebates are measured per customer).")
    custs = [c for c in d["customer"].unique() if rterms(c).get("rebate_type") in ("Flat", "Tiered")]
    n_sims = 2000                         # fixed, whatever the file size: Monte Carlo error on a probability ≤ ±2.2 pts (95%)
    rows, sims = [], {}
    liab = pd.DataFrame(index=P)
    rng = np.random.default_rng(seed)
    multi = [c for c in custs if c in lk["customer"] and any(r.get("customer") == c and r.get("rebate_type") in ("Flat", "Tiered")
                                                         for r in lk["contract"].values())]
    if multi:
        warns.append(f"Rebates are measured per customer: contract-level rebate terms for {', '.join(map(str, multi[:4]))} are "
                     "ignored because a customer-level row exists.")
    unmapped = sorted(set(reb["customer"]) - set(custs))
    if unmapped:
        warns.append(f"Rebate credit notes for {', '.join(map(str, unmapped[:4]))} have no rebate terms; they reduce revenue when issued.")
    for c in custs:
        t = rterms(c)
        kind = t["rebate_type"]
        pct = float(t.get("rebate_pct")) if t.get("rebate_pct") is not None and np.isfinite(t.get("rebate_pct")) else np.nan
        thr = t.get("rebate_threshold")
        thr = float(thr) if thr is not None and np.isfinite(thr) else np.nan
        if not np.isfinite(pct) or (kind == "Tiered" and not np.isfinite(thr)):
            warns.append(f"{c}: the rebate % or threshold in the terms could not be read, so no rebate is estimated.")
            continue
        base = gr[gr["customer"] == c].groupby("period")["amount"].sum().reindex(P).fillna(0.0)
        fys = sorted({fiscal_year(p, fy0) for p in P})
        fy_end = {fy: fy_start_period(fy, fy0) + 11 for fy in fys}
        # decisions per fiscal year and month
        dec = {}
        for fy in fys:
            months = [p for p in P if fiscal_year(p, fy0) == fy]
            for mth in months:
                ytd = float(base.reindex([p for p in months if p <= mth]).sum())
                prob, ml, rate, why = np.nan, pct, pct, "Flat rebate: certain"
                if kind == "Tiered":
                    if mth >= fy_end[fy]:
                        prob = 1.0 if ytd >= thr else 0.0
                        ml = rate = pct if prob == 1 else 0.0
                        why = "Year complete: " + ("threshold reached" if prob == 1 else "threshold missed")
                    else:
                        remaining = (fy_end[fy] - mth).n
                        # the customer's own last 12 months, including months with no purchases (once it started buying)
                        started = base[base > 0].index.min() if (base > 0).any() else mth
                        hist = base[(base.index <= mth) & (base.index > mth - 12) & (base.index >= started)].to_numpy()
                        ly = float(base.reindex([p - 12 for p in months if p <= mth]).sum())
                        growth = float(np.clip(ytd / ly, 0.7, 1.5)) if ly > 0 else 1.0
                        sim = _simulate(ytd, hist, remaining, growth, n_sims, rng)
                        prob = float(np.mean(sim >= thr))
                        sims[(c, fy, mth)] = sim.astype(np.float32)
                        ml = pct if prob >= 0.5 else 0.0
                        if ml > 0:
                            rate, why = pct, "Most likely amount: tier reached"
                        elif prob >= 1 - conf and pct >= sig:
                            rate, why = pct, "Constraint: reaching the tier is not unlikely enough and the rebate is significant"
                        else:
                            rate, why = 0.0, "Most likely amount: tier missed; reversal risk not significant"
                dec[(fy, mth)] = (ytd, prob, ml, rate, why)
        # settlements: named year, otherwise oldest outstanding year first
        applied = {fy: 0.0 for fy in fys}
        final = set()
        sett = reb[reb["customer"] == c].sort_values("invoice_date")
        sett_by_month = {}
        for r in sett.itertuples():
            sett_by_month.setdefault(r.inv_period, []).append((_named_fy(str(r.item_desc or "")), r.S))
        last_dec = {}
        L = np.zeros(len(P))
        for i, mth in enumerate(P):
            for fy in fys:
                if (fy, mth) in dec:
                    last_dec[fy] = dec[(fy, mth)]
            accr = {fy: (last_dec[fy][3] * last_dec[fy][0] if fy in last_dec else 0.0) for fy in fys}
            for named, amt in sett_by_month.get(mth, []):
                target = named if named in accr else None
                if target is None:
                    # unnamed credits settle the oldest open year that has ended, then the current year
                    open_fys = [fy for fy in fys if fy in last_dec and fy not in final and accr[fy] - applied[fy] > 0.5
                                and fy_end[fy] < mth]
                    target = open_fys[0] if open_fys else fiscal_year(mth, fy0)
                applied[target] = applied.get(target, 0.0) + amt
                # a finished year closes once its accrual is fully settled (any excess is a true-up to revenue)
                if mth > fy_end.get(target, mth) and applied[target] >= accr.get(target, 0.0) - 0.5:
                    final.add(target)
            # …or when the settlement window ends: anything still unpaid is released to revenue
            for fy in fys:
                if fy in last_dec and fy not in final and fy_end[fy] + SETTLEMENT_WINDOW < mth:
                    final.add(fy)
            for fy in fys:
                if fy not in last_dec:
                    continue
                bal = 0.0 if fy in final else max(accr[fy] - applied[fy], 0.0)
                L[i] += bal
                ytd, prob, ml, rate, why = last_dec[fy]
                if mth <= fy_end[fy] or bal > 0.5:
                    rows.append({"customer": c, "fiscal_year": fy, "period": mth, "rebate_type": kind, "rebate_pct": pct,
                                 "threshold": thr if kind == "Tiered" else np.nan, "ytd_purchases": ytd, "probability": prob,
                                 "most_likely_rate": ml, "accrual_rate": rate, "basis": why,
                                 "expected_value_rate": prob * pct if np.isfinite(prob) else pct,
                                 "accrued": rate * ytd, "settled": applied[fy], "liability": bal})
        liab[c] = L
    return {"liability": liab, "table": pd.DataFrame(rows), "sims": sims, "warnings": warns}


def run(m: Model, terms: pd.DataFrame) -> VariableResult:
    r = returns(m, terms)
    b = rebates(m, terms)
    grp = m.lines.drop_duplicates("customer").set_index("customer")["customer_group"].to_dict()
    return VariableResult(r["liability"], b["liability"], r["rates"], b["table"], b["sims"], r["window"], r["cost_ratio"],
                          grp, r["warnings"] + b["warnings"])
