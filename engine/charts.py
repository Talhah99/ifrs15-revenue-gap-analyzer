"""charts.py — Plotly figures for the IFRS 15 Revenue Gap Analyzer."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

INDIGO, TEAL, AMBER, RED, GREEN, SLATE = "#6366F1", "#14B8A6", "#F59E0B", "#EF4444", "#10B981", "#64748B"
PALETTE = [INDIGO, TEAL, AMBER, "#8B5CF6", "#0EA5E9", "#EC4899", "#84CC16", RED, SLATE]
FONT = dict(family="Inter, Segoe UI, sans-serif", size=12, color="#475569")


def _layout(fig: go.Figure, title: str = "", height: int = 380, legend: bool = True) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=14, color="#334155"), x=0, xanchor="left") if title else None,
        height=height, margin=dict(l=10, r=10, t=56 if title else 16, b=90 if legend else 10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=FONT,
        showlegend=legend, legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="left", x=0, bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font=dict(family=FONT["family"])),
    )
    fig.update_xaxes(showgrid=False, linecolor="#CBD5E1", tickfont=dict(color="#64748B"), automargin=True)
    fig.update_yaxes(gridcolor="rgba(148,163,184,.25)", zeroline=False, tickfont=dict(color="#64748B"), automargin=True)
    return fig


def scale_label(values) -> tuple:
    v = np.asarray(values, dtype=float)
    m = np.nanmax(np.abs(v)) if v.size and np.isfinite(v).any() else 0
    if m >= 1e9:
        return 1e9, "bn"
    if m >= 1e6:
        return 1e6, "m"
    if m >= 1e3:
        return 1e3, "k"
    return 1.0, ""


def _x(periods) -> list:
    return [p.to_timestamp() for p in periods]


# monthly bars and points sit in the middle of their month (no half-month offset against the period tick labels)
MID = dict(xperiod="M1", xperiodalignment="middle")


def _date_axis(fig: go.Figure, n: int, x: Optional[list] = None) -> go.Figure:
    fig.update_xaxes(type="date", tickformat="%b %y", dtick="M6" if n > 30 else "M3" if n > 14 else "M1",
                     ticklabelmode="period")
    if n <= 2 and x:                             # one or two months: plain centred bars with their own labels
        fig.update_traces(xperiod=None, xperiodalignment=None)
        fig.update_traces(width=20 * 86400000, selector=dict(type="bar"))
        lo, hi = min(x) - pd.Timedelta(days=40), max(x) + pd.Timedelta(days=40)
        fig.update_xaxes(range=[lo, hi], tickvals=list(x), ticktext=[f"{v:%b %y}" for v in x], ticklabelmode="instant")
    return fig


SHORT = {"Agent: pass-through presented net": "Agent netting", "Allocation of price and discounts": "Allocation",
         "Cut-off: control passes in another month": "Cut-off", "Bill-and-hold criteria not met": "Bill-and-hold",
         "Customer acceptance": "Acceptance", "Subscriptions & services over time": "Over time",
         "Set-up fees not distinct": "Set-up fees", "Expected returns (refund liability)": "Returns",
         "Volume rebates (refund liability)": "Rebates", "Other causes": "Other"}
# one colour per cause, the same in every chart
CAUSE_COLOR = {"Agent: pass-through presented net": SLATE, "Allocation of price and discounts": "#F97316",
               "Cut-off: control passes in another month": "#4F46E5", "Bill-and-hold criteria not met": "#0EA5E9",
               "Customer acceptance": "#EC4899", "Subscriptions & services over time": TEAL,
               "Set-up fees not distinct": "#84CC16", "Expected returns (refund liability)": "#EAB308",
               "Volume rebates (refund liability)": "#92400E", "Other causes": "#94A3B8"}


def fig_bridge(steps, unit: str = "PKR", title: str = "") -> go.Figure:
    """Horizontal waterfall: booked revenue at the top, each cause, IFRS 15 revenue at the bottom."""
    vals = [s[1] for s in steps]
    div, suf = scale_label(vals)
    measure = [s[2] for s in steps]
    labels = [SHORT.get(s[0], s[0]) for s in steps]
    fig = go.Figure(go.Waterfall(
        orientation="h", y=labels, x=[v / div for v in vals], measure=measure,
        text=[f"{v / div:+,.1f}{suf}" if k == "relative" else f"{v / div:,.1f}{suf}" for v, k in zip(vals, measure)],
        textposition="outside", cliponaxis=False,
        customdata=[s[0] for s in steps],
        connector=dict(line=dict(color="#CBD5E1", width=1)),
        increasing=dict(marker=dict(color=GREEN)), decreasing=dict(marker=dict(color=RED)), totals=dict(marker=dict(color=INDIGO)),
        hovertemplate="%{customdata}<br>%{x:,.2f}" + suf + "<extra></extra>"))
    running, totals = 0.0, []
    for v, k in zip(vals, measure):
        running = v if k in ("absolute", "total") else running + v
        totals.append(running / div)
    lo, hi = min(totals), max(totals)
    if lo > 0 and lo > 0.4 * hi:
        pad = (hi - lo) * 0.6 + hi * 0.03
        fig.update_xaxes(range=[max(0, lo - pad), hi + pad * 0.9], title=f"{unit} {suf} · not from zero")
    else:
        fig.update_xaxes(title=f"{unit} {suf}".strip())
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,.25)")
    return _layout(fig, title, max(300, 46 * len(steps) + 60), legend=False)


def fig_booked_vs_ifrs(monthly: pd.DataFrame, end: pd.Period, unit: str, n: int = 24) -> go.Figure:
    """Two panels on one time axis: revenue (booked and IFRS 15) above, the gap below."""
    m = monthly[monthly.index <= end].iloc[-n:]
    div, suf = scale_label(m[["L0", "L4"]].values)
    x = _x(m.index)
    gap = (m["L4"] - m["L0"]) / div
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.66, 0.34], vertical_spacing=0.06)
    fig.add_trace(go.Scatter(x=x, y=m["L0"] / div, name="Revenue as booked", line=dict(color=SLATE, width=2, dash="dot"),
                             hovertemplate="%{x|%b %Y}: %{y:,.1f}" + suf + "<extra>Booked</extra>", **MID), 1, 1)
    fig.add_trace(go.Scatter(x=x, y=m["L4"] / div, name="Revenue under IFRS 15", line=dict(color=INDIGO, width=2.5),
                             mode="lines+markers", marker=dict(size=5),
                             hovertemplate="%{x|%b %Y}: %{y:,.1f}" + suf + "<extra>IFRS 15</extra>", **MID), 1, 1)
    fig.add_trace(go.Bar(x=x, y=gap, name="Gap (IFRS 15 − booked)", marker_color=[GREEN if v >= 0 else RED for v in gap],
                         hovertemplate="%{x|%b %Y}: %{y:,.1f}" + suf + "<extra>Gap</extra>", **MID), 2, 1)
    fig.update_yaxes(title_text=f"Revenue ({unit} {suf})", row=1, col=1)
    fig.update_yaxes(title_text=f"Gap ({unit} {suf})", zeroline=True, zerolinecolor="#94A3B8", row=2, col=1)
    fig = _layout(fig, "Revenue as booked vs under IFRS 15", 470)
    return _date_axis(fig, len(m), x)


def fig_gap_by_cause(causes: pd.DataFrame, end: pd.Period, unit: str, n: int = 12) -> go.Figure:
    c = causes[causes.index <= end].iloc[-n:]
    c = c.loc[:, (c.abs() >= 0.5).any()]
    div, suf = scale_label(c.values if c.size else [0])
    fig = go.Figure()
    for col in c.columns:
        fig.add_trace(go.Bar(x=_x(c.index), y=c[col] / div, name=SHORT.get(col, col), marker_color=CAUSE_COLOR.get(col, SLATE),
                             hovertemplate="%{x|%b %Y}: %{y:,.2f}" + suf + "<extra>" + col + "</extra>", **MID))
    fig.add_trace(go.Scatter(x=_x(c.index), y=c.sum(axis=1) / div, name="Total gap", mode="lines+markers",
                             line=dict(color="#0F172A", width=2),
                             hovertemplate="%{x|%b %Y}: %{y:,.2f}" + suf + "<extra>Total</extra>", **MID))
    fig.update_layout(barmode="relative")
    fig.update_yaxes(title=f"{unit} {suf}", zeroline=True, zerolinecolor="#94A3B8")
    return _date_axis(_layout(fig, f"Gap by cause, last {len(c)} month{'s' if len(c) != 1 else ''}", 440), len(c), _x(c.index))


def fig_balances(bal: pd.DataFrame, end: pd.Period, unit: str) -> go.Figure:
    b = bal[bal.index <= end].iloc[-24:]
    cols = {"contract_liability": ("Contract liabilities", INDIGO), "billed_not_due": ("Invoiced, not yet due", "#A5B4FC"),
            "contract_asset": ("Contract assets", GREEN), "unbilled_receivable": ("Unbilled receivables", TEAL),
            "refund_liability": ("Refund liability: returns", AMBER), "rebate_liability": ("Refund liability: rebates", RED)}
    div, suf = scale_label(b[list(cols)].values)
    fig = go.Figure()
    for c, (n, col) in cols.items():
        if (b[c].abs() >= 0.5).any():
            fig.add_trace(go.Scatter(x=_x(b.index), y=b[c] / div, name=n, line=dict(color=col, width=2.2),
                                     hovertemplate="%{x|%b %Y}: %{y:,.2f}" + suf + "<extra>" + n + "</extra>", **MID))
    fig.update_yaxes(title=f"{unit} {suf}")
    return _date_axis(_layout(fig, "IFRS 15 balances at each month-end", 380), len(b), _x(b.index))


def fig_return_rates(rates: pd.DataFrame, group: str, end: pd.Period) -> go.Figure:
    """Actual rates only for sale months whose return window has closed (open months would understate them)."""
    r = rates[(rates["group"] == group) & (rates["period"] <= end)].iloc[-24:]
    x = _x(r["period"])
    closed = r["window_closed"].astype(bool) if "window_closed" in r else pd.Series(True, index=r.index)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=x, y=(r["actual_rate"] * 100).where(closed), name="Actual return rate (window closed)",
                         marker_color="#CBD5E1", hovertemplate="%{x|%b %Y}: %{y:.2f}%<extra>Actual</extra>", **MID))
    fig.add_trace(go.Scatter(x=x, y=r["expected_rate"] * 100, name="Expected value", line=dict(color=TEAL, width=2, dash="dash"),
                             hovertemplate="%{x|%b %Y}: %{y:.2f}%<extra>Expected</extra>", **MID))
    fig.add_trace(go.Scatter(x=x, y=r["constrained_rate"] * 100, name="Rate used (after the constraint)",
                             line=dict(color=INDIGO, width=2.5), hovertemplate="%{x|%b %Y}: %{y:.2f}%<extra>Used</extra>", **MID))
    fig.update_yaxes(title="% of goods revenue", rangemode="tozero")
    return _date_axis(_layout(fig, f"Return rate · {group}: actual vs rate provided", 400), len(r), x)


def fig_rate_distribution(rates: pd.DataFrame, group: str, end: pd.Period, conf: float) -> go.Figure:
    r = rates[(rates["group"] == group) & (rates["period"] <= end)]
    if "window_closed" in r:
        r = r[r["window_closed"].astype(bool)]
    r = r.dropna(subset=["actual_rate"]).iloc[-24:]
    v = r["actual_rate"].values * 100
    fig = go.Figure(go.Histogram(x=v, nbinsx=14, marker=dict(color="#A5B4FC", line=dict(color="#6366F1", width=1)),
                                 name="Monthly return rates", hovertemplate="%{x:.2f}%: %{y} months<extra></extra>"))
    if len(v):
        mean = float(np.mean(v))
        q = float(np.quantile(v, conf)) if len(v) >= 12 else float(mean + 1.28 * np.std(v))
        for val, name, col, yy, anchor in [(mean, "Expected", TEAL, 1.10, "right"), (q, f"{conf:.0%} level", INDIGO, 1.02, "left")]:
            fig.add_vline(x=val, line=dict(color=col, width=2, dash="dash" if name == "Expected" else "solid"))
            fig.add_annotation(x=val, y=yy, yref="paper", text=f"{name} {val:.2f}%", showarrow=False, xanchor=anchor,
                               font=dict(color=col, size=11))
    fig.update_xaxes(title="Monthly return rate (%, closed months)")
    fig.update_yaxes(title="months")
    fig = _layout(fig, "Distribution behind the constraint", 360, legend=False)
    fig.update_layout(margin=dict(t=80))
    return fig


def fig_rebate_sim(sim: np.ndarray, threshold: float, ytd: float, unit: str, customer: str) -> go.Figure:
    div, suf = scale_label([threshold, float(np.max(sim))])
    p = float(np.mean(sim >= threshold))
    fig = go.Figure(go.Histogram(x=sim / div, nbinsx=40, marker=dict(color="#A5B4FC", line=dict(color=INDIGO, width=1)),
                                 name="Simulated year totals",
                                 hovertemplate="%{x:,.1f}" + suf + ": %{y} runs<extra></extra>"))
    fig.add_vline(x=threshold / div, line=dict(color=RED, width=2.5))
    fig.add_annotation(x=threshold / div, y=1.05, yref="paper", showarrow=False, font=dict(color=RED, size=12),
                       text=f"Threshold {threshold / div:,.1f}{suf} · probability of reaching it {p:.0%}")
    fig.add_vline(x=ytd / div, line=dict(color=SLATE, width=1.5, dash="dot"))
    fig.add_annotation(x=ytd / div, y=0.92, yref="paper", showarrow=False, text="purchases to date", font=dict(color=SLATE, size=11),
                       xanchor="right")
    fig.update_xaxes(title=f"Purchases for the year ({unit} {suf})")
    fig.update_yaxes(title="simulation runs")
    return _layout(fig, f"{customer}: {len(sim):,} simulated outcomes for the year", 360, legend=False)


def fig_rebate_path(tbl: pd.DataFrame, customer: str, fy: int, conf: float, end: Optional[pd.Period] = None) -> go.Figure:
    t = tbl[(tbl["customer"] == customer) & (tbl["fiscal_year"] == fy)].dropna(subset=["probability"])
    if end is not None:
        t = t[t["period"] <= end]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=_x(t["period"]), y=t["probability"] * 100, mode="lines+markers", name="Probability of reaching tier",
                             line=dict(color=INDIGO, width=2.5), hovertemplate="%{x|%b %Y}: %{y:.0f}%<extra></extra>", **MID))
    fig.add_hline(y=50, line=dict(color=SLATE, dash="dot"))
    fig.add_annotation(x=0, xref="paper", y=50, text="most likely amount: the tier", showarrow=False, yshift=10, xanchor="left",
                       font=dict(color=SLATE, size=11))
    fig.add_hline(y=(1 - conf) * 100, line=dict(color=RED, dash="dash"))
    fig.add_annotation(x=0, xref="paper", y=(1 - conf) * 100, text=f"constraint: accrue above {1 - conf:.0%} if significant",
                       showarrow=False, yshift=10, xanchor="left", font=dict(color=RED, size=11))
    fig.update_yaxes(title="%", range=[0, 105])
    return _date_axis(_layout(fig, "Probability of reaching the rebate tier, month by month", 330, legend=False), len(t),
                      _x(t["period"]))


def fig_rpo(rpo: pd.DataFrame, unit: str) -> go.Figure:
    r = rpo[rpo["When"] != "Total"]
    div, suf = scale_label(r["Total"].values if len(r) else [0])
    fig = go.Figure()
    for c, col in [("At a point in time", INDIGO), ("Over time", TEAL), ("Not yet scheduled", "#94A3B8")]:
        if c in r:
            fig.add_trace(go.Bar(x=r["When"], y=r[c] / div, name=c, marker_color=col, hovertemplate="%{x}: %{y:,.2f}" + suf + "<extra></extra>"))
    fig.update_layout(barmode="stack")
    fig.update_yaxes(title=f"{unit} {suf}")
    fig.update_xaxes(tickangle=0, ticktext=[w.replace("On acceptance or delivery (date not known)", "On acceptance<br>or delivery")
                                            .replace("Contracted, not yet invoiced (timing to confirm)", "Order book<br>(not invoiced)")
                                            .replace(" months", "<br>months") for w in r["When"]], tickvals=list(r["When"]))
    return _layout(fig, "Remaining performance obligations: when they will be recognised", 360)


def fig_contract(detail: pd.DataFrame, schedule: pd.DataFrame, unit: str) -> go.Figure:
    s = schedule[schedule["line_id"].isin(detail["line_id"])].merge(detail[["line_id", "item_desc"]], on="line_id")
    s = s[s["period"].astype(str) != "2200-01"]
    billed = detail.groupby("inv_period")["L1"].sum()
    div, suf = scale_label(np.r_[s["amount"].values, billed.values] if len(s) else billed.values)
    fig = go.Figure()
    for i, (name, g) in enumerate(s.groupby("item_desc")):
        gg = g.groupby("period")["amount"].sum()
        fig.add_trace(go.Bar(x=[p.strftime("%b %y") for p in gg.index], y=gg.values / div, name=f"Recognised: {name[:32]}",
                             marker_color=PALETTE[i % len(PALETTE)]))
    fig.add_trace(go.Scatter(x=[p.strftime("%b %y") for p in billed.index], y=billed.values / div, mode="markers", name="Invoiced",
                             marker=dict(symbol="diamond", size=12, color="#0F172A")))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(type="category", categoryorder="array",
                     categoryarray=[p.strftime("%b %y") for p in sorted(set(s["period"]) | set(billed.index))])
    fig.update_yaxes(title=f"{unit} {suf}", rangemode="tozero")
    if s.empty:
        fig.add_annotation(x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False, font=dict(color=SLATE, size=12),
                           text="Nothing recognised yet: awaiting delivery or acceptance")
    return _layout(fig, "Invoiced vs recognised under IFRS 15", 380)
