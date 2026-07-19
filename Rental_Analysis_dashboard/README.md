📊 Rental Portfolio Analytics Dashboard
1. Project Vision & Goals
The objective of this project is to build a high-performance, centralized financial intelligence dashboard to track a growing portfolio of residential rental units.

When managing multiple properties, financial data often becomes fragmented across automated property management platforms (such as AppFolio), bank statements, and out-of-pocket expenses paid directly by owners (e.g., local property taxes, insurance premiums, or emergency repairs).

This project solves that fragmentation by providing a unified web-based interface that acts as the single source of truth for all income and expense items. The final system delivers:

Unified Financial Visibility: A single master ledger that handles both automated digital imports and manual entries seamlessly.

Real-Time Cash Flow Analytics: Immediate visualization of key performance indicators (KPIs) to monitor the financial health of the assets.

Operational Clarity: A scalable tool built to analyze net operational income, identify expense leaks, and track historical portfolio trends over time.

2. System Architecture & Technical Journey
The platform is designed around a decoupled architecture separating data processing from presentation.

       [ Raw Property Reports ]
                  │
                  ▼
     ┌────────────────────────┐
     │    run_pipeline.py     │  (Data Cleaning & Standardization)
     └────────────┬───────────┘
                  │
                  ▼
     ┌────────────────────────┐
     │ Rental_Portfolio.csv   │  (The Central Source of Truth)
     └────────────┬───────────┘
                  │
                  ▼
     ┌────────────────────────┐
     │      dashboard.py      │  (Streamlit Web Interface)
     └────────────────────────┘
Technical History & Key Architectural Decisions:
During development, we navigated specific format constraints that informed our current engineering design:

Excel Mismatch (.xlsx): The ingestion pipeline originally targeted a Microsoft Excel format. However, because the workflow utilizes Apple Numbers on macOS locally, parsing binary Excel structures led to configuration and engine issues (OptionError) within the containerized Python environment.

Native Apple Numbers (.numbers): We attempted to read native Apple Numbers files directly via the numbers-parser library. However, Numbers spreadsheets inherently format empty grids with padded null objects, causing ValueError: Duplicate column names found: [None, None...] when converting to data frames.

The Solution (CSV Standard): To establish a robust, platform-agnostic, and fast data exchange, we transitioned the master datastore entirely to a flat Comma-Separated Values format (data/Rental_Portfolio.csv). This approach avoids proprietary file format locks, executes efficiently in Pandas, and remains natively editable across both Excel and Apple Numbers.

3. Current Implementation Status
We have successfully engineered Phase 1 of the platform:

Data Pipeline (app/processor.py & run_pipeline.py): An automated python pipeline that cleans raw transaction inputs, standardizes column mapping, and systematically appends new historical data to the master CSV storage without overwriting past entries.

Central Datastore (data/Rental_Portfolio.csv): A clean, flat schema containing structural financial columns: Date, Payee, Type, Ref, Description, Cash In, Cash Out, and Balance.

Web Dashboard UI (dashboard.py): A frontend application powered by Streamlit that:

Dynamically monitors the local file system for the presence of the data file.

Ingests and cleans currency anomalies on the fly using Pandas.

Renders an interactive, searchable transaction ledger directly on screen.

Maintains aggregate metric cards tracking Total Cash In, Total Cash Out, and Net Cash Flow.

4. Phased Roadmap (Next Steps)
We are currently executing a programmatic, step-by-step rollout to build out full capability:

🟩 Phase 1: Manual Expense Tracking (Next Up)
Objective: Capture out-of-pocket expenses that never pass through automated agency ledgers (e.g., owner-funded maintenance, legal setup fees, property taxes).

Feature: Implement an intuitive input form embedded inside the Streamlit sidebar to capture Date, Payee, Amount, and Description.

Mechanism: Programmatically append entries to Rental_Portfolio.csv in real-time and trigger a UI state refresh (st.rerun()) to update the aggregated charts instantly.

⬜ Phase 2: Advanced Visual Analytics
Objective: Replace raw text tables with visual pattern discovery.

Feature 1: Chronological line/bar charts tracking Monthly Net Cash Flow trends to visualize cyclical performance.

Feature 2: Category-specific allocation breakdown charts (e.g., tracking total capital expenditure vs. recurring management fees) to isolate where capital is deployed.

⬜ Phase 3: Dynamic Multi-Attribute Filtering
Objective: Provide analytical granular control for portfolio scanning.

Feature: Build sidebar filtration controls allowing users to isolate data by specific calendar date ranges, transaction types (Income vs. Expense), or unique keyword patterns.

⬜ Phase 4: Production Polishing & Optimization
Objective: Prepare the tool for lightning-fast handling as data scales over several years.

Feature: Refine caching layers (st.cache_data) to prevent repetitive disk reads, formalize structural error handling for data edge cases, and visually brand the interface for maximum presentation utility.

5. Quickstart for Developers
Environment Setup
This repository uses uv for lightning-fast, predictable Python dependency management.

Install dependencies into your local virtual environment:

Bash
uv pip install -r requirements.txt
Run the ingestion pipeline to import your latest properties data:

Bash
uv run python run_pipeline.py
Launch the local interactive UI dashboard:

Bash
uv run streamlit run dashboard.py
Core Directory Layout
dashboard.py: Renders the Streamlit application framework and financial metrics.

run_pipeline.py: The execution trigger for extracting and cleaning incoming files.

app/processor.py: Core processing backend engine defining the transformation parameters.

data/: Secure directory housing the structural file asset (Rental_Portfolio.csv).


## 2. Status: What We Have Achieved
*   **Data Architecture**: Established `data/Rental_Portfolio.csv` as the system's "Source of Truth".
*   **Data Pipeline**: Built an automated `run_pipeline.py` script.
*   **Dashboard Foundation**: Created a functional Streamlit UI that calculates real-time KPIs.
*   **Manual Entry Framework**: Integrated a sidebar form for manual expense logging.

## 3. The Roadmap (Next Steps)
*   **[DEBUGGING] Manual Expense Logging**: The current implementation of the manual entry form needs troubleshooting to ensure it correctly updates `Rental_Portfolio.csv` without formatting conflicts.
*   **Advanced Visualization**: Implement monthly cash flow charts and expense category breakdowns.
*   **Interactive Filtering**: Add sidebar controls to filter data by date ranges and transaction types.
