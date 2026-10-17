# 🧾 IFRS 15 Revenue Gap Analyzer · v1.1

Most companies book revenue when they raise an invoice. IFRS 15 recognises it when control passes. This app measures the difference.

Upload an invoice-level sales register. You can also add contract terms and stand-alone selling prices. The app applies the IFRS 15 five-step model to every line. For each month it shows:

- **the gap** between revenue as booked and revenue under IFRS 15,
- **why** it arises,
- **the journals** that close it,
- **the disclosures** that go with it.

## What it covers

| Area | Treatment |
|---|---|
| Cut-off | Control passes on delivery (IFRS 15.38), not on the invoice date. A setting decides whether goods with no delivery date are treated as delivered or not yet delivered |
| Bill-and-hold | All four criteria in B81 are tested. A contract row in the terms file applies only to that contract and overrides the customer row |
| Customer acceptance | Substantive clauses (B83–B86) are applied after bill-and-hold. A formality-only clause can be marked as such |
| Over time | Subscriptions, warranties and services with a service period. One-off services are optional (35(b)/(c)). Credit notes reduce the rest of the period (87–90) |
| Set-up fees | Recognised with the related service, or over the expected relationship if longer (B48–B51) |
| Licences | Recognised when the customer can first use them (B61) |
| Allocation | By relative stand-alone selling price, tested line by line against your tolerance. Includes discount lines, the 82 option and the residual approach. Later discounts are a catch-up (88) |
| Principal vs agent | Pass-through recharges presented net (B34–B38), as a policy switch |
| Returns | Expected value from the company's own closed return history, using only returns known at each month-end (no hindsight). A return asset is recognised (B20–B27) |
| Rebates | Most likely amount from a simulation of the customer's year, including months with no purchases. Settlements are FIFO within a settlement window |
| Constraint | A confidence level and a significance threshold make "highly probable … significant reversal" (56–57) measurable |
| Balances | Contract assets, unbilled receivables, contract liabilities, and invoices issued in advance but not yet due (105–108). Deferred cost of sales |
| Disclosures | Disaggregation (114), roll-forward and 116(b) line by line, 118 reasons, remaining obligations (120, with 121(a) applied from the contract term, and an order book) and a draft note |

## Why the numbers can be trusted

- **The causes add up to the gap** every month.
- **The cumulative gap equals the IFRS 15 balances** at every month-end.
- **The journals tie to the gap.** Reversing last month's entries and posting this month's changes revenue by exactly the month's gap.
- **The Excel pack checks itself.** Its formulas recalculate and every check cell reads OK.
- **67 automated tests cover this**, including randomly generated registers and messy-data cases:
  - decimal commas;
  - SAP `yyyymmdd` dates and `31.12.9999` dates;
  - CR/DR amounts;
  - total rows;
  - UTF-16 files, with or without a BOM;
  - three-decimal currencies such as KWD;
  - customer-name variants.
- **It scales.** A 50,000-line register analyses in about 5 seconds.

## Outputs

- **Dashboard.** KPIs, a ranked "what needs your attention" list with one-click links, a tie-out strip, a horizontal bridge, balances, booked vs IFRS 15, and the gap by cause.
- **Contract walkthrough.** The five steps for any contract.
- **Returns and rebates.** The analysis behind each estimate.
- **Controls.** Timing exceptions, bill-and-hold, acceptance, principal vs agent, and financing.
- **Journals.** Debits and credits with your GL codes and a reconciliation to the gap. Upload last month's posted journal to see the change in estimate.
- **Judgements panel.** What each judgement is worth.
- **Exports:**
  - an audit-ready Excel pack with live check cells and an ERP upload sheet;
  - a revenue schedule (CSV);
  - a journal upload (CSV);
  - the draft revenue note as a Word document;
  - a close bundle (zip) with all of the above and a preparer/reviewer sign-off on the Excel summary.
- **Settings profile.** Save your policy choices and GL codes as a small file and load them next month.

Demo data: Cedar Panels Ltd, a fictional manufacturer, July 2023 to March 2026 in PKR. Issues are planted in March 2026.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
pytest -q
```

MIT License · Muhammad Talha Baig · a review aid, not a substitute for professional judgement
