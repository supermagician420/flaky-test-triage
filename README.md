# flaky-test-triage

A zero-dependency CLI that turns repeated test logs into a flaky-test triage report.

It separates three things teams often blur together:

- **flaky**: the test has both passed and failed across runs;
- **always failing**: it failed in every run;
- **slow**: it is stable but expensive.

## Quick start

Requires Python 3.8+, no third-party packages.

```bash
python flaky_triage.py \
  --log examples/run1.log \
  --log examples/run2.log \
  --log examples/run3.log
```

Example output:

```text
12 results across 4 tests: 2 flaky, 1 always failing.

Flaky (passed and failed):
- test_checkout: 1/3 failed (33%), p95 350ms
- test_login: 1/3 failed (33%), p95 140ms

Always failing:
- test_upload: 3/3 failed

Slowest by p95:
- test_upload: p50 910ms, p95 950ms, max 950ms
- test_checkout: p50 320ms, p95 350ms, max 350ms
- test_login: p50 130ms, p95 140ms, max 140ms
- test_search: p50 40ms, p95 45ms, max 45ms
```

JSON for dashboards or CI:

```bash
python flaky_triage.py --log examples/run1.log --log examples/run2.log --json
```

Pipe from stdin:

```bash
cat examples/run1.log | python flaky_triage.py
```

## Accepted log formats

Plain text, one result per line:

```text
PASS test_login 120ms
FAIL test_checkout 1.2s
OK test_search
```

- statuses: `PASS`, `PASSED`, `OK`, `FAIL`, `FAILED`, `ERROR`;
- duration is optional; `ms` is the default, `s` is converted to milliseconds;
- test names cannot contain spaces;
- blank lines and `#` comments are ignored.

JSON Lines:

```json
{"test": "test_login", "passed": true, "duration_ms": 120}
{"name": "test_checkout", "status": "failed", "duration": "2s"}
```

- `test` or `name` is required;
- provide either `passed` (boolean) or `status` (string);
- `duration_ms` or `duration` is optional;
- `run` is optional and defaults to the log's position.

## How to use it

1. Run the same test suite at least 5–10 times (more is better).
2. Save each run as a separate log.
3. Pass each log with `--log`.
4. Fix flaky tests before optimizing slow ones; an always-failing test is not flaky.

## Metrics

- **failure rate**: failures / runs for each test;
- **p50 / p95**: nearest-rank percentiles over runs that reported a duration;
- **max**: slowest observed duration.

The tool does not decide whether a failure is environmental or a real bug. It only surfaces the pattern.

## Design choices

- **Deterministic and offline.** No API keys, no network.
- **Explicit input contract.** Unparseable lines fail loudly with a line number instead of being silently skipped.
- **Flaky is not "failed".** A test that always fails is reported separately from one that is intermittent.
- **Stdlib only.** Easy to drop into any Python CI image.

## Limitations

- Flakiness is statistical: three runs cannot prove a test is stable.
- A test that always fails may be broken, misconfigured, or simply never reached.
- Test names are treated as opaque IDs; parameterized variants are separate tests.
- Percentiles from few samples are rough.
- It does not fetch logs from CI providers.

## Tests

```bash
python -m unittest -v
```

12 tests cover text/JSON parsing, invalid input, flaky classification, always-failing classification, percentiles, text rendering, and CLI behavior.

## Files

```text
flaky-test-triage/
├── flaky_triage.py
├── test_flaky_triage.py
├── examples/run1.log
├── examples/run2.log
├── examples/run3.log
├── README.md
├── .gitignore
└── .github/workflows/tests.yml
```
