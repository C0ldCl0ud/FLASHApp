"""FLASHApp adapter for FAIMS-aware MS1 discovery and inclusion-list generation.

The computational steps invoke the installed thesis processing CLIs. A user
can select validation-only mode to review their experiment before analysis.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.ms1_inclusion_validation import (
    DEFAULT_SETTINGS, validate_setup, write_validation_artifacts,
)
from src.ms1_inclusion_pipeline import execute_pipeline, EXECUTABLE_SETTINGS
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
    run_label = "MS1 inclusion planning"

    def __init__(self) -> None:
        super().__init__("MS1 inclusion planning", st.session_state["workspace"])

    @staticmethod
    def _selected(params: dict) -> list[str]:
        selected = params.get("ms1-files") or []
        if isinstance(selected, str):
            selected = [selected]
        return [str(path) for path in selected]

    def _saved_inputs(self, *, for_execution: bool = False):
        """Read persisted state; running work uses its immutable run snapshot.

        FLASHApp copies params.json and experimental-design.tsv into the new
        run before starting its worker. Do not observe later Configure edits
        from another browser session while that worker is executing.
        """
        if for_execution and self.multi_run and self.run_id:
            params = self.runs.params(self.run_id)
            design_dir = self.runs.run_dir(self.run_id)
        else:
            params = self.parameter_manager.get_parameters_from_json()
            design_dir = self.workflow_dir
        selected = self._selected(params)
        design = load_design(design_dir, selected)
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

        st.markdown("#### Execution")
        self.ui.input_widget(
            "execution-mode", "validation", "Processing mode",
            widget_type="selectbox", options=["validation", "full"],
            help="Validation saves the design and settings only. Full runs "
                 "FAIMS-specific deconvolution, alignment, differential "
                 "abundance, and targeted inclusion-list generation.",
        )
        st.caption("Choose **full** to start the scientific pipeline. "
                   "The required thesis package and modified FLASHQuant must "
                   "be installed in FLASHApp's Python environment.")

        tab_inputs, tab_deconv, tab_stats, tab_targets = st.tabs([
            "FAIMS and preprocessing", "Deconvolution and alignment",
            "Comparison", "Inclusion targets",
        ])

        with tab_inputs:
            self.ui.input_widget(
                "faims-mode", DEFAULT_SETTINGS["faims-mode"], "FAIMS handling",
                widget_type="selectbox", options=["auto", "require"],
                help="Auto retains observed CVs and no_cv; require rejects "
                     "unannotated MS1 scans. Deconvolution always splits by CV.",
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
            st.caption("Minimum mass/charge controls are reserved for a verified "
                       "FLASHDeconv CLI extension. Set both to zero for full runs; "
                       "nonzero values are rejected rather than ignored.")
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
            with st.expander("Native executable paths (optional overrides)"):
                st.caption("The correct OpenMS build must be accessible to the "
                           "FLASHApp Python process. This is particularly "
                           "important for the thesis's modified FLASHDeconv "
                           "and ConsensusFeatureGroupDetector.")
                for part, (key, default) in EXECUTABLE_SETTINGS.items():
                    self.ui.input_widget(
                        key, default,
                        part.replace("_", " ").capitalize(),
                        widget_type="text",
                        help="Executable on PATH or an absolute path.",
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
        """Run validation, then optionally execute the installed scientific pipeline."""
        try:
            selected, design, params = self._saved_inputs(for_execution=True)
            errors = validate_setup(selected, design, params)
            if errors:
                for error in errors:
                    self.logger.log(f"ERROR: {error}")
                return False
            report = write_validation_artifacts(
                Path(self.results_dir), selected, design, params,
            )
            self.logger.log(
                f"MS1 configuration validated for {len(selected)} acquisitions and "
                f"{report['contrast']['condition_a']} vs "
                f"{report['contrast']['condition_b']}."
            )
            if params.get("execution-mode", "validation") == "validation":
                self.logger.log("Validation only: scientific pipeline was not executed.")
                return True

            def convert_bruker(source):
                converted = self.executor.convert_to_mzml(
                    [str(source)],
                    out_dir=str(Path(self.workflow_dir, "converted-mzML")),
                )
                return converted[0] if converted else None

            result = execute_pipeline(
                selected, design, params, Path(self.results_dir),
                Path(self.workflow_dir, "cache", "thesis-deconv"),
                self.logger, self.executor.run_command,
                convert_bruker=convert_bruker,
            )
            self.logger.log(
                "Scientific pipeline completed; "
                f"{result['inclusion_summary'].get('total_targets', 0)} "
                "FAIMS inclusion targets exported."
            )
            return True
        except Exception as error:
            self.logger.log(f"ERROR: MS1 pipeline failed: {error}")
            import traceback
            self.logger.log(traceback.format_exc())
            return False

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

        pipeline_file = Path(self.results_dir, "pipeline_report.json")
        pipeline = None
        if pipeline_file.is_file():
            try:
                pipeline = json.loads(pipeline_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                st.error(f"Could not load pipeline summary: {error}")
        if pipeline and pipeline.get("pipeline_completed"):
            st.success("Scientific pipeline completed and inclusion targets exported.")
            summary = pipeline.get("inclusion_summary", {})
            st.metric("FAIMS targets", summary.get("total_targets", 0))
            cv_groups = summary.get("cv_groups") or {}
            if cv_groups:
                st.markdown("#### Targets by compensation voltage")
                st.dataframe([
                    {"CV group": key, "CV (V)": val.get("cv_volts"),
                     "Consensus features": val.get("input_consensus_features", 0),
                     "Exported targets": val.get("exported_targets", 0)}
                    for key, val in sorted(cv_groups.items())
                ], hide_index=True, use_container_width=True)
            target_file = Path(self.results_dir, "inclusion_list", "inclusion_list.tsv")
            if target_file.is_file():
                import pandas as pd
                targets = pd.read_csv(target_file, sep="\t")
                st.markdown("#### Inclusion targets")
                st.dataframe(targets, hide_index=True, use_container_width=True)
                st.download_button("Download combined inclusion list",
                                   data=target_file.read_bytes(),
                                   file_name="inclusion_list.tsv",
                                   mime="text/tab-separated-values")
            st.caption("Download native per-CV FLASHIda TSV files on this "
                       "page using the run's ZIP export or the Files view.")
        else:
            st.success("Experimental design and parameters validated.")
            st.info("Only configuration-validation artifacts are available for "
                    "this run. No completed scientific pipeline report was found.")
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
