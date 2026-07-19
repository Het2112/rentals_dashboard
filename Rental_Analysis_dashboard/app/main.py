import streamlit as st
import subprocess

st.set_page_config(page_title="Rental Portfolio Tracker", layout="wide")

st.title("Rental Portfolio Tracker")

# Navigation or main logic
page = st.sidebar.selectbox("Navigate", ["Portfolio Overview", "Sync AppFolio"])

if page == "Sync AppFolio":
    if st.button("Sync Now"):
        # This will trigger our parser logic
        st.info("Syncing data...")
        # Placeholder for parser execution
        st.success("Sync Complete!")
