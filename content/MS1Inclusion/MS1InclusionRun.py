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
st.info("Validation mode saves a reproducible setup report only. Full mode "
        "executes each thesis CLI in order and records the output files and native "
        "toolchain. Failed stages mark the run as failed.")
wf.show_execution_section(
    results_page="content/MS1Inclusion/MS1InclusionResults.py",
    results_label="View validation report",
)
save_params(params)
