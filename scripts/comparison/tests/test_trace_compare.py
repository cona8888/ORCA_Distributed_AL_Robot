from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "comparison" / "trace_compare.py"
SPEC = importlib.util.spec_from_file_location("trace_compare", MODULE_PATH)
assert SPEC and SPEC.loader
TRACE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TRACE
SPEC.loader.exec_module(TRACE)

TRACE_SCHEMA_V2 = "dancers-comparison-trace-v2"
TRACE_SCHEMA_V3 = "dancers-comparison-trace-v3"
V2_DIGEST_ALGORITHM = (
    "SHA-256 over canonical nlohmann JSON serialization of each "
    "implementation-neutral robot frame"
)
V3_DIGEST_ALGORITHM = (
    "SHA-256 over robot-semantic-digest-v2 canonical typed binary encoding"
)


def header(
    *,
    root: str = "/worktrees/legacy",
    metadata: dict | None = None,
    world_size: int = 100,
    schema_version: str = TRACE_SCHEMA_V3,
) -> dict:
    digest_algorithm = (
        V2_DIGEST_ALGORITHM
        if schema_version == TRACE_SCHEMA_V2
        else V3_DIGEST_ALGORITHM
    )
    observer = {
        "trace_detail": "digest",
        "timing_eligible": False,
        "digest_algorithm": digest_algorithm,
    }
    if schema_version == TRACE_SCHEMA_V3:
        observer.update(
            {
                "future_state_scope": "common future-affecting state",
                "message_checksum_scope": "common live-message state",
            }
        )
    return {
        "type": "header",
        "schema_version": schema_version,
        "metadata": metadata
        or {
            "run_id": "legacy-run",
            "implementation_id": "legacy",
            "binary_sha256": "1" * 64,
        },
        "resolved_config": {
            "config_file": f"{root}/config/comparison/laptop_smoke.yaml",
            "scenario_file": (
                f"{root}/comparison/environments/open/calibration/scenario.json"
            ),
            "obstacle_file": (
                f"{root}/comparison/environments/open/calibration/map.png"
            ),
            "world_size_m": world_size,
            "rng_seed": 104729,
        },
        "observer": observer,
    }


def robot(
    rid: int,
    digest_character: str,
    *,
    schema_version: str = TRACE_SCHEMA_V3,
) -> dict:
    return {
        "rid": rid,
        "state_schema": (
            "robot-frame-v1"
            if schema_version == TRACE_SCHEMA_V2
            else "robot-semantic-digest-v2"
        ),
        "state_sha256": digest_character * 64,
    }


def frame(
    tick: int,
    phase: str,
    *,
    digest_character: str = "a",
    schema_version: str = TRACE_SCHEMA_V3,
) -> dict:
    return {
        "type": "frame",
        "tick": tick,
        "phase": phase,
        "simulated_time_seconds": float(tick),
        "robots": [
            robot(0, digest_character, schema_version=schema_version),
            robot(1, "b", schema_version=schema_version),
        ],
        "metrics": {
            "robot_count": 2,
            "updated_this_frame": phase == "post_update",
        },
        "rng_state": {"normal_mt19937": "same-state"},
        "simulator_state": {"iterations": tick},
    }


def valid_records(
    *,
    schema_version: str = TRACE_SCHEMA_V3,
    **header_kwargs: object,
) -> list[dict]:
    return [
        header(schema_version=schema_version, **header_kwargs),
        frame(0, "pre_update", schema_version=schema_version),
        frame(1, "post_update", schema_version=schema_version),
        frame(1, "pre_update", schema_version=schema_version),
        frame(2, "post_update", schema_version=schema_version),
    ]


def write_trace(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, separators=(",", ":"), sort_keys=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


class TraceComparatorTests(unittest.TestCase):
    def test_historical_v2_trace_remains_strictly_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            left = directory / "historical-reference.jsonl"
            right = directory / "historical-optimized.jsonl"
            write_trace(
                left,
                valid_records(
                    schema_version=TRACE_SCHEMA_V2,
                    root="/tmp/historical-reference",
                ),
            )
            write_trace(
                right,
                valid_records(
                    schema_version=TRACE_SCHEMA_V2,
                    root="/tmp/historical-optimized",
                    metadata={
                        "run_id": "historical-optimized",
                        "implementation_id": "optimized",
                        "binary_sha256": "9" * 64,
                    },
                ),
            )

            result = TRACE.compare_traces(left, right)
            self.assertTrue(result.equal)
            self.assertEqual(result.frames_compared, 4)
            with TRACE.TraceReader(left) as reader:
                self.assertEqual(
                    reader.header.schema_version,
                    TRACE_SCHEMA_V2,
                )
                self.assertEqual(len(list(reader.frames())), 4)

    def test_mixed_v2_v3_comparison_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            historical = directory / "historical-v2.jsonl"
            current = directory / "current-v3.jsonl"
            write_trace(
                historical,
                valid_records(schema_version=TRACE_SCHEMA_V2),
            )
            write_trace(
                current,
                valid_records(schema_version=TRACE_SCHEMA_V3),
            )

            with self.assertRaises(TRACE.TraceFormatError) as raised:
                TRACE.compare_traces(historical, current)
            self.assertEqual(raised.exception.line, 1)
            self.assertEqual(
                raised.exception.path,
                "$.schema_version",
            )
            self.assertIn("mixed trace schema versions", str(raised.exception))

    def test_schema_specific_contract_mutations_are_rejected(self) -> None:
        variants: dict[str, tuple[list[dict], str]] = {}

        wrong_v3_algorithm = valid_records()
        wrong_v3_algorithm[0]["observer"][
            "digest_algorithm"
        ] = V2_DIGEST_ALGORITHM
        variants["wrong_v3_algorithm"] = (
            wrong_v3_algorithm,
            "$.observer.digest_algorithm",
        )

        missing_future_scope = valid_records()
        del missing_future_scope[0]["observer"]["future_state_scope"]
        variants["missing_future_scope"] = (
            missing_future_scope,
            "$.observer.future_state_scope",
        )

        empty_future_scope = valid_records()
        empty_future_scope[0]["observer"]["future_state_scope"] = " \t"
        variants["empty_future_scope"] = (
            empty_future_scope,
            "$.observer.future_state_scope",
        )

        missing_message_scope = valid_records()
        del missing_message_scope[0]["observer"]["message_checksum_scope"]
        variants["missing_message_scope"] = (
            missing_message_scope,
            "$.observer.message_checksum_scope",
        )

        empty_message_scope = valid_records()
        empty_message_scope[0]["observer"]["message_checksum_scope"] = ""
        variants["empty_message_scope"] = (
            empty_message_scope,
            "$.observer.message_checksum_scope",
        )

        wrong_v3_state_schema = valid_records()
        wrong_v3_state_schema[1]["robots"][0][
            "state_schema"
        ] = "robot-frame-v1"
        variants["wrong_v3_state_schema"] = (
            wrong_v3_state_schema,
            "$.robots[0].state_schema",
        )

        extra_v3_digest_key = valid_records()
        extra_v3_digest_key[1]["robots"][0]["unexpected"] = True
        variants["extra_v3_digest_key"] = (
            extra_v3_digest_key,
            "$.robots[0]",
        )

        wrong_v2_algorithm = valid_records(
            schema_version=TRACE_SCHEMA_V2
        )
        wrong_v2_algorithm[0]["observer"][
            "digest_algorithm"
        ] = V3_DIGEST_ALGORITHM
        variants["wrong_v2_algorithm"] = (
            wrong_v2_algorithm,
            "$.observer.digest_algorithm",
        )

        wrong_v2_state_schema = valid_records(
            schema_version=TRACE_SCHEMA_V2
        )
        wrong_v2_state_schema[1]["robots"][0][
            "state_schema"
        ] = "robot-semantic-digest-v2"
        variants["wrong_v2_state_schema"] = (
            wrong_v2_state_schema,
            "$.robots[0].state_schema",
        )

        unsupported_schema = valid_records()
        unsupported_schema[0]["schema_version"] = (
            "dancers-comparison-trace-v4"
        )
        variants["unsupported_schema"] = (
            unsupported_schema,
            "$.schema_version",
        )

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name, (records, expected_path) in variants.items():
                with self.subTest(name=name):
                    path = directory / f"{name}.jsonl"
                    output = directory / f"{name}.canonical.jsonl"
                    write_trace(path, records)
                    with self.assertRaises(
                        TRACE.TraceFormatError
                    ) as raised:
                        TRACE.canonicalize_trace(path, output)
                    self.assertEqual(
                        raised.exception.path,
                        expected_path,
                    )

    def test_provenance_and_worktree_paths_normalize_equal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            left = directory / "legacy.jsonl"
            right = directory / "optimized.jsonl"
            left_records = valid_records(root="/tmp/worktree-a")
            right_records = valid_records(
                root="C:\\different\\worktree-b",
                metadata={
                    "run_id": "optimized-run",
                    "implementation_id": "optimized",
                    "binary_sha256": "9" * 64,
                    "algorithm_revision": "new",
                },
            )
            write_trace(left, left_records)
            write_trace(right, right_records)

            result = TRACE.compare_traces(left, right)
            self.assertTrue(result.equal)
            self.assertEqual(result.frames_compared, 4)
            self.assertNotEqual(
                result.left_provenance, result.right_provenance
            )

            left_canonical = directory / "legacy.canonical.jsonl"
            right_canonical = directory / "optimized.canonical.jsonl"
            left_result = TRACE.canonicalize_trace(left, left_canonical)
            right_result = TRACE.canonicalize_trace(right, right_canonical)
            self.assertEqual(
                left_canonical.read_bytes(), right_canonical.read_bytes()
            )
            self.assertEqual(
                left_result.semantic_sha256, right_result.semantic_sha256
            )
            self.assertNotEqual(
                (directory / "legacy.canonical.jsonl.provenance.json").read_bytes(),
                (
                    directory / "optimized.canonical.jsonl.provenance.json"
                ).read_bytes(),
            )

    def test_robot_digest_semantic_mutation_reports_exact_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            left = directory / "left.jsonl"
            right = directory / "right.jsonl"
            left_records = valid_records()
            right_records = copy.deepcopy(left_records)
            right_records[2]["robots"][0]["state_sha256"] = "c" * 64
            write_trace(left, left_records)
            write_trace(right, right_records)

            result = TRACE.compare_traces(left, right)
            self.assertFalse(result.equal)
            assert result.mismatch
            self.assertEqual(result.mismatch.tick, 1)
            self.assertEqual(result.mismatch.phase, "post_update")
            self.assertEqual(
                result.mismatch.path, "$.robots[0].state_sha256"
            )
            self.assertIn("tick=1", result.mismatch.format())

    def test_non_path_header_and_numeric_type_mutations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            left = directory / "left.jsonl"
            right = directory / "right.jsonl"
            write_trace(left, valid_records())
            write_trace(right, valid_records(world_size=101))
            header_result = TRACE.compare_traces(left, right)
            self.assertFalse(header_result.equal)
            assert header_result.mismatch
            self.assertIsNone(header_result.mismatch.tick)
            self.assertEqual(header_result.mismatch.phase, "header")
            self.assertEqual(
                header_result.mismatch.path,
                "$.header.resolved_config.world_size_m",
            )

            right_records = valid_records()
            right_records[1]["metrics"]["robot_count"] = 2.0
            write_trace(right, right_records)
            frame_result = TRACE.compare_traces(left, right)
            self.assertFalse(frame_result.equal)
            assert frame_result.mismatch
            self.assertEqual(
                frame_result.mismatch.path, "$.metrics.robot_count"
            )
            self.assertEqual(frame_result.mismatch.reason, "JSON types differ")

    def test_environment_identifying_path_suffix_remains_semantic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            left = directory / "left.jsonl"
            right = directory / "right.jsonl"
            left_records = valid_records(root="/tmp/legacy")
            right_records = valid_records(root="/tmp/optimized")
            right_records[0]["resolved_config"]["scenario_file"] = (
                "/tmp/optimized/comparison/environments/"
                "fractal_high/calibration/scenario.json"
            )
            write_trace(left, left_records)
            write_trace(right, right_records)

            result = TRACE.compare_traces(left, right)
            self.assertFalse(result.equal)
            assert result.mismatch
            self.assertEqual(
                result.mismatch.path,
                "$.header.resolved_config.scenario_file",
            )

    def test_missing_duplicate_and_invalid_phases_are_rejected(self) -> None:
        variants = {
            "missing_terminal": [header(), frame(0, "pre_update")],
            "duplicate": [
                header(),
                frame(0, "pre_update"),
                frame(0, "pre_update"),
            ],
            "missing_pre": [
                header(),
                frame(0, "pre_update"),
                frame(1, "post_update"),
                frame(2, "post_update"),
            ],
            "invalid": [
                header(),
                frame(0, "before"),
                frame(1, "post_update"),
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name, records in variants.items():
                with self.subTest(name=name):
                    path = directory / f"{name}.jsonl"
                    output = directory / f"{name}.canonical.jsonl"
                    write_trace(path, records)
                    with self.assertRaises(TRACE.TraceFormatError):
                        TRACE.canonicalize_trace(path, output)

            valid = directory / "valid.jsonl"
            malformed_after_header_mismatch = (
                directory / "malformed-after-header-mismatch.jsonl"
            )
            write_trace(valid, valid_records())
            write_trace(
                malformed_after_header_mismatch,
                [
                    header(world_size=101),
                    frame(0, "pre_update"),
                ],
            )
            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.compare_traces(valid, malformed_after_header_mismatch)

    def test_malformed_json_duplicate_keys_and_bad_digest_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)

            malformed = directory / "malformed.jsonl"
            malformed.write_text('{"type":"header",}\n', encoding="utf-8")
            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.TraceReader(malformed)

            duplicate_key = directory / "duplicate-key.jsonl"
            duplicate_key.write_text(
                '{"type":"header","type":"header"}\n', encoding="utf-8"
            )
            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.TraceReader(duplicate_key)

            bad_digest = directory / "bad-digest.jsonl"
            records = valid_records()
            records[1]["robots"][0]["state_sha256"] = "not-a-sha"
            write_trace(bad_digest, records)
            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.canonicalize_trace(
                    bad_digest, directory / "bad-digest.canonical.jsonl"
                )

    def test_duplicate_robot_ids_and_empty_trace_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            duplicate = directory / "duplicate-robot.jsonl"
            records = valid_records()
            records[1]["robots"][1]["rid"] = 0
            write_trace(duplicate, records)
            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.canonicalize_trace(
                    duplicate, directory / "duplicate-robot.canonical.jsonl"
                )

            no_frames = directory / "no-frames.jsonl"
            write_trace(no_frames, [header()])
            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.canonicalize_trace(
                    no_frames, directory / "no-frames.canonical.jsonl"
                )

    def test_explicit_line_and_frame_bounds_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "trace.jsonl"
            write_trace(path, valid_records())

            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.TraceReader(
                    path,
                    TRACE.TraceLimits(
                        max_file_bytes=10_000,
                        max_line_bytes=64,
                        max_frames=10,
                        max_robots_per_frame=10,
                        max_json_depth=20,
                    ),
                )

            limits = TRACE.TraceLimits(
                max_file_bytes=100_000,
                max_line_bytes=10_000,
                max_frames=1,
                max_robots_per_frame=10,
                max_json_depth=20,
            )
            with self.assertRaises(TRACE.TraceFormatError):
                TRACE.canonicalize_trace(
                    path,
                    directory / "bounded.canonical.jsonl",
                    limits=limits,
                )


if __name__ == "__main__":
    unittest.main()
