"""MS1 inclusion planning: inspect reproducible validation artifacts."""
import streamlit as st

from src.MS1InclusionWorkflow import MS1InclusionWorkflow
from src.common.common import page_setup, save_params

params = page_setup()
wf = MS1InclusionWorkflow()

st.title("MS1 inclusion planning · Results")
st.caption("Completed full runs include a combined inclusion list and "
           "per-CV native FLASHIda files; validation-only runs contain configuration.")
wf.show_results_section()
st.divider()
wf.show_run_manager_section()
save_params(params)
