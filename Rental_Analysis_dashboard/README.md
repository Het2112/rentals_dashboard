# Rental Portfolio Dashboard

A private, local Streamlit application for importing AppFolio owner-statement PDFs, recording owner-paid expenses, and analyzing property, unit, debt, equity, NOI, CapEx, and cash-flow performance. No AppFolio credentials or browser automation are used.

## What it does

- Imports one or many historical owner-statement PDFs through the UI or CLI.
- Extracts consolidated property summaries, multipage transaction tables, and work-order financial details.
- Renames statements consistently and tracks SHA-256 checksums, periods, parser versions, and revisions.
- Skips exact duplicates and requires confirmation before replacing an existing period with a revision.
- Stores approved data in `data/Rental_Portfolio.xlsx` with timestamped backups and atomic writes.
- Records owner-paid items through a form or CSV/Excel upload.
- Lets the user delete manually entered/uploaded expenses while protecting generated reconciliation entries.
- Separates maintenance/operating expenses from capital improvements, debt, and owner transfers.
- Shows cumulative maintenance and CapEx as percentages of collected rental income for the selected period.
- Calculates monthly property and portfolio metrics and creates Streamlit and Excel charts.
- Supports properties, units, manually entered valuations, and amortized loans.
- Opens with a plain-language investor summary showing cash profit or loss overall and by property.
- Captures mortgage amount, interest rate, term, payment, and current balance during property setup.
- Preloads the owner-provided cumulative property baseline through June 2025 and keeps it separate from monthly statement activity.
- Preloads normalized monthly statement history from September 2023 through June 2026, including hashes for duplicate detection; private PDFs are not bundled.
- Reconciles historical statement maintenance to the baseline, reclassifies statement-supported capital work, and records only the positive remaining difference as owner-paid maintenance.

## Financial treatment

- **NOI** = operating revenue − operating expenses.
- **Maintenance / Operating Expense** reduces NOI.
- **Capital Improvement / CapEx** does not reduce NOI, but reduces cash flow and adds to invested capital.
- **Mortgage principal and interest** are excluded from NOI and included in after-debt cash flow.
- **Owner contributions and distributions** are transfers, not revenue or expenses.
- **Capital gain** applies when an asset is sold; it is not used as a repair classification.

The headline result is **cash profit/loss**:

```text
rent and other income
− operating expenses
− mortgage principal and interest
− capital improvements
= cash profit or loss
```

Mortgage principal reduces current cash but builds equity, so it is also shown separately. Unrealized appreciation is not included in cash profit. A property remains marked **Setup needed** until the user confirms cash purchase or supplies its mortgage details; incomplete properties are never presented as finalized results.

The **Since purchase** view uses the cumulative June 2025 baseline once, excludes overlapping statement activity through that cutoff, and then adds monthly activity after June 2025. Owner-paid maintenance remains provisional only when an expected historical statement is missing.

**Average maintenance %** is cumulative maintenance divided by cumulative collected rental income for the selected period. **Capital improvements %** uses the same denominator. This weighted calculation avoids distortion from averaging months with unusually low or zero rent.

The bundled history already completes the reconciliation through June 2025. Where statement costs exceed an earlier estimate, the statement amount is authoritative. Where the adjusted historical total exceeds statement maintenance, the positive difference is recorded as owner-paid. Reallocation between maintenance and CapEx preserves the combined cost; statement-supported costs missing from the earlier estimate increase the adjusted historical cash loss and remain visible beside the original figure.

The dashboard is an investment-analysis tool, not tax or accounting advice.

## Install and run

Python 3.11 or newer is required.

### macOS/Linux

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/streamlit run app/main.py
```

Or run:

```bash
./run.sh
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\streamlit run app/main.py
```

Streamlit prints a private local URL, normally `http://localhost:8501`.

## First use

1. Open **Investor Summary** to review the preloaded history through June 2026.
2. For July 2026 and later, open **Statements and Imports** and upload one or more AppFolio owner-statement PDFs.
3. Select **Analyze statements**.
4. Review the detected periods, properties, totals, and warnings.
5. Import new statements or explicitly approve revisions.
6. Open **Property Setup**, select every automatically discovered property, and confirm whether it was a cash purchase or mortgage. For a mortgage, enter its amount, interest rate, start date, amortization period, payment, and current balance.
7. Add the nine units and loan/valuation information.
8. Record expenses paid outside AppFolio under **Owner-Paid Expenses**.

The application stores newly imported copies under `data/statements/YYYY/`. Runtime PDFs, backups, and spreadsheets are intentionally ignored by Git. The tracked normalized statement seed makes a fresh clone immediately usable through June 2026 without publishing the private source PDFs.

## External-expense uploads

Download the CSV template from the **Owner-Paid Expenses** page. Required values are:

- `date`
- `property_id`
- `description`
- positive `amount`
- `financial_classification`

Valid classifications are shown in the UI and the workbook's `Lists` worksheet. Previewed rows are validated and deduplicated before they are appended.

## Command-line imports

```bash
.venv/bin/python run_pipeline.py /path/to/statement.pdf
.venv/bin/python run_pipeline.py /path/to/folder
.venv/bin/python run_pipeline.py --allow-revisions /path/to/revised.pdf
```

Without `--allow-revisions`, the CLI safely skips a changed PDF for an already imported period.

## Workbook safety

The workbook is the persistent source of truth. Every write:

1. Acquires a local lock.
2. Creates a timestamped backup.
3. Saves to a temporary workbook.
4. Reopens the temporary file to validate it.
5. Atomically replaces the live workbook.

Close the workbook in Excel or Apple Numbers before saving changes from Streamlit. If the process is forcibly terminated and no dashboard process remains, a stale `.lock` file may need to be removed manually.

## Tests

```bash
.venv/bin/pytest -q
```

The local regression suite validates the representative statement, multipage parsing, work orders, reconciliation, duplicate/revision behavior, workbook backups, external-expense deduplication, amortization, and financial formulas. The real statement is local-only because it contains private data.

## Data model

The workbook contains sheets for properties, units, loans, loan adjustments, statements, property summaries, normalized transactions, external expenses, valuations, work orders, monthly metrics, import warnings, lists, and Excel dashboards.

Stable IDs connect records even when a displayed property or unit name changes. A newly encountered statement property is created with `Needs review` type so it can be completed in the property editor without losing its transaction mapping.
