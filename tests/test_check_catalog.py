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


SSG = ROOT / "tests" / "fixtures" / "catalogs" / "ssg-mini"


@pytest.fixture
def ssg(tmp_path):
    dest = tmp_path / "ssg"
    shutil.copytree(SSG, dest)
    return dest


def test_ssg_fixture_conforms():
    """Version only in the index, and docs/ and tests/ matched by '*/*'."""
    assert check_catalog.check(str(SSG)) == []


def test_ssg_version_in_metadata_only(ssg):
    _edit(ssg / "Go/hugo/generator.json", lambda d: d.update(version="0.167.0"))
    _edit(ssg / "generators.json", lambda d: d["generators"][0].pop("version"))
    lines = check_catalog.check(str(ssg))
    assert [line.split(": ")[1] for line in lines] == ["index"]
    assert "version" in lines[0]


def test_ssg_version_absent_everywhere(ssg):
    _edit(ssg / "generators.json", lambda d: d["generators"][0].pop("version"))
    lines = check_catalog.check(str(ssg))
    assert any(line.startswith("Go/hugo/generator.json: metadata: version") for line in lines)


@pytest.mark.parametrize("mutate,text", [
    (lambda e: e.update(version=""), "version"),
    (lambda e: e.pop("id"), "id"),
    (lambda e: e.update(path=5), "path"),
    (lambda e: e.update(benchmark={"status": "done"}), "benchmark.status"),
    (lambda e: e.pop("benchmark"), "benchmark.status"),
])
def test_ssg_bad_index_entry(ssg, mutate, text):
    _edit(ssg / "generators.json", lambda d: mutate(d["generators"][1]))
    lines = [x for x in check_catalog.check(str(ssg)) if ": index: " in x]
    assert lines and any(text in line for line in lines)


@pytest.mark.parametrize("field", ["schemaVersion", "generatedFrom"])
def test_ssg_index_top_level_fields(ssg, field):
    _edit(ssg / "generators.json", lambda d: d.pop(field))
    [line] = check_catalog.check(str(ssg))
    assert ": index: " in line and field in line


def test_ssg_stray_dir_ignored_but_listed_dir_reports_missing(ssg):
    assert (ssg / "docs/specs").is_dir() and (ssg / "tests/reference").is_dir()
    assert check_catalog.check(str(ssg)) == []
    (ssg / "Python/pelican/pelican.dockerfile").unlink()
    (ssg / "Python/pelican/benchmark_config.json").unlink()
    (ssg / "Python/pelican/generator.json").unlink()
    (ssg / "Python/pelican/build.sh").unlink()
    lines = [x for x in check_catalog.check(str(ssg)) if ": files: " in x]
    assert len(lines) == 4 and all(x.startswith("Python/pelican/") and x.endswith(": missing") for x in lines)


# --- structurally malformed JSON: deterministic diagnostics, never a traceback ---

def _set_file(path, text):
    path.write_text(text)


@pytest.mark.parametrize("value", ['["hugo"]', '{"x": 1}', '7', 'null'])
def test_metadata_id_not_a_string(ssg, value):
    meta = ssg / "Go/hugo/generator.json"
    data = json.loads(meta.read_text())
    meta.write_text(json.dumps(data).replace('"id": "hugo"', f'"id": {value}'))
    lines = check_catalog.check(str(ssg))
    assert any(": ids: id must be a string" in line for line in lines)
    assert lines == check_catalog.check(str(ssg))


@pytest.mark.parametrize("text", ['["not", "an", "object"]', '"s"', '3', 'null'])
def test_metadata_not_an_object(ssg, text):
    _set_file(ssg / "Go/hugo/generator.json", text)
    lines = check_catalog.check(str(ssg))
    assert any(": metadata: /: expected object" in line for line in lines)
    assert lines == check_catalog.check(str(ssg))


@pytest.mark.parametrize("text", ['["x"]', '"s"', '3', 'null'])
def test_index_not_an_object(ssg, text):
    _set_file(ssg / "generators.json", text)
    lines = check_catalog.check(str(ssg))
    assert any(": index: document must be a JSON object" in line for line in lines)
    assert lines == check_catalog.check(str(ssg))


@pytest.mark.parametrize("entry", ['["hugo"]', '"hugo"', '{"id": ["hugo"], "path": ["Go/hugo"], "version": 1}'])
def test_index_entry_malformed(ssg, entry):
    _edit(ssg / "generators.json", lambda d: d["generators"].append(json.loads(entry)))
    lines = check_catalog.check(str(ssg))
    assert any(": index: " in line for line in lines)
    assert lines == check_catalog.check(str(ssg))


def test_index_key_not_a_list(ssg):
    _edit(ssg / "generators.json", lambda d: d.update(generators={"hugo": 1}))
    lines = check_catalog.check(str(ssg))
    assert any("generators: must be a list of entries" in line for line in lines)


def test_malformed_input_cli_has_no_traceback(ssg):
    _set_file(ssg / "generators.json", '["x"]')
    _set_file(ssg / "Go/hugo/generator.json", '["x"]')
    run = subprocess.run([sys.executable, str(ROOT / "tools" / "check_catalog.py"), str(ssg)],
                         capture_output=True, text=True)
    assert run.returncode == 1 and "Traceback" not in run.stderr
