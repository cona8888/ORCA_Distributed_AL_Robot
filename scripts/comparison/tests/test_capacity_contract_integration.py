from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.comparison import freeze_capacity_decision as DECISION
from scripts.comparison import run_paired as RUNNER
from scripts.comparison.tests.test_run_paired import RunnerFixture


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CapacityContractIntegrationTests(unittest.TestCase):
    def test_runner_session_is_directly_consumable_by_decision_freezer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary))
            grid = [2, 4]
            plan_path = fixture.make_plan(
                phase="calibration",
                mode="timing",
                pair_specs=[
                    ("open", 1, count) for count in grid
                ],
                timeout=300.0,
                capacity={
                    "environment": "open",
                    "reference_anchor_n": 2,
                    "grid": grid,
                    "max_points": len(grid),
                },
            )
            plan = fixture.load(plan_path)

            def controlled_host(
                candidate: RUNNER.Plan, filesystem_path: Path
            ) -> dict:
                return {
                    "checked_utc": "2026-07-28T00:00:00Z",
                    "filesystem_path": str(filesystem_path),
                    "free_disk_bytes": 10**12,
                    "minimum_free_disk_bytes": (
                        candidate.minimum_free_disk_bytes
                    ),
                    "disk_valid": True,
                    "power_mode": candidate.power_mode,
                    "power_probe": {
                        "returncode": 0,
                        "stdout": "AC Power\n",
                        "stderr": "",
                        "source": candidate.power_mode,
                        "valid": True,
                    },
                    "thermal_policy": candidate.thermal_policy,
                    "thermal_probe": {
                        "returncode": 0,
                        "stdout": "0\n",
                        "stderr": "",
                        "state": 0,
                        "valid": True,
                    },
                    "valid": True,
                }

            clock = [0.0]

            def advance(seconds: float) -> None:
                clock[0] += seconds

            output = fixture.root / "runner-session"
            with mock.patch.object(
                RUNNER,
                "_host_condition_snapshot",
                side_effect=controlled_host,
            ), mock.patch.object(
                RUNNER, "_validate_experiment_controls"
            ), mock.patch.object(
                RUNNER, "_cooldown_sleep", side_effect=advance
            ), mock.patch.object(
                RUNNER, "_cooldown_clock", side_effect=lambda: clock[0]
            ):
                session = RUNNER.execute(plan, output)
            self.assertTrue(session["all_pairs_valid"])

            raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            analysis_input = {
                "session_count": 1,
                "sessions": [
                    {
                        "session_id": "runner-produced-open-capacity",
                        "path": str(output),
                        "mode": "timing",
                        "hashes": {
                            "session_json_sha256": digest(
                                output / "session.json"
                            ),
                            "run_manifest_json_sha256": digest(
                                output / "run_manifest.json"
                            ),
                            "frozen_plan_sha256": digest(
                                output / "frozen_plan.json"
                            ),
                            "runs_jsonl_sha256": digest(
                                output / "runs.jsonl"
                            ),
                            "pairs_jsonl_sha256": digest(
                                output / "pairs.jsonl"
                            ),
                            "capacity_json_sha256": digest(
                                output / "capacity.json"
                            ),
                        },
                    }
                ],
            }
            artifacts = raw_plan["artifacts"]
            with mock.patch.object(DECISION, "FAMILIES", ("open",)):
                verified, plans = DECISION._verify_session_records(
                    analysis_input,
                    analyzer_sha256=artifacts[
                        "analysis_revision"
                    ]["sha256"],
                    experiment_manifest_sha256=artifacts[
                        "experiment_manifest"
                    ]["sha256"],
                    environment_manifest_sha256=artifacts[
                        "environment_manifest"
                    ]["sha256"],
                )
            self.assertEqual(
                verified[0]["hashes"]["frozen_plan_sha256"],
                plan.plan_sha256,
            )
            self.assertEqual(
                plans["open"]["session_id"],
                "runner-produced-open-capacity",
            )


if __name__ == "__main__":
    unittest.main()
