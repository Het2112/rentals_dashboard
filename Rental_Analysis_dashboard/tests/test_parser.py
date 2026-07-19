import pytest
from pathlib import Path
from app.parser import AppFolioParser

def test_can_read_statement():
    pdf_path = Path("monthly_statements/Owner packet (19).pdf")
    parser = AppFolioParser(pdf_path)
    df = parser.parse_and_validate()
    
    # Print the first 10 rows to inspect the structure
    print("\n--- Extracted Data Preview ---")
    print(df.head(10))
    
    assert df is not None and not df.empty
