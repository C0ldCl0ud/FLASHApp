"""Validation-only contract for the MS1 inclusion-list planning workflow.

This module has no Streamlit dependency. The scaffold validates experimental
metadata and saves configuration artifacts; it does not run the scientific
deconvolution, alignment, differential-abundance, or inclusion-list stages.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from src.workflow.ExperimentalDesign import to_openms_design

DEFAULT_SETTINGS = {
    "execution-mode": "validation",
    "faims-mode": "auto",
    "rt-filter-enabled": False,
    "rt-min-minutes": 0.0,
    "rt-max-minutes": 30.0,
    "min-mass-da": 0.0,
    "min-charge": 0,
    "rt-alignment": "pose",
    "coverage-mode": "partial",
    "contrast-a": "",
    "contrast-b": "",
    "q-threshold": 0.05,
    "abs-log2fc-threshold": 0.58,
    "min-qscore": 0.80,
    "top-fraction": 0.50,
    "rt-margin-seconds": 30.0,
    "priority-quality": 2,
    "priority-diff-abundance": 2,
    "priority-conditional": 2,
}


def effective_settings(params: dict) -> dict:
    """Apply the same defaults that the Streamlit parameter widgets display."""
    return {key: params.get(key, default) for key, default in DEFAULT_SETTINGS.items()}


def _finite(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(float(value)))


def _rows(design) -> list[dict]:
    """Accept pandas DataFrames and plain row dictionaries for validation."""
    return design.to_dict("records") if hasattr(design, "to_dict") else list(design)


def validate_setup(files: list[str], design, params: dict) -> list[str]:
    """Return actionable errors for the selected MS1 files and experimental design.

    The existing design editor emits notices for some missing assignments.
    Inclusion planning needs two defined conditions and unique biological
    replicates, so these are blocking errors here.
    """
    errors = []
    paths = [Path(file) for file in files]
    if len(paths) < 2:
        errors.append("Select at least two MS1 files for a comparison.")
    names = [path.name.casefold() for path in paths]
    if len(set(names)) != len(names):
        errors.append("Selected file names must be unique, even across folders.")
    for path in paths:
        suffix = path.suffix.casefold()
        if suffix not in {".mzml", ".raw", ".d"}:
            errors.append(f"Unsupported MS1 input type: {path.name}.")
        elif not (path.is_dir() if suffix == ".d" else path.is_file()):
            errors.append(f"Selected MS1 input is missing: {path}.")

    rows = _rows(design)
    if len(rows) != len(paths) or [str(row.get("file", "")).casefold()
                                   for row in rows] != names:
        errors.append("Experimental design must have one row for each selected file, in order.")

    conditions = set()
    seen_replicates = set()
    for row in rows:
        condition = str(row.get("condition") or "").strip()
        if not condition:
            errors.append(f"Set a condition for {row.get('file', 'an MS1 file')}.")
            continue
        conditions.add(condition)
        replicate = row.get("replicate")
        try:
            rep = int(replicate)
            if rep < 1 or float(replicate) != rep:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            errors.append(f"Replicate for {row.get('file', condition)} must be a positive integer.")
            continue
        pair = condition, rep
        if pair in seen_replicates:
            errors.append(
                f"Biological replicate {rep} of condition {condition!r} appears "
                "multiple times; this workflow requires one MS1 file per biological replicate."
            )
        seen_replicates.add(pair)

    settings = effective_settings(params)
    first = str(settings["contrast-a"] or "").strip()
    second = str(settings["contrast-b"] or "").strip()
    if not first or not second or first == second:
        errors.append("Specify two distinct, non-empty contrast conditions.")
    else:
        for condition in (first, second):
            if condition not in conditions:
                errors.append(f"Contrast condition {condition!r} is absent from the design.")

    if settings["execution-mode"] not in ("validation", "full"):
        errors.append("Execution mode must be validation or full.")
    if settings["faims-mode"] not in ("auto", "require"):
        errors.append("FAIMS mode must be auto or require.")
    if settings["rt-alignment"] not in ("pose", "none"):
        errors.append("RT alignment must be pose or none.")
    if settings["coverage-mode"] != "partial":
        errors.append("Inclusion-list planning requires partial-consensus coverage.")
    if type(settings["rt-filter-enabled"]) is not bool:
        errors.append("RT filtering must be enabled or disabled.")

    numeric_names = (
        "rt-min-minutes", "rt-max-minutes", "min-mass-da", "q-threshold",
        "abs-log2fc-threshold", "min-qscore", "top-fraction", "rt-margin-seconds",
    )
    numeric = {}
    for name in numeric_names:
        if not _finite(settings[name]):
            errors.append(f"{name} must be a finite number.")
        else:
            numeric[name] = float(settings[name])
    if numeric.get("rt-min-minutes", 0) < 0:
        errors.append("Minimum RT cannot be negative.")
    if (settings["rt-filter-enabled"]
            and {"rt-min-minutes", "rt-max-minutes"} <= numeric.keys()
            and numeric["rt-max-minutes"] <= numeric["rt-min-minutes"]):
        errors.append("Maximum RT must exceed minimum RT.")
    if numeric.get("min-mass-da", 0) < 0:
        errors.append("Minimum mass cannot be negative.")
    if "q-threshold" in numeric and not 0 < numeric["q-threshold"] <= 1:
        errors.append("Q-value threshold must be in (0, 1].")
    if numeric.get("abs-log2fc-threshold", 0) < 0:
        errors.append("Absolute log2 fold-change threshold cannot be negative.")
    if "min-qscore" in numeric and not 0 <= numeric["min-qscore"] <= 1:
        errors.append("Minimum Qscore2D must be in [0, 1].")
    if "top-fraction" in numeric and not 0 < numeric["top-fraction"] <= 1:
        errors.append("Top-intensity fraction must be in (0, 1].")
    if numeric.get("rt-margin-seconds", 0) < 0:
        errors.append("RT scheduling margin cannot be negative.")

    for name in (
        "min-charge", "priority-quality", "priority-diff-abundance",
        "priority-conditional",
    ):
        value = settings[name]
        if type(value) is not int or value < 0:
            errors.append(f"{name} must be a non-negative integer.")
    return errors


def write_validation_artifacts(output_dir: Path, files: list[str], design, params: dict) -> dict:
    """Save reproducible configuration artifacts, not scientific results."""
    errors = validate_setup(files, design, params)
    if errors:
        raise ValueError("; ".join(errors))

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = effective_settings(params)
    contrast = {
        "contrast_id": "contrast_1",
        "condition_a": settings["contrast-a"].strip(),
        "condition_b": settings["contrast-b"].strip(),
    }
    (output_dir / "experimental_design.tsv").write_text(
        to_openms_design(design, files), encoding="utf-8"
    )
    with (output_dir / "contrasts.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, delimiter="\t",
            fieldnames=("contrast_id", "condition_a", "condition_b"),
        )
        writer.writeheader()
        writer.writerow(contrast)

    report = {
        "status": "configuration_validated",
        "validation_only": True,
        "scientific_processing_executed": False,
        "selected_input_files": [str(path) for path in files],
        "experimental_design": _rows(design),
        "contrast": contrast,
        "settings": settings,
        "planned_stages": [
            "FAIMS-stratified deconvolution",
            "CV-specific RT alignment and partial consensus",
            "differential abundance",
            "inclusion-list target selection",
        ],
    }
    (output_dir / "validation_report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return report
