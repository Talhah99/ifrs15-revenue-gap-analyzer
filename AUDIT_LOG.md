# Audit log

Three independent review passes were run: a technical IFRS 15 review, a messy-data and QA stress test, and a UI and Excel review.

## Round 1 (12 Oct 2026)

About 70 findings were raised across accounting, data handling and UI. All were resolved in v1.0.

## Round 2 (14 Oct 2026)

36 findings were raised. All were resolved, including:

- **Credit-note allocation:** credit notes were being allocated as if they were obligations.
- **Contract asset vs receivable:** the split now reflects whether other obligations remain.
- **Later discounts:** a discount agreed after the sale is now a catch-up (88).
- **Invoices issued ahead but not yet due:** tested line by line, and presented apart from contract liabilities.
- **Rebate settlements:** FIFO within a settlement window.
- **Set-up fee period:** the longer of the service term and the expected relationship.
- **116(b):** measured line by line.
- **Open-ended dates:** dates such as 9999 no longer crash the load.
- **Empty registers:** no longer crash.
- **Data handling:** CR/DR markers, customer-name variants, and the Dashboard opening after the demo loads.
- **Tables and Excel:** layout, alignment and print settings.

## Round 3 verification

All round-2 findings were confirmed fixed. The follow-ups were also resolved:

- the order book now follows the 121(a) expedient;
- the open-ended date warning now appears;
- headings-only files get a clear message;
- `CR 500` prefixes are read;
- balances under 0.5 are now journalled;
- a contract-row rebate applies when the customer row has none;
- first-month charts and mobile tick labels display correctly.

## Round 4 (14 Oct 2026, v1.1)

A fresh accuracy and usability review. Fixed:

- **Terms leakage:** a contract row no longer applies to the customer's other contracts.
- **Re-allocation tolerance:** checked line by line, not on the contract total.
- **Return rates:** each month uses only returns received by that month-end.
- **Rebate simulation:** includes months with no purchases, so expected volume is not overstated.
- **121(a):** contract length comes from the "Contract Term" in the terms file. A contract with a value but no term is treated as longer than a year.
- **Decimal detection:** only unambiguous values vote. Three-decimal currencies are recognised.
- **Draft note wording:** control rather than invoice language, and 116(b) only when there is an opening balance.
- **Return asset:** a warning when cost data is missing.
- **Usability:** attention list on the Dashboard, faster tabs, demo confirmation, stale results cleared on a new upload, clearer sidebar labels and contrast.

Added: posted-journal reconciliation (change in estimate), GL codes, Word note, close bundle with sign-off, settings profile, and an "undelivered if no date" policy switch.

## Known limitations (by design)

- **Rebates and return rights** are measured per customer, not per contract.
- **Order book:** contracted amounts not yet invoiced come from a "Contract Value" per contract. Their timing is shown as "to confirm".
- **Asset vs receivable heuristic:** a contract asset is recognised when a contract with several distinct items still has obligations to satisfy. A single-item contract billed in arrears is treated as a receivable.
- **Financing components** are flagged for assessment but not adjusted.
- **Sensitivity run time:** the sensitivity analysis re-runs the whole model for each judgement, so it takes about 40 seconds on 50,000 lines.
