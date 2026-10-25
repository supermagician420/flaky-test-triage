import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from flaky_triage import LogError, analyze, main, parse_log, render_text


class ParseTests(unittest.TestCase):
    def test_text_lines(self) -> None:
        text = """
# run 1
PASS test_login 120ms
FAIL test_checkout 1.2s
OK test_search
"""
        results = parse_log(text)
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].passed)
        self.assertEqual(results[0].test, "test_login")
        self.assertEqual(results[0].duration_ms, 120.0)
        self.assertEqual(results[1].duration_ms, 1200.0)
        self.assertIsNone(results[2].duration_ms)

    def test_json_lines(self) -> None:
        text = "\n".join(
            [
                json.dumps({"test": "a", "passed": True, "duration_ms": 10}),
                json.dumps({"name": "b", "status": "failed", "duration": "2s"}),
            ]
        )
        results = parse_log(text)
        self.assertEqual([result.test for result in results], ["a", "b"])
        self.assertEqual(results[1].duration_ms, 2000.0)

    def test_invalid_line_reports_line_number(self) -> None:
        with self.assertRaises(LogError) as ctx:
            parse_log("PASS good\nnot a result\n")
        self.assertIn("line 2", str(ctx.exception))

    def test_invalid_json_is_rejected(self) -> None:
        with self.assertRaises(LogError):
            parse_log('{"test": "a", "passed": true\n')

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaises(LogError):
            parse_log('{"test": "a", "status": "maybe"}')


class AnalyzeTests(unittest.TestCase):
    def test_flaky_and_always_failing(self) -> None:
        results = []
        for run in range(1, 5):
            results.append(parse_log("PASS flaky_test 10ms", run)[0])
            results.append(parse_log("FAIL always_fail 20ms", run)[0])
        results.append(parse_log("FAIL flaky_test 12ms", 5)[0])
        report = analyze(results)
        self.assertEqual(report.flaky[0].test, "flaky_test")
        self.assertEqual(report.flaky[0].runs, 5)
        self.assertEqual(report.flaky[0].fails, 1)
        self.assertEqual(report.always_failing[0].test, "always_fail")
        self.assertIn("1 flaky", report.summary)

    def test_no_flaky_tests(self) -> None:
        results = parse_log("PASS a\nPASS a\nPASS b\n", 1)
        report = analyze(results)
        self.assertEqual(report.flaky, ())
        self.assertIn("0 flaky", report.summary)

    def test_percentiles(self) -> None:
        results = []
        for index, duration in enumerate([10, 20, 30, 40, 50]):
            results.append(parse_log("PASS t %dms" % duration, index + 1)[0])
        report = analyze(results)
        stats = report.slowest[0]
        self.assertEqual(stats.p50_ms, 30.0)
        self.assertEqual(stats.p95_ms, 50.0)
        self.assertEqual(stats.max_ms, 50.0)

    def test_text_rendering(self) -> None:
        results = parse_log("PASS a 10ms\nFAIL a 20ms\nFAIL b 30ms\n", 1)
        rendered = render_text(analyze(results))
        self.assertIn("Flaky", rendered)
        self.assertIn("Always failing", rendered)
        self.assertIn("Slowest", rendered)

    def test_empty_results_rejected(self) -> None:
        with self.assertRaises(LogError):
            analyze([])


class CliTests(unittest.TestCase):
    def test_cli_with_two_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "run1.log"
            second = Path(tmp) / "run2.log"
            first.write_text("PASS a 10ms\nFAIL b 20ms\n", encoding="utf-8")
            second.write_text("FAIL a 30ms\nFAIL b 20ms\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--log", str(first), "--log", str(second), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["total_results"], 4)
        self.assertEqual(data["total_tests"], 2)
        self.assertEqual(data["flaky"][0]["test"], "a")

    def test_cli_missing_file(self) -> None:
        self.assertEqual(main(["--log", "missing.log"]), 2)


if __name__ == "__main__":
    unittest.main()
