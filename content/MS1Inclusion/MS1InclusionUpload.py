"""MS1 inclusion planning: import/select acquisition files."""
import streamlit as st

from src.MS1InclusionWorkflow import MS1InclusionWorkflow
from src.common.common import page_setup, save_params

params = page_setup()
wf = MS1InclusionWorkflow()

st.title("MS1 inclusion planning · Input")
st.caption("Add MS1 mzML, Thermo RAW, or Bruker .d acquisitions. "
           "Conditions and biological replicates are assigned on Configure.")
wf.show_file_upload_section()
st.info("Next: open Configure to select the files and define the experimental design.")
save_params(params)
