import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from toolset.benchmark.test_types import algorithm_type

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
CATALOG = FIX / "catalogs" / "algorithms"
CELL = {"algorithm": "n-body", "n": 1000}


def test_env_names_and_values():
    test = {"run_command": "python3 nbody.py ${BENCH_N}", "build_command": ""}
    env = algorithm_type.env(CELL, test, "abc", runs=5, warmup=2)
    assert env["BENCH_TEST_TYPE"] == "algorithm"
    assert (env["BENCH_ALGORITHM"], env["BENCH_N"]) == ("n-body", "1000")
    assert (env["BENCH_RUNS"], env["BENCH_WARMUP"]) == ("5", "2")
    assert env["BENCH_EXPECTED_SHA256"] == "abc"
    assert env["BENCH_RUN_COMMAND"] == "python3 nbody.py ${BENCH_N}"
    assert env["BENCH_DATASET"] == ""


def test_parse_ok_recorded_output():
    status, reason, [r] = algorithm_type.parse_output(
        (FIX / "raw_algorithm_ok.txt").read_text(), CELL, runs=3)
    assert (status, reason) == ("ok", None)
    assert r["algorithm"] == "n-body" and r["n"] == 1000
    assert r["correctness"] == "ok" and r["mean"] > 0 and len(r["times"]) == 3
    assert r["engine"] == "hyperfine" and r["markerPrefix"] == "BENCH"
    assert "numberOfFiles" not in r and "postsPerSecond" not in r


def test_verify_fail_has_reason_and_no_timing():
    status, reason, results = algorithm_type.parse_output(
        (FIX / "raw_algorithm_verify_fail.txt").read_text(), CELL)
    assert status == "failed" and results == []
    assert reason.startswith("BENCH_VERIFY_FAIL") and "expected=76de83d6" in reason


def test_unsupported_recorded_output():
    status, reason, results = algorithm_type.parse_output(
        (FIX / "raw_algorithm_unsupported.txt").read_text(), CELL)
    assert (status, results) == ("unsupported", [])


def test_result_before_verification_is_a_protocol_failure():
    raw = (FIX / "raw_algorithm_ok.txt").read_text().splitlines(keepends=True)
    raw = raw[1:] + raw[:1]  # VERIFY_OK after the result block
    status, reason, results = algorithm_type.parse_output("".join(raw), CELL)
    assert status == "failed" and results == [] and "protocol" in reason


def test_missing_verification_gives_no_result():
    raw = "".join((FIX / "raw_algorithm_ok.txt").read_text().splitlines(keepends=True)[1:])
    assert algorithm_type.parse_output(raw, CELL)[2] == []


# --- the canonical runner's untimed verification, without Docker or an engine ---

def _run_runner(tmp_path, **env):
    # A fake `hyperfine` on PATH would fake the engine, so none is provided:
    # these cases must stop before timing. The ok path is the Docker E2E below.
    full = {"PATH": "/usr/bin:/bin", "BENCH_ALGORITHM": "n-body", "BENCH_N": "1000",
            "BENCH_RUN_COMMAND": f"{sys.executable} {CATALOG}/languages/python/n-body/src/nbody.py 1000",
            **env}
    return subprocess.run(["sh", str(CATALOG / "tools" / "run.sh")], env=full,
                          capture_output=True, text=True, cwd=tmp_path)


@pytest.mark.skipif(shutil.which("sha256sum") is None, reason="needs sha256sum")
def test_runner_stops_on_checksum_mismatch(tmp_path):
    run = _run_runner(tmp_path, BENCH_EXPECTED_SHA256="0" * 64)
    assert run.returncode == 1
    assert run.stdout.startswith("BENCH_VERIFY_FAIL")
    assert "STARTTIME" not in run.stdout and "RESULT_BEGIN" not in run.stdout


def test_runner_requires_an_expected_checksum(tmp_path):
    run = _run_runner(tmp_path)
    assert run.returncode == 1 and "expected=none" in run.stdout


def test_runner_unsupported_exits_3(tmp_path):
    run = _run_runner(tmp_path, BENCH_RUN_COMMAND="")
    assert run.returncode == 3 and run.stdout.startswith("BENCH_UNSUPPORTED")


# --- Docker E2E (builds the pinned image, downloads hyperfine) ---

docker = pytest.mark.skipif(
    os.environ.get("BENCH_E2E_DOCKER") != "1",
    reason="set BENCH_E2E_DOCKER=1 (needs Docker and network)")


def _driver(impl, n, out):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "run_algorithm.py"), str(CATALOG), impl,
         "--n", str(n), "--out", str(out)], capture_output=True, text=True)


@docker
def test_e2e_correct_implementation_gets_a_timing(tmp_path):
    run = _driver("python.n-body", 1000, tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    doc = json.loads((tmp_path / "result.json").read_text())
    assert doc["correctness"] == "ok" and doc["results"][0]["mean"] > 0


@docker
def test_e2e_wrong_output_fails_without_timing(tmp_path):
    run = _driver("python-wrong.n-body", 1000, tmp_path)
    assert run.returncode == 1
    doc = json.loads((tmp_path / "result.json").read_text())
    assert doc["correctness"] == "failed" and doc["results"] == []
    assert "RESULT_BEGIN" not in (tmp_path / "raw.txt").read_text()
    assert "mean" not in run.stdout


@docker
def test_e2e_cell_without_expected_checksum_fails(tmp_path):
    run = _driver("python.n-body", 2000, tmp_path)
    assert run.returncode == 1
    assert json.loads((tmp_path / "result.json").read_text())["results"] == []
