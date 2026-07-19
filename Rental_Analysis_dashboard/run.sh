#!/bin/bash
# 1. Sync dependencies and create venv if needed
uv sync
# 2. Run the application
uv run streamlit run app/main.py
