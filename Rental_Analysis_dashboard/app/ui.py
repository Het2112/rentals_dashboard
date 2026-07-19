import streamlit as st
import pandas as pd

def render_import_review():
    st.header("Import Review")
    # Load errors from Excel
    errors_df = pd.read_excel("data/Rental_Portfolio.xlsx", sheet_name="Import_Errors")
    
    if errors_df.empty:
        st.success("No import errors found.")
    else:
        st.warning(f"Found {len(errors_df)} records needing review.")
        st.dataframe(errors_df)
        
        # Example action: User selects a record to 'Ignore' or 'Correct'
        selected_idx = st.selectbox("Select error to resolve", errors_df.index)
        if st.button("Mark as Resolved"):
            # Update error status and move to main ledger
            pass
