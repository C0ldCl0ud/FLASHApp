"""Tests for the validation-only MS1 inclusion-list planning scaffold."""
import csv
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.ms1_inclusion_validation import (
    DEFAULT_SETTINGS, effective_settings, validate_setup, write_validation_artifacts,
)


@pytest.fixture
def setup_data(tmp_path):
    files = [tmp_path / "AA.mzML", tmp_path / "GA.mzML"]
    for file in files:
        file.write_text("test placeholder", encoding="utf-8")
    design = pd.DataFrame([
        {"file": "AA.mzML", "condition": "acetate", "replicate": 1, "fraction": 1},
        {"file": "GA.mzML", "condition": "glucose", "replicate": 1, "fraction": 1},
    ])
    settings = {"contrast-a": "acetate", "contrast-b": "glucose"}
    return [str(file) for file in files], design, settings


def test_default_settings_are_effective():
    values = effective_settings({"faims-mode": "require"})
    assert values["faims-mode"] == "require"
    assert values["coverage-mode"] == "partial"
    assert values["min-qscore"] == 0.8
    assert values["top-fraction"] == 0.5
    assert values["rt-margin-seconds"] == 30.0
    assert all(values[f"priority-{reason}"] == 2
               for reason in ("quality", "diff-abundance", "conditional"))
    assert set(values) == set(DEFAULT_SETTINGS)


def test_valid_ms1_design(setup_data):
    files, design, settings = setup_data
    assert validate_setup(files, design, settings) == []


def test_saved_validation_report_is_not_a_scientific_result(setup_data, tmp_path):
    files, design, settings = setup_data
    output_dir = tmp_path / "results"
    report = write_validation_artifacts(output_dir, files, design, settings)

    assert report["status"] == "configuration_validated"
    assert report["validation_only"] is True
    assert report["scientific_processing_executed"] is False
    assert report["settings"]["priority-quality"] == 2
    assert report["selected_input_files"] == files
    assert json.loads((output_dir / "validation_report.json").read_text()) == report
    assert "Spectra_Filepath" in (output_dir / "experimental_design.tsv").read_text()
    with (output_dir / "contrasts.tsv").open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert rows == [{"contrast_id": "contrast_1",
                     "condition_a": "acetate", "condition_b": "glucose"}]
    assert not (output_dir / "inclusion_list.tsv").exists()


@pytest.mark.parametrize("change,expected", [
    ({"contrast-a": ""}, "distinct"),
    ({"contrast-b": "not_in_design"}, "absent"),
    ({"faims-mode": "random"}, "FAIMS"),
    ({"coverage-mode": "complete"}, "partial-consensus"),
    ({"rt-filter-enabled": True, "rt-min-minutes": 4.0,
      "rt-max-minutes": 2.0}, "Maximum RT"),
    ({"min-mass-da": -2.0}, "Minimum mass"),
    ({"min-charge": -1}, "min-charge"),
    ({"q-threshold": 0.0}, "Q-value"),
    ({"abs-log2fc-threshold": -0.1}, "fold-change"),
    ({"min-qscore": 1.5}, "Qscore2D"),
    ({"top-fraction": 0.0}, "Top-intensity"),
    ({"rt-margin-seconds": -5.0}, "RT scheduling"),
    ({"priority-quality": -1}, "priority-quality"),
    ({"priority-diff-abundance": 2.5}, "priority-diff-abundance"),
    ({"priority-conditional": True}, "priority-conditional"),
])
def test_bad_scientific_settings_are_rejected(setup_data, change, expected):
    files, design, settings = setup_data
    errors = validate_setup(files, design, {**settings, **change})
    assert expected in " ".join(errors)


def test_reject_missing_file(setup_data):
    files, design, settings = setup_data
    Path(files[0]).unlink()
    assert "missing" in " ".join(validate_setup(files, design, settings))


def test_reject_missing_conditions(setup_data):
    files, design, settings = setup_data
    design.loc[1, "condition"] = ""
    assert "Set a condition" in " ".join(validate_setup(files, design, settings))


def test_reject_repeated_biological_replicate(tmp_path, setup_data):
    files, design, settings = setup_data
    design.loc[1, "condition"] = "acetate"
    settings["contrast-b"] = "acetate_2"
    errors = validate_setup(files, design, settings)
    assert any("appears multiple times" in error for error in errors)


def test_reject_mismatched_design_order(setup_data):
    files, design, settings = setup_data
    design = design.iloc[::-1].reset_index(drop=True)
    assert "in order" in " ".join(validate_setup(files, design, settings))


def test_reject_no_or_one_input(setup_data):
    files, design, settings = setup_data
    assert "at least two" in " ".join(validate_setup([], design.iloc[:0], settings))
    assert "at least two" in " ".join(validate_setup(files[:1], design.iloc[:1], settings))


def test_workflow_registered_with_four_pages_and_preset():
    root = Path(__file__).resolve().parents[1]
    spec = yaml.safe_load((root / "app.yaml").read_text(encoding="utf-8"))
    workflow = next(w for w in spec["workflows"] if w["name"] == "MS1 inclusion planning")
    assert workflow["class"] == "src/MS1InclusionWorkflow.py:MS1InclusionWorkflow"
    assert workflow["inputs"] == ["ms1-files"]
    assert workflow["multi_run"] is True
    assert workflow["steps"] == []
    assert workflow["presets"] is True
    pages = [
        "MS1InclusionUpload.py", "MS1InclusionConfigure.py",
        "MS1InclusionRun.py", "MS1InclusionResults.py",
    ]
    app = (root / "app.py").read_text(encoding="utf-8")
    for name in pages:
        path = f"content/MS1Inclusion/{name}"
        assert (root / path).is_file()
        assert path in spec["pages"]
        assert name in app
    presets = json.loads((root / "presets.json").read_text(encoding="utf-8"))
    assert "E. coli FAIMS MS1" in presets["ms1-inclusion-planning"]
