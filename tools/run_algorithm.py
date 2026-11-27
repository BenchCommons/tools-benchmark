#!/usr/bin/env python3
"""Run one `algorithm` cell of one catalog implementation in Docker (spec 010).

    python3 tools/run_algorithm.py <catalog-dir> <implementation-id> --n <n>
        [--runs 3] [--warmup 1] [--out DIR]

Reference path for the catalog contract, not the benchmarker: it builds the
implementation's pinned image, runs the canonical runner with the BENCH_*
environment, and parses the output with the `algorithm` test type. The
expected checksum comes from <catalog-dir>/algorithms/<algorithm>.json. Writes
raw.txt and result.json to --out. Exit 0 only when correctness is ok and a
timing result exists; a failed or unsupported cell exits 1 and prints no
timing numbers.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toolset.benchmark.test_types import algorithm_type  # noqa: E402


def find_implementation(root, impl_id):
    for path in sorted(glob.glob(os.path.join(root, '*', '*', '*', 'meta.json'))):
        with open(path, encoding='utf-8') as f:
            if json.load(f).get('id') == impl_id:
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
    args = ap.parse_args(argv)

    root = os.path.abspath(args.catalog_dir)
    impl = find_implementation(root, args.implementation)
    with open(os.path.join(impl, 'benchmark_config.json'), encoding='utf-8') as f:
        test = json.load(f)['tests'][0]['default']
    algorithm = test['algorithm']
    cell = {'algorithm': algorithm, 'n': args.n}
    expected = ''
    definition = os.path.join(root, 'algorithms', algorithm + '.json')
    if os.path.isfile(definition):
        with open(definition, encoding='utf-8') as f:
            expected = json.load(f)['cells'].get(str(args.n), {}).get('sha256', '')

    image = f'bench-catalog/{args.implementation}:local'
    build = subprocess.run(
        ['docker', 'build', '-q', '-t', image, '-f',
         os.path.join(impl, args.implementation + '.dockerfile'), impl],
        stdout=subprocess.DEVNULL)
    if build.returncode:
        print(f'build failed for {args.implementation}', file=sys.stderr)
        return 1
    env = algorithm_type.env(cell, test, expected, args.runs, args.warmup)
    cmd = ['docker', 'run', '--rm', '--network', 'none']
    for key, value in env.items():
        cmd += ['-e', f'{key}={value}']
    run = subprocess.run(cmd + [image], capture_output=True, text=True)
    status, reason, results = algorithm_type.parse_output(run.stdout, cell, args.runs)
    doc = {'implementation': args.implementation, 'cell': cell, 'status': status,
           'reason': reason, 'correctness': 'ok' if status == 'ok' else status,
           'results': results}
    out = args.out or os.path.join(root, 'results', args.implementation)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'raw.txt'), 'w', encoding='utf-8') as f:
        f.write(run.stdout)
    with open(os.path.join(out, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
    print(json.dumps(doc))
    return 0 if status == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
