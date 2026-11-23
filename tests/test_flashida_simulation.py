"""Pure tests for baseline FLASHIda MS1 replay without Windows or a native DLL."""
import csv
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src import flashida_simulation as sim


def test_windows_drive_to_wsl_path():
    assert sim.to_wsl_path(r"C:\flashida_sim\run_AA_100") == "/mnt/c/flashida_sim/run_AA_100"
    assert sim.to_wsl_path(r"D:\Data\AA.raw") == "/mnt/d/Data/AA.raw"
    assert sim.to_wsl_path("/mnt/c/Data/AA.raw") == "/mnt/c/Data/AA.raw"
    with pytest.raises(ValueError):
        sim.to_wsl_path(r"\\server\share\file.raw")


def test_producer_command_uses_real_source_and_safely_quotes_paths(tmp_path):
    raw = tmp_path / "E coli_AA.raw"
    raw.write_bytes(b"not real MS data: command construction only")
    with patch.object(sim, "to_wsl_path", side_effect=lambda x: "/mnt/c/" + Path(str(x)).name):
        cmd = sim.producer_command(
            project="/mnt/c/Thesis project", environment="openms-experiments",
            input_file=str(raw), run_dir=str(tmp_path / "replay output"),
            cv="-40", max_scans="100")
    assert cmd[:4] == ["wsl.exe", "--exec", "/bin/bash", "-lc"]
    assert "tdp_instrument_simulator" in cmd[-1]
    assert "'/mnt/c/E coli_AA.raw'" in cmd[-1]
    assert "--max-scans 100" in cmd[-1]
    assert "--cv -40.0" in cmd[-1]
    assert "cd '/mnt/c/Thesis project'" in cmd[-1]
    assert "openms-experiments" in cmd[-1]


def test_producer_rejects_bad_inputs(tmp_path):
    source = tmp_path / "AA.raw"
    source.write_text("x")
    base = {"project": "/mnt/c/project", "environment": "openms-experiments",
            "input_file": str(source), "run_dir": str(tmp_path / "run")}
    for kwargs in ({"cv": "nan"}, {"cv": "400"}, {"max_scans": "0"}):
        with pytest.raises(ValueError):
            sim.producer_command(**base, **kwargs)
    with pytest.raises(ValueError):
        sim.producer_command(**dict(base, input_file=str(tmp_path / "missing.raw")))


def test_summary_counts_unfulfilled_commands(tmp_path):
    (tmp_path / "consumer_status.json").write_text(json.dumps({
        "state": "complete", "last_received_sequence": 2,
        "last_completed_sequence": 2}), encoding="utf-8")
    (tmp_path / "commands.tsv").write_text(
        "sequence\tdisposition\n1\tsurvey_echo\n1\tunfulfilled\n"
        "1\tunfulfilled\n2\tsurvey_echo\n", encoding="utf-8")
    stats = sim.summarize(tmp_path)
    assert stats["scans_completed"] == 2
    assert stats["ms2_requests_unfulfilled"] == 2
    assert stats["commands_by_disposition"]["survey_echo"] == 2


def test_run_history_is_non_destructive(tmp_path):
    root = tmp_path / "runs"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "commands.tsv").write_text("keep")
    assert [p.name for p in sim.list_runs(tmp_path)] == ["b", "a"]
    assert (root / "a" / "commands.tsv").read_text() == "keep"


def test_design_command_refuses_overwrite(tmp_path):
    data = tmp_path / "input"
    data.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    (output / "contrast.tsv").write_text("previous experiment")
    with pytest.raises(FileExistsError):
        sim.design_command(project="/mnt/c/project", environment="openms-experiments",
                           raw_dir=str(data), design_dir=str(output))


def test_baseline_rejects_inclusion_method_before_launch(tmp_path):
    """No instrument commands may be issued by this entry point."""
    raw = tmp_path / "AA.raw"
    raw.write_text("stub")
    exe = tmp_path / "Flash.Simulator.exe"
    exe.write_text("stub")
    method = tmp_path / "method.json"
    method.write_text(json.dumps({"precursor_selection": {"targeting": "inclusion"}}))
    params = {"ms1-source": str(raw), "simulator-executable": str(exe),
              "simulation-method": str(method), "wsl-project": "/mnt/c/project"}
    class Logger:
        def log(self, text):
            pass
    with patch.object(sim, "os", SimpleNamespace(name="nt")):
        with pytest.raises(ValueError, match="baseline"):
            sim.execute(params, tmp_path, Logger())
    assert not (tmp_path / "runs").exists()


def test_design_uses_existing_thesis_api_and_creates_only_explicit_contrast(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    output = tmp_path / "experiment"
    output.mkdir()

    def fake_run(command, **kwargs):
        assert command[:2] == ["wsl.exe", "--exec"]
        assert "tdp_experiment_design init" in command[-1]
        assert "--preset ecoli-aa-gd" in command[-1]
        (output / "design.tsv").write_text(
            "Sample\tMSstats_Condition\tMSstats_BioReplicate\n"
            "AA\tacetate\t1\nAB\tacetate\t2\n"
            "GA\tglucose\t5\nGB\tglucose\t6\n",
            encoding="utf-8")
        (output / "run_metadata.tsv").write_text("run_id\nAA\nGA\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "generated", "")

    with patch.object(sim, "os", SimpleNamespace(name="nt")), \
         patch.object(sim, "to_wsl_path", side_effect=lambda path: "/mnt/c/" + Path(str(path)).name), \
         patch.object(sim.subprocess, "run", side_effect=fake_run):
        result = sim.create_experiment_design(
            project="/mnt/c/project", environment="openms-experiments",
            raw_dir=str(raw_dir), design_dir=str(output))

    assert result["contrast_created"]
    assert (output / "contrast.tsv").read_text(encoding="utf-8") == (
        "contrast_id\tcondition_a\tcondition_b\n"
        "glucose_vs_acetate\tglucose\tacetate\n")
    with pytest.raises(FileExistsError):
        sim.design_command(project="/mnt/c/project", environment="openms-experiments",
                           raw_dir=str(raw_dir), design_dir=str(output))


def test_one_biological_replicate_per_condition_produces_draft_without_contrast(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    output = tmp_path / "experiment"
    output.mkdir()

    def fake_run(command, **kwargs):
        (output / "design.tsv").write_text(
            "Sample\tMSstats_Condition\tMSstats_BioReplicate\n"
            "AA\tacetate\t1\nGA\tglucose\t5\n", encoding="utf-8")
        (output / "run_metadata.tsv").write_text("run_id\nAA\nGA\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch.object(sim, "os", SimpleNamespace(name="nt")), \
         patch.object(sim, "to_wsl_path", side_effect=lambda path: "/mnt/c/" + Path(str(path)).name), \
         patch.object(sim.subprocess, "run", side_effect=fake_run):
        result = sim.create_experiment_design(
            project="/mnt/c/project", environment="openms-experiments",
            raw_dir=str(raw_dir), design_dir=str(output))
    assert result["replicates_by_condition"] == {"acetate": 1, "glucose": 1}
    assert not result["contrast_created"]
    assert not (output / "contrast.tsv").exists()


def test_wsl_launches_producer_natively_and_windows_consumer_with_windows_paths(tmp_path):
    raw = tmp_path / "AA.raw"
    raw.write_text("test data")
    with patch.object(sim, "is_wsl", return_value=True), \
         patch.object(sim, "to_wsl_path", side_effect=lambda x: "/mnt/c/" + Path(str(x)).name):
        producer = sim.producer_command(
            project="/mnt/c/Thesis", environment="openms-experiments",
            input_file=str(raw), run_dir="/mnt/c/flashida_sim/flashapp_runs/r1",
            cv="-40", max_scans="100")
        assert producer[:2] == ["bash", "-lc"]
        assert "tdp_instrument_simulator" in producer[-1]
        consumer = sim.consumer_command(
            Path("/mnt/c/dev/FlashIDA/Flash.Simulator.exe"),
            Path("/mnt/c/flashida_sim/flashapp_runs/r1"))
        assert consumer[0] == "/mnt/c/dev/FlashIDA/Flash.Simulator.exe"
        assert consumer[2] == r"C:\flashida_sim\flashapp_runs\r1"
        assert consumer[3] == r"C:\flashida_sim\flashapp_runs\r1\method.json"


def test_wsl_runs_remain_on_shared_ntfs_even_if_flashapp_workspace_is_on_ext4():
    with patch.object(sim, "is_wsl", return_value=True):
        assert sim.run_root(Path("/home/langm/openms-development/FLASHApp"), {}) == (
            Path("/mnt/c/flashida_sim/flashapp_runs"))
        with pytest.raises(ValueError, match="Windows-mounted drive"):
            sim.run_root(Path("/home/langm/workspace"),
                         {"simulation-output-root": "/home/langm/isolated-runs"})
        root = sim.run_root(Path("/home/langm/workspace"),
                            {"simulation-output-root": r"C:\flashida_sim\custom"})
        assert str(root) == "/mnt/c/flashida_sim/custom"


def test_wsl_design_command_runs_inside_existing_wsl_conda(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    with patch.object(sim, "is_wsl", return_value=True), \
         patch.object(sim, "to_wsl_path", side_effect=lambda path: "/mnt/c/" + Path(str(path)).name):
        cmd = sim.design_command(
            project="/mnt/c/Thesis", environment="openms-experiments",
            raw_dir=str(raw_dir), design_dir=str(tmp_path / "design"))
    assert cmd[:2] == ["bash", "-lc"]
    assert "tdp_experiment_design init" in cmd[-1]
    assert "ecoli-aa-gd" in cmd[-1]
