"""MS1 inclusion planning: experimental design and effective parameters."""
import streamlit as st

from src.MS1InclusionWorkflow import MS1InclusionWorkflow, RUN_PAGE
from src.common.common import page_setup, save_params

params = page_setup()
wf = MS1InclusionWorkflow()

st.title("MS1 inclusion planning · Configure")
st.info("Use **validation** to check the design and settings, or choose **full** "
        "to execute the FAIMS-stratified scientific pipeline and generate inclusion targets.")
wf.show_parameter_section()
wf.show_run_button(
    RUN_PAGE,
    label="Start selected processing mode",
    name_label="Configuration name",
    summary="Validation-only by default. Select full processing above to run "
            "deconvolution, alignment, statistics and inclusion-list generation.",
)
save_params(params)
