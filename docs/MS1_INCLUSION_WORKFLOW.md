# MS1 inclusion-list planning workflow

This feature branch adds a **validation-only** workflow to FLASHApp using the
existing OpenMS Streamlit WorkflowManager infrastructure. It intentionally
does **not** execute FLASHDeconv, FLASHQuant, differential abundance, or
inclusion-list generation yet. A successful run means the configuration was
validated, *not* that any proteoforms or MS2 acquisition targets were found.

## Pages

The **MS1 inclusion planning** navigation group has four pages:

1. **Input**: upload or browse .mzML, Thermo .raw or Bruker .d MS1 acquisitions.
2. **Configure**: select files; assign conditions, biological replicates and
   fractions with the existing experimental-design editor; configure FAIMS
   handling, filtering, alignment, statistical comparison and target priorities.
3. **Run**: validate the selected files, sample assignments, contrast and
   numeric settings using the standard workflow run manager.
4. **Results**: inspect the saved configuration, download individual artifacts,
   and manage prior runs/ZIP archives.

The workflow's internal name is `MS1 inclusion planning`; it has its own
workspace directory, parameters and saved runs. The original Top-down
analysis and FLASHIda workflows are unchanged.

## Output of a successful validation-only run

A named run contains:

- `experimental_design.tsv`: OpenMS-compatible two-table experimental design.
- `contrasts.tsv`: the two named conditions used for the proposed comparison.
- `validation_report.json`: selected acquisition paths, design, all effective
  settings, planned stages, and an explicit `scientific_processing_executed: false`
  marker.
- Existing FLASHApp run metadata, parameter snapshot and logs.

All scientific outputs, including `inclusion_list.tsv`, remain absent until
the processing adapter is implemented.

## Default configuration

- FAIMS handling: `auto`; optional RT filtering off.
- Minimum precursor mass / charge: `0` (no added minimum).
- RT alignment: `pose`; consensus coverage: `partial`.
- Statistical q-value cutoff: `0.05`; absolute log2 fold change: `0.58`.
- Inclusion-list median Qscore2D cutoff: `0.80`.
- Quality top-intensity fraction: `0.50`; RT margin: `30` seconds per side.
- All three selection-reason priorities: `2`. Overlapping reasons will use
  the maximum numeric priority when the scientific adapter is connected.

The optional preset **E. coli FAIMS MS1** sets FAIMS handling to `require`
and gives suggested contrast labels `acetate` and `glucose`; edit them if
your experimental design uses different names.

## Running locally

From the FLASHApp checkout on `feature/ms1-inclusion-workflow`, use the app's
documented environment setup and start:

```sh
streamlit run app.py local
```

Then open the MS1 inclusion planning pages. When using local input paths,
ensure files can be accessed from the Python process running FLASHApp.

Run the focused tests and schema checker with the app's test dependencies:

```sh
python -m pytest -q tests/test_ms1_inclusion_workflow.py
python -m utils.app_spec
```

## Next integration milestone

Connect the existing `tdp-master-thesis` Python CLI and processing functions
via an execution adapter, in this order:

1. CV-aware preprocessing and FLASHDeconv (must use the correct custom
   executable and preserve required centroiding behavior).
2. CV-wise RT alignment and partial-consensus FLASHQuant.
3. Differential-abundance contrasts based on the validated design.
4. Inclusion-list generation with configurable per-reason priorities.
5. Reports and plots, with CLI-vs-web output equivalence tests.

The thesis project is a **separate private repository**. Do not hardcode a
local WSL checkout path in this public FLASHApp fork or copy private samples
into source control. The scientific modules need an explicit installation
and packaging strategy before public deployment.
