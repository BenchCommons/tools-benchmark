#!/usr/bin/env python3
"""Run one `algorithm` cell of one catalog implementation in Docker (spec 010).

    python3 tools/run_algorithm.py <catalog-dir> <implementation-id> --n <n>
        [--runs 3] [--warmup 1] [--out DIR]
        [--build-timeout 1800] [--run-timeout 1800]

Reference path for the catalog contract, not the benchmarker: it builds the
implementation's pinned image, runs the canonical runner with the BENCH_*
environment, and parses the output with the `algorithm` test type. The
expected checksum comes from <catalog-dir>/algorithms/<algorithm>.json. Writes
raw.txt and result.json to --out. Exit 0 only when correctness is ok and a
timing result exists and the container exited 0; a failed or unsupported cell
(including a build or run timeout, or a non-zero container exit after an
otherwise complete result; unsupported is signalled by its marker) exits 1 and prints no timing numbers.
"""
import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toolset.benchmark.test_types import algorithm_type  # noqa: E402

ALGORITHM_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')
IMPL_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def validate_config(config, where='benchmark_config.json'):
    """Return the first test's `default` dict, or exit with a clear message."""
    tests = config.get('tests') if isinstance(config, dict) else None
    if not isinstance(tests, list) or not tests:
        raise SystemExit(f'{where}: "tests" must be a non-empty list')
    default = tests[0].get('default') if isinstance(tests[0], dict) else None
    if not isinstance(default, dict):
        raise SystemExit(f'{where}: tests[0].default must be an object')
    algorithm = default.get('algorithm')
    if not isinstance(algorithm, str) or not ALGORITHM_RE.match(algorithm):
        raise SystemExit(
            f'{where}: algorithm {algorithm!r} must match {ALGORITHM_RE.pattern}')
    command = default.get('run_command')
    if not isinstance(command, str) or not command.strip():
        raise SystemExit(f'{where}: run_command must be a non-empty string')
    return default


def validate_args(args):
    if not IMPL_ID_RE.match(args.implementation):
        raise SystemExit(f'invalid implementation id {args.implementation!r}')
    if args.n < 1:
        raise SystemExit(f'--n must be a positive integer, got {args.n}')
    if args.runs < 1:
        raise SystemExit(f'--runs must be at least 1, got {args.runs}')
    if args.warmup < 0:
        raise SystemExit(f'--warmup must be 0 or more, got {args.warmup}')
    for name in ('build_timeout', 'run_timeout'):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            raise SystemExit(f'--{name.replace("_", "-")} must be a finite positive number')


def _load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f'{path}: cannot read JSON: {e}')


def expected_checksum(path, n):
    """Expected SHA-256 for cell `n` from an algorithm definition, '' if the
    cell is absent (the runner then fails verification). Malformed definitions
    exit with a message."""
    doc = _load_json(path)
    cells = doc.get('cells') if isinstance(doc, dict) else None
    if not isinstance(cells, dict):
        raise SystemExit(f'{path}: "cells" must be an object keyed by n')
    cell = cells.get(str(n), {})
    if not isinstance(cell, dict):
        raise SystemExit(f'{path}: cells[{n!r}] must be an object')
    sha = cell.get('sha256', '')
    if not isinstance(sha, str):
        raise SystemExit(f'{path}: cells[{n!r}].sha256 must be a string')
    return sha


def kill_container(name):
    """Stop a container whose `docker run` client was killed by a timeout."""
    try:
        subprocess.run(['docker', 'kill', name], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        print(f'could not kill container {name}', file=sys.stderr)


def find_implementation(root, impl_id):
    for path in sorted(glob.glob(os.path.join(root, '*', '*', '*', 'meta.json'))):
        meta = _load_json(path)
        if isinstance(meta, dict) and meta.get('id') == impl_id:
            return os.path.dirname(path)
    raise SystemExit(f'implementation {impl_id!r} not found in {root}')


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('catalog_dir')
    ap.add_argument('implementation')
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--warmup', type=int, default=1)
    ap.add_argument('--out', default=None)
    ap.add_argument('--build-timeout', type=float, default=1800,
                    help='seconds allowed for docker build (default 1800)')
    ap.add_argument('--run-timeout', type=float, default=1800,
                    help='seconds allowed for docker run (default 1800)')
    args = ap.parse_args(argv)
    validate_args(args)

    root = os.path.abspath(args.catalog_dir)
    impl = find_implementation(root, args.implementation)
    config_path = os.path.join(impl, 'benchmark_config.json')
    test = validate_config(_load_json(config_path), config_path)
    algorithm = test['algorithm']
    cell = {'algorithm': algorithm, 'n': args.n}
    expected = ''
    definition = os.path.join(root, 'algorithms', algorithm + '.json')
    if os.path.isfile(definition):
        expected = expected_checksum(definition, args.n)

    image = f'bench-catalog/{args.implementation}:local'
    out = args.out or os.path.join(root, 'results', args.implementation)
    os.makedirs(out, exist_ok=True)
    stdout = ''
    try:
        build = subprocess.run(
            ['docker', 'build', '-q', '-t', image, '-f',
             os.path.join(impl, args.implementation + '.dockerfile'), impl],
            stdout=subprocess.DEVNULL, timeout=args.build_timeout)
    except subprocess.TimeoutExpired:
        status, reason, results = 'failed', 'timeout', []
        print(f'build timed out for {args.implementation}', file=sys.stderr)
    else:
        if build.returncode:
            print(f'build failed for {args.implementation}', file=sys.stderr)
            return 1
        env = algorithm_type.env(cell, test, expected, args.runs, args.warmup)
        name = f'bench-run-{uuid.uuid4().hex[:12]}'
        cmd = ['docker', 'run', '--rm', '--name', name, '--network', 'none']
        for key, value in env.items():
            cmd += ['-e', f'{key}={value}']
        try:
            run = subprocess.run(cmd + [image], capture_output=True, text=True,
                                 timeout=args.run_timeout)
        except subprocess.TimeoutExpired:
            kill_container(name)
            status, reason, results = 'failed', 'timeout', []
            print(f'run timed out for {args.implementation}', file=sys.stderr)
        else:
            stdout = run.stdout
            status, reason, results = algorithm_type.parse_output(
                stdout, cell, args.runs)
            if status == 'ok' and run.returncode != 0:
                status, reason, results = (
                    'failed', f'container exit code {run.returncode}', [])
    doc = {'implementation': args.implementation, 'cell': cell, 'status': status,
           'reason': reason, 'correctness': 'ok' if status == 'ok' else status,
           'results': results}
    with open(os.path.join(out, 'raw.txt'), 'w', encoding='utf-8') as f:
        f.write(stdout)
    with open(os.path.join(out, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
    print(json.dumps(doc))
    return 0 if status == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
