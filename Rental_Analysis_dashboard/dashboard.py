import streamlit as st
import pandas as pd
from pathlib import Path
from numbers_parser import Document
from datetime import date  # This is the important part

# Configure Page
st.set_page_config(page_title="Rental Dashboard", layout="wide")

st.title("📊 Rental Property Performance")

def load_data():
    csv_path = Path("data/Rental_Portfolio.csv")
    
    st.write(f"Checking for file at: {csv_path.absolute()}")
    
    if not csv_path.exists():
        st.error("File does not exist!")
        return pd.DataFrame()
        
    df = pd.read_csv(csv_path)
    if df.empty:
        st.error("File exists but contains no data.")
        return pd.DataFrame()
        
    return df


df = load_data()

if not df.empty:
    st.subheader("Transaction Ledger")
    st.dataframe(df, use_container_width=True)

    # Basic KPI: Total Cash In
    total_in = df['Cash In'].sum()
    total_out = df['Cash Out'].sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Cash In", f"${total_in:,.2f}")
    col2.metric("Total Cash Out", f"${total_out:,.2f}")
    col3.metric("Net Cash Flow", f"${(total_in - total_out):,.2f}")
else:
    st.warning("No data found in the Excel file. Please run the pipeline first.")

st.sidebar.header("Add Manual Expense")
with st.sidebar.form("manual_expense_form"):
    date_val = st.date_input("Date", date.today())
    payee = st.text_input("Payee")
    amount = st.number_input("Amount ($)", min_value=0.0, step=0.01)
    desc = st.text_input("Description")
    submitted = st.form_submit_button("Log Expense")

if submitted:
    new_data = {
        'Date': [date_val],
        'Payee': [payee],
        'Type': ['Manual Expense'],
        'Cash Out': [amount],
        'Description': [desc]
    }
    new_df = pd.DataFrame(new_data)
    # Append to existing CSV
    new_df.to_csv("data/Rental_Portfolio.csv", mode='a', header=False, index=False)
    st.sidebar.success("Expense logged!")
    st.rerun() # Refresh the dashboard to show new data
