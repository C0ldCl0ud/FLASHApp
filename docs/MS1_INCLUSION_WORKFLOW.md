# FLASHApp: FAIMS-aware MS1 discovery and inclusion-list planning

This experimental FLASHApp workflow is on branch
\`feature/ms1-inclusion-workflow\`. It connects the Streamlit experimental-design
editor to the *installed* thesis Python package. Scientific processing is
delegated to the thesis CLIs and native OpenMS tools; FLASHApp does **not**
reimplement deconvolution, RT alignment, FLASHQuant clustering, differential
statistics, or the inclusion-list selection algorithm.

The original Top-down analysis and FLASHIda acquisition workflows are unchanged.

## Execution modes

Use **MS1 inclusion planning → Configure → Processing mode**:

- **validation** (default): checks selected files, design, contrast and
  numerical settings; saves \`experimental_design.tsv\`, \`contrasts.tsv\` and
  \`validation_report.json\`. It never launches scientific tools.
- **full**: performs the same validation, then calls all four stages sequentially.
  The workflow is successful only when each CLI exits successfully and its
  required output files are present. A failed run remains failed in run history.

The **Input**, **Configure**, **Run**, **Results** pages share FLASHApp's
workspace, parameter persistence, run isolation, logging and downloads.

## Required local installation

This workflow needs an existing checkout of the **private**
\`C0ldCl0ud/TDP_master_thesis\` project installed into the *same Python
environment* running FLASHApp. Do not publish the private repository or
hardcode its location into FLASHApp's public fork.

Example, adapting the thesis path if yours differs:

\`\`\`bash
conda activate flashapp-ms1
cd /path/to/TDP_master_thesis

git fetch origin
# The inclusion-priorities changes are not yet on thesis main:
git switch feature/inclusion_reason_priorities
git pull --ff-only

python -m pip install --no-deps -e .
python -m tdp_inclusion_list.cli --help
python -m tdp_feature_alignment.cli --help
python -m tdp_differential_abundance.cli --help
python -m tdp_flashdeconv.cli --help
\`\`\`

The scientific processing requires **OpenMS native executables** accessible to
the FLASHApp process: \`FileConverter\`, \`FileFilter\`,
\`IonMobilityBinning\`, \`PeakPickerHiRes\`, and \`FLASHDeconv\`.

Critically, partial consensus requires the thesis's modified
\`ConsensusFeatureGroupDetector\` executable. At startup, the full processing
adapter checks its \`--helphelp\` output for \`-min_required_replicates\` and
refuses to run if this option is missing. This prevents silent substitution of
complete-case grouping, which would invalidate condition-specific targeting.

Check your environment:

\`\`\`bash
which FileConverter FileFilter IonMobilityBinning PeakPickerHiRes FLASHDeconv
which ConsensusFeatureGroupDetector
ConsensusFeatureGroupDetector --helphelp | grep min_required_replicates
\`\`\`

In **Configure → Deconvolution and alignment → Native executable paths**, each
tool can be set to a full path, so a locally built OpenMS executable can be
used instead of the first instance on \`PATH\`.

For a locally compiled FLASHDeconv, set a path such as
`/path/to/openms-build/bin/FLASHDeconv`, after verifying that the binary
is the intended build. The modified FLASHQuant detector's path must likewise
be checked locally.

\`tdp_flashdeconv\` performs RAW conversion in its *own* cached pipeline and
splits by FAIMS CV **before** centroiding. FLASHApp's generic RAW conversion is
therefore not used for Thermo RAW input. Bruker \`.d\` inputs are converted to
mzML by FLASHApp's existing Bruker conversion helper before passing to the
thesis pipeline; this path needs instrument-specific validation.

The UI forwards positive minimum mass (Da) and minimum MS1 charge settings
to the thesis `deconv --min-mass` / `--min-charge` CLI, which passes
`-SD:min_mass` and `-SD:min_charge` into native FLASHDeconv before
feature detection. Omitted or zero limits preserve the native defaults.
This requires the thesis `main` implementation from PR #8 (or a feature
branch containing that merge). A preflight checks that the installed thesis
Python package accepts the filters; its native code verifies that the selected
FLASHDeconv binary advertises the flags.

## Processing stages

All CLI stages are run as subprocesses of the FLASHApp worker using the active
Python interpreter (\`sys.executable -m ...\`). The existing FLASHApp command
executor tracks child processes and captures output.

| Order | Thesis CLI | Behavior |
| --- | --- | --- |
| 1 | \`tdp_flashdeconv.cli\` per MS1 file | RAW→profile mzML, FAIMS CV split, centroiding per CV, FLASHDeconv |
| 2 | \`tdp_feature_alignment.cli\` | OpenMS RT alignment and native modified FLASHQuant partial-consensus, separately per CV |
| 3 | \`tdp_differential_abundance.cli\` | Complete-case Welch/BH testing, normalization and per-CV contrast results |
| 4 | \`tdp_inclusion_list.cli\` | Quality, differential-abundance and condition-specific selection; per-reason priorities |

The alignment input Qscore2D cutoff remains **zero** (the inclusion-list
median quality cutoff belongs to the final selection stage). The adjusted
p-value and absolute log2 fold-change cutoffs are forwarded only to
differential abundance. The reason priorities are forwarded as repeatable
\`--reason-priority reason=integer\` arguments. When reasons overlap the thesis
selector takes the largest numeric priority. It uses original apex RTs for
acquisition windows and exports neutral masses with charge=-1 as currently
required by FLASHIda.

### Experimental-design adaptation

FLASHApp saves an OpenMS two-table \`experimental-design.tsv\` with sample
conditions, biological replicate numbers and fractions. The thesis processing
code uses a **different strict one-table design.tsv** plus
\`run_metadata.tsv\`. The adapter converts these to the thesis package's
schema (using \`tdp_experiment_design.write_experiment\` and its draft
validator), stored under a run's \`experiment/\` directory.

The thesis design validator requires globally unique BioReplicate labels.
FLASHApp's editor allows e.g. acetate replicate 1 and glucose replicate 1.
The adapter assigns distinct global numeric labels per file (1, 2, ...)
while retaining the user's original within-condition biological replicate
number in the additional \`webapp_replicate_in_condition\` column of
\`run_metadata.tsv\`. Every input acquisition is still an independent sample;
the condition assignments themselves remain unchanged.

For Thermo RAW acquisitions the thesis CLI performs conversion internally in
its provenance-aware cache. The \`mzml_path\` is therefore marked \`pending\`
with a placeholder path at design generation time, not reported as an
existing file. The downstream alignment uses that exact path to link
metadata to the sample and resolves the actual feature TSV using the
\`cv_groups.tsv\` manifests. For directly supplied mzML, the actual existing
path is recorded. Reconciliation with generated profile-mzML paths is a
future provenance enhancement.

This version requires **one unfractionated MS1 acquisition per biological
replicate**; it validates that constraint before running. No biological
condition is inferred from file names. Missing FAIMS CV data cannot be
treated as a biological absence. For inclusion-list generation, the
scientific package refuses unsafe condition-specific inference if
partial-consensus coverage is not evaluable for every relevant run.

## Full-run outputs

Each named run keeps the original validation files and these additional paths:

\`\`\`text
experiment/
  design.tsv
  run_metadata.tsv
deconv/
  <run_id>/
    cv_groups.tsv
    <cv>/features.tsv
alignment/
  summary.json
  <cv>/
    consensus.tsv
    abundance_matrix.tsv
    feature_membership.tsv
    detection_summary.json
differential_abundance/
  summary.json
  <contrast>/<cv>/
    significant_features.tsv
inclusion_list/
  inclusion_list.tsv
  inclusion_list_<cv>.tsv
  inclusion_list_details.tsv
  selection_summary.json
pipeline_report.json
\`\`\`

All processing outputs are isolated inside the run's results folder.
Conversion and centroiding reuse a workflow-specific cache. The Results
page displays the combined inclusion-list table, target counts per CV,
and downloadable configuration artifacts. FLASHApp's existing run ZIP export
includes the per-CV native FLASHIda lists, provenance files and workflow logs.

The \`pipeline_report.json\` is published only after all four stages succeed.
If a stage fails, check the run's history and logs; partial output folders may
remain and must not be mistaken for a completed scientific result.

## Verification

With the app environment active:

\`\`\`bash
python -m pytest -q tests/test_ms1_inclusion_workflow.py tests/test_ms1_inclusion_pipeline.py
python -m utils.app_spec
streamlit run app.py local
\`\`\`

Tests mock native processing and verify argument forwarding, metadata
adaptation, failure propagation, and result publication. They **do not**
constitute an end-to-end test on real spectra. Before scientific use, compare
CLI and FLASHApp outputs for the same AA/GA run pair and then for the full
eight-replicate E. coli experiment, per CV: feature counts, consensus IDs,
abundance matrices, p/q-values, target reasons and priorities.

The public GitHub Actions workflow runs the tests and schema checks without
accessing the private thesis repository or installing OpenMS tools.

## Instrument acceptance

Inclusion files are neutral-mass targets for the existing FLASHIda format.
Their validity for the actual acquisition software, FAIMS CV settings, cycle
time, isolation width and precursor mass range must be checked in the
instrument method editor before running targeted MS2.
