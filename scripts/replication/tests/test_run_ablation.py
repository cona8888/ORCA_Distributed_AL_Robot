from __future__ import annotations

import hashlib
import importlib.util
import io
import math
import pathlib
import sys
import tarfile
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = REPO / "scripts" / "replication" / "run_ablation.py"
SPEC = importlib.util.spec_from_file_location("run_ablation_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
A = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = A
SPEC.loader.exec_module(A)


class AblationTests(unittest.TestCase):
    def test_geometric_mean_uses_stable_summation(self):
        values = [
            46268416,
            46219264,
            46350336,
            46252032,
            46186496,
            46399488,
            46333952,
            46104576,
            46120960,
            46301184,
        ]
        expected = math.exp(math.fsum(math.log(value) for value in values) / len(values))
        self.assertEqual(A.geometric_mean(values), expected)

    def test_01_frozen_inputs_and_stage_chain(self) -> None:
        self.assertEqual(A.sha256_file(REPO / A.CONFIG_REL), A.CONFIG_SHA256)
        self.assertEqual(A.sha256_file(REPO / "pixi.lock"), A.LOCK_SHA256)
        self.assertEqual(A.sha256_file(REPO / A.MANIF_REL), A.MANIF_SHA256)
        self.assertEqual(A.sha256_file(REPO / A.RAYLIB_REL), A.RAYLIB_SHA256)
        tools = A.validate_frozen_inputs(REPO)
        self.assertEqual(set(tools), {"cmake", "ninja", "clang", "clangxx", "libomp"})

    def test_02_schedule_is_complete_and_balanced(self) -> None:
        records = A.schedule_records()
        warmups = [record for record in records if record["phase"] == "warmup"]
        measured = [record for record in records if record["phase"] == "measured"]
        self.assertEqual(len(warmups), 12)
        self.assertEqual(len(measured), 360)
        self.assertEqual([record["sequence"] for record in records], list(range(372)))

        keys = {
            (record["stage"], record["count"], record["seed"], record["repetition"])
            for record in measured
        }
        self.assertEqual(len(keys), 6 * 2 * 10 * 3)

        position_counts = {(stage["id"], position): 0 for stage in A.STAGES for position in range(6)}
        count_first = {500: 0, 1000: 0}
        for block in range(A.MEASURED_BLOCKS):
            block_records = [record for record in measured if record["block"] == block]
            order = []
            for record in block_records:
                if record["stage"] not in order:
                    order.append(record["stage"])
            self.assertEqual(len(order), 6)
            for position, stage_id in enumerate(order):
                position_counts[(stage_id, position)] += 1
            count_first[block_records[0]["count"]] += 1
        self.assertEqual(set(position_counts.values()), {5})
        self.assertEqual(count_first, {500: 15, 1000: 15})

    def test_03_statistics_are_deterministic(self) -> None:
        self.assertAlmostEqual(A.geometric_mean([1.0, 4.0]), 2.0)
        previous = A.BOOTSTRAP_SAMPLES
        A.BOOTSTRAP_SAMPLES = 500
        try:
            first = A.bootstrap_interval([1.1, 1.2, 1.3, 1.4], salt=7)
            second = A.bootstrap_interval([1.1, 1.2, 1.3, 1.4], salt=7)
        finally:
            A.BOOTSTRAP_SAMPLES = previous
        self.assertEqual(first, second)
        self.assertLessEqual(first["lower_95"], first["estimate"])
        self.assertGreaterEqual(first["upper_95"], first["estimate"])

    def test_04_safe_extract_rejects_traversal_and_links(self) -> None:
        for kind in ("traversal", "symlink"):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                info = tarfile.TarInfo("../escape" if kind == "traversal" else "link")
                if kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/tmp/target"
                else:
                    raw = b"bad"
                    info.size = len(raw)
                    archive.addfile(info, io.BytesIO(raw))
                if kind == "symlink":
                    archive.addfile(info)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(A.AblationError):
                    A.safe_extract(buffer.getvalue(), pathlib.Path(tmp) / "out")

    def test_05_synthetic_analysis_uses_seed_medians(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            lines = []
            for plan in A.schedule_records():
                attempt = root / "attempts" / f"{plan['sequence']:03d}"
                attempt.mkdir(parents=True)
                stdout = f"N={plan['count']} seed={plan['seed']}\n".encode()
                stderr = b""
                stdout_path = attempt / "stdout.txt"
                stderr_path = attempt / "stderr.txt"
                stdout_path.write_bytes(stdout)
                stderr_path.write_bytes(stderr)
                stage_index = int(str(plan["stage"])[1:])
                repetition = 0 if plan["repetition"] is None else int(plan["repetition"])
                seed = int(plan["seed"])
                base = float(plan["count"]) / 100.0 + seed / 100.0
                noise = 1.0 + repetition / 1000.0
                factor = 1.0 + stage_index / 10.0
                wall = base * noise / factor
                record = {
                    **plan,
                    "wall_seconds": wall,
                    "user_cpu_seconds": wall * 0.9,
                    "system_cpu_seconds": wall * 0.02,
                    "total_cpu_seconds": wall * 0.92,
                    "max_rss_raw": 100_000.0 / factor,
                    "returncode": 0,
                    "stdout": {
                        "path": str(stdout_path.relative_to(root)),
                        "bytes": len(stdout),
                        "sha256": hashlib.sha256(stdout).hexdigest(),
                    },
                    "stderr": {
                        "path": str(stderr_path.relative_to(root)),
                        "bytes": 0,
                        "sha256": hashlib.sha256(stderr).hexdigest(),
                    },
                }
                lines.append(A.canonical_json_bytes(record))
            (root / "runs.jsonl").write_bytes(b"".join(lines))
            previous = A.BOOTSTRAP_SAMPLES
            A.BOOTSTRAP_SAMPLES = 200
            try:
                analysis = A.analyze(root)
            finally:
                A.BOOTSTRAP_SAMPLES = previous
            self.assertEqual(analysis["inventory"]["measured"], 360)
            cumulative = [
                row
                for row in analysis["comparisons"]
                if row["count"] == 500 and row["before"] == "S0" and row["after"] == "S5"
            ][0]
            self.assertAlmostEqual(cumulative["wall_ratio"]["estimate"], 1.5, places=12)
            first_summary = analysis["stage_summaries"][0]
            self.assertAlmostEqual(
                first_summary["max_rss_geomean_mib"],
                first_summary["max_rss_geomean_raw"] / (1024.0 * 1024.0),
            )
            self.assertTrue((root / "analysis.json").is_file())
            self.assertTrue((root / "analysis.csv").is_file())
            self.assertNotIn(b"\r", (root / "analysis.csv").read_bytes())
            self.assertTrue((root / "FINDINGS.md").is_file())

    def test_06_legacy_config_argument_has_no_whitespace(self) -> None:
        source = REPO / "build path with spaces" / "publication" / "S0" / "source"
        argument = A.config_argument(REPO, source)
        self.assertFalse(any(character.isspace() for character in argument))
        self.assertEqual((source / argument).resolve(), (REPO / A.CONFIG_REL).resolve())

    def test_07_historical_log_counts_external_iterations(self) -> None:
        stdout = (
            b"timesteps:  100  |  proportion majority:  0.894\n"
            b"Final iterations: [100]\n"
            b"Final robot count: [500]\n"
            b"Test complete\n"
        )
        A.validate_fixed_work_stdout(stdout, 500)
        with self.assertRaises(A.AblationError):
            A.validate_fixed_work_stdout(stdout.replace(b"[100]", b"[200]", 1), 500)
        with self.assertRaises(A.AblationError):
            A.validate_fixed_work_stdout(stdout, 1000)


if __name__ == "__main__":
    unittest.main()
