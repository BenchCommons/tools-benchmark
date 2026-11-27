#!/bin/sh
# Canonical runner (catalog contract, spec 010 R-5). Byte-identical in every
# implementation; behaviour comes from the BENCH_* environment set by the
# toolset. Order: untimed build, untimed verification against the expected
# SHA-256, then STARTTIME, the timed runs and the result block.
set -u

algorithm=${BENCH_ALGORITHM:-}
n=${BENCH_N:-}
runs=${BENCH_RUNS:-3}
warmup=${BENCH_WARMUP:-1}

if [ -z "${BENCH_RUN_COMMAND:-}" ]; then
  echo "BENCH_UNSUPPORTED no run_command for $algorithm"
  exit 3
fi

if [ -n "${BENCH_BUILD_COMMAND:-}" ] && ! sh -c "$BENCH_BUILD_COMMAND" >&2; then
  echo "BENCH_VERIFY_FAIL algorithm=$algorithm n=$n build failed"
  exit 1
fi

out=$(mktemp)
sh -c "$BENCH_RUN_COMMAND" >"$out" 2>/dev/null
status=$?
if [ "$status" -ne 0 ]; then
  echo "BENCH_VERIFY_FAIL algorithm=$algorithm n=$n exit=$status"
  exit 1
fi
got=$(sha256sum <"$out" | cut -d' ' -f1)
rm -f "$out"
expected=${BENCH_EXPECTED_SHA256:-}
if [ -z "$expected" ] || [ "$got" != "$expected" ]; then
  echo "BENCH_VERIFY_FAIL algorithm=$algorithm n=$n sha256 expected=${expected:-none} got=$got"
  exit 1
fi
echo "BENCH_VERIFY_OK algorithm=$algorithm n=$n sha256=$got"

json=$(mktemp)
echo "STARTTIME $(date +%s)"
hyperfine --warmup "$warmup" --runs "$runs" --export-json "$json" "$BENCH_RUN_COMMAND" >&2 || exit 1
echo "BENCH_RESULT_BEGIN engine=hyperfine version=$(hyperfine --version | cut -d' ' -f2)"
cat "$json"
echo "BENCH_RESULT_END"
echo "ENDTIME $(date +%s)"
