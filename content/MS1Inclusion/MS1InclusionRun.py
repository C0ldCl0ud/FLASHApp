"""MS1 inclusion planning: execute and inspect configuration validation."""
import streamlit as st

from src.MS1InclusionWorkflow import MS1InclusionWorkflow
from src.common.common import page_setup, save_params

params = page_setup()
wf = MS1InclusionWorkflow()

st.title("MS1 inclusion planning · Run")
st.caption("Validation checks that input files exist, the experimental design has "
           "distinct biological replicates, contrasts match the condition labels, "
           "and parameter values are valid.")
st.warning("This stage only validates configuration and writes a reproducible setup "
           "report; it does not execute deconvolution, alignment or inclusion-list selection.")
wf.show_execution_section(
    results_page="content/MS1Inclusion/MS1InclusionResults.py",
    results_label="View validation report",
)
save_params(params)
