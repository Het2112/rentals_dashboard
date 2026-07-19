import pandas as pd
from pathlib import Path
from openpyxl import load_workbook

class PortfolioManager:
    def __init__(self, excel_path: Path):
        self.excel_path = excel_path
    def append_transactions(self, df):
        csv_path = Path("data/Rental_Portfolio.csv")
        # Append to CSV instead of Excel
        df.to_csv(csv_path, mode='a', index=False, header=not csv_path.exists())
