"""The `algorithm` test type (spec 010 R-12 to R-14).

One cell is (algorithm, n). The runner verifies the program's output
against the expected SHA-256 before it times anything, and prints BENCH_*
markers that `parse_output` turns into a result. A failed or missing
verification never yields timing numbers.

Not yet wired into Benchmarker or Results: `benchmark_config.py` registers
only `datarate`. See docs/specs/010-catalog-contract/plan.md (Status).
"""
from toolset.benchmark.test_types.framework_test_type import FrameworkTestType
from toolset.utils.results import classify_build_output, parse_build_output

PARAMETERS = {'algorithm': str, 'n': int, 'dataset': str}
CONFIG_KEYS = ['run_command']
# Timing keys of a result, as for datarate (plan.md, results.json additions).
_TIMING_KEYS = ('mean', 'stddev', 'median', 'min', 'max', 'times', 'user', 'system',
                'memoryUsageBytes', 'peakRssBytes', 'startTime', 'endTime',
                'markerPrefix', 'engine', 'engineVersion')


def env(cell, test, expected_sha256, runs=3, warmup=1, verbose=False):
    """Environment the runner reads (plan.md, Runner environment).

    `test` is the benchmark_config.json test entry. `expected_sha256` is the
    checksum for (algorithm, n) from the catalog's algorithm definition; an
    empty value makes the runner fail verification.
    """
    return {
        'BENCH_TEST_TYPE': 'algorithm',
        'BENCH_RUNS': str(runs),
        'BENCH_WARMUP': str(warmup),
        'BENCH_VERBOSE': 'true' if verbose else 'false',
        'BENCH_ALGORITHM': str(cell['algorithm']),
        'BENCH_N': str(cell['n']),
        'BENCH_DATASET': str(cell.get('dataset', '')),
        'BENCH_RUN_COMMAND': test.get('run_command', ''),
        'BENCH_BUILD_COMMAND': test.get('build_command', ''),
        'BENCH_EXPECTED_SHA256': expected_sha256 or '',
    }


def parse_output(raw, cell, runs=3):
    """(status, reason, results) for one runner output.

    status is ok, failed or unsupported (classify_build_output). results has
    one entry when ok and is empty otherwise: a failure carries no timing.
    """
    status, reason = classify_build_output(raw)
    if status != 'ok':
        return status, reason, []
    parsed = parse_build_output(raw, None, None, runs)
    if not parsed:
        return 'failed', 'protocol: no parseable result block', []
    result = {k: parsed[0].get(k) for k in _TIMING_KEYS}
    result.update(algorithm=cell['algorithm'], n=cell['n'], minRuns=runs, status='ok',
                  correctness='ok')
    return 'ok', None, [result]


class AlgorithmTestType(FrameworkTestType):
    def __init__(self, config):
        FrameworkTestType.__init__(self, config, name='algorithm', args=CONFIG_KEYS)

    def get_script_name(self):
        return 'run.sh'

    def get_script_variables(self):
        raise NotImplementedError(
            'algorithm: cell parameters come from a suite; use '
            'toolset.benchmark.test_types.algorithm_type.env')
