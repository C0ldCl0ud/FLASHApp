"""Simulation mode embedded in FLASHIda Acquisition.

The selected MS1 files, experimental design and contrasts share one persistent
plan. This is a local developer feature; instrument mode remains separate.
"""
from pathlib import Path
import hashlib
import json

import pandas as pd
import streamlit as st

from src import flashida_simulation as replay, flashida_experiment as design
from src.common.common import native_dialog_available, tk_file_dialog
from src.Workflow import FlashidaSimulationWorkflow


def _save_and_reset(workflow_dir, plan: dict, key: str) -> None:
    design.save_plan(workflow_dir, plan)
    st.session_state[key] = st.session_state.get(key, 0) + 1
    st.rerun()


def _edit_plan(wf, running: bool) -> dict:
    root = wf.workflow_dir
    plan = design.load_plan(root)
    token = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    version_key = f"flashida-sim-edition-{token}"

    st.subheader("Precollected MS1 files and experimental design")
    st.caption("Choose the spectra to replay, then annotate **each acquisition** with "
               "its sample ID, condition, biological replicate and FAIMS CV. "
               "These annotations are saved in the same plan used for the downstream OpenMS design.")

    if not running:
        defaults = wf.parameter_manager.get_parameters_from_json()
        suggested = str(Path(defaults.get("wsl-project") or ".") / "data")
        folder = st.text_input("Look for RAW/mzML files in a local folder",
                               value=suggested, key=f"flashida-sim-folder-{token}")
        available = []
        if Path(folder).is_dir():
            available = sorted(str(p.resolve()) for p in Path(folder).iterdir()
                               if p.is_file() and p.suffix.lower() in (".raw", ".mzml"))
        elif folder:
            st.caption("Folder not found. Enter a different folder, or add an absolute file path below.")
        picked = st.multiselect("Select precollected MS1 files", available,
                                format_func=lambda p: Path(p).name,
                                key=f"flashida-sim-selected-{token}",
                                help="Selecting does not upload or copy RAW data. Add the selection to the design table.")
        a, b = st.columns([2, 1])
        if a.button("Add selected MS1 files", disabled=not picked,
                    key=f"flashida-sim-add-{token}"):
            try:
                plan["runs"] = design.add_sources(plan["runs"], picked)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _save_and_reset(root, plan, version_key)
        manual_path = b.text_input("Or add file path", key=f"flashida-sim-manual-{token}",
                                   placeholder="/mnt/c/.../sample.raw")
        if manual_path:
            if b.button("Add this file", key=f"flashida-sim-manual-add-{token}"):
                try:
                    plan["runs"] = design.add_sources(plan["runs"], [manual_path])
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    _save_and_reset(root, plan, version_key)
        if native_dialog_available() and st.button("Browse MS1 files",
                                                    key=f"flashida-sim-browse-{token}"):
            paths = tk_file_dialog("Select RAW/mzML files",
                                   [("MS1 RAW", "*.raw *.RAW"),
                                    ("mzML", "*.mzML *.mzml")],
                                   str(Path.home()), multiple=True)
            if paths:
                try:
                    plan["runs"] = design.add_sources(plan["runs"], list(paths))
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    _save_and_reset(root, plan, version_key)

        if plan["runs"] and st.button("Apply E. coli AA–GD assignments",
                                     help="Explicitly fill sample/condition/replicate from AA–AD and GA–GD suffixes. "
                                          "No mapping is guessed unless you click this.",
                                     key=f"flashida-sim-preset-{token}"):
            try:
                plan["runs"] = design.apply_ecoli_preset(plan["runs"])
            except ValueError as exc:
                st.error(str(exc))
            else:
                _save_and_reset(root, plan, version_key)

    rows = pd.DataFrame(plan["runs"], columns=design.SOURCE_COLUMNS).fillna("")
    if len(rows):
        edited = st.data_editor(
            rows, num_rows="dynamic", hide_index=True, width="stretch",
            disabled=["file_path"] if not running else list(design.SOURCE_COLUMNS),
            key=f"flashida-sim-table-{token}-{st.session_state.get(version_key, 0)}",
            column_config={
                "file_path": st.column_config.TextColumn("MS1 RAW/mzML file", width="large"),
                "sample_id": st.column_config.TextColumn("Sample ID", required=True,
                                                          help="Biological sample identity, e.g. AA or GA"),
                "condition": st.column_config.TextColumn("Condition", required=True,
                                                          help="e.g. acetate or glucose; arbitrary condition labels supported"),
                "biological_replicate": st.column_config.TextColumn(
                    "Bio. replicate", required=True,
                    help="Positive integer, globally unique for different biological samples"),
                "faims_cv": st.column_config.TextColumn("FAIMS CV (V)",
                                                       help="CV for this file; e.g. -40"),
            },
        )
        edited_rows = [{field: str(r.get(field) or "").strip()
                        for field in design.SOURCE_COLUMNS}
                       for r in edited.to_dict(orient="records")]
        if not running and edited_rows != plan["runs"]:
            plan["runs"] = edited_rows
            design.save_plan(root, plan)
    else:
        st.info("Add one or more precollected MS1 files to begin. Each selected file becomes a design row.")

    st.subheader("Differential-abundance contrasts")
    st.caption("Contrasts are defined here, using the conditions entered above. "
               "A single-file simulation may have no contrast; statistical testing needs "
               "at least two biological replicates in each compared condition.")
    conditions = sorted({str(r.get("condition") or "").strip() for r in plan["runs"]}
                        - {""})
    if not running and {"acetate", "glucose"} <= set(conditions):
        if st.button("Add glucose_vs_acetate contrast", key=f"flashida-sim-default-contrast-{token}",
                     disabled=any(x.get("contrast_id") == "glucose_vs_acetate"
                                  for x in plan["contrasts"])):
            plan["contrasts"].append({
                "contrast_id": "glucose_vs_acetate",
                "condition_a": "glucose", "condition_b": "acetate",
            })
            _save_and_reset(root, plan, version_key)
    contrasts = pd.DataFrame(plan["contrasts"], columns=design.CONTRAST_COLUMNS).fillna("")
    if not running:
        st.caption("Add or remove contrast rows in the editable table.")
    edited_contrasts = st.data_editor(
        contrasts, num_rows="fixed" if running else "dynamic", hide_index=True,
        width="stretch", disabled=running,
        key=f"flashida-sim-contrast-{token}-{st.session_state.get(version_key, 0)}",
        column_config={
            "contrast_id": st.column_config.TextColumn("Contrast ID", required=True),
            "condition_a": st.column_config.SelectboxColumn("Condition A", options=conditions,
                                                              required=True),
            "condition_b": st.column_config.SelectboxColumn("Condition B", options=conditions,
                                                              required=True),
        },
    )
    if not running:
        updated = [{field: str(r.get(field) or "").strip()
                    for field in design.CONTRAST_COLUMNS}
                   for r in edited_contrasts.to_dict(orient="records")]
        if updated != plan["contrasts"]:
            plan["contrasts"] = updated
            design.save_plan(root, plan)
    return plan


@st.fragment(run_every="3s")
def _show_results(wf):
    runs = replay.list_runs(wf.workflow_dir, wf.parameter_manager.get_parameters_from_json())
    if not runs:
        st.caption("No saved acquisition simulations in this workspace.")
        return
    names = [p.name for p in runs]
    selected = st.selectbox("Previous simulation", names, key="flashida-acquisition-simulation-result")
    folder = runs[names.index(selected)]
    data = replay.read_run(folder)
    st.write("**State:**", data.get("state", "unknown"))
    st.caption("Sample: " + str(data.get("sample_id", "")) +
               " · Condition: " + str(data.get("condition", "")) +
               " · Biological replicate: " + str(data.get("biological_replicate", "")))
    status_path = folder / "consumer_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            st.write("MS1 scans processed: ", status.get("last_completed_sequence", 0),
                     "/", status.get("last_received_sequence", 0))
        except (ValueError, OSError):
            st.caption("Consumer status is being updated.")
    if data.get("state") == "complete":
        st.success("All selected MS1 scans acknowledged.")
        st.metric("MS2 requests (unfulfilled)", data.get("ms2_requests_unfulfilled", 0))
        st.json(data.get("commands_by_disposition", {}))
    elif data.get("state") in ("failed", "cancelled"):
        st.error(data.get("error", "Simulation stopped."))
    for name in ("commands.tsv", "replay_log.tsv", "consumer_status.json",
                 "simulation.json", "producer.log", "consumer.log", "method.json"):
        file = folder / name
        if file.is_file():
            st.download_button("Download " + name, data=file.read_bytes(), file_name=name,
                               key=f"flashida-simulation-dl-{folder.name}-{name}")


def render_simulation() -> None:
    wf = FlashidaSimulationWorkflow()
    running = wf.get_workflow_status().get("running", False)
    st.caption("Offline MS1 acquisition replay. Every file's design metadata is stored "
               "with its simulated MS2 requests; no MS2 spectra are generated.")

    plan = _edit_plan(wf, running)
    st.subheader("Simulator and FLASHIda method")
    with st.expander("Local simulator configuration", expanded=not running):
        if running:
            st.caption("The simulation is running; stop it to change these settings.")
            current = wf.parameter_manager.get_parameters_from_json()
            for field in ("simulator-executable", "simulation-method", "wsl-project",
                          "wsl-environment", "scan-limit", "simulation-output-root"):
                st.text_input(field, str(current.get(field) or ""), disabled=True)
        else:
            wf.configure()
            wf.parameter_manager.save_parameters()

    st.caption("The selected method currently uses baseline targeting (none). "
               "A later milestone can compare these requests against inclusion-list targets.")
    design_folder = Path(wf.workflow_dir) / "experimental_design"
    plan_file = design_folder / "simulation_plan.json"
    saved_design_matches = False
    if plan_file.is_file():
        try:
            saved_design_matches = json.loads(plan_file.read_text(encoding="utf-8")) == (
                design.validated_plan(plan))
        except (OSError, ValueError):
            pass
    if not saved_design_matches and plan["runs"]:
        st.warning("The OpenMS design is not yet saved for the current acquisition table.")

    a, b, c = st.columns([2, 2, 2])
    if running:
        a.info("MS1 replay in progress.")
        if b.button("⏹ Stop simulation", use_container_width=True):
            current = wf.parameter_manager.get_parameters_from_json()
            active = [p for p in replay.list_runs(wf.workflow_dir, current)
                      if replay.read_run(p).get("state") in ("preparing", "running")]
            for folder in active:
                replay.stop_children(folder)
            if wf.stop_workflow():
                for folder in active:
                    metadata = replay.read_run(folder)
                    if metadata.get("state") in ("preparing", "running"):
                        metadata.update(state="cancelled", error="Stopped by user")
                        replay.write_json(folder / "simulation.json", metadata)
            st.rerun()
    else:
        if a.button("Save experimental design", use_container_width=True):
            try:
                output = design.generate_design(
                    plan, design_folder,
                    project_dir=str(wf.parameter_manager.get_parameters_from_json().get("wsl-project") or ""))
            except (ValueError, OSError, ImportError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved {output['ms1_files']} MS1 acquisitions and "
                           f"{output['contrasts']} contrasts to {output['directory']}.")
                if output["warnings"]:
                    st.caption(f"{len(output['warnings'])} draft warning(s): "
                               "converted mzML paths may still be pending.")

        if b.button("▶ Run MS1 simulation", type="primary", use_container_width=True):
            params = wf.parameter_manager.get_parameters_from_json()
            try:
                design.validated_plan(plan)
                exe = Path(str(params.get("simulator-executable") or "").strip())
                method = Path(str(params.get("simulation-method") or "").strip())
                if not exe.is_file() or exe.name.lower() != "flash.simulator.exe":
                    raise ValueError("Select the standalone Flash.Simulator.exe.")
                if not method.is_file():
                    raise ValueError("Select an existing method.json.")
                parsed = json.loads(method.read_text(encoding="utf-8-sig"))
                if (parsed.get("precursor_selection") or {}).get("targeting", "none") != "none":
                    raise ValueError("Baseline simulation requires precursor_selection.targeting = none.")
                design.generate_design(
                    plan, design_folder, project_dir=str(params.get("wsl-project") or ""))
            except (ValueError, OSError, ImportError, json.JSONDecodeError) as exc:
                st.error(str(exc))
            else:
                wf.start_workflow()
                st.rerun()

    if (design_folder / "design.tsv").is_file():
        with c.popover("OpenMS design outputs", use_container_width=True):
            st.caption("Design and contrast files for the last saved acquisition plan. "
                       "For RAW-only data the expected mzML paths remain pending "
                       "until real conversion paths are supplied.")
            for name in ("design.tsv", "run_metadata.tsv", "contrast.tsv", "simulation_plan.json"):
                file = design_folder / name
                if file.is_file():
                    st.download_button("Download " + name, data=file.read_bytes(),
                                       file_name=name, key="flashida-plan-dl-" + name)
            st.code(str(design_folder), language=None)

    st.subheader("Simulated acquisition results")
    _show_results(wf)

    log = wf.workflow_dir / "logs" / "minimal.log"
    if log.is_file():
        with st.expander("Simulation workflow log"):
            st.code("\n".join(log.read_text(encoding="utf-8", errors="replace")
                              .splitlines()[-100:]), language=None)
