"""Explicit MS1 per-file acquisition design shared by FLASHIda replay and OpenMS.

No condition or replicate is inferred silently. The normal thesis
tdp_experiment_design API remains responsible for TSV writing and validation.
"""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import re
import sys

from src.flashida_simulation import write_json

PLAN_FILE = "simulation-plan.json"
SOURCE_COLUMNS = ("file_path", "sample_id", "condition",
                  "biological_replicate", "faims_cv")
CONTRAST_COLUMNS = ("contrast_id", "condition_a", "condition_b")
_SAFE_CONTRAST = re.compile(r"^[A-Za-z0-9_-]+$")
_ECOLI = re.compile(r"(?:^|[_-])([AG])([ABCD])$", re.IGNORECASE)


def load_plan(workflow_dir: Path) -> dict:
    try:
        value = json.loads((Path(workflow_dir) / PLAN_FILE).read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("runs"), list) and isinstance(value.get("contrasts"), list):
            return value
    except (OSError, ValueError):
        pass
    return {"runs": [], "contrasts": []}


def save_plan(workflow_dir: Path, plan: dict) -> None:
    root = Path(workflow_dir)
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / PLAN_FILE, {"runs": plan["runs"], "contrasts": plan["contrasts"]})


def new_source(path: str | Path) -> dict:
    return dict(file_path=str(Path(path).resolve()), sample_id="",
                condition="", biological_replicate="", faims_cv="")


def add_sources(rows: list[dict], paths: list[str]) -> list[dict]:
    result = [dict(r) for r in rows]
    existing = {str(r.get("file_path", "")).casefold() for r in result}
    for raw in paths:
        source = Path(raw).expanduser().resolve()
        if source.suffix.lower() not in (".raw", ".mzml") or not source.is_file():
            raise ValueError(f"Not an existing RAW or mzML file: {raw}")
        if str(source).casefold() in existing:
            continue
        result.append(new_source(source))
        existing.add(str(source).casefold())
    return result


def apply_ecoli_preset(rows: list[dict]) -> list[dict]:
    """Explicit button action; suffixes are never interpreted automatically."""
    result = []
    for row in rows:
        entry = dict(row)
        stem = Path(str(entry.get("file_path", ""))).stem
        match = _ECOLI.search(stem)
        if match is None:
            raise ValueError(f"No AA–AD/GA–GD suffix for {stem}; edit assignments manually.")
        group, rep = match.groups()
        group, rep = group.upper(), rep.upper()
        entry["sample_id"] = group + rep
        entry["condition"] = "acetate" if group == "A" else "glucose"
        entry["biological_replicate"] = str("ABCD".index(rep) + (1 if group == "A" else 5))
        result.append(entry)
    return result


def validated_plan(plan: dict, *, check_files: bool = True) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("Simulation plan must be an object.")
    original = plan.get("runs", [])
    if not original:
        raise ValueError("Add at least one MS1 RAW/mzML file.")
    runs = []
    seen_paths, seen_stems = set(), set()
    assignments, replicate_owner = {}, {}
    for i, row in enumerate(original, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Run {i}: invalid design row.")
        path = str(row.get("file_path") or "").strip()
        if not path:
            raise ValueError(f"Run {i}: choose a RAW/mzML file.")
        file = Path(path).expanduser()
        if not file.is_absolute() or file.suffix.lower() not in (".raw", ".mzml"):
            raise ValueError(f"Run {i}: file must be an absolute .raw/.mzML path.")
        if check_files and not file.is_file():
            raise ValueError(f"Run {i}: MS1 file does not exist: {path}")
        source = str(file.resolve()) if check_files else str(file)
        key = source.casefold()
        if key in seen_paths:
            raise ValueError(f"Run {i}: file selected twice.")
        seen_paths.add(key)
        stem = file.stem.casefold()
        if stem in seen_stems:
            raise ValueError(f"Run {i}: duplicate file stem {file.stem}; OpenMS needs unique acquisition IDs.")
        seen_stems.add(stem)
        sample = str(row.get("sample_id") or "").strip()
        condition = str(row.get("condition") or "").strip()
        replicate = str(row.get("biological_replicate") or "").strip()
        if not sample or not condition or not replicate:
            raise ValueError(f"Run {i}: enter sample ID, condition and biological replicate.")
        if not replicate.isdecimal() or int(replicate) <= 0:
            raise ValueError(f"Run {i}: biological replicate must be a positive integer.")
        previous = assignments.setdefault(sample, (condition, replicate))
        if previous != (condition, replicate):
            raise ValueError(f"Run {i}: sample {sample} has conflicting condition/replicate.")
        owner = replicate_owner.setdefault(replicate, sample)
        if owner != sample:
            raise ValueError(f"Run {i}: replicate {replicate} belongs to both {owner} and {sample}. "
                             "Use globally unique biological replicate numbers.")
        raw_cv = str(row.get("faims_cv") or "").strip()
        if raw_cv:
            try:
                cv = float(raw_cv)
            except ValueError as exc:
                raise ValueError(f"Run {i}: FAIMS CV must be a number.") from exc
            if not math.isfinite(cv) or not -300 <= cv <= 300:
                raise ValueError(f"Run {i}: FAIMS CV must be finite and in -300..300 V.")
        runs.append(dict(file_path=source, sample_id=sample, condition=condition,
                         biological_replicate=replicate, faims_cv=raw_cv))
    conditions = {r["condition"] for r in runs}
    replicate_counts = {condition: len({r["biological_replicate"] for r in runs
                                        if r["condition"] == condition})
                        for condition in sorted(conditions)}
    contrasts = []
    used_contrasts = set()
    for i, row in enumerate(plan.get("contrasts") or [], 1):
        if not isinstance(row, dict):
            raise ValueError(f"Contrast {i}: invalid row.")
        cid = str(row.get("contrast_id") or "").strip()
        a = str(row.get("condition_a") or "").strip()
        b = str(row.get("condition_b") or "").strip()
        if not cid or not _SAFE_CONTRAST.fullmatch(cid):
            raise ValueError(f"Contrast {i}: use a unique ID containing letters, digits, _ or -.")
        if cid in used_contrasts:
            raise ValueError(f"Contrast {i}: duplicate ID {cid}.")
        if a not in conditions or b not in conditions or a == b:
            raise ValueError(f"Contrast {i}: choose two distinct conditions represented in the design.")
        if min(replicate_counts[a], replicate_counts[b]) < 2:
            raise ValueError(f"Contrast {i}: Welch testing requires at least two biological "
                             "replicates in both compared conditions.")
        used_contrasts.add(cid)
        contrasts.append(dict(contrast_id=cid, condition_a=a, condition_b=b))
    return {"runs": runs, "contrasts": contrasts}


def generate_design(plan: dict, design_dir: Path, *, project_dir: str) -> dict:
    """Reuse tdp_experiment_design in the thesis checkout; nothing guessed."""
    plan = validated_plan(plan)
    root = Path(design_dir).resolve()
    project = Path(project_dir).expanduser().resolve()
    if not (project / "tdp_experiment_design" / "core.py").is_file():
        raise ValueError(f"Thesis experimental-design package not found under {project}")
    # This package has no pyOpenMS dependency, and the WSL FLASHApp environment
    # can call its functions directly without launching an extra tool.
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    from tdp_experiment_design.core import (  # type: ignore
        DiscoveredRun, build_template, validate_experiment, write_experiment,
    )
    source_rows = {Path(row["file_path"]).stem.casefold(): row for row in plan["runs"]}
    discovered = [
        DiscoveredRun(stem=Path(row["file_path"]).stem,
                      raw=Path(row["file_path"]) if Path(row["file_path"]).suffix.lower() == ".raw" else None,
                      mzml=Path(row["file_path"]) if Path(row["file_path"]).suffix.lower() == ".mzml" else None)
        for row in plan["runs"]
    ]
    mapping = {stem: (row["sample_id"], row["condition"], row["biological_replicate"])
               for stem, row in source_rows.items()}
    experiment = build_template(discovered, root, sample_map=mapping)
    # These are explicitly chosen MS1 acquisitions, not filenames inferred
    # from an 'MS1' token. The thesis generator marks unknown suffixes UNKNOWN.
    # Restore the explicit known acquisition type and create missing design rows.
    from tdp_experiment_design.core import DESIGN_COLUMNS
    experiment.design_rows = []
    sorted_sources = sorted(plan["runs"], key=lambda r: Path(r["file_path"]).stem.casefold())
    for index, (metadata, source) in enumerate(zip(experiment.run_rows, sorted_sources), 1):
        metadata["acquisition_type"] = "MS1_ONLY"
        if source["faims_cv"]:
            metadata["faims_cv_volts"] = json.dumps([float(source["faims_cv"])])
        experiment.design_rows.append(dict(zip(DESIGN_COLUMNS, (
            str(index), "1", metadata["mzml_path"], "1",
            source["sample_id"], source["condition"], source["biological_replicate"],
        ))))
    report = validate_experiment(experiment, root, mode="draft")
    if not report.valid:
        raise ValueError("Experimental design failed validation: " +
                         "; ".join(x.message for x in report.issues if x.severity == "error"))
    root.mkdir(parents=True, exist_ok=True)
    write_experiment(experiment, root, force=True)
    contrast = root / "contrast.tsv"
    with contrast.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CONTRAST_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(plan["contrasts"])
    write_json(root / "simulation_plan.json", plan)
    return {"directory": str(root), "ms1_files": len(plan["runs"]),
            "conditions": sorted({r["condition"] for r in plan["runs"]}),
            "contrasts": len(plan["contrasts"]), "warnings": [x.message for x in report.issues
                                                           if x.severity == "warning"]}
