from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.comparison import freeze_capacity_decision as FREEZER


ROOT = Path(__file__).resolve().parents[3]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DecisionFixture:
    def __init__(
        self,
        root: Path,
        *,
        lie_about_candidate: bool = False,
        maximum_wall_seconds: float = 101.0,
        candidate_kind: str = "previous",
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.analyzer = root / "analyze_results.py"
        self.analyzer.write_text("# frozen test analyzer\n", encoding="utf-8")
        environment = (
            ROOT / "comparison" / "environments" / "manifest.json"
        )
        experiment = json.loads(
            (
                ROOT / "docs" / "comparison" / "experiment_manifest.json"
            ).read_text(encoding="utf-8")
        )
        experiment["software_versions"]["analysis_revision"] = {
            "path": str(self.analyzer),
            "sha256": digest(self.analyzer),
        }
        experiment["environment_and_map"]["map_manifest_sha256"] = digest(
            environment
        )
        self.experiment = root / "experiment_manifest.json"
        self.experiment.write_text(
            json.dumps(experiment, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sessions = []
        decisions = {}
        for family in FREEZER.FAMILIES:
            design = experiment["calibration_design"]["environments"][
                family
            ]
            grid = design["grid"]
            session = root / f"session-{family}"
            session.mkdir()
            frozen_plan = {
                "schema_version": "dancers-laptop-comparison-plan-v1",
                "phase": "calibration",
                "mode": "timing",
                "fixed_work_timesteps": 100,
                "timestep_seconds": 0.1,
                "idle_cooldown_seconds": 300,
                "timeout_seconds": 300.0,
                "artifacts": {
                    "analysis_revision": {
                        "sha256": digest(self.analyzer)
                    },
                    "experiment_manifest": {
                        "sha256": digest(self.experiment)
                    },
                    "environment_manifest": {
                        "sha256": digest(environment)
                    },
                },
                "capacity_search": {
                    "environment": family,
                    "reference_anchor_n": design["reference_anchor_n"],
                    "grid": grid,
                    "max_points": len(grid),
                },
            }
            (session / "frozen_plan.json").write_text(
                json.dumps(frozen_plan, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            for filename in (
                "session.json",
                "run_manifest.json",
                "runs.jsonl",
                "pairs.jsonl",
                "capacity.json",
            ):
                (session / filename).write_text(
                    f"test-only {family} {filename}\n",
                    encoding="utf-8",
                )
            hashes = {
                "session_json_sha256": digest(session / "session.json"),
                "run_manifest_json_sha256": digest(
                    session / "run_manifest.json"
                ),
                "frozen_plan_sha256": digest(
                    session / "frozen_plan.json"
                ),
                "runs_jsonl_sha256": digest(session / "runs.jsonl"),
                "pairs_jsonl_sha256": digest(session / "pairs.jsonl"),
                "capacity_json_sha256": digest(session / "capacity.json"),
            }
            sessions.append(
                {
                    "session_id": f"session-{family}",
                    "path": str(session.resolve()),
                    "mode": "timing",
                    "hashes": hashes,
                }
            )
            if candidate_kind == "anchor":
                candidate = grid[0]
            elif candidate_kind == "ceiling":
                candidate = grid[-1]
            elif candidate_kind == "previous":
                candidate = grid[-2] if len(grid) > 1 else grid[-1]
            else:
                raise ValueError(f"unknown candidate kind {candidate_kind}")
            per_grid = {}
            for count in grid:
                passing = count <= candidate
                gates = {
                    key: passing for key in FREEZER.FULL_GATE_KEYS
                }
                gates["all_gates_pass"] = passing
                per_grid[str(count)] = gates
            decisions[family] = {
                "reference_anchor_n": design["reference_anchor_n"],
                "grid": grid,
                "per_grid": per_grid,
                "calibration_passing_n": (
                    grid[-1] if lie_about_candidate else candidate
                ),
                "next_grid_point": (
                    grid[grid.index(candidate) + 1]
                    if grid.index(candidate) + 1 < len(grid)
                    else None
                ),
                "completed_measured_wall_seconds": [
                    10.0,
                    maximum_wall_seconds,
                ],
            }
        analysis = {
            "schema_version": "dancers-comparison-analysis-v1",
            "analysis_revision_sha256": digest(self.analyzer),
            "input": {
                "phase": "calibration",
                "session_count": len(sessions),
                "sessions": sessions,
                "shared_artifacts": {
                    "analysis_revision_sha256": digest(self.analyzer),
                    "experiment_manifest_sha256": digest(self.experiment),
                    "environment_manifest_sha256": digest(environment),
                },
            },
            "capacity": {"calibration_decisions": decisions},
        }
        self.analysis = root / "analysis.json"
        self.analysis.write_text(
            json.dumps(analysis, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.environment = environment

    def freeze(self, output: Path) -> FREEZER.FrozenCapacityDecision:
        return FREEZER.freeze_capacity_decision(
            calibration_analysis=self.analysis,
            analyzer=self.analyzer,
            experiment_manifest=self.experiment,
            environment_manifest=self.environment,
            output=output,
        )


class CapacityDecisionFreezerTests(unittest.TestCase):
    def test_derives_highest_all_gate_n_and_timeout_then_writes_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = DecisionFixture(root)
            output = root / "decision.json"
            frozen = fixture.freeze(output)
            decision, artifact = FREEZER.load_capacity_decision(output)
            self.assertEqual(frozen.sha256, digest(output))
            self.assertEqual(artifact["sha256"], digest(output))
            self.assertFalse(output.stat().st_mode & 0o222)
            self.assertEqual(
                decision["status"], "frozen-before-validation"
            )
            self.assertEqual(
                decision["seeds"]["validation"], list(range(11, 31))
            )
            open_record = decision["environments"][0]
            self.assertEqual(open_record["candidate_n_star"], 1500)
            self.assertEqual(open_record["next_grid_point"], 1935)
            self.assertEqual(
                open_record["validation_timing_counts"],
                [500, 1000, 1500, 1935],
            )
            self.assertEqual(
                open_record["validation_fidelity_counts"],
                [500, 1000, 1500, 1935],
            )
            self.assertEqual(
                open_record["validation_timeout_seconds"], 310.0
            )
            before = output.read_bytes()
            with self.assertRaisesRegex(
                FileExistsError, "refusing to overwrite"
            ):
                fixture.freeze(output)
            self.assertEqual(output.read_bytes(), before)

    def test_refuses_runner_provisional_candidate_and_excessive_timeout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dishonest = DecisionFixture(
                root / "dishonest", lie_about_candidate=True
            )
            with self.assertRaisesRegex(
                FREEZER.DecisionError, "not the highest pass"
            ):
                dishonest.freeze(root / "dishonest-decision.json")
            self.assertFalse((root / "dishonest-decision.json").exists())

            excessive = DecisionFixture(
                root / "excessive", maximum_wall_seconds=601.0
            )
            with self.assertRaisesRegex(
                FREEZER.DecisionError, "exceeds 1800"
            ):
                excessive.freeze(root / "excessive-decision.json")
            self.assertFalse((root / "excessive-decision.json").exists())

    def test_anchor_fallback_and_ceiling_candidate_are_freezeable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            negative = DecisionFixture(
                root / "negative", candidate_kind="anchor"
            )
            negative_frozen = negative.freeze(root / "negative.json")
            negative_value, _ = FREEZER.load_capacity_decision(
                negative_frozen.path
            )
            self.assertEqual(
                negative_value["environments"][0]["candidate_n_star"], 500
            )
            self.assertEqual(
                negative_value["environments"][0]["next_grid_point"], 1000
            )

            ceiling = DecisionFixture(
                root / "ceiling", candidate_kind="ceiling"
            )
            ceiling_frozen = ceiling.freeze(root / "ceiling.json")
            ceiling_value, _ = FREEZER.load_capacity_decision(
                ceiling_frozen.path
            )
            self.assertEqual(
                ceiling_value["environments"][0]["candidate_n_star"], 1935
            )
            self.assertIsNone(
                ceiling_value["environments"][0]["next_grid_point"]
            )


if __name__ == "__main__":
    unittest.main()
