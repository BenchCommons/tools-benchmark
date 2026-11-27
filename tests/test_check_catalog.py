import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_catalog  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "catalogs" / "algorithms"
IMPL = "languages/python/n-body"


@pytest.fixture
def catalog(tmp_path):
    dest = tmp_path / "catalog"
    shutil.copytree(FIXTURE, dest)
    return dest


def _rules(root):
    return [line.split(": ")[1] for line in check_catalog.check(str(root))]


def _edit(path, fn):
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


def test_fixture_conforms():
    assert check_catalog.check(str(FIXTURE)) == []


def test_cli_exit_codes(catalog):
    cmd = [sys.executable, str(ROOT / "tools" / "check_catalog.py"), str(catalog)]
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    (catalog / "catalog.json").unlink()
    run = subprocess.run(cmd, capture_output=True, text=True)
    assert run.returncode == 1 and "manifest" in run.stdout


def test_manifest_missing(catalog):
    (catalog / "catalog.json").unlink()
    assert _rules(catalog) == ["manifest"]


def test_manifest_invalid_field(catalog):
    _edit(catalog / "catalog.json", lambda d: d.update(contractVersion="1"))
    [line] = check_catalog.check(str(catalog))
    assert "/contractVersion" in line and "manifest" in line


def test_manifest_unsupported_version_names_both(catalog):
    _edit(catalog / "catalog.json", lambda d: d.update(contractVersion=2))
    [line] = check_catalog.check(str(catalog))
    assert "2" in line and "[1, 1]" in line


def test_files_missing_dockerfile(catalog):
    (catalog / IMPL / "python.n-body.dockerfile").unlink()
    assert _rules(catalog) == ["files"]


def test_runner_differs_by_one_byte(catalog):
    with open(catalog / IMPL / "run.sh", "ab") as f:
        f.write(b"\n")
    assert _rules(catalog) == ["runner"]


@pytest.mark.parametrize("old,new", [
    ("python:3.12.11-slim-bookworm", "python"),
    ("python:3.12.11-slim-bookworm", "python:latest"),
])
def test_dockerfile_unpinned_base(catalog, old, new):
    path = catalog / IMPL / "python.n-body.dockerfile"
    path.write_text(path.read_text().replace(old, new, 1))
    assert _rules(catalog) == ["dockerfile"]


def test_dockerfile_download_without_version_arg(catalog):
    path = catalog / IMPL / "python.n-body.dockerfile"
    text = "\n".join(line for line in path.read_text().splitlines()
                     if not line.startswith("ARG HYPERFINE_VERSION"))
    path.write_text(text + "\n")
    assert _rules(catalog) == ["dockerfile"]


def test_config_algorithm_without_run_command(catalog):
    def drop(d):
        del d["tests"][0]["default"]["run_command"]
    _edit(catalog / IMPL / "benchmark_config.json", drop)
    [line] = check_catalog.check(str(catalog))
    assert "config" in line and "run_command" in line


def test_metadata_id_must_match_directories(catalog):
    _edit(catalog / IMPL / "meta.json", lambda d: d.update(id="python.merge-sort"))
    assert "ids" in _rules(catalog)


def test_metadata_invalid_status(catalog):
    _edit(catalog / IMPL / "meta.json", lambda d: d["benchmark"].update(status="done"))
    assert _rules(catalog) == ["metadata"]


def test_duplicate_id(catalog):
    shutil.copytree(catalog / IMPL, catalog / "languages" / "python" / "n-body-copy")
    assert "ids" in _rules(catalog)


def test_index_check_fails(catalog):
    _edit(catalog / "catalog.json",
          lambda d: d["index"].update(check="python3 -c 'import sys; sys.exit(1)'"))
    assert _rules(catalog) == ["index"]


def test_index_lists_missing_implementation(catalog):
    _edit(catalog / "implementations.json", lambda d: d["implementations"].pop())
    assert "index" in _rules(catalog)
