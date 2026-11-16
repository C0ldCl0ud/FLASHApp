"""Unit tests for FLASHApp orchestration (no proprietary files or OpenMS binary needed)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import types

import pandas as pd
import pytest

from src.ms1_inclusion_pipeline import (
    command_for_deconv, command_for_alignment, command_for_abundance,
    command_for_inclusion, execute_pipeline, prepare_thesis_experiment,
    resolved_executables, CLI_MODULES,
)

TOOLS = {
    "fileconverter": "/tools/FileConverter",
    "filefilter": "/tools/FileFilter",
    "ionmobilitybinning": "/tools/IonMobilityBinning",
    "peakpicker": "/tools/PeakPickerHiRes",
    "flashdeconv": "/tools/FLASHDeconv",
    "consensus_detector": "/tools/ConsensusFeatureGroupDetector",
}


@pytest.fixture()
def experiment(tmp_path):
    files = [tmp_path / "AA.mzML", tmp_path / "GA.mzML"]
    for file in files:
        file.write_text("example indexed mzML placeholder")
    design = pd.DataFrame([
        {"file": "AA.mzML", "condition": "acetate", "replicate": 1, "fraction": 1},
        {"file": "GA.mzML", "condition": "glucose", "replicate": 1, "fraction": 1},
    ])
    params = {
        "execution-mode": "full", "contrast-a": "acetate", "contrast-b": "glucose",
        "faims-mode": "require", "rt-alignment": "pose",
        "q-threshold": 0.01, "abs-log2fc-threshold": 1.5,
        "min-qscore": 0.8, "top-fraction": 0.5,
        "rt-margin-seconds": 30.0,
        "priority-quality": 1, "priority-diff-abundance": 7,
        "priority-conditional": 5,
    }
    return [str(x) for x in files], design, params


def _arg(command, name):
    return command[command.index(name) + 1]


def test_cli_argument_contracts(experiment, tmp_path):
    files, _, p = experiment
    deconv = command_for_deconv(
        Path(files[0]), tmp_path / "deconv", tmp_path / "cache", p, TOOLS, "python3"
    )
    assert deconv[:3] == ["python3", "-m", CLI_MODULES["deconv"]]
    assert _arg(deconv, "--faims-mode") == "require"
    assert _arg(deconv, "--flashdeconv") == "/tools/FLASHDeconv"
    assert "--rt-min" not in deconv

    aligned = command_for_alignment(tmp_path / "exp", tmp_path / "deconv",
                                     tmp_path / "align", p, TOOLS, "python3")
    assert aligned[:3] == ["python3", "-m", CLI_MODULES["alignment"]]
    assert _arg(aligned, "--coverage-mode") == "partial"
    assert _arg(aligned, "--min-qscore") == "0"  # Quality applies at inclusion, not alignment.
    assert _arg(aligned, "--consensus-detector") == "/tools/ConsensusFeatureGroupDetector"

    abundance = command_for_abundance(
        tmp_path / "exp", tmp_path / "align", tmp_path / "contrasts.tsv",
        tmp_path / "abundance", p, "python3"
    )
    assert abundance[:3] == ["python3", "-m", CLI_MODULES["differential_abundance"]]
    assert _arg(abundance, "--q-threshold") == "0.01"
    assert _arg(abundance, "--abs-log2fc-threshold") == "1.5"

    inclusion = command_for_inclusion(
        tmp_path / "exp", tmp_path / "align", tmp_path / "abundance",
        tmp_path / "inclusion", p, "python3"
    )
    assert inclusion[:3] == ["python3", "-m", CLI_MODULES["inclusion_list"]]
    assert [inclusion[i + 1] for i, part in enumerate(inclusion)
            if part == "--reason-priority"] == [
                "quality=1", "diff_abundance=7", "conditional=5",
            ]


def test_rt_filter_only_when_enabled(experiment, tmp_path):
    files, _, params = experiment
    params.update({"rt-filter-enabled": True, "rt-min-minutes": 5.0,
                   "rt-max-minutes": 25.0})
    cmd = command_for_deconv(
        Path(files[0]), tmp_path, tmp_path, params, TOOLS, "python3"
    )
    assert _arg(cmd, "--rt-min") == "5.0"
    assert _arg(cmd, "--rt-max") == "25.0"


def test_reject_unsupported_native_mass_filter_before_expensive_analysis(experiment):
    files, _, params = experiment
    params["min-mass-da"] = 2000.0
    with pytest.raises(RuntimeError, match="minimum precursor mass or charge"):
        resolved_executables(params, files)


@pytest.fixture()
def mock_thesis_design(monkeypatch):
    """Simulate the private design package while exercising the public adapter."""
    class FakeExperiment:
        def __init__(self, design_rows, run_rows):
            self.design_rows = design_rows
            self.run_rows = run_rows

    class FakeReport:
        valid = True
        issues = []

        @staticmethod
        def as_dict():
            return {"valid": True, "errors": 0, "warnings": 0, "issues": []}

    spec = types.ModuleType("tdp_experiment_design")
    spec.Experiment = FakeExperiment
    spec.DESIGN_COLUMNS = (
        "Fraction_Group", "Fraction", "Spectra_Filepath", "Label",
        "Sample", "MSstats_Condition", "MSstats_BioReplicate",
    )
    spec.RUN_COLUMNS = (
        "run_id", "sample_id", "acquisition_type", "raw_path", "mzml_path",
        "mzml_status", "faims_cv_volts", "injection_volume_ul",
        "run_duration_min", "instrument", "method", "acquisition_date",
        "batch", "results_dir", "parent_run_id",
    )
    recorded = []

    def validate(exp, folder, mode):
        assert mode == "draft"
        recorded.append(exp)
        return FakeReport()

    def write(exp, folder):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        for name, columns, rows in (
            ("design.tsv", spec.DESIGN_COLUMNS, exp.design_rows),
            ("run_metadata.tsv", tuple(exp.run_rows[0]), exp.run_rows),
        ):
            with (path / name).open("w", newline="") as stream:
                writer = csv.DictWriter(stream, columns, delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)

    spec.validate_experiment = validate
    spec.write_experiment = write
    monkeypatch.setitem(sys.modules, "tdp_experiment_design", spec)
    return recorded


def test_thesis_design_adapter_maps_duplicate_replicate_numbers_across_conditions(
    experiment, tmp_path, mock_thesis_design,
):
    files, design, _ = experiment
    report = prepare_thesis_experiment(files, design, files, tmp_path / "experiment")
    assert report["valid"]
    exp = mock_thesis_design[0]
    assert [r["Sample"] for r in exp.design_rows] == ["sample_0001", "sample_0002"]
    assert [r["MSstats_Condition"] for r in exp.design_rows] == ["acetate", "glucose"]
    assert [r["MSstats_BioReplicate"] for r in exp.design_rows] == ["1", "2"]
    assert [r["webapp_replicate_in_condition"] for r in exp.run_rows] == ["1", "1"]
    assert [r["run_id"] for r in exp.run_rows] == ["AA", "GA"]
    assert all(r["mzml_status"] == "present" for r in exp.run_rows)


def test_raw_is_recorded_as_pending_and_processed_without_fake_conversion(
    experiment, tmp_path, mock_thesis_design,
):
    files, design, _ = experiment
    raw = tmp_path / "AA.raw"
    raw.write_text("raw placeholder")
    files[0] = str(raw)
    design.loc[0, "file"] = raw.name
    prepare_thesis_experiment(files, design, files, tmp_path / "exp")
    exp = mock_thesis_design[0]
    assert exp.run_rows[0]["mzml_status"] == "pending"
    assert exp.run_rows[0]["mzml_path"].endswith("AA.mzML")
    assert exp.run_rows[0]["raw_path"].endswith("AA.raw")


class FakeLogger:
    def __init__(self):
        self.messages = []

    def log(self, message):
        self.messages.append(str(message))


def test_full_pipeline_runs_all_steps_and_writes_results(
    experiment, tmp_path, mock_thesis_design,
):
    files, design, params = experiment
    results = tmp_path / "results"
    results.mkdir()
    (results / "contrasts.tsv").write_text(
        "contrast_id\tcondition_a\tcondition_b\ncontrast_1\tacetate\tglucose\n"
    )
    executed = []

    def fake_run(command):
        executed.append(command)
        module = command[2]
        out = Path(_arg(command, "--out"))
        if module == CLI_MODULES["deconv"]:
            output = out / Path(_arg(command, "--input")).stem / "cv_groups.tsv"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("run_id\tcv_group\n")
        elif module == CLI_MODULES["alignment"]:
            out.mkdir(parents=True, exist_ok=True)
            (out / "summary.json").write_text('{"cv_groups": {}}')
        elif module == CLI_MODULES["differential_abundance"]:
            out.mkdir(parents=True, exist_ok=True)
            (out / "summary.json").write_text('{"contrasts": {}}')
        elif module == CLI_MODULES["inclusion_list"]:
            out.mkdir(parents=True, exist_ok=True)
            (out / "selection_summary.json").write_text(
                '{"total_targets": 3, "cv_groups": {}}'
            )
            (out / "inclusion_list.tsv").write_text(
                "mass\tcharge\trt_start\trt_end\tpriority\tcv\treason\n"
            )
        else:
            raise AssertionError(f"Unexpected CLI: {module}")
        return True

    report = execute_pipeline(
        files, design, params, results, tmp_path / "cache",
        FakeLogger(), fake_run, python="python3",
        tool_resolver=lambda _p, _s: TOOLS,
    )
    assert len(executed) == 5  # Two deconv runs + alignment + stats + inclusion.
    assert all(c[:2] == ["python3", "-m"] for c in executed)
    assert report["pipeline_completed"] is True
    assert report["inclusion_summary"]["total_targets"] == 3
    assert (results / "pipeline_report.json").is_file()
    assert (results / "experiment" / "design.tsv").is_file()
    assert (results / "experiment" / "run_metadata.tsv").is_file()


def test_failed_external_process_cannot_publish_pipeline_report(
    experiment, tmp_path, mock_thesis_design,
):
    files, design, params = experiment
    root = tmp_path / "output"
    root.mkdir()
    (root / "contrasts.tsv").write_text(
        "contrast_id\tcondition_a\tcondition_b\ncontrast_1\tacetate\tglucose\n"
    )
    with pytest.raises(RuntimeError, match="MS1 stage failed"):
        execute_pipeline(
            files, design, params, root, tmp_path / "cache",
            FakeLogger(), lambda command: False, python="python3",
            tool_resolver=lambda _p, _s: TOOLS,
        )
    assert not (root / "pipeline_report.json").exists()


def test_partial_consensus_required_by_alignment_commands(experiment):
    """Never substitute complete-case FLASHQuant when partial consensus is requested."""
    _, _, params = experiment
    command = command_for_alignment(
        Path("exp"), Path("deconv"), Path("align"), params, TOOLS,
    )
    assert _arg(command, "--coverage-mode") == "partial"
    assert _arg(command, "--consensus-detector") == TOOLS["consensus_detector"]
