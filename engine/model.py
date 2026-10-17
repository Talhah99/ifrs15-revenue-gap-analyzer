"""
model.py — the IFRS 15 five-step model applied to an invoice-level sales register.

Revenue is built in layers so every difference from the books can be explained:

  L0  As booked       every invoice line is revenue on its invoice date (the usual invoice-based policy)
  L1  Principal/agent pass-through lines (freight or media recharged to customers) are presented net when the entity
                      does not control the service before it is transferred (IFRS 15.B34–B38): revenue = amount − cost
  L2  Allocation      within a contract the transaction price, including any discount lines on the contract, is
                      allocated to the performance obligations on the relative stand-alone selling price basis
                      (IFRS 15.73–80), or, when the IFRS 15.82 criteria are evidenced, the discount is allocated only to
                      the obligations sold below their stand-alone price
  L3  Timing          each obligation is recognised when (or as) control passes (IFRS 15.31–38):
                        goods: on delivery; on customer acceptance where acceptance is substantive (B83–B86);
                               bill-and-hold before delivery only when all four B81 criteria are met
                        subscriptions, maintenance, service-type warranties, and services with a service period:
                               straight-line over the period (35(a))
                        one-off services (e.g. installation): on completion, or over time from start to completion
                               when the user selects 35(b)/(c) treatment
                        licences (right to use): when the licence is delivered AND the licence period has begun (B61)
                        set-up fees that are not distinct: with the related service, or over the expected customer
                               relationship when it is longer (B48–B51)
  L4  Variable        returns and rebates are estimated and constrained (IFRS 15.50–59); see variable.py

Contract balances (IFRS 15.105–109), per contract at each month-end:
  recognised to date − billed to date
    > 0  contract asset while obligations remain unsatisfied (right conditional), otherwise an unbilled receivable
    < 0  billing in advance of performance: the part not yet due is not a receivable (106), so the invoice is reversed
         against trade receivables; the part due or paid is a contract liability
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .io import OVER_TIME_KINDS, SETTLEMENT_KINDS

UNKNOWN = pd.Period("2200-01", "M")      # recognition date not yet known (awaiting acceptance or delivery)
TIMING_CAUSE = {
    "cutoff": "Cut-off: control passes in another month",
    "bill_and_hold": "Bill-and-hold criteria not met",
    "acceptance": "Customer acceptance",
    "over_time": "Subscriptions & services over time",
    "setup": "Set-up fees not distinct",
}


@dataclass
class Settings:
    fy_start_month: int = 7
    passthrough_agent: bool = True       # pass-through lines: the entity is an agent → present net
    setup_distinct: bool = False         # set-up / onboarding fees: distinct performance obligation?
    setup_months: int = 0                # non-distinct set-up fees: expected customer relationship in months (0 = the service term)
    services_over_time: bool = False     # one-off services (installation): over time from start to completion (35(b)/(c))
    discount_specific: bool = False      # allocate a contract discount only to items sold below stand-alone price (82)
    default_term_months: int = 12        # service period when a subscription line has no dates
    confidence: float = 0.90             # "highly probable" level for the variable-consideration constraint
    significance: float = 0.02           # a reversal is significant above this share of the revenue concerned
    allocation_tolerance: float = 0.005  # re-allocate only when invoiced prices differ from SSP by more than this
    default_payment_days: int = 30       # when no payment terms are given
    rpo_expedient: bool = True           # IFRS 15.121(a): leave out contracts of one year or less
    undelivered_if_no_date: bool = False # goods with no delivery date: not yet delivered (else delivered on the invoice date)


def fiscal_year(p: pd.Period, fy_start: int) -> int:
    return p.year if fy_start == 1 else (p.year + 1 if p.month >= fy_start else p.year)


def fy_start_period(fy: int, fy_start: int) -> pd.Period:
    return pd.Period(year=fy if fy_start == 1 else fy - 1, month=fy_start, freq="M")


# ── Step 1–2: contract terms ─────────────────────────────────────────────────
def terms_lookup(terms: pd.DataFrame) -> Dict:
    """Terms keyed by contract id (takes precedence), by customer, and — for customers whose only terms rows are
    contract-specific — the first of those rows as the customer-level fallback."""
    by_contract, by_customer, any_customer = {}, {}, {}
    if terms is not None and len(terms):
        def key(v):
            return v if isinstance(v, str) and v.strip() else None
        for r in terms.to_dict("records"):
            k, c = key(r.get("contract_id")), key(r.get("customer"))
            if k:
                by_contract[k] = r
                if c:
                    any_customer.setdefault(c, r)
            elif c:
                by_customer[c] = r
    return {"contract": by_contract, "customer": by_customer, "any": any_customer}


def line_terms(lk: Dict, contract_id, customer) -> Dict:
    """Terms for one line: its own contract's row, else the customer's row. A row for another contract of the same
    customer never applies."""
    return lk["contract"].get(contract_id) or lk["customer"].get(customer) or {}


def customer_terms(lk: Dict, customer) -> Dict:
    """Customer-level terms (rebates and return rights are measured per customer): the customer row only."""
    return lk["customer"].get(customer) or {}


# ── Step 4: stand-alone selling prices ───────────────────────────────────────
def estimate_ssp(reg: pd.DataFrame, ssp_table: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Unit stand-alone selling price per item and its basis (IFRS 15.77–79):
       1. a price list provided by the user (observable)
       2. the median unit price when the item is sold on its own (observable stand-alone sales)
       3. the median unit price across all sales, bundles included (an estimate; review it)"""
    po = reg[~reg["kind"].isin(SETTLEMENT_KINDS | {"Pass-through"}) & (reg["amount"] > 0)].copy()
    po["unit"] = np.where(po["qty"].abs() > 1e-9, po["amount"] / po["qty"].replace(0, np.nan), po["amount"])
    n_po = po.groupby("contract_id")["line_id"].transform("count")
    alone = po[n_po == 1].groupby("item_code")["unit"].median()
    cnt = po.groupby("item_code")["contract_id"].nunique()
    allm = po.groupby("item_code")["unit"].median()
    allm = allm[cnt.reindex(allm.index).fillna(0) >= 3]
    items = sorted(reg.loc[~reg["kind"].isin(SETTLEMENT_KINDS | {"Pass-through"}), "item_code"].dropna().unique())
    given = {} if ssp_table is None or ssp_table.empty else dict(zip(ssp_table["item_code"], ssp_table["ssp"]))
    rows = []
    for it in items:
        if it in given and given[it] > 0:
            rows.append((it, float(given[it]), "Price list provided"))
        elif it in alone.index and alone[it] > 0:
            rows.append((it, float(alone[it]), "Observed stand-alone sales (median)"))
        elif it in allm.index and allm[it] > 0:
            rows.append((it, float(allm[it]), "Estimate: median of all sales incl. bundles"))
        else:
            rows.append((it, np.nan, "Not available"))
    return pd.DataFrame(rows, columns=["item_code", "ssp_unit", "ssp_source"])


# ── Step 5: when control passes ──────────────────────────────────────────────
def _mi(ts: pd.Series) -> np.ndarray:
    """Month index (year*12 + month-1) of a datetime series."""
    return (ts.dt.year * 12 + ts.dt.month - 1).to_numpy()


def _over_time_dates(d: pd.DataFrame, s: Settings, warns: Optional[List[str]] = None) -> pd.DataFrame:
    """Start/end for over-time lines: subscriptions and warranties, services with a service period, one-off services
    when the user selects over-time treatment, and credit notes on those services. A credit note reduces the rest of the
    original service period (IFRS 15.87–90): the original is found by the reference invoice, else by the contract and
    item (or category) whose period is still running."""
    inv, ss, se, dl = d["invoice_date"], d["service_start"], d["service_end"], d["delivery_date"]
    ot = d["kind"].isin(OVER_TIME_KINDS)
    svc_period = (d["kind"] == "Service") & ss.notna() & se.notna() & (se > ss)
    svc_oneoff = (d["kind"] == "Service") & ~svc_period & s.services_over_time & dl.notna() & (dl > ss.fillna(inv))
    start = ss.where(ss.notna(), inv).copy()
    default_end = start + pd.DateOffset(months=s.default_term_months) - pd.Timedelta(days=1)
    end = se.where(se.notna(), default_end).copy()
    end = end.where(~svc_oneoff, dl)
    flag = (ot | svc_period | svc_oneoff).copy()
    pos = flag & (d["amount"] > 0)
    orig = pd.DataFrame({"inv": d.loc[pos, "invoice_no"], "item": d.loc[pos, "item_code"], "con": d.loc[pos, "contract_id"],
                         "kind": d.loc[pos, "kind"], "s": start[pos], "e": end[pos]})
    cand = d.index[(d["amount"] < 0) & d["kind"].isin(OVER_TIME_KINDS | {"Service"})]
    unmatched = 0
    for i in cand:
        own = pd.notna(ss.at[i]) and pd.notna(se.at[i])
        ref, it, con, kd, iv = d.at[i, "ref_invoice"], d.at[i, "item_code"], d.at[i, "contract_id"], d.at[i, "kind"], inv.at[i]
        o = orig.iloc[0:0]
        if isinstance(ref, str) and ref:
            o = orig[(orig["inv"] == ref) & (orig["item"] == it)]
            if o.empty:
                o = orig[orig["inv"] == ref]
        if o.empty:
            o = orig[(orig["con"] == con) & (orig["item"] == it) & (orig["e"] >= iv)]
            if o.empty:
                o = orig[(orig["con"] == con) & (orig["kind"] == kd) & (orig["e"] >= iv)]
        if len(o) and not own:
            r = o.sort_values("e").iloc[-1]
            flag.at[i] = True
            end.at[i] = r["e"]
            start.at[i] = max(iv, r["s"])
        elif not len(o) and flag.at[i] and not own:
            unmatched += 1
    if unmatched and warns is not None:
        warns.append(f"{unmatched} subscription / warranty credit note(s) match no original service period; each is spread over "
                     f"{s.default_term_months} months from its own date. Add the reference invoice or service dates.")
    end = end.where(end >= start, start)
    return pd.DataFrame({"ot": flag, "start": start, "end": end})


def _recognise(d: pd.DataFrame, lk: Dict, s: Settings, warns: List[str]) -> pd.DataFrame:
    """Pattern, control date / service period, cause and note for every performance-obligation line."""
    otd = _over_time_dates(d, s, warns)
    out = pd.DataFrame(index=d.index)
    out["pattern"] = np.where(otd["ot"], "over_time", "point")
    out["start"], out["end"] = otd["start"].where(otd["ot"]), otd["end"].where(otd["ot"])
    out["control_date"] = pd.NaT
    out["cause"] = np.where(otd["ot"], "over_time", "cutoff")
    out["note"] = ""
    # the service each contract's set-up fee belongs to: the longest over-time obligation, else a one-off service
    svc = {}
    ot_rows = d[otd["ot"]].assign(_len=(otd["end"] - otd["start"]).dt.days, _s=otd["start"], _e=otd["end"])
    if len(ot_rows):
        best = ot_rows.sort_values("_len").groupby("contract_id").tail(1)
        svc = {c: ("over_time", a, b) for c, a, b in zip(best["contract_id"], best["_s"], best["_e"])}
    one = d[(d["kind"] == "Service") & ~otd["ot"]]
    for c, dl, inv in zip(one["contract_id"], one["delivery_date"], one["invoice_date"]):
        svc.setdefault(c, ("point", dl if pd.notna(dl) else inv, None))

    recs = d[~otd["ot"]][["contract_id", "customer", "kind", "invoice_date", "delivery_date", "service_start",
                          "acceptance_date"]].to_dict("records")
    idxs = d.index[~otd["ot"]]
    pat, ctrl, st_, en_, cause, note = [], [], [], [], [], []
    for i, r in zip(idxs, recs):
        t = line_terms(lk, r["contract_id"], r["customer"])
        kind, inv, dl = r["kind"], r["invoice_date"], r["delivery_date"]
        p_, c_, a_, b_, cs, nt = "point", inv, pd.NaT, pd.NaT, "cutoff", ""
        if kind == "Setup" and not s.setup_distinct:
            cs = "setup"
            sv = svc.get(r["contract_id"])
            if s.setup_months > 0:
                a_ = sv[1] if sv and sv[0] == "over_time" else (dl if pd.notna(dl) else inv)
                b_ = a_ + pd.DateOffset(months=s.setup_months) - pd.Timedelta(days=1)
                if sv and sv[0] == "over_time" and sv[2] > b_:
                    b_, nt = sv[2], "With the related service period (longer than the expected relationship)"
                else:
                    nt = f"Over the expected customer relationship ({s.setup_months} months, material right B49)"
                p_, c_ = "over_time", pd.NaT
            elif sv and sv[0] == "over_time":
                p_, c_, a_, b_, nt = "over_time", pd.NaT, sv[1], sv[2], "With the related service period"
            elif sv:
                c_, nt = sv[1], "With the related service (on completion)"
            else:
                c_ = dl if pd.notna(dl) else inv
        else:
            if kind == "Licence":
                ssd = r["service_start"]
                c_ = max(x for x in [dl, ssd] if pd.notna(x)) if (pd.notna(dl) or pd.notna(ssd)) else inv
                if pd.notna(ssd) and (pd.isna(dl) or ssd > dl):
                    nt = "Licence period begins (B61)"
            elif kind == "Goods" and t.get("bill_and_hold") and (pd.isna(dl) or (dl - inv).days > 7):
                crit = [t.get("bh_reason"), t.get("bh_identified"), t.get("bh_ready"), t.get("bh_no_redirect")]
                if all(crit):
                    c_, nt = inv, "Bill-and-hold: all four B81 criteria met"
                else:
                    cs = "bill_and_hold"
                    c_ = dl if pd.notna(dl) else pd.NaT
                    nt = "Bill-and-hold criteria not all met: " + ("recognised on delivery" if pd.notna(dl) else
                                                                    "not recognised until delivery")
            elif kind == "Goods" and pd.isna(dl) and s.undelivered_if_no_date:
                c_, nt = pd.NaT, "No delivery date yet: treated as not yet delivered"
            else:
                c_ = dl if pd.notna(dl) else (r["service_start"] if pd.notna(r["service_start"]) else inv)
            if t.get("acceptance_required") and not t.get("acceptance_formality") and kind in ("Goods", "Service", "Licence"):
                acc = r["acceptance_date"]
                cs = "acceptance"
                if pd.notna(acc) and pd.notna(c_):
                    c_ = max(c_, acc)
                    nt = "Recognised on customer acceptance"
                else:
                    c_ = pd.NaT
                    nt = "Substantive acceptance clause and no acceptance yet: not recognised"
        pat.append(p_); ctrl.append(c_); st_.append(a_); en_.append(b_); cause.append(cs); note.append(nt)
    out.loc[idxs, "pattern"] = pat
    out.loc[idxs, "control_date"] = pd.to_datetime(pd.Series(ctrl, index=idxs))
    out.loc[idxs, "start"] = pd.to_datetime(pd.Series(st_, index=idxs))
    out.loc[idxs, "end"] = pd.to_datetime(pd.Series(en_, index=idxs))
    out.loc[idxs, "cause"] = cause
    out.loc[idxs, "note"] = note
    svc_flag = otd["ot"] & d["kind"].eq("Service")
    out.loc[svc_flag, "note"] = "Service performed over its service period"
    out.loc[otd["ot"] & (d["amount"] < 0), "note"] = "Credit for the remaining service period (IFRS 15.87–90)"
    return out


# ── allocation ───────────────────────────────────────────────────────────────
def _alloc_rows(l1: np.ndarray, ssp: np.ndarray, tp: float, s: Settings):
    """Allocate a contract price `tp` across obligations. Returns (L2 array, method, warn_flag)."""
    tol = s.allocation_tolerance
    known = np.isfinite(ssp) & (ssp > 0)
    if len(l1) == 1:
        return np.array([tp]), "Contract discount applied", False
    if known.all():
        tot = ssp.sum()
        at_ssp = np.abs(l1 - ssp) <= tol * ssp
        if s.discount_specific and at_ssp.any() and (~at_ssp).any() and tp - l1[at_ssp].sum() > 0:
            new = l1.copy()
            new[~at_ssp] = (tp - l1[at_ssp].sum()) * ssp[~at_ssp] / ssp[~at_ssp].sum()
            return new, "Discount allocated to specific obligations (IFRS 15.82)", False
        return tp * ssp / tot, "Relative stand-alone selling price", False
    if known.any() and tp - ssp[known].sum() > 0:
        new = np.where(known, ssp, 0.0)
        u = ~known
        w = l1[u] if l1[u].sum() > 0 else np.ones(u.sum())
        new[u] = (tp - ssp[known].sum()) * w / w.sum()
        return new, "Residual approach for items without a stand-alone price (79(c): confirm the price is highly variable or uncertain)", False
    if l1.sum() > 0:
        return tp * l1 / l1.sum(), "Pro rata to invoiced prices (no stand-alone prices)", bool(known.any())
    return l1.copy(), "", False


def _allocate(d: pd.DataFrame, is_po: pd.Series, linked_disc: pd.Series, s: Settings, warns: List[str]):
    """L2 for performance obligations: the contract price, including linked discount lines, allocated by stand-alone
    selling price (IFRS 15.73–86). Credit notes (negative lines) are price changes of the obligation they relate to and
    keep their amount (87–90); non-distinct set-up fees are not obligations and follow their service. Also returns each
    line's share of the discount lines, used for the catch-up when a discount is agreed after the sale (88)."""
    L2 = d["L1"].to_numpy(dtype=float).copy()
    share = np.zeros(len(d))
    method = np.array([""] * len(d), dtype=object)
    L2[linked_disc.to_numpy()] = 0.0
    alloc_po = is_po & (d["L1"] >= 0) & ~((d["kind"] == "Setup") & (not s.setup_distinct))
    po = d[alloc_po]
    disc = d[linked_disc].groupby("contract_id")["L1"].sum()
    sig = pd.Series(list(zip(po["pattern"].map(str), po["control_date"].map(str),
                             po["start"].map(str), po["end"].map(str))), index=po.index).map("|".join)
    n_sig = sig.groupby(po["contract_id"]).nunique()
    pos = {c: np.asarray(ix) for c, ix in po.groupby("contract_id").indices.items()}
    po_index = po.index.to_numpy()
    rowpos = pd.Series(np.arange(len(d)), index=d.index)
    ssp_all = d["ssp_line"].to_numpy(dtype=float)
    l1_all = d["L1"].to_numpy(dtype=float)
    tol = s.allocation_tolerance
    residual_warn = 0
    orphan = [c for c in disc.index if c not in pos]
    for c, ix in pos.items():
        D = float(disc.get(c, 0.0))
        rows = rowpos[po_index[ix]].to_numpy()
        l1 = l1_all[rows]
        ssp = ssp_all[rows]
        base_needed = n_sig.get(c, 1) > 1 and len(rows) > 1
        base, mth, w = l1.copy(), "", False
        if base_needed:
            known = np.isfinite(ssp) & (ssp > 0)
            # re-allocate unless every line is already invoiced at (or within tolerance of) its own stand-alone price
            if not (known.all() and np.all(np.abs(l1 - ssp) <= tol * ssp)):
                base, mth, w = _alloc_rows(l1, ssp, float(l1.sum()), s)
        if abs(D) > 1e-9:
            withd, mth2, w2 = _alloc_rows(l1, ssp, float(l1.sum() + D), s)
            share[rows] = withd - base
            mth, w = mth2, w2
            L2[rows] = withd
        else:
            L2[rows] = base
        if mth:
            method[rows] = mth
        residual_warn += int(w)
    if orphan:
        warns.append(f"{len(orphan)} discount line(s) belong to a contract with no other sale line in the data; they reduce revenue "
                     "when issued.")
    if residual_warn:
        warns.append(f"{residual_warn} contract(s) have stand-alone prices above the contract price; their price was spread "
                     "pro rata to the invoiced prices. Review the stand-alone prices.")
    return L2, method, share


def _discount_catch_up(d: pd.DataFrame, sch: pd.DataFrame) -> pd.DataFrame:
    """A discount agreed after the contract started (a later discount line) is a change in the transaction price: the part
    allocated to obligations already satisfied is recognised in the month of the change (IFRS 15.88), not in past months."""
    dl = d[(d["pattern"] == "discount")]
    if dl.empty or not (d["disc_share"] != 0).any():
        return sch
    first_inv = d[d["pattern"].isin(["point", "over_time"])].groupby("contract_id")["inv_period"].min()
    adj = []
    lines = d[d["disc_share"] != 0].set_index("line_id")
    disc_by_c = dl.groupby("contract_id")
    for c, g in disc_by_c:
        late = g[g["inv_period"] > first_inv.get(c, g["inv_period"].min())]
        if late.empty:
            continue
        Dtot = float(g["L1"].sum())
        if abs(Dtot) < 1e-9:
            continue
        li = lines[lines["contract_id"] == c]
        for lid, r in li.iterrows():
            L2i = float(r["L2"])
            if abs(L2i) < 1e-9:
                continue
            rows_i = sch[(sch["line_id"] == lid)]
            for k in late.itertuples():
                part = float(r["disc_share"]) * float(k.L1) / Dtot
                before = rows_i[rows_i["period"] < k.inv_period]
                if before.empty:
                    continue
                moved = part * before["amount"] / L2i
                adj.append(pd.DataFrame({"line_id": lid, "period": before["period"].values, "amount": -moved.values,
                                         "amount_l1": 0.0}))
                adj.append(pd.DataFrame({"line_id": [lid], "period": [k.inv_period], "amount": [float(moved.sum())],
                                         "amount_l1": [0.0]}))
    if not adj:
        return sch
    out = pd.concat([sch] + adj, ignore_index=True)
    out = out.groupby(["line_id", "period"], as_index=False, sort=False)[["amount", "amount_l1"]].sum()
    out["period"] = out["period"].astype(object)
    return out[(out["amount"].abs() > 1e-9) | (out["amount_l1"].abs() > 1e-9)].reset_index(drop=True)


# ── schedule ─────────────────────────────────────────────────────────────────
def _schedule(d: pd.DataFrame) -> pd.DataFrame:
    """Long schedule: line_id, period, amount (L2 basis), amount_l1 (invoiced basis)."""
    parts = []
    ot = d["pattern"] == "over_time"
    pt = d["pattern"] == "point"
    other = ~(ot | pt)
    # point in time
    p = d[pt]
    per = p["control_date"].dt.to_period("M").astype(object)
    per = per.where(p["control_date"].notna(), UNKNOWN)
    parts.append(pd.DataFrame({"line_id": p["line_id"].values, "period": per.values, "amount": p["L2"].values,
                               "amount_l1": p["L1"].values}))
    # settlements, linked discounts and pass-through: invoice month (linked discounts carry 0 on the L2 basis)
    o = d[other]
    parts.append(pd.DataFrame({"line_id": o["line_id"].values, "period": o["inv_period"].values, "amount": o["L2"].values,
                               "amount_l1": o["L1"].values}))
    # over time: straight-line by days, vectorised
    t = d[ot]
    if len(t):
        s0, e0 = t["start"].dt.normalize(), t["end"].dt.normalize()
        ms, me = _mi(s0), _mi(e0)
        n = (me - ms + 1).astype(int)
        rep = np.repeat(np.arange(len(t)), n)
        k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
        mi = ms[rep] + k
        y, m = mi // 12, mi % 12 + 1
        mstart = pd.to_datetime(pd.DataFrame({"year": y, "month": m, "day": 1})).to_numpy()
        mend = (pd.to_datetime(pd.DataFrame({"year": y, "month": m, "day": 1})) + pd.offsets.MonthEnd(0)).to_numpy()
        a = np.maximum(s0.to_numpy()[rep], mstart)
        b = np.minimum(e0.to_numpy()[rep], mend)
        days = ((b - a) / np.timedelta64(1, "D")).astype(float) + 1
        total = ((e0.to_numpy() - s0.to_numpy()) / np.timedelta64(1, "D")).astype(float) + 1
        frac = np.clip(days, 0, None) / total[rep]
        periods = pd.PeriodIndex.from_fields(year=y, month=m, freq="M")
        parts.append(pd.DataFrame({"line_id": t["line_id"].values[rep], "period": periods.astype(object),
                                   "amount": t["L2"].values[rep] * frac, "amount_l1": t["L1"].values[rep] * frac}))
    sch = pd.concat(parts, ignore_index=True)
    sch["period"] = sch["period"].astype(object)
    return sch[(sch["amount"] != 0) | (sch["amount_l1"] != 0)].reset_index(drop=True)


@dataclass
class Model:
    lines: pd.DataFrame                  # per line: L0–L2, recognition pattern and dates, cause, note
    schedule: pd.DataFrame               # long: line_id, period, amount (L2), amount_l1 (incl. periods after the data)
    ssp: pd.DataFrame
    periods: pd.PeriodIndex
    settings: Settings
    terms: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: List[str] = field(default_factory=list)


def build(reg: pd.DataFrame, terms: pd.DataFrame, ssp_table: Optional[pd.DataFrame], s: Settings) -> Model:
    lk = terms_lookup(terms)
    d = reg.copy().reset_index(drop=True)
    d["inv_period"] = d["invoice_date"].dt.to_period("M")
    warns: List[str] = []

    # L0, L1
    d["L0"] = d["amount"]
    is_pt = d["kind"] == "Pass-through"
    d["L1"] = d["L0"]
    if s.passthrough_agent:
        cost = d["cost_amount"].where(d["cost_amount"].notna(), d["amount"])
        d.loc[is_pt, "L1"] = d.loc[is_pt, "amount"] - cost[is_pt]
    is_po = ~d["kind"].isin(SETTLEMENT_KINDS) & ~is_pt
    po_contracts = set(d.loc[is_po, "contract_id"])
    linked_disc = (d["kind"] == "Discount") & d["contract_id"].isin(po_contracts)

    # Step 5 first (allocation only matters when obligations are recognised at different times)
    rec = _recognise(d[is_po], lk, s, warns)
    for c in ["pattern", "control_date", "start", "end", "cause", "note"]:
        d[c] = rec[c].reindex(d.index) if c in rec else None
    d["control_date"] = pd.to_datetime(d["control_date"])
    d["start"], d["end"] = pd.to_datetime(d["start"]), pd.to_datetime(d["end"])
    d.loc[~is_po, "pattern"] = np.select([is_pt[~is_po], linked_disc[~is_po]], ["pass_through", "discount"], "settlement")
    d["cause"] = d["cause"].fillna("")
    d["note"] = d["note"].fillna("")

    # Step 4
    ssp = estimate_ssp(d, ssp_table)
    unit = dict(zip(ssp["item_code"], ssp["ssp_unit"]))
    d["ssp_line"] = np.where(is_po, d["item_code"].map(unit) * d["qty"].abs() * np.sign(d["amount"]).replace(0, 1), np.nan)
    d["L2"], d["alloc_method"], d["disc_share"] = _allocate(d, is_po, linked_disc, s, warns)

    sch = _discount_catch_up(d, _schedule(d))
    known = sch.loc[sch["period"] != UNKNOWN, "period"]
    first = min(d["inv_period"].min(), known.min()) if len(known) else d["inv_period"].min()
    periods = pd.period_range(first, d["inv_period"].max(), freq="M")
    if first < d["inv_period"].min():
        warns.append(f"Some revenue is recognised before the first invoice month ({first.strftime('%b %Y')}): goods delivered or "
                     "services performed before they were invoiced. Those months are included.")
    n_pending = int(((d["pattern"] == "point") & d["control_date"].isna()).sum())
    if n_pending:
        warns.append(f"{n_pending} line(s) await customer acceptance or delivery, so no revenue is recognised for them yet.")
    return Model(d, sch, ssp, periods, s, terms if terms is not None else pd.DataFrame(), warns)


def monthly_layers(m: Model) -> pd.DataFrame:
    """Rows = period; L0, L1, L2 by invoice month; L3a timing on invoiced prices; L3 timing on allocated prices."""
    d, P = m.lines, m.periods
    out = pd.DataFrame(index=P)
    for L in ["L0", "L1", "L2"]:
        out[L] = d.groupby("inv_period")[L].sum().reindex(P).fillna(0.0)
    sch = m.schedule[m.schedule["period"] != UNKNOWN]
    out["L3a"] = sch.groupby("period")["amount_l1"].sum().reindex(P).fillna(0.0)
    out["L3"] = sch.groupby("period")["amount"].sum().reindex(P).fillna(0.0)
    return out


def timing_by_cause(m: Model) -> pd.DataFrame:
    """Timing effect by month and cause on invoiced prices (positive = revenue moved INTO the month)."""
    d, P = m.lines, m.periods
    po = d[d["pattern"].isin(["point", "over_time"])]
    cause = dict(zip(po["line_id"], po["cause"]))
    a = m.schedule[m.schedule["line_id"].isin(po["line_id"]) & (m.schedule["period"] != UNKNOWN)]
    a = a.assign(cause=a["line_id"].map(cause))
    b = po.assign(period=po["inv_period"])[["period", "cause", "L1"]].rename(columns={"L1": "amount_l1"})
    diff = pd.concat([a[["period", "cause", "amount_l1"]], b.assign(amount_l1=-b["amount_l1"])])
    return diff.pivot_table(index="period", columns="cause", values="amount_l1", aggfunc="sum").reindex(P).fillna(0.0)


# ── contract balances ────────────────────────────────────────────────────────
def _cube(m: Model) -> Dict:
    """Line × month arrays (cumulative billed, recognised, and the unearned part of invoices not yet due) and their
    contract totals. Recognised revenue is on the allocated basis; billing on the invoiced basis net of agent costs."""
    d, P = m.lines, m.periods
    nP = len(P)
    bl = d[d["pattern"].isin(["point", "over_time", "discount"])].reset_index(drop=True)
    contracts = pd.Index(sorted(bl["contract_id"].unique()))
    nC, nL = len(contracts), len(bl)
    if nL == 0:
        z = np.zeros((0, nP))
        return {"contracts": contracts, "cust": pd.Series(dtype=object), "B": z, "R": z, "ND": z, "final": np.zeros(0),
                "multi": np.zeros(0, dtype=bool), "lines": bl, "Bl": z, "Rl": z, "row": np.zeros(0, dtype=int)}
    lk = terms_lookup(m.terms)
    p0 = P[0]

    def pidx(per):
        return np.array([(x - p0).n for x in per], dtype=int)

    pay = np.array([line_terms(lk, c, cu).get("payment_days") for c, cu in zip(bl["contract_id"], bl["customer"])], dtype=float)
    pay = np.where(np.isfinite(pay), pay, m.settings.default_payment_days)
    due = (bl["invoice_date"] + pd.to_timedelta(pay, unit="D")).dt.to_period("M")
    inv_i = np.clip(pidx(bl["inv_period"]), 0, nP - 1)
    due_i = np.clip(np.maximum(pidx(due), inv_i), 0, nP)          # nP = not due within the data
    row = bl["contract_id"].map({c: k for k, c in enumerate(contracts)}).to_numpy(dtype=int)
    L1 = bl["L1"].to_numpy(dtype=float)
    L2 = bl["L2"].to_numpy(dtype=float)
    t = np.arange(nP)
    Bl = np.where(t[None, :] >= inv_i[:, None], L1[:, None], 0.0)
    nd = (t[None, :] >= inv_i[:, None]) & (t[None, :] < due_i[:, None])
    Rl = np.zeros((nL, nP))
    sc = m.schedule[m.schedule["line_id"].isin(bl["line_id"]) & (m.schedule["period"] != UNKNOWN)]
    sc = sc[sc["period"] <= P[-1]]
    if len(sc):
        lrow = sc["line_id"].map(pd.Series(np.arange(nL), index=bl["line_id"])).to_numpy(dtype=int)
        np.add.at(Rl, (lrow, np.clip(pidx(sc["period"]), 0, nP - 1)), sc["amount"].to_numpy(dtype=float))
    Rl = Rl.cumsum(1)
    # the unearned share of each line not yet due: (1 − recognised / allocated price); discount lines take the contract's
    with np.errstate(divide="ignore", invalid="ignore"):
        u = np.where(np.abs(L2)[:, None] > 1e-9, np.clip(1 - Rl / np.where(np.abs(L2) > 1e-9, L2, 1)[:, None], 0, 1), np.nan)
    B = np.zeros((nC, nP)); R = np.zeros((nC, nP))
    np.add.at(B, row, Bl)
    np.add.at(R, row, Rl)
    final = np.zeros(nC)
    np.add.at(final, row, L2)
    with np.errstate(divide="ignore", invalid="ignore"):
        uc = np.clip(1 - R / np.where(np.abs(final) > 1e-9, final, 1)[:, None], 0, 1)
    u = np.where(np.isnan(u), uc[row], u)
    # positive lines: the invoiced amount not yet recognised (floored at zero); discount lines: their share of the contract
    unearned = np.where(L1[:, None] > 0, np.clip(L1[:, None] - Rl, 0, L1[:, None]), L1[:, None] * u)
    ND = np.zeros((nC, nP))
    np.add.at(ND, row, np.where(nd, unearned, 0.0))
    po = bl[bl["pattern"].isin(["point", "over_time"])]
    n_items = po.groupby("contract_id")["item_code"].nunique().reindex(contracts).fillna(0).to_numpy()
    cust = bl.drop_duplicates("contract_id").set_index("contract_id")["customer"].reindex(contracts)
    return {"contracts": contracts, "cust": cust, "B": B, "R": R, "ND": ND, "final": final, "multi": n_items >= 2,
            "lines": bl, "Bl": Bl, "Rl": Rl, "row": row}


def _split(B, R, ND, final, multi):
    """IFRS 15.105–108 at each month-end, per contract:
    • recognised more than billed → a contract asset when the right depends on other obligations in the contract still to
      be satisfied (a multi-obligation contract not yet complete); otherwise an unbilled receivable (only time is needed)
    • billed more than recognised → a contract liability, except the unearned part of invoices not yet due: until payment is
      due the company has no unconditional right and no liability is recognised for it (106)."""
    net = R - B
    excess = np.clip(-net, 0, None)
    rec_rev = np.minimum(excess, np.clip(ND, 0, None))
    cl = excess - rec_rev
    pos = np.clip(net, 0, None)
    final = np.asarray(final).reshape(-1, *([1] * (np.ndim(R) - 1))) if np.ndim(R) > 1 else np.asarray(final)
    multi = np.asarray(multi).reshape(final.shape)
    unsat = ((final - R) > 0.5) & multi
    ca = np.where(unsat, pos, 0.0)
    unbilled = np.where(unsat, 0.0, pos)
    return ca, unbilled, cl, rec_rev


def balances_series(m: Model) -> pd.DataFrame:
    """Totals at every month-end: contract assets, unbilled receivables, contract liabilities, and advance billings not
    yet due (invoices that are not yet receivables under IFRS 15.106)."""
    c = _cube(m)
    ca, ub, cl, rr = (np.where(np.abs(x) < 1e-6, 0.0, x) for x in _split(c["B"], c["R"], c["ND"], c["final"], c["multi"]))
    return pd.DataFrame({"contract_asset": ca.sum(0), "unbilled_receivable": ub.sum(0), "contract_liability": cl.sum(0),
                         "billed_not_due": rr.sum(0)}, index=m.periods)


def contract_balances(m: Model, at: pd.Period) -> pd.DataFrame:
    c = _cube(m)
    j = (at - m.periods[0]).n
    cols = ["contract_id", "customer", "billed", "recognised", "contract_asset", "unbilled_receivable", "contract_liability",
            "billed_not_due"]
    if not len(c["contracts"]):
        return pd.DataFrame(columns=cols)
    ca, ub, cl, rr = _split(c["B"][:, j], c["R"][:, j], c["ND"][:, j], c["final"], c["multi"])
    return pd.DataFrame({"contract_id": c["contracts"], "customer": c["cust"].values, "billed": c["B"][:, j],
                         "recognised": c["R"][:, j], "contract_asset": ca, "unbilled_receivable": ub, "contract_liability": cl,
                         "billed_not_due": rr})[cols]


def revenue_from_opening_liability(m: Model, open_p: pd.Period, at: pd.Period) -> float:
    """IFRS 15.116(b): revenue recognised in the period that was in the contract liability at its start. Measured line by
    line (the unearned part of each line invoiced by the opening date that is recognised since), capped per contract at
    the opening contract liability."""
    if open_p not in m.periods or at not in m.periods:
        return 0.0
    c = _cube(m)
    if not len(c["contracts"]):
        return 0.0
    i, j = (open_p - m.periods[0]).n, (at - m.periods[0]).n
    bl = c["lines"]
    L2 = bl["L2"].to_numpy(dtype=float)
    invoiced = c["Bl"][:, i] != 0
    unearned = np.where(invoiced & (bl["pattern"] != "discount").to_numpy(), np.clip(L2 - c["Rl"][:, i], 0, None), 0.0)
    rec_since = np.clip(c["Rl"][:, j] - c["Rl"][:, i], 0, None)
    per_line = np.minimum(unearned, rec_since)
    per_c = np.zeros(len(c["contracts"]))
    np.add.at(per_c, c["row"], per_line)
    _, _, cl_open, _ = _split(c["B"][:, i], c["R"][:, i], c["ND"][:, i], c["final"], c["multi"])
    return float(np.minimum(per_c, cl_open).sum())
