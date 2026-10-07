import tempfile
import unittest
from pathlib import Path

from scripts.report import load_results, ratio, render
from scripts.workload import OPERATIONS


def result(backend="deep", size=10):
    return {
        "backend": backend,
        "background_links": size,
        "links": 3,
        "samples": 2,
        "warmup": 0,
        "metadata": {
            "cpu": "test CPU",
            "date": "2026-10-06",
            "run": "https://github.com/linksplatform/Comparisons.DeepGqlStandard/actions/runs/1",
            "commit": "a" * 40,
            "versions": {"test": "1"},
        },
        "operations": {
            op: {"median_ns": 100, "stdev_ns": 0, "samples_ns": [100, 100]}
            for op in OPERATIONS
        },
    }


class ReportTests(unittest.TestCase):
    def test_generated_raw_archive_can_be_committed(self):
        import subprocess

        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--", "Docs/results/deep-1000.json"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_ratios_and_noise(self):
        self.assertEqual(ratio(100, 50, 0, 0), "2× faster")
        self.assertEqual(ratio(100, 200, 0, 0), "2× slower")
        self.assertEqual(ratio(100, 102, 0, 0), "≈ same")
        self.assertEqual(ratio(100, 120, 15, 15), "≈ same")

    def test_requires_every_backend_and_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            import json

            path = Path(directory)
            for backend in ["deep", "rust", "csharp"]:
                (path / f"{backend}.json").write_text(json.dumps(result(backend)))
            self.assertEqual(len(load_results(path)), 3)
            (path / "rust.json").unlink()
            with self.assertRaises(ValueError):
                load_results(path)

    def test_rejects_nan_missing_and_mismatched_workloads(self):
        for mutate in [
            lambda r: r[0]["operations"].pop("Create"),
            lambda r: r[0]["operations"]["Create"].update(median_ns=float("nan")),
            lambda r: r[0].update(links=4),
            lambda r: r[0]["metadata"].pop("versions"),
            lambda r: r.append(r[0]),
            lambda r: r[0]["operations"]["Create"].update(stdev_ns=1),
            lambda r: r[0]["operations"]["Create"].update(samples_ns=[-100, 100]),
        ]:
            records = [result(b) for b in ["deep", "rust", "csharp"]]
            mutate(records)
            with self.assertRaises(ValueError):
                render(records)

    def test_keeps_handwritten_text(self):
        from scripts.report import replace_section

        self.assertEqual(
            replace_section(
                "before\n<!-- results:start -->\nold\n<!-- results:end -->\nafter",
                "new",
            ),
            "before\n<!-- results:start -->\nnew\n<!-- results:end -->\nafter",
        )
        with self.assertRaises(ValueError):
            replace_section("no markers", "new")


if __name__ == "__main__":
    unittest.main()
