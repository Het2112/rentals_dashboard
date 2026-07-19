import pandas as pd
import pdfplumber
import hashlib
from pathlib import Path

class AppFolioParser:
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def get_file_hash(self):
        return hashlib.sha256(self.file_path.read_bytes()).hexdigest()

    def clean_currency(self, value):
        if not value or pd.isna(value): return 0.0
        val = str(value).replace('$', '').replace(',', '').replace('\n', '')
        if '(' in val: val = '-' + val.replace('(', '').replace(')', '')
        try: return float(val)
        except: return 0.0

    def parse_and_validate(self):
        all_transactions = []
        with pdfplumber.open(self.file_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    header = [str(c).strip() for c in table[0] if c] if table[0] else []
                    if 'Date' in header:
                        for row in table[1:]:
                            if not row[0] or 'Total' in str(row[0]): continue
                            cleaned_row = [str(c).replace('\n', ' ') if c else '' for c in row]
                            all_transactions.append(cleaned_row)
        
        cols = ['Date', 'Payee', 'Type', 'Ref', 'Description', 'Cash In', 'Cash Out', 'Balance']
        # Ensure we don't exceed column count if table format varies
        df = pd.DataFrame(all_transactions, columns=cols[:len(all_transactions[0])])
        
        df['Cash In'] = df['Cash In'].apply(self.clean_currency)
        df['Cash Out'] = df['Cash Out'].apply(self.clean_currency)
        return df
