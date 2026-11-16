"""Streamlit adapter for validation-only MS1 inclusion-list planning.

Scientific processing stays in the thesis pipeline. This first milestone only
collects settings, validates the selected MS1 runs and experimental design, and
writes versioned setup artifacts into FLASHApp's per-run results directory.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.ms1_inclusion_validation import (
    DEFAULT_SETTINGS, validate_setup, write_validation_artifacts,
)
from src.workflow.ExperimentalDesign import load_design
from src.workflow.WorkflowManager import WorkflowManager

MS1_DATA_TYPES = ["mzML", "raw", "d"]
RUN_PAGE = Path("content", "MS1Inclusion", "MS1InclusionRun.py")
CONFIGURE_PAGE = Path("content", "MS1Inclusion", "MS1InclusionConfigure.py")


class MS1InclusionWorkflow(WorkflowManager):
    """Independent four-page workflow; no modifications to TopDownWorkflow."""

    multi_run = True
    run_page = RUN_PAGE
    configure_page = CONFIGURE_PAGE
    run_label = "MS1 inclusion-list setup"

    def __init__(self) -> None:
        super().__init__("MS1 inclusion planning", st.session_state["workspace"])

    @staticmethod
    def _selected(params: dict) -> list[str]:
        selected = params.get("ms1-files") or []
        if isinstance(selected, str):
            selected = [selected]
        return [str(path) for path in selected]

    def _saved_inputs(self):
        """Read inputs only from the workspace, including in background workers."""
        params = self.parameter_manager.get_parameters_from_json()
        selected = self._selected(params)
        design = load_design(self.workflow_dir, selected)
        return selected, design, params

    def input_problems(self) -> str:
        collisions = super().input_problems()
        try:
            selected, design, params = self._saved_inputs()
            problems = validate_setup(selected, design, params)
        except (OSError, ValueError, KeyError) as error:
            problems = [f"Could not read MS1 input metadata: {error}"]
        return " ".join(part for part in [collisions, *problems] if part)

    def upload(self) -> None:
        st.markdown("Add the MS1 acquisitions from each biological replicate.")
        self.ui.upload_widget(
            key="ms1-files",
            name="MS1 acquisitions",
            file_types=MS1_DATA_TYPES,
            suggest_folders=True,
        )

    @st.fragment
    def configure(self) -> None:
        st.markdown("#### MS1 inputs and experimental design")
        self.ui.select_input_file(
            "ms1-files",
            name="MS1 acquisitions",
            multiple=True,
            reactive=True,
            upload=dict(
                file_types=MS1_DATA_TYPES,
                suggest_folders=True,
            ),
        )
        self.ui.experimental_design("ms1-files", name="Samples and biological replicates",
                                    suggest=True)

        tab_inputs, tab_deconv, tab_stats, tab_targets = st.tabs([
            "FAIMS and preprocessing", "Deconvolution and alignment",
            "Comparison", "Inclusion targets",
        ])

        with tab_inputs:
            self.ui.input_widget(
                "faims-mode", DEFAULT_SETTINGS["faims-mode"], "FAIMS handling",
                widget_type="selectbox", options=["auto", "require"],
                help="Auto accepts acquisitions without FAIMS; require rejects them. "
                     "Scientific FAIMS splitting is integrated in the next milestone.",
            )
            self.ui.input_widget(
                "rt-filter-enabled", False, "Restrict retention-time range",
                widget_type="checkbox",
            )
            st.caption("The RT range below applies only if the checkbox is enabled.")
            c1, c2 = st.columns(2)
            with c1:
                self.ui.input_widget(
                    "rt-min-minutes", 0.0, "Minimum RT (minutes)",
                    widget_type="number", min_value=0.0, step_size=0.5,
                )
            with c2:
                self.ui.input_widget(
                    "rt-max-minutes", 30.0, "Maximum RT (minutes)",
                    widget_type="number", min_value=0.0, step_size=0.5,
                )

        with tab_deconv:
            st.caption("Zero disables the optional precursor mass/charge minimum.")
            a, b = st.columns(2)
            with a:
                self.ui.input_widget(
                    "min-mass-da", 0.0, "Minimum precursor mass (Da)",
                    widget_type="number", min_value=0.0, step_size=100.0,
                )
            with b:
                self.ui.input_widget(
                    "min-charge", 0, "Minimum charge state",
                    widget_type="number", min_value=0, step_size=1,
                )
            self.ui.input_widget(
                "rt-alignment", "pose", "Retention-time alignment",
                widget_type="selectbox", options=["pose", "none"],
            )
            self.ui.input_widget(
                "coverage-mode", "partial", "Consensus coverage",
                widget_type="selectbox", options=["partial"],
                help="Partial-consensus groups are required for condition-specific targets.",
            )

        with tab_stats:
            st.caption("Type condition labels exactly as shown in the design table.")
            a, b = st.columns(2)
            with a:
                self.ui.input_widget(
                    "contrast-a", "", "Reference condition",
                    widget_type="text", help="First condition in the contrast.",
                )
            with b:
                self.ui.input_widget(
                    "contrast-b", "", "Comparison condition",
                    widget_type="text", help="Second condition in the contrast.",
                )
            a, b = st.columns(2)
            with a:
                self.ui.input_widget(
                    "q-threshold", 0.05, "Adjusted p-value cutoff",
                    widget_type="number", min_value=0.000001, max_value=1.0,
                    step_size=0.01,
                )
            with b:
                self.ui.input_widget(
                    "abs-log2fc-threshold", 0.58, "Absolute log2 fold-change cutoff",
                    widget_type="number", min_value=0.0, step_size=0.01,
                )

        with tab_targets:
            a, b, c = st.columns(3)
            with a:
                self.ui.input_widget(
                    "min-qscore", 0.80, "Minimum median Qscore2D",
                    widget_type="number", min_value=0.0, max_value=1.0, step_size=0.05,
                )
            with b:
                self.ui.input_widget(
                    "top-fraction", 0.50, "Top intensity fraction",
                    widget_type="number", min_value=0.01, max_value=1.0, step_size=0.05,
                )
            with c:
                self.ui.input_widget(
                    "rt-margin-seconds", 30.0, "RT margin per side (seconds)",
                    widget_type="number", min_value=0.0, step_size=5.0,
                )
            st.markdown("**Priorities by selection reason**")
            st.caption("Unspecified reasons default to 2 in the CLI; when reasons "
                       "overlap, the maximum numeric priority is chosen.")
            a, b, c = st.columns(3)
            with a:
                self.ui.input_widget(
                    "priority-quality", 2, "Quality",
                    widget_type="number", min_value=0, step_size=1,
                )
            with b:
                self.ui.input_widget(
                    "priority-diff-abundance", 2, "Differential abundance",
                    widget_type="number", min_value=0, step_size=1,
                )
            with c:
                self.ui.input_widget(
                    "priority-conditional", 2, "Condition-specific",
                    widget_type="number", min_value=0, step_size=1,
                )

    def execution(self) -> bool:
        """Validate and snapshot inputs. Returning True means *validation* passed."""
        try:
            selected, design, params = self._saved_inputs()
            errors = validate_setup(selected, design, params)
            if errors:
                for error in errors:
                    self.logger.log(f"ERROR: {error}")
                return False
            report = write_validation_artifacts(
                Path(self.results_dir), selected, design, params,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.logger.log(f"ERROR: MS1 configuration validation failed: {error}")
            return False

        self.logger.log(
            f"MS1 configuration validated for {len(selected)} acquisitions and "
            f"{report['contrast']['condition_a']} vs {report['contrast']['condition_b']}."
        )
        self.logger.log(
            "VALIDATION ONLY: no deconvolution, alignment, abundance tests, "
            "or inclusion-list generation was executed."
        )
        return True

    @st.fragment
    def results(self) -> None:
        path = Path(self.results_dir, "validation_report.json")
        if not path.is_file():
            st.info("This run has no configuration-validation report.")
            return
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            st.error(f"Could not load the saved validation report: {error}")
            return

        st.success("Experimental design and parameters validated.")
        st.warning(
            "Scaffold milestone: scientific processing has not run and no "
            "inclusion targets have been generated.",
            icon="⚠️",
        )
        st.caption(
            f"{len(report['selected_input_files'])} acquisitions · "
            f"{report['contrast']['condition_a']} vs "
            f"{report['contrast']['condition_b']}"
        )
        st.markdown("#### Biological replicates")
        st.dataframe(report["experimental_design"], hide_index=True,
                     use_container_width=True)
        with st.expander("Effective analysis settings"):
            st.json(report["settings"])

        st.markdown("#### Download configuration artifacts")
        for name, label, mime in [
            ("validation_report.json", "Validation report (JSON)", "application/json"),
            ("experimental_design.tsv", "Experimental design (TSV)", "text/tab-separated-values"),
            ("contrasts.tsv", "Contrast specification (TSV)", "text/tab-separated-values"),
        ]:
            file = Path(self.results_dir, name)
            if file.is_file():
                st.download_button(
                    label, data=file.read_bytes(), file_name=name, mime=mime,
                    key=f"ms1-download-{self.run_id}-{name}",
                )
