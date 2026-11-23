"""Offline MS1 replay in the developer-only, local FLASHIda area."""
from pathlib import Path
import json

import streamlit as st

from src import flashida, flashida_simulation as simulation
from src.common.common import page_setup, save_params, native_dialog_available, tk_file_dialog
from src.workflow._log_status import classify_log_outcome

params = page_setup()
if not flashida.available():
    st.info("MS1 simulation is a developer-only feature of local FLASHApp.")
    st.stop()

from src.Workflow import FlashidaSimulationWorkflow  # noqa: E402

wf = FlashidaSimulationWorkflow()
running = wf.get_workflow_status()["running"]

st.title("FLASHIda MS1 simulation")
st.caption("🛠️ Developer feature · offline simulation with a precollected MS1-only RAW/mzML")
st.info("Runs baseline FLASHIda precursor selection only. MS2 commands are logged as "
        "unfulfilled requests; this does not produce fragment spectra or identifications.")

with st.expander("Replay configuration", expanded=not running):
    if running:
        st.caption("Stop the replay before changing its inputs.")
        for label, value in wf.parameter_manager.get_parameters_from_json().items():
            if label in ("ms1-source", "simulator-executable", "simulation-method",
                         "wsl-project", "wsl-environment", "faims-cv", "scan-limit",
                         "simulation-output-root"):
                st.text_input(label, str(value), disabled=True)
    else:
        if native_dialog_available() and st.button("📁 Browse precollected MS1 file",
                                                   key="flashida-sim-pick-ms1"):
            picked = tk_file_dialog("Select MS1-only RAW or mzML file",
                                    [("MS1 RAW", "*.raw *.RAW"),
                                     ("centroided mzML", "*.mzML *.mzml")],
                                    str(Path.home()), multiple=False)
            if picked:
                st.session_state[
                    f"{wf.parameter_manager.param_prefix}ms1-source"
                ] = str(picked[0])
                st.rerun()
        wf.configure()
        wf.parameter_manager.save_parameters()

st.caption("The MS1 file is reused; preprocessing uses the thesis-project cache. "
           "Each replay is saved in a new directory, including the method and both process logs.")

a, b = st.columns([4, 1])
if running:
    a.write("Replay in progress. The results below update while the native engine runs.")
    if b.button("⏹ Stop replay", use_container_width=True):
        active = [folder for folder in simulation.list_runs(wf.workflow_dir, wf.parameter_manager.get_parameters_from_json())
                  if simulation.read_run(folder).get("state") in ("preparing", "running")]
        for folder in active:
            simulation.stop_children(folder)
        if wf.stop_workflow():
            # Terminating a workflow worker bypasses its Python finally block.
            # Explicitly mark preserved runs so they are never shown as running forever.
            for folder in active:
                manifest = simulation.read_run(folder)
                if manifest.get("state") in ("preparing", "running"):
                    manifest["state"] = "cancelled"
                    manifest["error"] = "Stopped by user"
                    simulation.write_json(folder / "simulation.json", manifest)
        st.rerun()
else:
    a.write("Start a new baseline MS1-only replay.")
    if b.button("▶ Run simulation", type="primary", use_container_width=True):
        config = wf.parameter_manager.get_parameters_from_json()
        try:
            source = Path(str(config.get("ms1-source") or "").strip())
            exe = Path(str(config.get("simulator-executable") or "").strip())
            method = Path(str(config.get("simulation-method") or "").strip())
            if not source.is_file() or source.suffix.lower() not in (".raw", ".mzml"):
                raise ValueError("Choose an existing MS1 RAW or mzML file.")
            if not exe.is_file() or exe.name.lower() != "flash.simulator.exe":
                raise ValueError("Choose a Windows Flash.Simulator.exe.")
            if not method.is_file():
                raise ValueError("Choose an existing method.json.")
            method_data = json.loads(method.read_text(encoding="utf-8-sig"))
            if (method_data.get("precursor_selection") or {}).get("targeting", "none") != "none":
                raise ValueError("This milestone supports only targeting = none.")
            if not str(config.get("wsl-project") or "").startswith("/"):
                raise ValueError("Enter the absolute thesis-project path inside WSL.")
            if not str(config.get("wsl-environment") or "").strip():
                raise ValueError("Specify a WSL conda environment.")
            simulation.producer_command(
                project=str(config["wsl-project"]),
                environment=str(config["wsl-environment"]),
                input_file=str(source), run_dir=str(simulation.run_root(wf.workflow_dir, config) / "preflight"),
                cv=str(config.get("faims-cv") or ""),
                max_scans=str(config.get("scan-limit") or ""))
        except (ValueError, OSError, json.JSONDecodeError) as error:
            st.error(str(error))
        else:
            wf.start_workflow()
            st.rerun()


@st.fragment(run_every="3s")
def show_results():
    runs = simulation.list_runs(wf.workflow_dir, wf.parameter_manager.get_parameters_from_json())
    if not runs:
        st.caption("No simulations have been run in this workspace yet.")
        return
    names = [p.name for p in runs]
    selected = st.selectbox("Saved simulation", names, key="flashida-simulation-selected")
    folder = runs[names.index(selected)]
    manifest = simulation.read_run(folder)
    state = manifest.get("state", "unknown")
    st.write(f"**State:** {state} · **Input:** {manifest.get('source', '?')}")
    status_file = folder / "consumer_status.json"
    if status_file.is_file():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
            completed = status.get("last_completed_sequence", 0)
            received = status.get("last_received_sequence", 0)
            st.write(f"MS1 surveys received: **{received}** · processed: **{completed}**")
        except (ValueError, OSError):
            st.caption("Consumer status is being updated.")
    if state == "complete":
        st.success("MS1 replay completed, with all surveys acknowledged.")
        st.metric("MS2 acquisition requests (not spectra)", manifest.get("ms2_requests_unfulfilled", 0))
        st.json(manifest.get("commands_by_disposition", {}))
    elif state == "failed":
        st.error(manifest.get("error", "Replay stopped with an error."))
    st.caption(f"Saved results: {folder}")
    files = ("commands.tsv", "replay_log.tsv", "consumer_status.json",
             "method.json", "simulation.json", "producer.log", "consumer.log")
    existing = [folder / name for name in files if (folder / name).is_file()]
    for path in existing:
        st.download_button("Download " + path.name, path.read_bytes(),
                           file_name=path.name, key="sim-dl-" + selected + path.name)
    if state in ("preparing", "running", "failed"):
        with st.expander("Recent process output"):
            for name in ("producer.log", "consumer.log"):
                log = folder / name
                if log.is_file():
                    st.markdown("**" + name + "**")
                    st.code("\n".join(log.read_text(encoding="utf-8", errors="replace")
                                      .splitlines()[-35:]), language=None)


st.subheader("Simulation results")
show_results()

st.divider()
st.subheader("Experimental design and differential-abundance contrast")
st.caption("Independent MS1 analysis step: reuse the thesis project's experiment_design "
           "generator. The E. coli preset explicitly maps AA–AD to acetate and GA–GD "
           "to glucose. It writes design.tsv and run_metadata.tsv, plus a "
           "glucose_vs_acetate contrast.tsv when each condition has at least two biological replicates.")
with st.form("ecoli-design"):
    raw_dir = st.text_input("Folder containing the eight E. coli MS1 RAW files",
                            placeholder="/mnt/c/Users/langm/PycharmProjects/TDP_master_thesis/data"
                            if simulation.is_wsl() else "C:\\path\\data")
    output_dir = st.text_input("Experimental design output folder",
                               placeholder="/mnt/c/Users/langm/PycharmProjects/TDP_master_thesis/experiments/ecoli_flashapp_draft"
                               if simulation.is_wsl() else "C:\\path\\experiments\\ecoli_full")
    create = st.form_submit_button("Create E. coli design and contrast",
                                   disabled=running)
if create:
    settings = wf.parameter_manager.get_parameters_from_json()
    try:
        result = simulation.create_experiment_design(
            project=str(settings.get("wsl-project") or "").strip(),
            environment=str(settings.get("wsl-environment") or "openms-experiments").strip(),
            raw_dir=raw_dir.strip(), design_dir=output_dir.strip())
    except (ValueError, OSError, RuntimeError) as error:
        st.error(str(error))
    else:
        st.success(f"Design created with {result['runs']} MS1 runs in {result['directory']}.")
        counts = result.get("replicates_by_condition", {})
        st.write("Biological replicates:", counts)
        if not result["contrast_created"]:
            st.warning("Contrast not created: both acetate and glucose need at least two "
                       "biological replicates in the design.")
        elif counts.get("acetate") != 4 or counts.get("glucose") != 4:
            st.warning("Contrast created, but the planned 4+4 biological replicates "
                       "are not all represented; review the design.")
        st.code(result["output"], language=None)
st.caption("Inspect the biological assignments and validate design.tsv with "
           "experiment_design validate --mode ready after conversion to real mzML paths. "
           "The statistical test remains a separate step; this page does not run it.")

log = Path(wf.workflow_dir, "logs", "minimal.log")
if log.is_file():
    content = log.read_text(encoding="utf-8", errors="replace")
    if not running and classify_log_outcome(content) == "error":
        st.error("The last simulation workflow failed. Inspect the saved process logs.")
    with st.expander("Workflow log"):
        st.code("\n".join(content.splitlines()[-100:]), language=None)

save_params(params)
