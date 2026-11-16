"""MS1 inclusion planning: experimental design and effective parameters."""
import streamlit as st

from src.MS1InclusionWorkflow import MS1InclusionWorkflow, RUN_PAGE
from src.common.common import page_setup, save_params

params = page_setup()
wf = MS1InclusionWorkflow()

st.title("MS1 inclusion planning · Configure")
st.info("Scaffold milestone: Run validates and saves configuration only. "
        "Scientific processing and inclusion-target generation are not connected yet.")
wf.show_parameter_section()
wf.show_run_button(
    RUN_PAGE,
    label="Validate configuration",
    name_label="Configuration name",
    summary="Validate the MS1 files, biological replicates, contrasts and settings. "
            "No processing is executed.",
)
save_params(params)
