"""Explicit per-acquisition MS1 design and contrast tests."""
from pathlib import Path
import os
from unittest.mock import patch
import json

import pytest

from src import flashida_experiment as d
from src import flashida_simulation as sim


def _file(tmp_path, name):
    f = tmp_path / name
    f.write_text("placeholder MS1 source")
    return str(f)


def _row(path, sample, condition, replicate, cv="-40"):
    return dict(file_path=path, sample_id=sample, condition=condition,
                biological_replicate=replicate, faims_cv=cv)


def test_precollected_files_get_explicit_design_rows_and_are_not_inferred(tmp_path):
    aa = _file(tmp_path, "20260315_MS1_AA.raw")
    ga = _file(tmp_path, "20260315_MS1_GA.raw")
    rows = d.add_sources([], [aa, ga, aa])
    assert len(rows) == 2
    assert rows[0]["sample_id"] == ""
    with pytest.raises(ValueError, match="sample ID"):
        d.validated_plan({"runs": rows, "contrasts": []})
    annotated = d.apply_ecoli_preset(rows)
    assert [(r["sample_id"], r["condition"], r["biological_replicate"])
            for r in annotated] == [("AA", "acetate", "1"), ("GA", "glucose", "5")]
    assert len(d.validated_plan({"runs": annotated, "contrasts": []})["runs"]) == 2


def test_plan_persists_file_assignments_and_contrasts(tmp_path):
    rows = [_row(_file(tmp_path, "AA.raw"), "culture_1", "control", "1")]
    plan = {"runs": rows, "contrasts": []}
    d.save_plan(tmp_path / "workspace", plan)
    assert d.load_plan(tmp_path / "workspace") == plan
    plan["runs"][0]["condition"] = "treated"
    d.save_plan(tmp_path / "workspace", plan)
    assert d.load_plan(tmp_path / "workspace")["runs"][0]["condition"] == "treated"


def test_arbitrary_condition_labels_and_multiple_files_per_sample(tmp_path):
    plan = {"runs": [
        _row(_file(tmp_path, "run_AA.raw"), "AA", "acetate", "1"),
        _row(_file(tmp_path, "technical_MS1_AA.mzML"), "AA", "acetate", "1"),
        _row(_file(tmp_path, "run_GA.raw"), "GA", "glucose", "5"),
        _row(_file(tmp_path, "run_GB.raw"), "GB", "glucose", "6"),
    ], "contrasts": [{"contrast_id": "glucose_vs_acetate",
                      "condition_a": "glucose", "condition_b": "acetate"}]}
    with pytest.raises(ValueError, match="at least two biological"):
        d.validated_plan(plan)
    plan["contrasts"] = []
    assert len(d.validated_plan(plan)["runs"]) == 4


def test_contrast_requires_two_replicates_and_known_condition(tmp_path):
    rows = [_row(_file(tmp_path, name + ".raw"), name, condition, number)
            for name, condition, number in [
                ("AA", "acetate", "1"), ("AB", "acetate", "2"),
                ("GA", "glucose", "5"), ("GB", "glucose", "6")]]
    contrast = {"contrast_id": "glucose_vs_acetate",
                "condition_a": "glucose", "condition_b": "acetate"}
    plan = {"runs": rows, "contrasts": [contrast]}
    assert len(d.validated_plan(plan)["contrasts"]) == 1
    plan["contrasts"] = [dict(contrast, condition_a="missing")]
    with pytest.raises(ValueError, match="represented"):
        d.validated_plan(plan)
    plan["contrasts"] = [dict(contrast, contrast_id="bad identifier")]
    with pytest.raises(ValueError, match="unique ID"):
        d.validated_plan(plan)
    plan["contrasts"] = [contrast]
    plan["runs"][1]["biological_replicate"] = "1"
    with pytest.raises(ValueError, match="belongs to both"):
        d.validated_plan(plan)


def test_batch_uses_same_file_design_to_replay_every_input(tmp_path):
    first = _row(_file(tmp_path, "AA.raw"), "AA", "acetate", "1")
    second = _row(_file(tmp_path, "GA.raw"), "GA", "glucose", "5")
    workspace = tmp_path / "workspace"
    plan = {"runs": [first, second], "contrasts": []}
    d.save_plan(workspace, plan)
    invocations = []

    class Logger:
        def log(self, message):
            pass

    def fake_execute(params, root, logger):
        invocations.append((params["ms1-source"], params["sample-id"],
                            params["condition"], params["biological-replicate"],
                            params["faims-cv"]))
        directory = tmp_path / "output" / f"run_{len(invocations)}"
        directory.mkdir()
        return True

    with patch.object(sim, "execute", side_effect=fake_execute), \
         patch.object(sim, "run_root", return_value=tmp_path / "output"), \
         patch.object(d, "generate_design", return_value={"directory": "design"}):
        assert sim.execute_batch({"wsl-project": str(tmp_path)}, workspace, Logger())

    assert invocations == [
        (first["file_path"], "AA", "acetate", "1", "-40"),
        (second["file_path"], "GA", "glucose", "5", "-40"),
    ]
    manifests = list((tmp_path / "output").glob("batch_*.json"))
    assert len(manifests) == 1
    batch = json.loads(manifests[0].read_text())
    assert batch["state"] == "complete"
    assert len(batch["outcomes"]) == 2
    assert all(x["success"] for x in batch["outcomes"])



def test_openms_design_generated_with_real_thesis_api(tmp_path):
    project = os.environ.get("FLASHIDA_THESIS_SOURCE")
    if not project:
        pytest.skip("Set FLASHIDA_THESIS_SOURCE to run against the real thesis design package.")
    files = [(_file(tmp_path, name + ".raw"), name, group, rep)
             for name, group, rep in [
                 ("20260315_MS1_AA", "acetate", "1"),
                 ("20260315_MS1_AB", "acetate", "2"),
                 ("20260315_MS1_GA", "glucose", "5"),
                 ("20260315_MS1_GB", "glucose", "6")]]
    plan = {
        "runs": [_row(path, name[-2:], group, rep) for path, name, group, rep in files],
        "contrasts": [{"contrast_id": "glucose_vs_acetate",
                       "condition_a": "glucose", "condition_b": "acetate"}],
    }
    output = tmp_path / "design"
    result = d.generate_design(plan, output, project_dir=project)
    assert result["ms1_files"] == 4
    import csv
    with (output / "design.tsv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    assert len(rows) == 4
    assert {row["MSstats_Condition"] for row in rows} == {"glucose", "acetate"}
    assert {row["MSstats_BioReplicate"] for row in rows} == {"1", "2", "5", "6"}
    with (output / "run_metadata.tsv").open(encoding="utf-8", newline="") as f:
        metadata = list(csv.DictReader(f, delimiter="\t"))
    assert all(row["acquisition_type"] == "MS1_ONLY" for row in metadata)
    assert all(row["mzml_status"] == "pending" for row in metadata)
    assert all(json.loads(row["faims_cv_volts"]) == [-40.0] for row in metadata)
    assert (output / "contrast.tsv").read_text(encoding="utf-8") == (
        "contrast_id\tcondition_a\tcondition_b\n"
        "glucose_vs_acetate\tglucose\tacetate\n"
    )
