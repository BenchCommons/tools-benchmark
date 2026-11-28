# 010 Catalog Contract: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. The steps are in `tasks.md` and use checkbox (`- [ ]`) syntax for tracking.

**Goal:** write the catalog contract down as JSON schemas and a checker that catalogs run in CI, and make BT select a catalog, load its manifest, accept generic `BENCH_*` markers (with `SSGBERK_*` aliases) and dispatch to per-catalog test types. CS output parses exactly as before.

**Architecture:**
1. **Schemas** in `schema/` (new, repo root): `catalog.schema.json`, `benchmark_config.schema.json`, `implementation.base.schema.json`. Catalogs reference them by raw URL at a BT tag.
2. **Checker** `tools/check_catalog.py`: stdlib only, no Docker, run by catalog CI.
3. **Catalog loader** `toolset/utils/catalog.py`: resolves `--catalog`/`--catalog-dir`, loads and validates `catalog.json` (or the built-in CS default), lists implementations from the glob. `metadata.py` uses it instead of the hard-coded `frameworks/*/*`.
4. **Marker parser:** `results.py` normalizes `SSGBERK_` to `BENCH_` once, then runs the existing BT 002/008 rules unchanged.
5. **Test types:** `algorithm_type.py` next to `datarate_type.py`; `http` is declared (parameters and config keys only) and refused at run time until its own spec.

**Tech stack:** Python 3.12 stdlib and docker-py 7.1.0, as today. JSON Schema draft 2020-12 for the schema files; validation in BT uses the small stdlib validator subset already used by CS `build_index.py` (`type`, `enum`, `required`, `properties`, `additionalProperties`, `items`, `pattern`, `allOf`, `$ref` within the file), so no new dependency.

**Spec:** `docs/specs/010-catalog-contract/spec.md`. Requirement ids (R-n) refer to it.

## Global Constraints

- CS output parses to identical `rawData`, `succeeded`, `failed`, `unsupported`, `failureReasons` and rankings, apart from the new `markerPrefix` and `catalog` keys (goal 1, 2).
- `results.json` stays `schemaVersion: 2`; every new key is optional for readers (R-19, BT 008 R-28).
- No new runtime dependency in the toolset image.
- No change to any catalog repo from this spec.
- `SSGBERK_*` aliases are accepted for all of `contractVersion: 1` (R-16).
- Commits: author and committer `Matheus Breguêz <matbrgz@gmail.com>`, GPG-signed (key `B6FA8458D5176E83`), trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never use `--no-gpg-sign` or `--author`.

## Contracts

### `catalog.json` (R-1)

CS (to be added by CS #203; also BT's built-in default while `frameworks/` exists):

```json
{
  "$schema": "https://raw.githubusercontent.com/BenchCommons/benchmark-tool/<tag>/schema/catalog.schema.json",
  "contractVersion": 1,
  "name": "ssg",
  "testTypes": ["datarate"],
  "implementations": "*/*",
  "runner": {"file": "build.sh", "canonical": "Go/hugo/build.sh"},
  "metadata": {"file": "generator.json", "schema": "schema/generator.schema.json"},
  "index": {"file": "generators.json", "key": "generators", "check": "python3 tools/build_index.py --check"}
}
```

CA (illustrative; CA 011 decides the real paths):

```json
{
  "contractVersion": 1,
  "name": "algorithms",
  "testTypes": ["algorithm"],
  "implementations": "languages/*/*",
  "runner": {"file": "run.sh", "canonical": "tools/run.sh"},
  "metadata": {"file": "meta.json", "schema": "schema/implementation.schema.json"},
  "index": {"file": "implementations.json", "key": "implementations", "check": "python3 tools/build_index.py --check"}
}
```

CW: same shape with `"testTypes": ["http"]`, `"implementations": "frameworks/*/*"` and no `runner`.

`runner.canonical` may sit outside the implementation glob (CA `tools/run.sh`); then every implementation's copy is compared with it and none is the reference.

### Runner environment (R-7)

| Variable | Test types | Meaning | Legacy name (still set for `datarate`) |
|---|---|---|---|
| `BENCH_TEST_TYPE` | all | `datarate`, `algorithm` | — |
| `BENCH_RUNS` | all | timed runs | `min_runs` |
| `BENCH_WARMUP` | all | untimed warmup runs before timing | — |
| `BENCH_VERBOSE` | all | `true`/`false` | `verbose_build` |
| `BENCH_NUMBER_OF_FILES` | `datarate` | cell `numberOfFiles` | `number_of_files` |
| `BENCH_CONTENT_SIZE` | `datarate` | cell `contentSize` | `content_size` |
| `BENCH_ALGORITHM` | `algorithm` | algorithm id | — |
| `BENCH_N` | `algorithm` | input size | — |
| `BENCH_DATASET` | `algorithm` | `<name>@<version>` or empty | — |
| `BENCH_RUN_COMMAND` | `algorithm` | the config's `run_command`; empty means the cell is unsupported | — |
| `BENCH_BUILD_COMMAND` | `algorithm` | the config's `build_command` (untimed), may be empty | — |
| `BENCH_EXPECTED_SHA256` | `algorithm` | expected SHA-256 of stdout for `(algorithm, n)`, from the catalog's algorithm definition; empty fails verification | — |

The runner's build context is the implementation directory, so it cannot read the catalog's algorithm definition or `benchmark_config.json` itself. BT reads both and passes the values above (added during implementation; the first draft of this table lacked them).

Profiles (`profile`, BT 008) and `KEEP_CONTENT`/`SSGBERK_GENERATE_ONLY` stay CS-specific and unchanged.

### `benchmark_config.json` test entry (R-8, R-12)

`algorithm` (CA):

```json
{
  "framework": "nbody-python",
  "tests": [{
    "default": {
      "test_type": "algorithm",
      "approach": "Realistic", "classification": "Micro", "framework": "nbody-python",
      "language": "Python", "display_name": "n-body (CPython)", "notes": "", "versus": "c",
      "algorithm": "nbody",
      "build_command": "",
      "run_command": "python3 nbody.py ${BENCH_N}"
    }
  }]
}
```

`http` (CW, TechEmpower keys kept as they are so imported configs need no rewrite):

```json
{
  "framework": "actix",
  "tests": [{
    "default": {
      "test_type": "http",
      "approach": "Realistic", "classification": "Micro", "framework": "actix",
      "language": "Rust", "display_name": "actix", "notes": "", "versus": "",
      "port": 8080, "json_url": "/json", "plaintext_url": "/plaintext"
    }
  }]
}
```

### Markers (R-15, R-16)

Parser normalization, applied once to the raw text before every existing regex:

```python
_PREFIX = re.compile(r'^([ \t]*)(?:BENCH|SSGBERK)_', re.M)   # same line start as _BOL
text = _PREFIX.sub(r'\1BENCH_', raw)
text = re.sub(r'^([ \t]*)BENCH_PROFILE_UNSUPPORTED\b', r'\1BENCH_UNSUPPORTED', text, flags=re.M)
```

`markerPrefix` is `SSGBERK` if any line starts with `SSGBERK_`, else `BENCH` (mixed output is legal and counts as `SSGBERK`). The `BENCH_RESULT_BEGIN` line's optional `engine=`/`version=` pairs are stored as `engine` and `engineVersion` in the result; missing means `hyperfine` and `null`.

### `suites.json` (R-18)

New optional keys per suite: `"catalog": "ssg"`, `"testType": "datarate"`. An `algorithm` suite looks like:

```json
"algo-smoke": {
  "catalog": "algorithms", "testType": "algorithm",
  "version": 1, "runs": 3, "cooldownSeconds": 0, "timeoutSeconds": 600,
  "cells": [{"algorithm": "nbody", "n": 1000}, {"algorithm": "nbody", "n": 50000}]
}
```

Cell keys are validated against the test type's declared parameters.

### `results.json` additions (R-19)

```json
{
  "schemaVersion": 2,
  "catalog": {"name": "ssg", "commit": "327bd5e", "contractVersion": 1},
  "rawData": {"datarate": {"hugo": [{"...": "...", "markerPrefix": "SSGBERK", "engine": "hyperfine", "engineVersion": null}]}}
}
```

`rawData.algorithm.<id>[0]` has the same timing keys as `datarate` (`mean`, `stddev`, `median`, `min`, `max`, `times`, `user`, `system`, `memoryUsageBytes`, `peakRssBytes`) plus `algorithm` and `n` instead of `numberOfFiles` and `contentSize`.

## Decisions

1. **A manifest file, not a convention.** Layouts already differ (`*/*`, `languages/*/*`, `frameworks/*/*`) and the runner and metadata file names differ. One small `catalog.json` states them; BT keeps a built-in CS manifest only until CS adds its own (spec, Migration step 5).
2. **Byte-identical runner copies, not a shared file.** The Docker build context is the implementation directory (R-3), so a shared script cannot be `COPY`ed without widening every context. CS already works this way and checks it with `cmp`.
3. **Prefix `BENCH_`.** Short, catalog-neutral, and not an org name that may change again. `SSGBERK_` stays as an alias for the whole `contractVersion: 1`.
4. **`http` has no runner.** A server cannot time itself; the load generator must run in another container, as in TechEmpower. Verification for `http` is BT's job, so the same `VERIFY_*` markers are written by BT and parsed by the same code.
5. **One catalog per run.** Results, suites and rankings are per catalog; mixing them in one `results.json` adds keys for no current use. `merge_results` refuses inputs from different catalogs.
6. **`schemaVersion` stays 2.** The additions are optional keys. Engine-driven changes (energy, custom metrics) are the reason for v3 and belong to BT #30.
7. **Submodules under `catalogs/<name>/`.** One directory per catalog, pinned like `frameworks/` is today. `--catalog-dir` covers local development (it replaces mounting a worktree at `frameworks/`).

## Review Focus

1. **Alias normalization changes a CS result.** The rewrite must touch only line-initial `SSGBERK_`, never JSON content between the result markers (a generator name or path containing `SSGBERK_` inside the hyperfine JSON command string). Pinned in Task 3 (`test_alias_inside_json_untouched`).
2. **`FWROOT/frameworks` is assumed in several places** (`benchmark_config.py` `lang_root`, `metadata.py`, `audit.py`, `github_actions_diff.py`, `scaffolding.py`, `run-tests.py`). Task 4 replaces every path use with `catalog.root`; afterwards `grep -rn "frameworks" toolset` shows no path built from it except the built-in default manifest (the word may remain in log text and results keys such as `frameworks` lists).
3. **Glob depth differs per catalog.** `metadata.gather_tests` and `--test-lang` assume `<Lang>/<id>`. For `languages/*/*` the language is the parent path segment and the algorithm the leaf (CA spec 011); language-level metadata lives in `languages/<lang>/language.json`. Pinned in Task 4 (`test_gather_tests_algorithms_layout`).
4. **Checker false positives on CS today.** `check_catalog.py` must pass on the current CS default branch with only `catalog.json` added (goal 1). Task 2 runs it against a CS checkout in CI.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `schema/catalog.schema.json`, `schema/benchmark_config.schema.json`, `schema/implementation.base.schema.json` | the contract as schemas (R-1, R-8, R-10) | 1 |
| `toolset/utils/jsonschema_lite.py` | stdlib validator subset | 1 |
| `tools/check_catalog.py`, `tests/test_check_catalog.py`, `tests/fixtures/catalogs/**` | conformance checker (R-20) | 2 |
| `toolset/utils/results.py`, `tests/test_results.py` | alias normalization, `markerPrefix`, `engine` (R-15, R-16) | 3 |
| `toolset/utils/catalog.py`, `toolset/utils/benchmark_config.py`, `toolset/utils/metadata.py`, `toolset/utils/docker_helper.py`, `toolset/run-tests.py`, `tests/test_catalog.py` | `--catalog`, `--catalog-dir`, manifest loading, implementation discovery (R-2, R-11, R-17) | 4 |
| `toolset/benchmark/test_types/algorithm_type.py`, `toolset/benchmark/test_types/http_type.py`, `toolset/benchmark/suites.py`, `toolset/benchmark/suites.json`, `tests/test_algorithm_type.py` | test types and suites (R-12–R-14, R-18) | 5 |
| `toolset/utils/results.py`, `toolset/utils/merge_results.py` | `catalog` key, merge refusal across catalogs (R-19) | 6 |
| `.gitmodules`, `catalogs/ssg`, `README.md`, `.github/workflows/ci.yml` | submodule, docs, CI (Migration step 2) | 7 |

## Status and blockers

Delivered (branch `matbrgz/catalog-contract-e2e`):

| Task | State |
|---|---|
| 1 Schemas and validator | done: `schema/*.json`, `toolset/utils/jsonschema_lite.py` (adds `if`/`then`, `const`, `minimum`, `minItems`, `minLength` to the planned subset) |
| 2 `tools/check_catalog.py` | done; CI runs it on the algorithms fixture. Checked against the real CS default branch plus a `catalog.json`: exit 0 (before: 63 `version: required` and 28 `files: missing` for `docs/` and `tests/`). `version` may come from the metadata or the index entry (R-10, R-11); glob matches with no implementation files and no index entry are skipped; index entries are validated (R-11); malformed JSON types (list/scalar for id, metadata or index) give diagnostics, not a traceback. Fixture: `tests/fixtures/catalogs/ssg-mini/`. Not done: the `catalog-ssg-contract` CI job in CS The catalog's own metadata schema is not applied, only the base fields |
| 3 Generic markers | done, with one change: the parser matches `(BENCH\|SSGBERK)_` in each pattern instead of rewriting the text, so failure reasons keep the original spelling and CS output is unchanged. Results carry `markerPrefix`, `engine`, `engineVersion` |
| 4 Catalog selection | not done (`--catalog`, `--catalog-dir`, loader, discovery by glob) |
| 5 Test types and suites | partly: `algorithm_type.py` (`env`, `parse_output`) and recorded-output tests. Not done: `http_type.py`, `suites.json` changes, `docker_helper` env, registering the type in `Benchmarker`/`Results` |
| 6 `catalog` in `results.json` | not done |
| 7 Submodule and docs | README section only; no `catalogs/ssg` submodule |

End-to-end demonstration: `tools/run_algorithm.py` builds the pinned image of a fixture implementation (`tests/fixtures/catalogs/algorithms`, n-body in CPython, `n=1000`), runs the canonical runner, and parses the output with the `algorithm` type. The expected checksum for `n=1000` is the independent reference value from catalog-algorithms spec 010 (R4). Correct output gives a hyperfine timing. A second fixture implementation with a deliberate numeric error (`python-wrong`) and a cell without an expected checksum (`n=2000`) both end as `failed` with a `BENCH_VERIFY_FAIL` reason, no `STARTTIME`, no result block and no timing. `tools/run_algorithm.py` is a reference driver, not the benchmarker. CI job `catalog-algorithms-e2e` runs it with Docker.

Known limits:

- The runner compares the SHA-256 of raw stdout. CA spec 010 R2 also normalises CRLF to LF for text outputs; that is not done here.
- The fixture covers one algorithm and one checked `n`. No other CA algorithm definition is copied here (CA owns them).
- Fixture images are checked on the host architecture only (arm64 locally); the Dockerfile carries both hyperfine digests. The CI job `catalog-algorithms-e2e` runs on `ubuntu-24.04` (amd64) only, so the arm64 pin is not built in CI (BT #46). `tools/run_algorithm.py` has `--build-timeout` and `--run-timeout` (default 1800 s). It exits 1 on a non-zero container exit, a result block whose `mean` or `times` is missing or not finite, whose `times` count differs from `--runs`, or whose other numeric fields are not finite, and on a timeout. A `docker run` timeout also runs `docker kill` on the named container; a `docker build` timeout stops only the client, so the daemon may finish the build. A malformed algorithm definition or metadata file exits with a message naming the file. The timeout and kill behaviour is covered with Docker by `test_e2e_run_timeout_fails_and_stops_the_container`.

Dependencies and blockers (all still open when this was written):

- [tools-dataset#1](https://github.com/BenchCommons/tools-dataset/issues/1) (input contract, formerly `dataset-tool#1`) and `dataset-names#24`: `merge-sort`, `binary-search` and `sha-256` need name samples. `BENCH_DATASET` and the cell parameter `dataset` are declared but nothing reads them. No input format is defined here. These algorithms are blocked until that contract exists.
- [perfratio#240](https://github.com/BenchCommons/perfratio/issues/240) and BT #30: the only engine is hyperfine. The `engine=`/`version=` header on `BENCH_RESULT_BEGIN` is parsed, but no perfratio output schema is read, and `results.json` stays at `schemaVersion: 2`.
- CA specs 011 to 013 (layout, runner, measurement): CA has not adopted this contract. Its `meta.json` and `run.sh` differ from the illustrative manifest above, and `catalog-algorithms` is unchanged by this work.
