"""Execution adapter: validated FLASHApp experiment -> thesis pipeline CLIs.

The scientific algorithms are *not* implemented in FLASHApp. Each step runs the
installed tdp-master-thesis package, preserving its FAIMS stratification,
OpenMS/FLASHQuant consensus, Welch/BH tests and target selection behavior.
No fallback algorithm is used if a native executable or CLI is unavailable.
"""
from __future__ import annotations

import csv
import importlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from src.ms1_inclusion_validation import effective_settings, validate_setup

EXECUTABLE_SETTINGS = {
    "fileconverter": ("fileconverter-executable", "FileConverter"),
    "filefilter": ("filefilter-executable", "FileFilter"),
    "ionmobilitybinning": ("ionmobilitybinning-executable", "IonMobilityBinning"),
    "peakpicker": ("peakpicker-executable", "PeakPickerHiRes"),
    "flashdeconv": ("flashdeconv-executable", "FLASHDeconv"),
    "consensus_detector": ("consensus-executable", "ConsensusFeatureGroupDetector"),
}

# CLI module import paths; these are entry points from tdp-master-thesis.
CLI_MODULES = {
    "deconv": "tdp_flashdeconv.cli",
    "alignment": "tdp_feature_alignment.cli",
    "differential_abundance": "tdp_differential_abundance.cli",
    "inclusion_list": "tdp_inclusion_list.cli",
}


def command_for_deconv(source: Path, deconv_root: Path, cache: Path, params: dict,
                       executables: dict[str, str], python: str = sys.executable) -> list[str]:
    """Build a native CLI call; never combine samples or FAIMS CVs here."""
    s = effective_settings(params)
    args = [
        python, "-m", CLI_MODULES["deconv"],
        "--input", str(source), "--out", str(deconv_root),
        "--cache-dir", str(cache), "--faims-mode", s["faims-mode"],
        "--fileconverter", executables["fileconverter"],
        "--filefilter", executables["filefilter"],
        "--ionmobilitybinning", executables["ionmobilitybinning"],
        "--peakpicker", executables["peakpicker"],
        "--flashdeconv", executables["flashdeconv"],
    ]
    if s["rt-filter-enabled"]:
        args += ["--rt-min", str(s["rt-min-minutes"]),
                 "--rt-max", str(s["rt-max-minutes"])]
    # Zero preserves the FLASHDeconv native defaults. Positive values act
    # during spectral deconvolution (SD:min_mass / SD:min_charge), not as
    # postprocessing filters on the exported feature tables.
    if s["min-mass-da"] > 0:
        args += ["--min-mass", str(s["min-mass-da"])]
    if s["min-charge"] > 0:
        args += ["--min-charge", str(s["min-charge"])]
    return args


def command_for_alignment(experiment: Path, deconv_root: Path, alignment: Path,
                          params: dict, executables: dict[str, str],
                          python: str = sys.executable) -> list[str]:
    s = effective_settings(params)
    return [
        python, "-m", CLI_MODULES["alignment"],
        "--experiment", str(experiment), "--results-root", str(deconv_root),
        "--out", str(alignment), "--rt-alignment", s["rt-alignment"],
        "--coverage-mode", "partial",
        # Do NOT apply the inclusion Qscore2D cutoff during alignment:
        # missingness, abundance estimation, and target filtering are different stages.
        "--min-qscore", "0",
        "--consensus-detector", executables["consensus_detector"],
    ]


def command_for_abundance(experiment: Path, alignment: Path, contrasts: Path,
                          abundance: Path, params: dict,
                          python: str = sys.executable) -> list[str]:
    s = effective_settings(params)
    return [
        python, "-m", CLI_MODULES["differential_abundance"],
        "--experiment", str(experiment), "--alignment", str(alignment),
        "--contrasts", str(contrasts), "--out", str(abundance),
        "--q-threshold", str(s["q-threshold"]),
        "--abs-log2fc-threshold", str(s["abs-log2fc-threshold"]),
    ]


def command_for_inclusion(experiment: Path, alignment: Path, abundance: Path,
                          inclusion: Path, params: dict,
                          python: str = sys.executable) -> list[str]:
    s = effective_settings(params)
    cmd = [
        python, "-m", CLI_MODULES["inclusion_list"],
        "--experiment", str(experiment), "--alignment", str(alignment),
        "--differential-abundance", str(abundance), "--out", str(inclusion),
        "--min-qscore", str(s["min-qscore"]),
        "--top-fraction", str(s["top-fraction"]),
        "--rt-margin-seconds", str(s["rt-margin-seconds"]),
    ]
    for reason, key in (
        ("quality", "priority-quality"),
        ("diff_abundance", "priority-diff-abundance"),
        ("conditional", "priority-conditional"),
    ):
        cmd.extend(["--reason-priority", f"{reason}={s[key]}"])
    return cmd


def resolved_executables(params: dict, sources: list[str],
                         *, which=shutil.which) -> dict[str, str]:
    """Check installed CLI and native toolchain *before* costly RAW processing."""
    s = effective_settings(params)
    # Do not confuse an acquisition dimension with a missing biological signal.
    if s["coverage-mode"] != "partial":
        raise RuntimeError("Partial FLASHQuant consensus is required for the inclusion-list workflow.")

    for module in (*CLI_MODULES.values(), "tdp_experiment_design"):
        try:
            importlib.import_module(module)
        except ImportError as exc:
            raise RuntimeError(
                f"Missing thesis Python module {module!r}. Install the thesis "
                "repository in FLASHApp's Python environment (pip install --no-deps -e <thesis-repo>)."
            ) from exc

    # Explicit preflight: don't run expensive RAW conversion using an older
    # installed thesis package that cannot honor requested native thresholds.
    if s["min-mass-da"] or s["min-charge"]:
        from tdp_flashdeconv.workflow import deconvolve
        supported = inspect.signature(deconvolve).parameters
        missing = {"min_mass", "min_charge"} - set(supported)
        if missing:
            raise RuntimeError(
                "Installed tdp_flashdeconv does not support native minimum "
                "mass/charge yet. Update the editable thesis checkout to main "
                "(PR #8) or merge main into the inclusion-priorities branch "
                "and reinstall it in the FLASHApp environment."
            )

    from tdp_inclusion_list.selection import generate
    if "reason_priorities" not in inspect.signature(generate).parameters:
        raise RuntimeError(
            "The installed inclusion-list CLI is outdated: it lacks reason priorities. "
            "Install the thesis feature/inclusion_reason_priorities branch."
        )

    names = {part: str(params.get(key, default)).strip()
             for part, (key, default) in EXECUTABLE_SETTINGS.items()}
    if any(not name for name in names.values()):
        raise RuntimeError("An executable path/name is empty.")
    resolved = {}
    for part, name in names.items():
        # Not every dataset needs these, but fail early so subsequent stages
        # can execute reproducibly regardless of the observed CV count.
        found = which(name)
        if not found:
            raise FileNotFoundError(
                f"Required native tool {name!r} ({part}) was not found. "
                "Configure its executable path or add it to the FLASHApp process PATH."
            )
        resolved[part] = str(Path(found).resolve())

    # Do not waste hours on deconvolution if the installed detector cannot
    # produce the missing-value groups used for condition-specific targets.
    probe = subprocess.run(
        [resolved["consensus_detector"], "--helphelp"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    description = (probe.stdout or "") + "\n" + (probe.stderr or "")
    if not re.search(r"(?m)^\s+-min_required_replicates\b", description):
        raise RuntimeError(
            "ConsensusFeatureGroupDetector does not expose "
            "'-min_required_replicates'. Install the modified FLASHQuant "
            "detector used by the thesis before running partial-consensus analysis."
        )
    return resolved


def _relative(source: Path, root: Path) -> str:
    return Path(os.path.relpath(source.resolve(), root.resolve())).as_posix()


def prepare_thesis_experiment(files: list[str], design, sources: list[str],
                              experiment_dir: Path):
    """Adapt the FLASHApp design to the thesis's strict one-table design.

    OpenMS's FLASHApp editor permits replicate '1' in two different conditions.
    The thesis design validator uses globally unique BioReplicate labels.
    Assign a unique numeric label *per acquired file* while retaining the
    original within-condition replicate in metadata for auditability.

    The 'deconv' CLI emits results/<source.stem>/cv_groups.tsv, so run IDs
    are intentionally identical to the processing-source basename.
    """
    from tdp_experiment_design import (
        DESIGN_COLUMNS, RUN_COLUMNS, Experiment, write_experiment, validate_experiment,
    )

    experiment_dir = Path(experiment_dir).resolve()
    experiment_dir.mkdir(parents=True, exist_ok=True)
    if len(files) != len(sources):
        raise ValueError("Source conversion changed the number of selected MS1 acquisitions.")
    rows = design.to_dict("records") if hasattr(design, "to_dict") else list(design)
    if len(rows) != len(files):
        raise ValueError("Experiment design and acquisition counts differ.")

    design_rows = []
    run_rows = []
    seen_ids = set()
    for index, (original, source, row) in enumerate(zip(files, sources, rows), 1):
        input_path = Path(original).resolve()
        processing_path = Path(source).resolve()
        run_id = processing_path.stem
        if run_id.casefold() in seen_ids:
            raise ValueError(f"Non-unique processing run name: {run_id}")
        seen_ids.add(run_id.casefold())
        if processing_path.suffix.lower() not in {".raw", ".mzml"}:
            raise ValueError(f"Unsupported deconvolution source: {processing_path}")
        if not processing_path.is_file():
            raise FileNotFoundError(f"Missing deconvolution source: {processing_path}")

        if processing_path.suffix.lower() == ".mzml":
            mzml_path = _relative(processing_path, experiment_dir)
            mzml_status = "present"
        else:
            # RAW -> profile mzML is performed by the thesis 'deconv' CLI in
            # its provenance-aware shared cache. There is not yet a physical
            # mzML to reference at metadata-generation time. Only the path
            # equality is needed by the later alignment reader. Mark pending
            # rather than inventing a successfully converted file.
            mzml_path = f"pending-mzml/{run_id}.mzML"
            mzml_status = "pending"
        sample = f"sample_{index:04d}"
        condition = str(row["condition"]).strip()
        unique_replicate = str(index)

        design_rows.append(dict(zip(DESIGN_COLUMNS, (
            str(index), "1", mzml_path, "1", sample, condition, unique_replicate,
        ))))
        metadata = dict.fromkeys(RUN_COLUMNS, "")
        metadata.update(
            run_id=run_id,
            sample_id=sample,
            acquisition_type="MS1_ONLY",
            raw_path=_relative(processing_path, experiment_dir)
            if processing_path.suffix.lower() == ".raw" else "",
            mzml_path=mzml_path,
            mzml_status=mzml_status,
            # Empty results_dir: align_features resolves from --results-root/run_id.
            results_dir="",
        )
        metadata["webapp_source_path"] = _relative(input_path, experiment_dir)
        metadata["webapp_replicate_in_condition"] = str(row["replicate"])
        run_rows.append(metadata)

    experiment = Experiment(design_rows, run_rows)
    report = validate_experiment(experiment, experiment_dir, mode="draft")
    if not report.valid:
        raise ValueError(
            "Thesis experimental-design validation failed: "
            + "; ".join(x.message for x in report.issues if x.severity == "error")
        )
    write_experiment(experiment, experiment_dir)
    return report.as_dict()


def _ensure_output(path: Path, name: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"{name} did not produce a nonempty output file: {path}")


def execute_pipeline(files: list[str], design, params: dict, results_dir: Path,
                     cache_dir: Path, logger, run_command, *,
                     convert_bruker=None, python: str = sys.executable,
                     tool_resolver=resolved_executables) -> dict:
    """Run four thesis CLIs using FLASHApp's tracked command executor.

    run_command(command) should return bool and must track subprocesses for
    stop/cancel support (CommandExecutor.run_command does this).
    """
    errors = validate_setup(files, design, params)
    if errors:
        raise ValueError("; ".join(errors))

    results_dir = Path(results_dir).resolve()
    cache_dir = Path(cache_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    executables = tool_resolver(params, files)
    sources = list(files)
    if any(Path(path).suffix.lower() == ".d" for path in files):
        if convert_bruker is None:
            raise RuntimeError("Bruker .d conversion adapter is unavailable.")
        for index, original in enumerate(files):
            if Path(original).suffix.lower() == ".d":
                converted = convert_bruker(original)
                if not converted or Path(converted).suffix.lower() != ".mzml":
                    raise RuntimeError(f"Failed to convert Bruker acquisition: {original}")
                sources[index] = str(converted)

    # The thesis processing stage writes into 'deconv/<source.stem>/...'.
    experiment_dir = results_dir / "experiment"
    metadata_validation = prepare_thesis_experiment(
        files, design, sources, experiment_dir
    )
    deconv = results_dir / "deconv"
    alignment = results_dir / "alignment"
    abundance = results_dir / "differential_abundance"
    inclusion = results_dir / "inclusion_list"
    contrasts = results_dir / "contrasts.tsv"
    _ensure_output(contrasts, "Configuration validation")

    stages = []
    def run_stage(name: str, command: list[str], expected: Path) -> None:
        logger.log(f"MS1 stage started: {name}")
        if not run_command(command):
            raise RuntimeError(f"MS1 stage failed: {name}; inspect FLASHApp workflow logs.")
        _ensure_output(expected, name)
        stages.append({"stage": name, "command": command, "output": str(expected)})
        logger.log(f"MS1 stage completed: {name}")

    for original, source in zip(files, sources):
        run_stage(
            f"FAIMS deconvolution: {Path(original).name}",
            command_for_deconv(
                Path(source), deconv, cache_dir, params, executables, python,
            ),
            deconv / Path(source).stem / "cv_groups.tsv",
        )
    run_stage(
        "CV-wise RT alignment and partial FLASHQuant consensus",
        command_for_alignment(
            experiment_dir, deconv, alignment, params, executables, python,
        ),
        alignment / "summary.json",
    )
    run_stage(
        "Welch differential abundance and BH correction",
        command_for_abundance(
            experiment_dir, alignment, contrasts, abundance, params, python,
        ),
        abundance / "summary.json",
    )
    run_stage(
        "FAIMS inclusion-list target selection",
        command_for_inclusion(
            experiment_dir, alignment, abundance, inclusion, params, python,
        ),
        inclusion / "selection_summary.json",
    )
    _ensure_output(inclusion / "inclusion_list.tsv", "Inclusion-list selection")

    result = {
        "scientific_processing_executed": True,
        "pipeline_completed": True,
        "input_count": len(files),
        "stages": stages,
        "experimental_design_validation": metadata_validation,
        "selected_sources": sources,
        "executables": executables,
        "inclusion_summary": json.loads(
            (inclusion / "selection_summary.json").read_text(encoding="utf-8")
        ),
    }
    (results_dir / "pipeline_report.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result
