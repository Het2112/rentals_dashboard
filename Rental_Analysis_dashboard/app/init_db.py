import pandas as pd
from pathlib import Path

def init_workbook():
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    excel_path = data_dir / "Rental_Portfolio.xlsx"
    
    # Define the 12 required worksheets
    sheets = [
        "Properties", "Units", "Loans", "Statements", "Property_Summaries", 
        "Transactions", "External_Expenses", "Property_Values", 
        "Monthly_Metrics", "Import_Errors", "Portfolio_Dashboard", "Lists"
    ]
    
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        for sheet in sheets:
            # Initialize with empty DataFrame but defined columns if needed later
            pd.DataFrame().to_excel(writer, sheet_name=sheet, index=False)
            
    print(f"✅ Workbook initialized at {excel_path}")

if __name__ == "__main__":
    init_workbook()
