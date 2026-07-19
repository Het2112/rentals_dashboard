from pathlib import Path
from app.parser import AppFolioParser
from app.processor import PortfolioManager

# 1. Initialize
pdf_path = Path("monthly_statements/Owner packet (19).pdf")
excel_path = Path("data/Rental_Portfolio.xlsx")

parser = AppFolioParser(pdf_path)
manager = PortfolioManager(excel_path)

# 2. Extract
df = parser.parse_and_validate()

# 3. Save
if not df.empty:
    manager.append_transactions(df)
else:
    print("⚠️ No transactions found to save.")
