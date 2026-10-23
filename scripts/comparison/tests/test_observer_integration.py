from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BINARY = REPO_ROOT / "build" / "pixi" / "dancers"
CONFIG = REPO_ROOT / "config" / "comparison" / "laptop_smoke.yaml"


def provenance() -> dict[str, object]:
    return {
        "run_id": "observer-integration",
        "implementation_id": "common-patch-test",
        "algorithm_revision": "test-revision",
        "common_patch_sha256": "1" * 64,
        "binary_sha256": "2" * 64,
        "pixi_lock_sha256": "3" * 64,
        "config_sha256": "4" * 64,
        "map_sha256": "5" * 64,
        "scenario_sha256": "6" * 64,
        "omp_threads": 1,
    }


@unittest.skipUnless(BINARY.is_file(), "build/pixi/dancers has not been built")
class ComparisonObserverIntegrationTests(unittest.TestCase):
    def run_observer(
        self,
        directory: Path,
        *,
        summary_path: Path | None = None,
        trace_detail: str = "full",
        metadata: dict[str, object] | None = None,
        config: Path = CONFIG,
        extra_environment: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        trace_path = directory / "trace.jsonl"
        summary_path = summary_path or directory / "summary.json"
        metadata_path = directory / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata or provenance(), sort_keys=True),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.pop(
            "DANCERS_TEST_DISABLE_DIGEST_BOUNDARY_REUSE",
            None,
        )
        environment.pop(
            "DANCERS_TEST_DIGEST_BOUNDARY_STATS_OUT",
            None,
        )
        environment.update(
            {
                "DANCERS_TRACE_OUT": str(trace_path),
                "DANCERS_SUMMARY_OUT": str(summary_path),
                "DANCERS_RUN_METADATA": str(metadata_path),
                "DANCERS_REQUIRE_PROVENANCE": "1",
                "DANCERS_TRACE_DETAIL": trace_detail,
                "OMP_NUM_THREADS": "1",
                "OMP_DYNAMIC": "FALSE",
            }
        )
        if extra_environment:
            environment.update(extra_environment)
        try:
            config_argument = str(config.relative_to(REPO_ROOT))
        except ValueError:
            config_argument = str(config)
        result = subprocess.run(
            [
                str(BINARY),
                "--cfg",
                config_argument,
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result, trace_path, summary_path

    def test_trace_is_exact_repeatable_and_schema_complete(self) -> None:
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first, first_trace, first_summary = self.run_observer(
                Path(first_tmp)
            )
            second, second_trace, _ = self.run_observer(Path(second_tmp))

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first_trace.read_bytes(), second_trace.read_bytes())

            records = [
                json.loads(line)
                for line in first_trace.read_text(encoding="utf-8").splitlines()
            ]
            header = records[0]
            frames = records[1:]
            self.assertEqual(header["type"], "header")
            self.assertEqual(
                header["schema_version"], "dancers-comparison-trace-v3"
            )
            self.assertEqual(
                header["observer"]["digest_algorithm"],
                "SHA-256 over robot-semantic-digest-v2 "
                "canonical typed binary encoding",
            )
            reuse_policy = header["observer"][
                "digest_boundary_reuse_policy"
            ]
            self.assertEqual(
                set(reuse_policy),
                {"eligibility", "fallback", "scope"},
            )
            self.assertTrue(
                header["observer"]["strict_provenance_required"]
            )
            self.assertTrue(
                header["resolved_config"]["obstacles_present"]
            )
            self.assertEqual(
                header["resolved_config"]["obstacle_field_width"], 1000
            )
            self.assertEqual(len(frames), 20)
            self.assertEqual(
                [(frame["tick"], frame["phase"]) for frame in frames[:4]],
                [
                    (0, "pre_update"),
                    (1, "post_update"),
                    (1, "pre_update"),
                    (2, "post_update"),
                ],
            )

            first_frame = frames[0]
            self.assertEqual(len(first_frame["robots"]), 8)
            self.assertIn("normal_mt19937", first_frame["rng_state"])
            self.assertIn(
                "last_created_robots",
                first_frame["simulator_state"],
            )
            robot = first_frame["robots"][0]
            self.assertIn("remaining_waypoints", robot)
            self.assertIn("next_variable_ids", robot)
            self.assertIn("next_factor_ids", robot)
            self.assertGreater(len(robot["layers"]), 0)
            variable = robot["layers"][0]["variables"][0]
            self.assertIn("belief_precision_hex", variable)
            self.assertIn("prior", variable)
            factor = robot["layers"][0]["factors"][0]
            self.assertIn("linearization_points", factor)
            self.assertIn("last_outbox", factor)

            summary = json.loads(first_summary.read_text(encoding="utf-8"))
            self.assertEqual(summary["frames_recorded"], 20)
            self.assertEqual(summary["final_tick"], 10)
            self.assertEqual(summary["maximum_robot_count"], 8)
            self.assertEqual(summary["nonfinite_state_observations"], 0)
            self.assertEqual(summary["invalid_robot_frames"], 0)
            self.assertEqual(summary["nonfinite_numeric_observations"], 0)
            self.assertEqual(summary["invalid_geometry_evaluations"], 0)
            self.assertTrue(summary["run_valid"])
            self.assertEqual(
                summary["termination_reason"],
                "maximum_time",
            )

    def test_digest_trace_is_bounded_and_provenance_independent(self) -> None:
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first_metadata = provenance()
            second_metadata = provenance()
            second_metadata["run_id"] = "different-run"
            second_metadata["implementation_id"] = "different-method"
            second_metadata["algorithm_revision"] = "different-revision"
            second_metadata["binary_sha256"] = "a" * 64

            first, first_trace, _ = self.run_observer(
                Path(first_tmp),
                trace_detail="digest",
                metadata=first_metadata,
            )
            second, second_trace, _ = self.run_observer(
                Path(second_tmp),
                trace_detail="digest",
                metadata=second_metadata,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertLess(first_trace.stat().st_size, 1_000_000)
            self.assertNotEqual(
                first_trace.read_bytes(),
                second_trace.read_bytes(),
                "provenance remains visible in the raw artifact",
            )

            first_records = [
                json.loads(line)
                for line in first_trace.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            second_records = [
                json.loads(line)
                for line in second_trace.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(
                first_records[1:],
                second_records[1:],
                "semantic frames ignore provenance-only differences",
            )
            robot_digest = first_records[1]["robots"][0]
            self.assertEqual(
                set(robot_digest),
                {"rid", "state_schema", "state_sha256"},
            )
            self.assertEqual(
                robot_digest["state_schema"],
                "robot-semantic-digest-v2",
            )
            self.assertRegex(robot_digest["state_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                robot_digest["state_sha256"],
                "59103a5e771008a9cbb1232ded00da3e"
                "0c203d35c84345a3b034425beb0d1cc1",
                "the reviewed pre-update robot-0 encoding is a golden vector",
            )
            self.assertNotIn("layers", robot_digest)
            self.assertGreater(
                len(
                    {
                        frame["robots"][0]["state_sha256"]
                        for frame in first_records[1:]
                    }
                ),
                1,
                "the semantic digest must respond to evolving robot state",
            )

    def test_static_digest_boundary_reuse_matches_recomputation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reused_directory = directory / "reused"
            recomputed_directory = directory / "recomputed"
            reused_directory.mkdir()
            recomputed_directory.mkdir()
            reused_stats = reused_directory / "stats.json"
            recomputed_stats = recomputed_directory / "stats.json"

            reused, reused_trace, reused_summary = self.run_observer(
                reused_directory,
                trace_detail="digest",
                extra_environment={
                    "DANCERS_TEST_DIGEST_BOUNDARY_STATS_OUT":
                        str(reused_stats),
                },
            )
            recomputed, recomputed_trace, recomputed_summary = (
                self.run_observer(
                    recomputed_directory,
                    trace_detail="digest",
                    extra_environment={
                        "DANCERS_TEST_DISABLE_DIGEST_BOUNDARY_REUSE": "1",
                        "DANCERS_TEST_DIGEST_BOUNDARY_STATS_OUT":
                            str(recomputed_stats),
                    },
                )
            )
            self.assertEqual(reused.returncode, 0, reused.stderr)
            self.assertEqual(
                recomputed.returncode,
                0,
                recomputed.stderr,
            )
            self.assertEqual(
                reused_trace.read_bytes(),
                recomputed_trace.read_bytes(),
                "reuse must not alter any header or frame byte",
            )
            reused_summary_payload = json.loads(
                reused_summary.read_text(encoding="utf-8")
            )
            recomputed_summary_payload = json.loads(
                recomputed_summary.read_text(encoding="utf-8")
            )
            reused_summary_payload.pop("trace_path")
            recomputed_summary_payload.pop("trace_path")
            self.assertEqual(
                reused_summary_payload,
                recomputed_summary_payload,
                "reuse must preserve every summary metric",
            )

            reused_counts = json.loads(
                reused_stats.read_text(encoding="utf-8")
            )
            recomputed_counts = json.loads(
                recomputed_stats.read_text(encoding="utf-8")
            )
            self.assertEqual(
                reused_counts,
                {
                    "candidate_boundaries": 9,
                    "eligible_boundaries": 9,
                    "recomputed_boundaries": 11,
                    "recomputed_robot_digests": 88,
                    "reused_boundaries": 9,
                    "reused_robot_digests": 72,
                },
            )
            self.assertEqual(
                recomputed_counts,
                {
                    "candidate_boundaries": 9,
                    "eligible_boundaries": 9,
                    "recomputed_boundaries": 20,
                    "recomputed_robot_digests": 160,
                    "reused_boundaries": 0,
                    "reused_robot_digests": 0,
                },
            )

            records = [
                json.loads(line)
                for line in reused_trace.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            frames = records[1:]
            for post_frame, pre_frame in zip(
                    frames[1::2],
                    frames[2::2]):
                self.assertEqual(
                    post_frame["robots"],
                    pre_frame["robots"],
                )

    def test_dynamic_full_and_positions_runs_never_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dynamic_config = directory / "dynamic.yaml"
            dynamic_config.write_text(
                CONFIG.read_text(encoding="utf-8")
                .replace("SCENARIO: file", "SCENARIO: random")
                .replace("MAX_TIME: 10", "MAX_TIME: 2"),
                encoding="utf-8",
            )
            cases = (
                ("dynamic", "digest", dynamic_config, 4),
                ("full", "full", CONFIG, 0),
                ("positions", "positions", CONFIG, 0),
            )
            for label, detail, config, recomputed_boundaries in cases:
                with self.subTest(label=label):
                    run_directory = directory / label
                    run_directory.mkdir()
                    stats_path = run_directory / "stats.json"
                    result, _, _ = self.run_observer(
                        run_directory,
                        trace_detail=detail,
                        config=config,
                        extra_environment={
                            "DANCERS_TEST_DIGEST_BOUNDARY_STATS_OUT":
                                str(stats_path),
                        },
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stderr,
                    )
                    counts = json.loads(
                        stats_path.read_text(encoding="utf-8")
                    )
                    self.assertEqual(
                        counts["candidate_boundaries"],
                        0,
                    )
                    self.assertEqual(
                        counts["eligible_boundaries"],
                        0,
                    )
                    self.assertEqual(
                        counts["reused_boundaries"],
                        0,
                    )
                    self.assertEqual(
                        counts["reused_robot_digests"],
                        0,
                    )
                    self.assertEqual(
                        counts["recomputed_boundaries"],
                        recomputed_boundaries,
                    )

    def test_positions_trace_structure_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, trace_path, summary_path = self.run_observer(
                Path(tmp),
                trace_detail="positions",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            records = [
                json.loads(line)
                for line in trace_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(
                records[0]["schema_version"],
                "dancers-comparison-trace-v3",
            )
            self.assertEqual(
                records[0]["observer"]["trace_detail"],
                "positions",
            )
            self.assertEqual(
                set(records[1]["robots"][0]),
                {
                    "bearing",
                    "current_waypoint",
                    "final_goal",
                    "goal_reached",
                    "radius_m",
                    "rid",
                    "state",
                    "state_hex",
                },
            )
            summary = json.loads(
                summary_path.read_text(encoding="utf-8")
            )
            self.assertEqual(summary["trace_detail"], "positions")

    def test_summary_write_failure_never_aborts_destructor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            unwritable_as_file = directory / "summary-is-a-directory"
            unwritable_as_file.mkdir()
            result, trace_path, _ = self.run_observer(
                directory,
                summary_path=unwritable_as_file,
            )
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(
                result.returncode,
                -6,
                "finalization failure must not terminate through noexcept",
            )
            self.assertTrue(trace_path.is_file())
            self.assertIn(
                "Comparison recorder finalization failed",
                result.stderr,
            )

    def test_missing_strict_provenance_fails_without_abort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            incomplete = provenance()
            del incomplete["binary_sha256"]
            result, trace_path, summary_path = self.run_observer(
                Path(tmp),
                metadata=incomplete,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotEqual(result.returncode, -6)
            self.assertFalse(trace_path.exists())
            self.assertFalse(summary_path.exists())
            self.assertIn(
                "Required comparison provenance field",
                result.stderr,
            )
