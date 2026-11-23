# FLASHIda Acquisition — instrument or offline MS1 simulation

The hidden local developer FLASHIda → Acquisition page now offers an
**Instrument / Simulation** selector rather than a separate simulation page.

- **Instrument** preserves the existing Thermo/Xcalibur raw-file watcher and
  launches the real instrument Flash.exe.
- **Simulation** reads precollected MS1-only RAW or mzML files and launches the
  standalone Windows Flash.Simulator.exe. It NEVER controls the instrument.

Changing modes is disabled while either backend is running.

## Simulation: files and experimental design together

Choose the folder containing the precollected MS1 files and select which files
to add to the simulation acquisition table. Each file has its own editable row:

| Column | Meaning |
| --- | --- |
| MS1 RAW/mzML file | Absolute source path, no Streamlit upload/copy |
| Sample ID | Biological sample identity |
| Condition | Experiment group, e.g. acetate or glucose |
| Bio. replicate | Positive ID unique between biological samples |
| FAIMS CV (V) | Compensation voltage to replay, e.g. -40 |

The source and contrast tables are edited inside a **single Streamlit form**.
Type all annotations and click **Save acquisition design** once. The page does
not rerun or rewrite the table after each cell edit; this avoids losing the
first value to Streamlit's stale edit state. Save the draft before clicking
other acquisition actions such as Add files or Run simulation.

All rows are saved with the acquisition configuration, not in a later
disconnected form. Different files of the same biological sample can retain
the same sample/replicate combination, as technical injections.

The optional **Apply E. coli AA–GD assignments** button explicitly maps
AA–AD to acetate replicates 1–4 and GA–GD to glucose replicates 5–8.
File suffixes are not interpreted unless you click it. Other conditions and
samples may be entered manually.

Use the adjacent **Differential-abundance contrasts** table to define
contrast ID, condition A and condition B. The convenience action adds
glucose_vs_acetate = glucose vs acetate. Each contrast requires at least
two biological replicates in both conditions. A one-file simulator test can
produce a valid draft design without any contrasts.

This editable configuration is persisted in the workspace file
flashida-ms1-simulation/simulation-plan.json. It is also the input to the
simulator worker: you cannot accidentally run a different file list than the
one used to generate the statistical design.

## Exported design and contrasts

Click **Save experimental design**. FLASHApp uses the existing thesis package
tdp_experiment_design to build and validate:

  <workspace>/flashida-ms1-simulation/experimental_design/design.tsv
  <workspace>/flashida-ms1-simulation/experimental_design/run_metadata.tsv
  <workspace>/flashida-ms1-simulation/experimental_design/contrast.tsv
  <workspace>/flashida-ms1-simulation/experimental_design/simulation_plan.json

The OpenMS design contains only the selected MS1 sources, with each
acquisition's Sample, MSstats_Condition and MSstats_BioReplicate. The separate
run metadata holds the original source file and FAIMS CV.

Important: RAW-only acquisitions have expected mzML paths marked pending.
These must be replaced by real converted mzML paths before the normal
FLASHDeconv → FLASHQuant alignment → differential-abundance workflow.
The saved contrast.tsv has the same required columns as that workflow,
and is header-only for a draft without contrasts.

## Running the replay

Select the standalone simulator executable, baseline method JSON, thesis
project location and WSL conda environment in Simulator configuration.
Click **Run MS1 simulation**. This validates and saves the design again,
then replays every selected source sequentially, recording its condition,
sample and replicate in the corresponding simulation.json.

The run folder includes commands.tsv (MS2 requests), replay_log.tsv,
consumer_status.json, producer.log, consumer.log and method.json.
The output root also contains a batch manifest mapping source files and
experimental-design rows to the individual result folders.

These MS2 requests remain unfulfilled: the original files have MS1 spectra
only, and the simulator does not create fragments or identifications.
Evaluation against the subsequent inclusion list is manual at this stage.

## Reuse our already installed WSL FLASHApp

In the existing WSL terminal:

    conda activate flashapp-ms1
    cd ~/openms-development/FLASHApp
    git fetch origin
    git switch feature/flashida-ms1-simulation
    git pull --ff-only
    python -m streamlit run app.py local

Open http://localhost:8501 and unlock developer mode.

Known local paths:

- MS1 folder: /mnt/c/Users/langm/PycharmProjects/TDP_master_thesis/data
- Windows simulator: /mnt/c/dev/FlashIDA/src/Flash.Simulator/bin/x64/Debug/Flash.Simulator.exe
- Baseline method: /mnt/c/flashida_sim/method_cv_m40.json
- Thesis project: /mnt/c/Users/langm/PycharmProjects/TDP_master_thesis
- WSL conda environment: openms-experiments
- FAIMS CV for AA test: -40
- Scan limit for first test: 100
- Replay output: /mnt/c/flashida_sim/flashapp_runs

The Windows .NET simulator must retain the compatible OpenMS.dll and its
native dependencies. Results must be stored on a Windows-mounted drive under
/mnt/c or equivalent, so WSL and Windows share the same directory.

## Current scope and limitations

- The real-instrument mode is unchanged and isolated from simulation.
- This milestone supports baseline FLASHIda targeting only, not inclusion-list
  guided replay or artificial MS2 fragmentation.
- A batch currently shares one method JSON, while FAIMS CV is specified per
  file. Ensure the method's CV matches each source.
- Windows/WSL integration tests and protocol tests run in CI; an actual
  end-to-end click-through still requires a test in the installed FLASHApp.
- No automated evaluation or statistical analysis is performed by the
  acquisition page. The generated design and contrasts feed those next stages.
