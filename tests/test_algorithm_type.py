import json
import os
import pathlib
import re
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


SHA = r"[0-9a-f]{64}"
EXPECTED_N1000 = "76de83d6d51a74f82828547423f516e7815cfe8cfea6290a2831aa173f806de7"


def _driver(impl, n, out, catalog=CATALOG):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "run_algorithm.py"), str(catalog), impl,
         "--n", str(n), "--out", str(out)], capture_output=True, text=True)


def _lines(out):
    return (out / "raw.txt").read_text().splitlines()


def _assert_no_timing(out, run):
    """A failed cell has no STARTTIME, result block, ENDTIME or timing values."""
    raw = (out / "raw.txt").read_text()
    for text in (raw, run.stdout):
        for token in ("STARTTIME", "ENDTIME", "BENCH_RESULT_BEGIN", "BENCH_RESULT_END"):
            assert token not in text
    doc = json.loads((out / "result.json").read_text())
    assert doc["status"] == "failed" and doc["correctness"] == "failed"
    assert doc["results"] == []
    printed = json.loads(run.stdout)  # the driver prints the doc: no timing keys in it
    assert printed["results"] == [] and '"mean"' not in run.stdout
    assert '"times"' not in run.stdout
    return doc


@docker
def test_e2e_correct_implementation_gets_a_timing(tmp_path):
    run = _driver("python.n-body", 1000, tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    lines = _lines(tmp_path)
    ok = [i for i, line in enumerate(lines) if line.startswith("BENCH_VERIFY_OK ")]
    start = [i for i, line in enumerate(lines) if line.startswith("STARTTIME ")]
    begin = [i for i, line in enumerate(lines) if line.startswith("BENCH_RESULT_BEGIN ")]
    end = [i for i, line in enumerate(lines) if line == "BENCH_RESULT_END"]
    assert len(ok) == len(start) == len(begin) == len(end) == 1
    assert ok[0] < start[0] < begin[0] < end[0]
    assert lines[ok[0]] == f"BENCH_VERIFY_OK algorithm=n-body n=1000 sha256={EXPECTED_N1000}"
    raw = "\n".join(lines)
    assert "BENCH_VERIFY_FAIL" not in raw and "BENCH_VERIFY_FAIL" not in run.stdout
    doc = json.loads((tmp_path / "result.json").read_text())
    assert doc["status"] == "ok" and doc["correctness"] == "ok"
    assert doc["results"][0]["mean"] > 0


@docker
def test_e2e_wrong_output_fails_without_timing(tmp_path):
    run = _driver("python-wrong.n-body", 1000, tmp_path)
    assert run.returncode == 1
    lines = _lines(tmp_path)
    assert len(lines) == 1 and re.fullmatch(
        rf"BENCH_VERIFY_FAIL algorithm=n-body n=1000 sha256 expected={EXPECTED_N1000} got={SHA}",
        lines[0])
    assert lines[0] != f"{lines[0].split('got=')[0]}got={EXPECTED_N1000}"  # got differs
    assert "BENCH_VERIFY_OK" not in "\n".join(lines)
    doc = _assert_no_timing(tmp_path, run)
    assert doc["reason"].startswith("BENCH_VERIFY_FAIL")


@docker
def test_e2e_cell_without_expected_checksum_fails(tmp_path):
    run = _driver("python.n-body", 2000, tmp_path)
    assert run.returncode == 1
    lines = _lines(tmp_path)
    assert len(lines) == 1 and re.fullmatch(
        rf"BENCH_VERIFY_FAIL algorithm=n-body n=2000 sha256 expected=none got={SHA}", lines[0])
    assert "BENCH_VERIFY_OK" not in lines[0]
    doc = _assert_no_timing(tmp_path, run)
    assert "expected=none" in doc["reason"]


@docker
def test_e2e_run_command_crash_fails_without_timing(tmp_path):
    # No new fixture implementation: copy the python one into a temporary
    # catalog whose run command exits 7 (keeps the real fixture catalog and
    # check_catalog.py untouched).
    catalog = tmp_path / "catalog"
    shutil.copytree(CATALOG, catalog)
    src = catalog / "languages" / "python" / "n-body"
    dst = catalog / "languages" / "python-crash" / "n-body"
    dst.parent.mkdir()
    src.rename(dst)
    (dst / "python.n-body.dockerfile").rename(dst / "python-crash.n-body.dockerfile")
    for name in ("meta.json", "benchmark_config.json"):
        path = dst / name
        path.write_text(path.read_text().replace("python.n-body", "python-crash.n-body"))
    cfg = dst / "benchmark_config.json"
    data = json.loads(cfg.read_text())
    data["tests"][0]["default"]["run_command"] = "python3 -c 'import sys; sys.exit(7)'"
    cfg.write_text(json.dumps(data))
    out = tmp_path / "out"
    run = _driver("python-crash.n-body", 1000, out, catalog)
    assert run.returncode == 1, run.stdout + run.stderr
    assert _lines(out) == ["BENCH_VERIFY_FAIL algorithm=n-body n=1000 exit=7"]
    doc = _assert_no_timing(out, run)
    assert doc["reason"] == "BENCH_VERIFY_FAIL algorithm=n-body n=1000 exit=7"
