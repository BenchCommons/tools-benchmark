import json
import pathlib

from toolset.utils.jsonschema_lite import validate

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = ROOT / "tests" / "fixtures" / "catalogs" / "algorithms"


def _schema(name):
    return json.loads((ROOT / "schema" / name).read_text())


def test_catalog_manifest_valid_and_invalid():
    schema = _schema("catalog.schema.json")
    manifest = json.loads((CATALOG / "catalog.json").read_text())
    assert validate(manifest, schema) == []
    manifest["testTypes"] = ["quantum"]
    [msg] = validate(manifest, schema)
    assert msg.startswith("/testTypes/0:")
    del manifest["implementations"]
    assert "/implementations: required" in validate(manifest, schema)


def test_benchmark_config_examples():
    schema = _schema("benchmark_config.schema.json")
    cfg = json.loads((CATALOG / "languages/python/n-body/benchmark_config.json").read_text())
    assert validate(cfg, schema) == []
    http = {"framework": "actix", "tests": [{"default": {"test_type": "http", "port": 8080}}]}
    assert validate(http, schema) == []
    ssg = {"framework": "hugo", "tests": [{"default": {"approach": "Realistic"}}]}
    assert validate(ssg, schema) == []  # test_type absent means datarate
    del cfg["tests"][0]["default"]["run_command"]
    assert any("run_command" in m for m in validate(cfg, schema))
    http["tests"][0]["default"].pop("port")
    assert any("port" in m for m in validate(http, schema))


def test_implementation_base_fields():
    schema = _schema("implementation.base.schema.json")
    meta = json.loads((CATALOG / "languages/python/n-body/meta.json").read_text())
    assert validate(meta, schema) == []
    meta["platforms"] = ["linux/s390x"]
    assert any("platforms" in m for m in validate(meta, schema))
