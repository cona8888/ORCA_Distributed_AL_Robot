from __future__ import annotations

import copy
import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.comparison import freeze_visual_plan as FREEZER


REPO_ROOT = Path(__file__).resolve().parents[3]


class VisualPlanFreezerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = FREEZER.build_bundle(
            REPO_ROOT, require_git_clean=False
        )

    def test_real_bundle_is_the_exact_registered_crossing(self) -> None:
        plan = self.bundle.plan
        self.assertEqual(plan["schema_version"], FREEZER.PLAN_SCHEMA)
        self.assertEqual(len(plan["scenes"]), 250)
        self.assertEqual(len(self.bundle.scenarios), 25)
        self.assertEqual(plan["expected_attempt_count"], 500)
        self.assertEqual(
            set(plan["environment_descriptors"]),
            set(FREEZER.FAMILIES),
        )
        self.assertEqual(
            plan["environment_descriptors"]["fractal_high"][
                "boundary_box_counting"
            ]["dimension"],
            1.15060445,
        )
        self.assertEqual(
            plan["scenes"][0]["scene_id"], "open-n8-seed01"
        )
        self.assertEqual(
            plan["scenes"][-1]["scene_id"],
            "fractal_high-n128-seed10",
        )
        self.assertEqual(
            len({scene["scene_id"] for scene in plan["scenes"]}), 250
        )
        self.assertEqual(
            len({scene["output_relpath"] for scene in plan["scenes"]}),
            250,
        )
        self.assertEqual(
            self.bundle.plan_bytes,
            FREEZER.canonical_json_bytes(plan),
        )
        for relative, data in self.bundle.scenarios.items():
            self.assertTrue(
                relative.as_posix().startswith(
                    FREEZER.PLAN_DIRECTORY.as_posix() + "/scenarios/"
                )
            )
            self.assertTrue(data.endswith(b"\n"))
            parsed = json.loads(data)
            count = parsed["num_robots"]
            self.assertEqual(len(parsed["decisions"]), count)
            self.assertEqual(len(parsed["waypoints"]), count)

    def test_plan_binds_diagnostic_only_tools_and_methods(self) -> None:
        plan = self.bundle.plan
        self.assertTrue(plan["diagnostic_only"])
        self.assertFalse(plan["compute_claim"])
        self.assertFalse(plan["timing_unlock"])
        self.assertFalse(plan["paper_replication_evidence"])
        self.assertEqual(
            set(plan["artifacts"]), set(FREEZER.ARTIFACT_PATHS)
        )
        for key, relative in FREEZER.ARTIFACT_PATHS.items():
            record = plan["artifacts"][key]
            self.assertEqual(record["path"], relative.as_posix())
            self.assertEqual(
                record["sha256"],
                hashlib.sha256(
                    REPO_ROOT.joinpath(*relative.parts).read_bytes()
                ).hexdigest(),
            )
        self.assertEqual(
            plan["methods"]["reference"]["source_revision"],
            FREEZER.REFERENCE_REVISION,
        )
        self.assertEqual(
            plan["methods"]["optimized"]["source_revision"],
            FREEZER.OPTIMIZED_REVISION,
        )
        forbidden = {"wall_seconds", "cpu_seconds", "peak_rss_mib"}
        self.assertTrue(forbidden.isdisjoint(plan))

    def test_crossing_mutation_fails_closed(self) -> None:
        changed = copy.deepcopy(self.bundle.plan)
        changed["scenes"][1], changed["scenes"][2] = (
            changed["scenes"][2],
            changed["scenes"][1],
        )
        with self.assertRaisesRegex(
            FREEZER.PlanFreezeError, "crossing differs"
        ):
            FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["expected_scene_count"] = 249
        with self.assertRaisesRegex(
            FREEZER.PlanFreezeError, "expected_scene_count differs"
        ):
            FREEZER.validate_plan(changed)

    def test_publication_is_read_only_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            destination = root / "published"
            plan_path = FREEZER.publish_bundle(
                self.bundle, root, destination
            )
            self.assertEqual(
                plan_path.read_bytes(), self.bundle.plan_bytes
            )
            published = [plan_path, *destination.rglob("n-*.json")]
            self.assertEqual(len(published), 26)
            for path in published:
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o444)
            directories = [
                destination,
                *(
                    path
                    for path in destination.rglob("*")
                    if path.is_dir()
                ),
            ]
            for directory in directories:
                self.assertEqual(
                    stat.S_IMODE(directory.stat().st_mode),
                    0o555,
                )
            with self.assertRaises(FileExistsError):
                FREEZER.publish_bundle(
                    self.bundle, root, destination
                )

    def test_result_root_guard_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            result = FREEZER._resolve(
                root, FREEZER.RESULT_ROOT, "test result root"
            )
            self.assertEqual(
                result,
                root / "results" / "simulator-demo",
            )
            outside = root.parent / "outside-plan"
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "escapes repository"
            ):
                FREEZER.publish_bundle(
                    self.bundle, root, outside
                )

    def test_resolver_rejects_symlinked_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "target"
            target.mkdir()
            (root / "alias").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "symlink component"
            ):
                FREEZER._resolve(
                    root,
                    FREEZER.PurePosixPath("alias", "plan.json"),
                    "test plan",
                )


if __name__ == "__main__":
    unittest.main()
