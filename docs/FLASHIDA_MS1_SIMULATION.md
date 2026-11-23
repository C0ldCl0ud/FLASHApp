# Offline FLASHIda MS1 simulation (developer-only desktop feature)

The standalone Windows FLASHIda simulator can replay a *previously acquired
MS1-only* Thermo RAW or mzML file through the native FLASHIda acquisition
engine. Its MS2 requests are logged but **not fulfilled** by fragment spectra.
This is **not** an instrument-control interface and never launches Flash.exe.

This page is available in **local FLASHApp with developer mode unlocked**:
**FLASHIda → MS1 simulation**. Hosted FLASHApp/Linux cannot use this Windows
build. The existing real-instrument Acquisition page is unchanged.

## Prerequisites

- Windows desktop FLASHApp with working WSL2.
- WSL thesis repository, e.g.
  /mnt/c/Users/langm/PycharmProjects/TDP_master_thesis, checked out with
  tdp_instrument_simulator installed or importable from that directory.
- WSL conda environment openms-experiments with FileConverter, PeakPickerHiRes
  and the existing centroiding cache. The runner uses
  $HOME/miniconda3/bin/conda by default; override via CONDA_EXE in the WSL
  environment if conda is elsewhere.
- Standalone C# executable Flash.Simulator.exe built from
  Flash.Simulator.sln, with the **compatible** OpenMS.dll and dependent native
  DLLs beside it. The proprietary Thermo instrument DLLs are not required.
- FLASHIda method JSON for the same FAIMS CV as the selected source; set
  precursor_selection.targeting to none. The first milestone deliberately
  excludes guided selection.

## Replay an MS1-only file

1. In FLASHApp developer settings unlock developer mode and select
   **FLASHIda → MS1 simulation**.
2. Enter the local Windows path to the precollected AA or GA RAW/mzML file.
   Do not upload/copy an enormous RAW into Streamlit: the runner accesses the
   source directly through the Windows/WSL shared drive.
3. Enter the paths to Flash.Simulator.exe and method_cv_m40.json, plus the
   WSL thesis project directory and conda environment.
4. Choose FAIMS CV -40 and optionally **100** as the scan limit; a blank
   limit processes the whole run. Click **Run simulation**.
5. The app starts the **WSL producer first** and waits for its "Prepared"
   message (so cold-cache centroiding cannot make the Windows consumer time
   out). It then launches the Windows native consumer in the **same
   per-run directory**.
6. View the status and download commands.tsv, replay_log.tsv,
   consumer_status.json, producer.log, consumer.log, simulation.json and the
   copied method.json.

Replays are preserved in:
  <workspace>/flashida-ms1-simulation/runs/<UTC timestamp>_<random ID>/

The workflow status is "complete" only if the Windows consumer reaches
state=complete and acknowledges every scan. Requests in commands.tsv with
disposition=unfulfilled are prospective MS2 targets only. No identification
or statistical comparison is calculated by this page.

Stopping a running replay attempts to terminate the recorded Windows and
WSL child process trees. Run directories are never reused, and interrupted
runs remain available for diagnosis.

The initial working configuration used:
- WSL input: data/20260315_Ecoli_SPE_FAIMS1CV_0p5ul_30min_MS1_AA.raw
- Windows simulator:
  C:\dev\FlashIDA\src\Flash.Simulator\bin\x64\Debug\Flash.Simulator.exe
- Windows method: C:\flashida_sim\method_cv_m40.json
- WSL environment: openms-experiments
- FAIMS CV: -40 V

## Experimental design and contrasts are separate from simulation

The bottom of the page includes **Create E. coli design and contrast**.
Point it to the folder with AA–AD (acetate) and GA–GD (glucose) MS1
acquisitions and to a new output folder. The action invokes the existing
tdp_experiment_design init --preset ecoli-aa-gd implementation from the
thesis repository. It saves:

- design.tsv: OpenMS MS1 one-table design with condition and BioReplicate.
- run_metadata.tsv: separate source/acquisition metadata.
- contrast.tsv: header contrast_id, condition_a, condition_b and row
  glucose_vs_acetate, glucose, acetate **if both conditions occur**.

The tool refuses to overwrite existing design/contrast files. The E. coli
preset is explicit: do not use it for unrelated filenames or experiments.
Review sample assignments before analysis.

**Crucial**: a RAW-only input produces pending *expected* mzML paths in
design.tsv. These are not yet the actual cached centroided/converted mzML
paths. Once the real conversion is available, update the design and metadata
paths and validate:

    python -m tdp_experiment_design validate --input-dir experiments/ecoli_full --mode ready

Only then run the normal MS1 pipeline (FLASHDeconv → FLASHQuant alignment →
differential_abundance with --contrasts contrast.tsv → inclusion-list
generation). The simulation and MS1 statistics remain independent in this
milestone; comparison of the resulting MS2 requests with the inclusion list is
manual.

## Compatibility and limitations

- The WSL conversion assumes local Windows drive letters mounted at /mnt/c,
  /mnt/d etc. UNC/network drives and custom WSL mount roots are not supported.
- Native MS2 fragmentation, AGC scan response and identification are outside
  this MS1-only simulation. No generated requests are claimed to be acquired.
- Real-time scheduling in the native engine has wall-clock components; this
  replay does not prove instrument-equivalent timing.
- The Windows native binaries are not packaged with FLASHApp. Configure an
  already built compatible simulator, and keep matching OpenMS.dll in its
  executable directory.
- The implementation is covered by platform-independent orchestration tests;
  an actual Windows/WSL end-to-end desktop run still needs validation.
