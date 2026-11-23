"""Windows-only MS1 replay for the hidden developer FLASHIda interface."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time
from uuid import uuid4


def to_wsl_path(path: str | Path) -> str:
    value = str(path)
    if value.startswith("/mnt/"):
        return value
    match = re.fullmatch(r"([A-Za-z]):[\\/](.*)", value)
    if not match:
        raise ValueError("Select an absolute Windows drive path accessible through /mnt in WSL.")
    return "/mnt/" + match.group(1).lower() + "/" + match.group(2).replace("\\", "/")


def wsl_python(project: str, environment: str) -> str:
    if not project.startswith("/") or not environment.strip():
        raise ValueError("Specify an absolute WSL thesis-project path and conda environment.")
    prefix = ('CONDA="$HOME/miniconda3/bin/conda"; '
              'if [ -n "$CONDA_EXE" ]; then CONDA="$CONDA_EXE"; fi; '
              'test -x "$CONDA" || { echo "WSL conda executable not found" >&2; exit 2; }; ')
    return (prefix + "cd " + shlex.quote(project) + " && "
            '"$CONDA" run --no-capture-output -n '
            + shlex.quote(environment) + " python -u")


def producer_command(*, project: str, environment: str, input_file: str,
                     run_dir: str, cv: str = "", max_scans: str = "") -> list[str]:
    source = Path(input_file)
    if source.suffix.lower() not in (".raw", ".mzml") or not source.is_file():
        raise ValueError("Choose an existing MS1 .raw or .mzML file.")
    script = wsl_python(project, environment)
    script += (" -m tdp_instrument_simulator --input " +
               shlex.quote(to_wsl_path(source.resolve())) +
               " --output-dir " + shlex.quote(to_wsl_path(run_dir)) +
               " --cache-dir .cache/data")
    if cv.strip():
        if not -300 <= float(cv) <= 300:
            raise ValueError("Invalid FAIMS CV.")
        script += " --cv " + shlex.quote(str(float(cv)))
    if max_scans.strip():
        if int(max_scans) <= 0:
            raise ValueError("Scan limit must be positive.")
        script += " --max-scans " + shlex.quote(str(int(max_scans)))
    return ["wsl.exe", "--exec", "/bin/bash", "-lc", script]


def design_command(*, project: str, environment: str, raw_dir: str,
                   design_dir: str) -> list[str]:
    if not Path(raw_dir).is_dir():
        raise ValueError("Experimental input folder does not exist.")
    root = Path(design_dir)
    if any((root / f).exists() for f in ("design.tsv", "run_metadata.tsv", "contrast.tsv")):
        raise FileExistsError("Design files already exist; refusing to overwrite.")
    script = (wsl_python(project, environment) +
              " -m tdp_experiment_design init --input-dir " +
              shlex.quote(to_wsl_path(Path(raw_dir).resolve())) +
              " --out " + shlex.quote(to_wsl_path(root.resolve())) +
              " --preset ecoli-aa-gd")
    return ["wsl.exe", "--exec", "/bin/bash", "-lc", script]


def create_experiment_design(*, project: str, environment: str, raw_dir: str,
                             design_dir: str) -> dict:
    command = design_command(project=project, environment=environment,
                             raw_dir=raw_dir, design_dir=design_dir)
    if os.name != "nt":
        raise RuntimeError("The WSL design integration requires the local Windows app.")
    result = subprocess.run(command, capture_output=True, text=True,
                            errors="replace", timeout=180, check=False)
    if result.returncode:
        raise RuntimeError((result.stdout + "\n" + result.stderr)[-3500:])
    root = Path(design_dir)
    if not (root / "design.tsv").is_file() or not (root / "run_metadata.tsv").is_file():
        raise RuntimeError("Design tool completed without its two expected output files.")
    with (root / "design.tsv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    conditions = {r.get("MSstats_Condition") for r in rows}
    contrast = root / "contrast.tsv"
    if {"acetate", "glucose"} <= conditions:
        with contrast.open("x", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(["contrast_id", "condition_a", "condition_b"])
            writer.writerow(["glucose_vs_acetate", "glucose", "acetate"])
    return {"runs": len(rows), "conditions": sorted(conditions - {None}),
            "contrast_created": contrast.exists(), "directory": str(root),
            "output": result.stdout[-4000:]}


def write_json(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_run(folder: Path) -> dict:
    try:
        return json.loads((folder / "simulation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_runs(root: Path) -> list[Path]:
    directory = root / "runs"
    return sorted((p for p in directory.iterdir() if p.is_dir()), reverse=True) if directory.is_dir() else []


def summarize(folder: Path) -> dict:
    status = json.loads((folder / "consumer_status.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    with (folder / "commands.tsv").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            kind = row["disposition"]
            counts[kind] = counts.get(kind, 0) + 1
    return {"consumer_state": status.get("state"),
            "scans_received": int(status.get("last_received_sequence", 0)),
            "scans_completed": int(status.get("last_completed_sequence", 0)),
            "commands_by_disposition": counts,
            "ms2_requests_unfulfilled": counts.get("unfulfilled", 0)}


def _capture(proc: subprocess.Popen, log: Path,
             ready: threading.Event | None = None) -> None:
    def reader():
        with log.open("w", encoding="utf-8") as out:
            for line in proc.stdout:
                out.write(line)
                out.flush()
                if ready is not None and "waiting for FLASHIda consumer" in line:
                    ready.set()
    threading.Thread(target=reader, daemon=True).start()


def _kill(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def execute(params: dict, workflow_dir: Path, logger) -> bool:
    if os.name != "nt":
        raise RuntimeError("FLASHIda MS1 simulation requires local Windows FLASHApp.")
    source = Path(str(params.get("ms1-source") or "").strip()).resolve()
    exe = Path(str(params.get("simulator-executable") or "").strip()).resolve()
    method = Path(str(params.get("simulation-method") or "").strip()).resolve()
    project = str(params.get("wsl-project") or "").strip()
    env = str(params.get("wsl-environment") or "openms-experiments").strip()
    cv = str(params.get("faims-cv") or "").strip()
    limit = str(params.get("scan-limit") or "").strip()
    if not exe.is_file() or exe.name.lower() != "flash.simulator.exe":
        raise ValueError("Select Flash.Simulator.exe (not instrument Flash.exe).")
    if not method.is_file():
        raise ValueError("Select a FLASHIda method JSON.")
    parsed = json.loads(method.read_text(encoding="utf-8-sig"))
    targeting = (parsed.get("precursor_selection") or {}).get("targeting", "none")
    if targeting != "none":
        raise ValueError("Only baseline targeting=none is supported in this milestone.")
    root = workflow_dir / "runs"
    root.mkdir(parents=True, exist_ok=True)
    folder = root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                     + "_" + uuid4().hex[:8])
    folder.mkdir()
    (folder / "method.json").write_bytes(method.read_bytes())
    command = producer_command(project=project, environment=env,
                               input_file=str(source), run_dir=str(folder),
                               cv=cv, max_scans=limit)
    meta = {"state": "preparing", "source": str(source), "method": str(method),
            "cv": cv, "max_scans": limit or "all", "run_directory": str(folder),
            "started_utc": datetime.now(timezone.utc).isoformat()}
    write_json(folder / "simulation.json", meta)
    producer = consumer = None
    try:
        logger.log("Preparing centroided MS1 input through WSL...")
        producer = subprocess.Popen(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    errors="replace", bufsize=1)
        meta["producer_pid"] = producer.pid
        write_json(folder / "simulation.json", meta)
        ready = threading.Event()
        _capture(producer, folder / "producer.log", ready)
        deadline = time.monotonic() + 1800
        while not ready.wait(0.2):
            if producer.poll() is not None:
                raise RuntimeError("MS1 preparation failed; inspect producer.log.")
            if time.monotonic() > deadline:
                raise TimeoutError("MS1 preparation exceeded 30 minutes.")
        logger.log("Starting FLASHIda standalone native acquisition simulator...")
        meta["state"] = "running"
        consumer = subprocess.Popen([str(exe), "--simulate-directory", str(folder),
                                     str(folder / "method.json")],
                                    cwd=str(exe.parent), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    errors="replace", bufsize=1)
        meta["consumer_pid"] = consumer.pid
        write_json(folder / "simulation.json", meta)
        _capture(consumer, folder / "consumer.log")
        while producer.poll() is None or consumer.poll() is None:
            if producer.poll() not in (None, 0):
                raise RuntimeError("MS1 producer failed; inspect producer.log.")
            if consumer.poll() not in (None, 0):
                raise RuntimeError("FLASHIda simulator failed; inspect consumer.log.")
            time.sleep(0.2)
        if producer.returncode or consumer.returncode:
            raise RuntimeError(f"Simulation exited: producer={producer.returncode}, "
                               f"consumer={consumer.returncode}")
        stats = summarize(folder)
        if stats["consumer_state"] != "complete" or (
                stats["scans_completed"] != stats["scans_received"]):
            raise RuntimeError(f"Consumer has incomplete acknowledgements: {stats}")
        meta.update(stats)
        meta["state"] = "complete"
        logger.log(f"Completed MS1 replay: {stats}")
        return True
    except Exception as error:
        meta["state"] = "failed"
        meta["error"] = str(error)
        logger.log(f"MS1 replay failed: {error}")
        return False
    finally:
        _kill(producer)
        _kill(consumer)
        meta["ended_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(folder / "simulation.json", meta)


def stop_children(folder: Path) -> None:
    """Stop tracked Windows children only if their process names still match."""
    if os.name != "nt":
        return
    import psutil
    data = read_run(folder)
    for key, needle in (("producer_pid", "wsl"), ("consumer_pid", "flash.simulator")):
        pid = data.get(key)
        if not isinstance(pid, int):
            continue
        try:
            p = psutil.Process(pid)
            if needle not in (p.name() + " " + " ".join(p.cmdline())).lower():
                continue
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10, check=False)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
