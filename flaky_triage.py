#!/usr/bin/env python3
"""Turn repeated test runs into a flaky-test triage report."""

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


class LogError(ValueError):
    """A log line or input file could not be parsed."""


@dataclass(frozen=True)
class TestResult:
    run: int
    test: str
    passed: bool
    duration_ms: Optional[float]


@dataclass(frozen=True)
class TestStats:
    test: str
    runs: int
    passes: int
    fails: int
    flaky: bool
    p50_ms: Optional[float]
    p95_ms: Optional[float]
    max_ms: Optional[float]

    @property
    def failure_rate(self) -> float:
        return self.fails / self.runs if self.runs else 0.0


@dataclass(frozen=True)
class TriageReport:
    total_results: int
    total_tests: int
    flaky: Tuple[TestStats, ...]
    always_failing: Tuple[TestStats, ...]
    slowest: Tuple[TestStats, ...]
    summary: str


_TEXT_RE = re.compile(
    r"^(?P<status>PASS|PASSED|OK|FAIL|FAILED|ERROR)\s+(?P<test>\S+)"
    r"(?:\s+(?P<duration>\d+(?:\.\d+)?)(?P<unit>ms|s)?)?\s*$",
    re.IGNORECASE,
)
_TRUE_STATUS = {"pass", "passed", "ok", "success"}
_FALSE_STATUS = {"fail", "failed", "error", "failure"}


def _parse_duration(value: object, where: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise LogError("%s: duration must be a number" % where)
    if isinstance(value, (int, float)):
        if value < 0:
            raise LogError("%s: duration must not be negative" % where)
        return float(value)
    if isinstance(value, str):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s)?", value.strip(), re.IGNORECASE)
        if not match:
            raise LogError("%s: invalid duration %r" % (where, value))
        number = float(match.group(1))
        if (match.group(2) or "").lower() == "s":
            number *= 1000.0
        return number
    raise LogError("%s: duration must be a number or string" % where)


def _parse_json_line(raw: str, line_number: int, default_run: int) -> TestResult:
    where = "line %d" % line_number
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LogError("%s: invalid JSON: %s" % (where, exc.msg)) from exc
    if not isinstance(data, dict):
        raise LogError("%s: JSON line must be an object" % where)

    test = data.get("test") or data.get("name")
    if not isinstance(test, str) or not test.strip():
        raise LogError("%s: test/name must be a non-empty string" % where)

    if "passed" in data:
        if not isinstance(data["passed"], bool):
            raise LogError("%s: passed must be true or false" % where)
        passed = data["passed"]
    elif "status" in data:
        status = data["status"]
        if not isinstance(status, str):
            raise LogError("%s: status must be a string" % where)
        normalized = status.strip().lower()
        if normalized in _TRUE_STATUS:
            passed = True
        elif normalized in _FALSE_STATUS:
            passed = False
        else:
            raise LogError("%s: unknown status %r" % (where, status))
    else:
        raise LogError("%s: missing passed/status" % where)

    run = data.get("run", default_run)
    if not isinstance(run, int) or isinstance(run, bool) or run < 0:
        raise LogError("%s: run must be a non-negative integer" % where)
    duration = _parse_duration(data.get("duration_ms", data.get("duration")), where)
    return TestResult(run, test.strip(), passed, duration)


def _parse_text_line(raw: str, line_number: int, run: int) -> TestResult:
    match = _TEXT_RE.match(raw)
    if not match:
        raise LogError("line %d: expected 'PASS test 12ms' or 'FAIL test' (got %r)" % (line_number, raw[:120]))
    passed = match.group("status").lower() in _TRUE_STATUS
    duration = None
    if match.group("duration") is not None:
        duration = float(match.group("duration"))
        if (match.group("unit") or "ms").lower() == "s":
            duration *= 1000.0
    return TestResult(run, match.group("test"), passed, duration)


def parse_log(text: str, run: int = 1) -> List[TestResult]:
    results: List[TestResult] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        raw = raw_line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("{"):
            results.append(_parse_json_line(raw, line_number, run))
        else:
            results.append(_parse_text_line(raw, line_number, run))
    return results


def _percentile(values: Sequence[float], percent: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percent / 100.0 * len(ordered)) - 1))
    return round(ordered[index], 1)


def _stats(test: str, results: Sequence[TestResult]) -> TestStats:
    passes = sum(1 for result in results if result.passed)
    fails = len(results) - passes
    durations = [result.duration_ms for result in results if result.duration_ms is not None]
    return TestStats(
        test=test,
        runs=len(results),
        passes=passes,
        fails=fails,
        flaky=passes > 0 and fails > 0,
        p50_ms=_percentile(durations, 50),
        p95_ms=_percentile(durations, 95),
        max_ms=round(max(durations), 1) if durations else None,
    )


def analyze(results: Sequence[TestResult], top: int = 10) -> TriageReport:
    if not results:
        raise LogError("no test results found")
    if top <= 0:
        raise LogError("top must be a positive integer")

    grouped: Dict[str, List[TestResult]] = {}
    for result in results:
        grouped.setdefault(result.test, []).append(result)
    stats = [_stats(test, grouped[test]) for test in grouped]

    flaky = sorted((item for item in stats if item.flaky), key=lambda item: (-item.failure_rate, item.test))
    always_failing = sorted(
        (item for item in stats if item.fails == item.runs),
        key=lambda item: (-item.fails, item.test),
    )
    slowest = sorted(
        (item for item in stats if item.p95_ms is not None),
        key=lambda item: (-item.p95_ms, item.test),
    )

    flaky_count = len(flaky)
    failing_count = len(always_failing)
    summary = "%d results across %d tests: %d flaky, %d always failing." % (
        len(results),
        len(stats),
        flaky_count,
        failing_count,
    )
    return TriageReport(
        total_results=len(results),
        total_tests=len(stats),
        flaky=tuple(flaky[:top]),
        always_failing=tuple(always_failing[:top]),
        slowest=tuple(slowest[:top]),
        summary=summary,
    )


def _duration(value: Optional[float]) -> str:
    return "-" if value is None else ("%gms" % value)


def render_text(report: TriageReport) -> str:
    lines = [report.summary, "", "Flaky (passed and failed):"]
    if report.flaky:
        for item in report.flaky:
            lines.append(
                "- %s: %d/%d failed (%.0f%%), p95 %s"
                % (item.test, item.fails, item.runs, 100.0 * item.failure_rate, _duration(item.p95_ms))
            )
    else:
        lines.append("- none")

    lines.extend(["", "Always failing:"])
    if report.always_failing:
        for item in report.always_failing:
            lines.append("- %s: %d/%d failed" % (item.test, item.fails, item.runs))
    else:
        lines.append("- none")

    lines.extend(["", "Slowest by p95:"])
    if report.slowest:
        for item in report.slowest:
            lines.append("- %s: p50 %s, p95 %s, max %s" % (
                item.test,
                _duration(item.p50_ms),
                _duration(item.p95_ms),
                _duration(item.max_ms),
            ))
    else:
        lines.append("- no duration data")
    return "\n".join(lines).rstrip() + "\n"


def render_json(report: TriageReport) -> str:
    return json.dumps(asdict(report), ensure_ascii=False, indent=2)


def _read_inputs(paths: Sequence[str]) -> List[str]:
    if not paths:
        return [sys.stdin.read()]
    texts = []
    for path in paths:
        texts.append(Path(path).read_text(encoding="utf-8"))
    return texts


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Triage flaky tests from repeated run logs")
    parser.add_argument("--log", action="append", default=[], help="one log per run; repeatable")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--top", type=int, default=10, help="show at most N tests per section")
    args = parser.parse_args(argv)

    try:
        results: List[TestResult] = []
        for run, text in enumerate(_read_inputs(args.log), 1):
            results.extend(parse_log(text, run=run))
        report = analyze(results, top=args.top)
    except OSError as exc:
        print("error: cannot read log: %s" % exc, file=sys.stderr)
        return 2
    except LogError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    print(render_json(report) if args.json else render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
