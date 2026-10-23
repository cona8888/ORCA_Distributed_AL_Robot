from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from typing import Any


REPO = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_BINARY = REPO / "build" / "orca-wrapper-smoke" / "dancers-orca-wrapper"
EXPLICIT_BINARY = os.environ.get("ORCA_WRAPPER_BINARY")
BINARY = pathlib.Path(EXPLICIT_BINARY) if EXPLICIT_BINARY else DEFAULT_BINARY
FIXTURES = pathlib.Path(__file__).resolve().parent


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def scenario(starts: list[list[float]], goals: list[list[float]]) -> dict[str, Any]:
    assert len(starts) == len(goals)
    return {
        "decisions": {str(index): 0 for index in range(len(starts))},
        "metadata": {
            "classification": "non-confirmatory wrapper test",
            "scenario_kind": "dancers-orca-generated-smoke-v1",
        },
        "num_robots": len(starts),
        "seed_robots": [],
        "waypoints": {
            str(index): [start, goals[index]]
            for index, start in enumerate(starts)
        },
    }


@unittest.skipUnless(
    EXPLICIT_BINARY is not None,
    "set ORCA_WRAPPER_BINARY to run the compiled-wrapper integration suite",
)
class Rvo2WrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not BINARY.is_file():
            raise AssertionError(f"explicit wrapper binary is absent: {BINARY}")

    def run_smoke(
        self,
        root: pathlib.Path,
        value: object,
        *,
        scene_id: str,
        cadence: str = "smoke-main",
        observer: str = "positions",
        check: bool = True,
        extra: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        root.mkdir()
        raw = canonical_bytes(value)
        scenario_path = root / "scenario.json"
        scenario_path.write_bytes(raw)
        command = [
            str(BINARY),
            "smoke",
            "--scenario",
            str(scenario_path),
            "--scenario-sha256",
            hashlib.sha256(raw).hexdigest(),
            "--scene-id",
            scene_id,
            "--cadence",
            cadence,
            "--observer",
            observer,
            "--summary-out",
            str(root / "summary.json"),
        ]
        if observer == "positions":
            command.extend(["--trace-out", str(root / "trace.ndjson")])
        if extra:
            command.extend(extra)
        completed = subprocess.run(command, text=True, capture_output=True)
        if check and completed.returncode != 0:
            self.fail(f"wrapper failed: {completed.stderr}")
        return completed

    def test_crossing_is_byte_deterministic_and_parameter_audited(self) -> None:
        fixture_raw = (FIXTURES / "smoke_crossing.json").read_bytes()
        value = json.loads(fixture_raw)
        self.assertEqual(canonical_bytes(value), fixture_raw)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_smoke(root / "first", value, scene_id="smoke-crossing")
            self.run_smoke(root / "second", value, scene_id="smoke-crossing")
            for filename in ("summary.json", "trace.ndjson"):
                self.assertEqual(
                    (root / "first" / filename).read_bytes(),
                    (root / "second" / filename).read_bytes(),
                )
            summary = json.loads((root / "first" / "summary.json").read_bytes())
            self.assertEqual(summary["schema_version"], "dancers-orca-summary-v1")
            self.assertEqual(summary["completed_native_steps"], 20)
            self.assertEqual(summary["trace_frames_written"], 21)
            self.assertEqual(
                summary["parameter_audit"]["accepted"]["time_step"]["hex"],
                "3dcccccd",
            )
            self.assertTrue(
                summary["parameter_audit"]["verified_through_public_getters"]
            )
            lines = (root / "first" / "trace.ndjson").read_text().splitlines()
            self.assertEqual(len(lines), 22)
            header = json.loads(lines[0])
            self.assertEqual(header["type"], "header")
            self.assertNotIn("attempt_sequence", header["input"])
            self.assertNotIn("repetition", header["input"])

    def test_goal_latch_stays_zero_through_fixed_ceiling(self) -> None:
        fixture_raw = (FIXTURES / "smoke_goal_latch.json").read_bytes()
        value = json.loads(fixture_raw)
        self.assertEqual(canonical_bytes(value), fixture_raw)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_smoke(root / "latch", value, scene_id="smoke-goal-latch")
            rows = [
                json.loads(line)
                for line in (root / "latch" / "trace.ndjson").read_text().splitlines()
            ]
            frames = [row for row in rows if row["type"] == "frame"]
            robot_zero = [frame["robots"][0] for frame in frames]
            self.assertEqual(len(robot_zero), 21)
            self.assertTrue(all(robot["goal_latched"] for robot in robot_zero))
            self.assertTrue(
                all(robot["first_goal_native_step"] == 0 for robot in robot_zero)
            )
            self.assertTrue(
                all(
                    robot["preferred_velocity"]["hex"]
                    == ["00000000", "00000000"]
                    for robot in robot_zero
                )
            )
            self.assertGreater(
                max(robot["goal_distance_m"] for robot in robot_zero), 1.0
            )
            summary = json.loads((root / "latch" / "summary.json").read_bytes())
            self.assertEqual(summary["termination"], "fixed_ceiling")
            self.assertEqual(summary["completed_native_steps"], 20)

    def test_half_cadence_emits_only_even_native_steps(self) -> None:
        value = scenario([[-4.0, 0.0], [4.0, 0.0]], [[4.0, 0.0], [-4.0, 0.0]])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_smoke(
                root / "half",
                value,
                scene_id="smoke-half-grid",
                cadence="smoke-half",
            )
            rows = [
                json.loads(line)
                for line in (root / "half" / "trace.ndjson").read_text().splitlines()
            ]
            frames = [row for row in rows if row["type"] == "frame"]
            self.assertEqual([row["native_step"] for row in frames], list(range(0, 21, 2)))
            self.assertEqual([row["common_tick"] for row in frames], list(range(11)))
            self.assertEqual([row["nominal_time_seconds"] for row in frames], [i / 10 for i in range(11)])

    def test_numeric_robot_order_for_n20_without_trace(self) -> None:
        starts = [[float(index * 3), 0.0] for index in range(20)]
        goals = [[float(index * 3), 10.0] for index in range(20)]
        value = scenario(starts, goals)
        value["decisions"] = {str(index): 19 - index for index in range(20)}
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_smoke(
                root / "n20",
                value,
                scene_id="smoke-n20-order",
                observer="none",
            )
            summary = json.loads((root / "n20" / "summary.json").read_bytes())
            agents = summary["parameter_audit"]["accepted"]["agents"]
            self.assertEqual([agent["rid"] for agent in agents], list(range(20)))
            self.assertEqual(agents[2]["initial_position"]["value"], [6.0, 0.0])
            self.assertEqual(agents[10]["initial_position"]["value"], [30.0, 0.0])
            self.assertEqual(summary["trace_frames_written"], 0)
            self.assertIsNone(summary["trace_sha256"])
            self.assertFalse((root / "n20" / "trace.ndjson").exists())

    def test_outputs_are_create_exclusive_and_cli_is_strict(self) -> None:
        value = scenario([[-4.0, 0.0], [4.0, 0.0]], [[4.0, 0.0], [-4.0, 0.0]])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            target = root / "exclusive"
            self.run_smoke(target, value, scene_id="smoke-exclusive")
            before = {
                name: (target / name).read_bytes()
                for name in ("summary.json", "trace.ndjson")
            }
            raw = (target / "scenario.json").read_bytes()
            repeated = subprocess.run(
                [
                    str(BINARY),
                    "smoke",
                    "--scenario",
                    str(target / "scenario.json"),
                    "--scenario-sha256",
                    hashlib.sha256(raw).hexdigest(),
                    "--scene-id",
                    "smoke-exclusive",
                    "--cadence",
                    "smoke-main",
                    "--observer",
                    "positions",
                    "--summary-out",
                    str(target / "summary.json"),
                    "--trace-out",
                    str(target / "trace.ndjson"),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(repeated.returncode, 2)
            self.assertIn("already exists", repeated.stderr)
            self.assertEqual(
                before,
                {
                    name: (target / name).read_bytes()
                    for name in ("summary.json", "trace.ndjson")
                },
            )
            invalid = subprocess.run(
                [str(BINARY), "smoke", "--unknown", "value"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("unknown option", invalid.stderr)
            unbound = subprocess.run(
                [
                    str(BINARY),
                    "run",
                    "--input-root",
                    str(root / "not-an-input-root"),
                    "--attempt-sequence",
                    "0",
                    "--summary-out",
                    str(root / "unbound-summary.json"),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(unbound.returncode, 2)
            self.assertIn("smoke-only", unbound.stderr)
            validate_unbound = subprocess.run(
                [
                    str(BINARY),
                    "validate",
                    "--input-root",
                    str(root / "not-an-input-root"),
                    "--attempt-sequence",
                    "0",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(validate_unbound.returncode, 2)
            self.assertIn("smoke-only", validate_unbound.stderr)
            validate_with_output = subprocess.run(
                [
                    str(BINARY),
                    "validate",
                    "--input-root",
                    str(root / "not-an-input-root"),
                    "--attempt-sequence",
                    "0",
                    "--summary-out",
                    str(root / "forbidden-validation-output.json"),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(validate_with_output.returncode, 2)
            self.assertIn("unknown option", validate_with_output.stderr)
            self.assertFalse((root / "forbidden-validation-output.json").exists())


if __name__ == "__main__":
    unittest.main()
