import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_algorithm  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
CATALOG = FIX / "catalogs" / "algorithms"
OK_RAW = (FIX / "raw_algorithm_ok.txt").read_text()
UNSUPPORTED_RAW = (FIX / "raw_algorithm_unsupported.txt").read_text()
IMPL = "python.n-body"


def _fake_run(monkeypatch, run_result=None, build_rc=0, timeout_on=None):
    calls = []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        kind = cmd[1]
        if kind == timeout_on:
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        if kind == "build":
            return SimpleNamespace(returncode=build_rc)
        return run_result

    monkeypatch.setattr(run_algorithm.subprocess, "run", fake)
    return calls


def _drive(tmp_path, *extra, n="1000"):
    rc = run_algorithm.main([str(CATALOG), IMPL, "--n", n, "--out", str(tmp_path), *extra])
    return rc, json.loads((tmp_path / "result.json").read_text())


def test_ok_run_exits_0(monkeypatch, tmp_path):
    _fake_run(monkeypatch, SimpleNamespace(returncode=0, stdout=OK_RAW))
    rc, doc = _drive(tmp_path)
    assert rc == 0 and doc["status"] == "ok" and doc["results"][0]["mean"] > 0


def test_nonzero_container_exit_fails_despite_complete_block(monkeypatch, tmp_path):
    _fake_run(monkeypatch, SimpleNamespace(returncode=2, stdout=OK_RAW))
    rc, doc = _drive(tmp_path)
    assert rc == 1
    assert doc["status"] == "failed" and doc["results"] == []
    assert doc["reason"] == "container exit code 2"


def test_unsupported_marker_keeps_unsupported_status(monkeypatch, tmp_path):
    _fake_run(monkeypatch, SimpleNamespace(returncode=3, stdout=UNSUPPORTED_RAW))
    rc, doc = _drive(tmp_path)
    assert rc == 1 and doc["status"] == "unsupported" and doc["results"] == []


def test_default_timeouts_are_passed(monkeypatch, tmp_path):
    calls = _fake_run(monkeypatch, SimpleNamespace(returncode=0, stdout=OK_RAW))
    _drive(tmp_path)
    assert [kw["timeout"] for _, kw in calls] == [1800, 1800]


def test_custom_timeouts_are_passed(monkeypatch, tmp_path):
    calls = _fake_run(monkeypatch, SimpleNamespace(returncode=0, stdout=OK_RAW))
    _drive(tmp_path, "--build-timeout", "5", "--run-timeout", "7")
    assert [kw["timeout"] for _, kw in calls] == [5, 7]


@pytest.mark.parametrize("phase", ["build", "run"])
def test_timeout_fails_and_writes_result(monkeypatch, tmp_path, phase):
    _fake_run(monkeypatch, SimpleNamespace(returncode=0, stdout=OK_RAW), timeout_on=phase)
    rc, doc = _drive(tmp_path)
    assert rc == 1
    assert doc["status"] == "failed" and doc["reason"] == "timeout" and doc["results"] == []


def test_build_failure_exits_1(monkeypatch, tmp_path):
    _fake_run(monkeypatch, build_rc=1)
    rc = run_algorithm.main([str(CATALOG), IMPL, "--n", "1000", "--out", str(tmp_path)])
    assert rc == 1


# --- validation (never reaches docker) ---

@pytest.fixture
def no_docker(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("docker must not run")

    monkeypatch.setattr(run_algorithm.subprocess, "run", boom)


GOOD = {"tests": [{"default": {"algorithm": "n-body", "run_command": "x"}}]}


def _with(**changes):
    cfg = json.loads(json.dumps(GOOD))
    cfg["tests"][0]["default"].update(changes)
    return cfg


def test_validate_config_accepts_good():
    assert run_algorithm.validate_config(GOOD)["algorithm"] == "n-body"


@pytest.mark.parametrize("cfg,msg", [
    ({}, "tests"),
    ({"tests": []}, "tests"),
    ({"tests": "x"}, "tests"),
    ({"tests": [{}]}, "default"),
    ({"tests": [{"default": []}]}, "default"),
    (_with(algorithm="N-Body"), "algorithm"),
    (_with(algorithm="-x"), "algorithm"),
    (_with(algorithm="a/../b"), "algorithm"),
    (_with(algorithm="a\nBENCH_VERIFY_OK"), "algorithm"),
    (_with(algorithm=5), "algorithm"),
    (_with(run_command=""), "run_command"),
    (_with(run_command="  "), "run_command"),
    (_with(run_command=["x"]), "run_command"),
])
def test_validate_config_rejects(cfg, msg):
    with pytest.raises(SystemExit) as exc:
        run_algorithm.validate_config(cfg)
    assert msg in str(exc.value)


def test_driver_rejects_invalid_config_before_docker(no_docker, tmp_path):
    cat = tmp_path / "cat"
    (cat / "languages" / "l" / "i").mkdir(parents=True)
    impl = cat / "languages" / "l" / "i"
    (impl / "meta.json").write_text(json.dumps({"id": "l.i"}))
    (impl / "benchmark_config.json").write_text(json.dumps(_with(algorithm="../x")))
    with pytest.raises(SystemExit, match="algorithm"):
        run_algorithm.main([str(cat), "l.i", "--n", "10", "--out", str(tmp_path / "o")])


@pytest.mark.parametrize("argv,msg", [
    (["--n", "0"], "--n"),
    (["--n", "-5"], "--n"),
    (["--n", "10", "--runs", "0"], "--runs"),
    (["--n", "10", "--warmup", "-1"], "--warmup"),
    (["--n", "10", "--build-timeout", "0"], "--build-timeout"),
    (["--n", "10", "--run-timeout", "-1"], "--run-timeout"),
    (["--n", "10", "--build-timeout", "nan"], "--build-timeout"),
    (["--n", "10", "--build-timeout", "inf"], "--build-timeout"),
    (["--n", "10", "--run-timeout", "nan"], "--run-timeout"),
    (["--n", "10", "--run-timeout", "Infinity"], "--run-timeout"),
])
def test_invalid_numeric_args(no_docker, tmp_path, argv, msg):
    with pytest.raises(SystemExit, match=msg):
        run_algorithm.main([str(CATALOG), IMPL, *argv, "--out", str(tmp_path)])


@pytest.mark.parametrize("impl", ["../x", "a b", "", "a;b"])
def test_invalid_implementation_id(no_docker, tmp_path, impl):
    with pytest.raises(SystemExit, match="implementation id"):
        run_algorithm.main([str(CATALOG), impl, "--n", "10", "--out", str(tmp_path)])


def test_unknown_implementation(no_docker, tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        run_algorithm.main([str(CATALOG), "nope.x", "--n", "10", "--out", str(tmp_path)])


def test_run_timeout_kills_the_named_container(monkeypatch, tmp_path):
    calls = _fake_run(monkeypatch, SimpleNamespace(returncode=0, stdout=OK_RAW),
                      timeout_on="run")
    rc, doc = _drive(tmp_path)
    run_cmd = next(c for c, _ in calls if c[1] == "run")
    name = run_cmd[run_cmd.index("--name") + 1]
    assert ["docker", "kill", name] in [c for c, _ in calls]
    assert rc == 1 and doc["reason"] == "timeout"
    assert (tmp_path / "raw.txt").read_text() == ""


def test_build_timeout_does_not_start_a_container(monkeypatch, tmp_path):
    calls = _fake_run(monkeypatch, SimpleNamespace(returncode=0, stdout=OK_RAW),
                      timeout_on="build")
    _drive(tmp_path)
    assert [c[1] for c, _ in calls] == ["build"]


def _definition_catalog(tmp_path, text):
    import shutil
    dest = tmp_path / "cat"
    shutil.copytree(CATALOG, dest)
    (dest / "algorithms" / "n-body.json").write_text(text)
    return dest


@pytest.mark.parametrize("text", [
    "{}", "[]", "not json", '{"cells": []}', '{"cells": {"1000": 5}}',
    '{"cells": {"1000": {"sha256": 7}}}',
])
def test_malformed_algorithm_definition_exits_with_message(no_docker, tmp_path, text):
    catalog = _definition_catalog(tmp_path, text)
    with pytest.raises(SystemExit) as e:
        run_algorithm.main([str(catalog), IMPL, "--n", "1000", "--out", str(tmp_path / "o")])
    assert "n-body.json" in str(e.value)


def test_definition_without_the_cell_still_runs_and_fails_verification(monkeypatch, tmp_path):
    catalog = _definition_catalog(tmp_path, '{"cells": {}}')
    _fake_run(monkeypatch, SimpleNamespace(returncode=1, stdout="BENCH_VERIFY_FAIL x\n"))
    rc = run_algorithm.main([str(catalog), IMPL, "--n", "1000", "--out", str(tmp_path / "o")])
    assert rc == 1


def test_malformed_meta_json_exits_with_message(no_docker, tmp_path):
    catalog = _definition_catalog(tmp_path, '{"cells": {}}')
    (catalog / "languages" / "python" / "n-body" / "meta.json").write_text("{")
    with pytest.raises(SystemExit, match="meta.json"):
        run_algorithm.main([str(catalog), IMPL, "--n", "1000", "--out", str(tmp_path / "o")])
