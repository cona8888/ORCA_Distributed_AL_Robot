from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from scripts.comparison import freeze_visual_plan_v2 as FREEZER
from scripts.comparison import run_visual_diagnostic_v2 as CONTROLLER


REPO_ROOT = Path(__file__).resolve().parents[3]


class VisualPlanFreezerV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        plan_path = REPO_ROOT.joinpath(*FREEZER.PLAN_PATH.parts)
        if plan_path.exists() or plan_path.is_symlink():
            if plan_path.is_symlink() or not plan_path.is_file():
                raise AssertionError(
                    "published v2 plan must be a regular non-symlink file"
                )
            plan_bytes = plan_path.read_bytes()
            plan = FREEZER.validate_plan(
                FREEZER._strict_json_bytes(
                    plan_bytes, "published v2 visual plan"
                )
            )
            if plan_bytes != FREEZER.canonical_json_bytes(plan):
                raise AssertionError(
                    "published v2 plan must use canonical JSON bytes"
                )
            parsed = CONTROLLER.load_plan(plan_path)
            if (
                parsed.execution_epoch_id != FREEZER.EXECUTION_EPOCH_ID
                or len(parsed.scenes) != FREEZER.EXPECTED_SCENE_COUNT
            ):
                raise AssertionError(
                    "published v2 plan is not controller-compatible"
                )

            scenario_hashes: dict[PurePosixPath, str] = {}
            for scene in plan["scenes"]:
                record = scene["scenario"]
                relative = PurePosixPath(record["path"])
                prior = scenario_hashes.setdefault(
                    relative, record["sha256"]
                )
                if prior != record["sha256"]:
                    raise AssertionError(
                        "published v2 scenario hashes conflict"
                    )
            scenarios: dict[PurePosixPath, bytes] = {}
            for relative, expected_sha256 in scenario_hashes.items():
                path = REPO_ROOT.joinpath(*relative.parts)
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise AssertionError(
                        "published v2 scenario must be a regular file"
                    )
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != expected_sha256:
                    raise AssertionError(
                        "published v2 scenario SHA-256 differs"
                    )
                scenarios[relative] = data
            cls.bundle = FREEZER.PlanBundle(
                plan=plan,
                plan_bytes=plan_bytes,
                scenarios=scenarios,
            )
        else:
            cls.bundle = FREEZER.build_bundle(
                REPO_ROOT, require_git_clean=False
            )
        cls.registry = json.loads(
            (REPO_ROOT / FREEZER.RECOVERY_REGISTRY_PATH).read_text(
                encoding="utf-8"
            )
        )

    def test_real_bundle_is_exact_controller_compatible_recovery(self) -> None:
        plan = self.bundle.plan
        self.assertEqual(plan["schema_version"], CONTROLLER.PLAN_SCHEMA)
        self.assertEqual(plan["plan_id"], CONTROLLER.PLAN_ID)
        self.assertEqual(
            plan["execution_epoch_id"], CONTROLLER.EXECUTION_EPOCH_ID
        )
        self.assertEqual(plan["run_id_prefix"], CONTROLLER.RUN_ID_PREFIX)
        self.assertEqual(
            plan["config_cli_path_policy"],
            CONTROLLER.CONFIG_CLI_PATH_POLICY,
        )
        self.assertEqual(plan["output_schemas"], CONTROLLER.OUTPUT_SCHEMAS)
        self.assertEqual(
            plan["execution"]["failure_policy"],
            CONTROLLER.FAILURE_POLICY,
        )
        self.assertEqual(len(plan["scenes"]), 250)
        self.assertEqual(len(self.bundle.scenarios), 25)
        self.assertEqual(plan["expected_attempt_count"], 500)
        self.assertEqual(
            set(plan["artifacts"]), CONTROLLER.MANDATORY_ARTIFACT_KEYS
        )
        self.assertEqual(
            plan["recovery"],
            {
                "commit": FREEZER.RECOVERY_COMMIT,
                "registry": {
                    "path": FREEZER.RECOVERY_REGISTRY_PATH.as_posix(),
                    "sha256": FREEZER.RECOVERY_REGISTRY_SHA256,
                },
                "addendum": {
                    "path": FREEZER.RECOVERY_ADDENDUM_PATH.as_posix(),
                    "sha256": FREEZER.RECOVERY_ADDENDUM_SHA256,
                },
            },
        )
        self.assertEqual(
            plan["scenes"][0]["scene_id"], "open-n8-seed01"
        )
        self.assertEqual(
            plan["scenes"][-1]["scene_id"],
            "fractal_high-n128-seed10",
        )
        config_arguments = {
            scene["config_cli_argument"] for scene in plan["scenes"]
        }
        run_ids = {
            run_id
            for scene in plan["scenes"]
            for run_id in scene["run_ids"].values()
        }
        self.assertEqual(len(config_arguments), 250)
        self.assertEqual(len(run_ids), 500)
        self.assertTrue(
            all(
                argument.startswith(
                    FREEZER.ALLOWED_OUTPUT_ROOT.as_posix() + "/"
                )
                and " " not in argument
                for argument in config_arguments
            )
        )
        self.assertTrue(
            all(
                run_id.startswith(FREEZER.RUN_ID_PREFIX + "__")
                for run_id in run_ids
            )
        )
        self.assertEqual(
            self.bundle.plan_bytes,
            FREEZER.canonical_json_bytes(plan),
        )

    def test_committed_controller_parser_accepts_published_plan_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            destination = root.joinpath(*FREEZER.PLAN_DIRECTORY.parts)
            plan_path = FREEZER.publish_bundle(
                self.bundle, root, destination
            )
            parsed = CONTROLLER.load_plan(plan_path)
            self.assertEqual(
                parsed.execution_epoch_id, FREEZER.EXECUTION_EPOCH_ID
            )
            self.assertEqual(len(parsed.scenes), 250)
            self.assertEqual(
                {
                    run_id
                    for scene in parsed.scenes
                    for run_id in scene.run_ids.values()
                },
                {
                    run_id
                    for scene in self.bundle.plan["scenes"]
                    for run_id in scene["run_ids"].values()
                },
            )

    def test_scenarios_are_committed_v1_bytes_republished_disjointly(
        self,
    ) -> None:
        self.assertEqual(len(self.bundle.scenarios), 25)
        for v2_relative, data in self.bundle.scenarios.items():
            within_plan = v2_relative.relative_to(FREEZER.PLAN_DIRECTORY)
            v1_relative = FREEZER.V1_PLAN_DIRECTORY / within_plan
            self.assertNotEqual(v1_relative, v2_relative)
            self.assertEqual(
                data,
                REPO_ROOT.joinpath(*v1_relative.parts).read_bytes(),
            )
            self.assertEqual(
                data,
                FREEZER._committed_bytes(
                    REPO_ROOT,
                    FREEZER.V1_PLAN_COMMIT,
                    v1_relative,
                    "test v1 scenario",
                ),
            )
            self.assertEqual(
                hashlib.sha256(data).hexdigest(),
                next(
                    scene["scenario"]["sha256"]
                    for scene in self.bundle.plan["scenes"]
                    if scene["scenario"]["path"]
                    == v2_relative.as_posix()
                ),
            )

    def test_committed_and_regenerated_scenario_drift_fails_closed(
        self,
    ) -> None:
        target = (
            FREEZER.V1_PLAN_DIRECTORY / "scenarios/open/n-8.json"
        )
        original_committed_bytes = FREEZER._committed_bytes

        def drifted_commit(
            root: Path,
            commit: str,
            relative: PurePosixPath,
            label: str,
        ) -> bytes:
            data = original_committed_bytes(root, commit, relative, label)
            return data + b"x" if relative == target else data

        with mock.patch.object(
            FREEZER, "_committed_bytes", side_effect=drifted_commit
        ):
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "scenario bytes differ"
            ):
                FREEZER._load_v1_plan(REPO_ROOT)

        (
            _,
            _,
            scenario_records,
            scenario_bytes,
        ) = FREEZER._load_v1_plan(REPO_ROOT)
        changed_bytes = dict(scenario_bytes)
        changed_bytes[("open", 8)] += b"x"
        environment_manifest = json.loads(
            (
                REPO_ROOT
                / FREEZER.ARTIFACT_PATHS["environment_manifest"]
            ).read_text(encoding="utf-8")
        )
        cases = FREEZER._environment_cases(
            REPO_ROOT, environment_manifest
        )
        with self.assertRaisesRegex(
            FREEZER.PlanFreezeError,
            "regenerated science differs from v1",
        ):
            FREEZER._family_materials(
                REPO_ROOT,
                "open",
                cases["open"],
                scenario_records,
                changed_bytes,
            )

    def test_scientific_projection_is_exactly_v1(self) -> None:
        v1_plan = json.loads(
            (REPO_ROOT / FREEZER.V1_PLAN_PATH).read_text(encoding="utf-8")
        )
        FREEZER._verify_scientific_projection(v1_plan, self.bundle.plan)
        for key in FREEZER.COMMON_SCIENCE_ARTIFACT_KEYS:
            self.assertEqual(
                self.bundle.plan["artifacts"][key],
                v1_plan["artifacts"][key],
            )
        self.assertEqual(self.bundle.plan["methods"], v1_plan["methods"])
        self.assertEqual(
            self.bundle.plan["environment_descriptors"],
            v1_plan["environment_descriptors"],
        )

    def test_recovery_and_controller_pins_match_committed_blobs(self) -> None:
        pins = (
            (
                FREEZER.CONTRACT_COMMIT,
                FREEZER.CONTRACT_PATH,
                FREEZER.CONTRACT_SHA256,
                "parent contract",
            ),
            (
                FREEZER.RECOVERY_COMMIT,
                FREEZER.RECOVERY_REGISTRY_PATH,
                FREEZER.RECOVERY_REGISTRY_SHA256,
                "registry",
            ),
            (
                FREEZER.RECOVERY_COMMIT,
                FREEZER.RECOVERY_ADDENDUM_PATH,
                FREEZER.RECOVERY_ADDENDUM_SHA256,
                "addendum",
            ),
            (
                FREEZER.RECOVERY_COMMIT,
                FREEZER.DARGS_HEADER_PATH,
                FREEZER.DARGS_HEADER_SHA256,
                "DArgs header",
            ),
            (
                FREEZER.V1_PLAN_COMMIT,
                FREEZER.V1_PLAN_PATH,
                FREEZER.V1_PLAN_SHA256,
                "v1 plan",
            ),
            (
                FREEZER.CONTROLLER_COMMIT,
                FREEZER.ARTIFACT_PATHS["controller"],
                FREEZER.CONTROLLER_SHA256,
                "controller",
            ),
            (
                FREEZER.CONTROLLER_COMMIT,
                FREEZER.CONTROLLER_TEST_PATH,
                FREEZER.CONTROLLER_TEST_SHA256,
                "controller tests",
            ),
        )
        for commit, relative, digest, label in pins:
            data = FREEZER._verify_pinned_working_blob(
                REPO_ROOT,
                commit=commit,
                relative=relative,
                sha256=digest,
                label=label,
            )
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        FREEZER._verify_commit_chain(REPO_ROOT)
        self.assertEqual(
            FREEZER._git(REPO_ROOT, ("remote",)).strip(), ""
        )
        for commit, relative, _, label in pins:
            with self.subTest(drifted_pin=label):
                with self.assertRaisesRegex(
                    FREEZER.PlanFreezeError, "SHA-256 differs"
                ):
                    FREEZER._verify_pinned_working_blob(
                        REPO_ROOT,
                        commit=commit,
                        relative=relative,
                        sha256="0" * 64,
                        label=label,
                    )
        original_committed_bytes = FREEZER._committed_bytes

        def drifted_blob(
            root: Path,
            commit: str,
            relative: PurePosixPath,
            label: str,
        ) -> bytes:
            return (
                original_committed_bytes(root, commit, relative, label)
                + b"x"
            )

        with mock.patch.object(
            FREEZER, "_committed_bytes", side_effect=drifted_blob
        ):
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "registered commit"
            ):
                FREEZER._verify_pinned_working_blob(
                    REPO_ROOT,
                    commit=FREEZER.RECOVERY_COMMIT,
                    relative=FREEZER.RECOVERY_ADDENDUM_PATH,
                    sha256=FREEZER.RECOVERY_ADDENDUM_SHA256,
                    label="recovery addendum",
                )

    def test_test_files_are_clean_prerequisites_not_plan_keys(self) -> None:
        self.assertIn(
            FREEZER.CONTROLLER_TEST_PATH, FREEZER.TRACKED_INPUTS
        )
        self.assertIn(FREEZER.FREEZER_TEST_PATH, FREEZER.TRACKED_INPUTS)
        self.assertNotIn("controller_test", self.bundle.plan["artifacts"])
        self.assertNotIn("plan_freezer_test", self.bundle.plan["artifacts"])

    def test_plan_identity_and_crossing_mutations_fail_closed(self) -> None:
        mutations = (
            ("execution_epoch_id", "wrong-epoch"),
            ("run_id_prefix", "wrong-prefix"),
            ("config_cli_path_policy", "absolute"),
            ("expected_scene_count", 249),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.bundle.plan)
                changed[key] = value
                with self.assertRaises(FREEZER.PlanFreezeError):
                    FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["output_schemas"]["success"] = "wrong"
        with self.assertRaisesRegex(
            FREEZER.PlanFreezeError, "output_schemas"
        ):
            FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["output_schemas"]["failure"] = "wrong"
        with self.assertRaisesRegex(
            FREEZER.PlanFreezeError, "output_schemas"
        ):
            FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["recovery"]["commit"] = "0" * 40
        with self.assertRaisesRegex(FREEZER.PlanFreezeError, "recovery"):
            FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["execution"]["failure_policy"] = "resume"
        with self.assertRaisesRegex(FREEZER.PlanFreezeError, "execution"):
            FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["scenes"][1], changed["scenes"][2] = (
            changed["scenes"][2],
            changed["scenes"][1],
        )
        with self.assertRaisesRegex(
            FREEZER.PlanFreezeError, "crossing differs"
        ):
            FREEZER.validate_plan(changed)

    def test_config_cli_and_run_id_mutations_fail_closed(self) -> None:
        bad_arguments = (
            "/absolute/config.yaml",
            "results/with space/config.yaml",
            "results/../config.yaml",
            "results/./config.yaml",
            "results\\config.yaml",
            "results/\u0001/config.yaml",
        )
        for value in bad_arguments:
            with self.subTest(value=repr(value)):
                changed = copy.deepcopy(self.bundle.plan)
                changed["scenes"][0]["config_cli_argument"] = value
                with self.assertRaises(FREEZER.PlanFreezeError):
                    FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["scenes"][0]["run_ids"]["reference"] = (
            changed["scenes"][0]["run_ids"]["optimized"]
        )
        with self.assertRaisesRegex(FREEZER.PlanFreezeError, "run_ids"):
            FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["scenes"][0]["run_ids"]["reference"] = "unprefixed"
        with self.assertRaisesRegex(FREEZER.PlanFreezeError, "run_ids"):
            FREEZER.validate_plan(changed)

        changed = copy.deepcopy(self.bundle.plan)
        changed["scenes"][0]["run_ids"].pop("optimized")
        with self.assertRaisesRegex(FREEZER.PlanFreezeError, "keys differ"):
            FREEZER.validate_plan(changed)

    def test_metric_fields_are_forbidden_recursively(self) -> None:
        changed = copy.deepcopy(self.bundle.plan)
        changed["scenes"][0]["map"]["wall_seconds"] = 1.0
        with self.assertRaises(FREEZER.PlanFreezeError):
            FREEZER.validate_plan(changed)

    def test_publication_is_read_only_exclusive_and_symlink_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            destination = root / "published"
            plan_path = FREEZER.publish_bundle(
                self.bundle, root, destination
            )
            self.assertEqual(plan_path.read_bytes(), self.bundle.plan_bytes)
            directories, files = FREEZER._scan_regular_tree(
                destination, "test publication"
            )
            self.assertEqual(len(files), 26)
            self.assertEqual(len(directories), 7)
            self.assertTrue(
                all(
                    stat.S_IMODE(metadata.st_mode) == 0o444
                    for metadata in files.values()
                )
            )
            self.assertTrue(
                all(
                    stat.S_IMODE(metadata.st_mode) == 0o555
                    for metadata in directories.values()
                )
            )
            with self.assertRaises(FileExistsError):
                FREEZER.publish_bundle(
                    self.bundle, root, destination
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "symlink"
            ):
                FREEZER.publish_bundle(
                    self.bundle, root, alias / "published"
                )

    def test_descriptor_publication_rejects_rebinding_and_file_links(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent_fd = os.open(str(root), FREEZER._directory_open_flags())
            child_fd = -1
            try:
                os.mkdir("child", dir_fd=parent_fd)
                child_fd = FREEZER._open_directory_at(
                    parent_fd, "child", "test child"
                )
                os.rename(
                    "child",
                    "moved",
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.symlink("moved", "child", dir_fd=parent_fd)
                with self.assertRaisesRegex(
                    FREEZER.PlanFreezeError, "binding changed"
                ):
                    FREEZER._assert_descriptor_binding(
                        parent_fd,
                        "child",
                        child_fd,
                        "test child",
                        require_directory=True,
                    )
                os.symlink("missing", "linked-file", dir_fd=parent_fd)
                with self.assertRaises(FileExistsError):
                    FREEZER._write_exclusive_at(
                        parent_fd,
                        "linked-file",
                        b"data",
                        "linked file",
                    )
            finally:
                if child_fd >= 0:
                    os.close(child_fd)
                os.close(parent_fd)

    def test_failed_descriptor_publication_removes_its_destination(
        self,
    ) -> None:
        original_write = FREEZER._write_exclusive_at
        write_count = 0

        def fail_third_write(
            parent_fd: int,
            name: str,
            data: bytes,
            label: str,
        ) -> int:
            nonlocal write_count
            write_count += 1
            if write_count == 3:
                raise FREEZER.PlanFreezeError("injected publication failure")
            return original_write(parent_fd, name, data, label)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            destination = root / "published"
            with (
                mock.patch.object(
                    FREEZER,
                    "_write_exclusive_at",
                    side_effect=fail_third_write,
                ),
                self.assertRaisesRegex(
                    FREEZER.PlanFreezeError, "injected"
                ),
            ):
                FREEZER.publish_bundle(
                    self.bundle, root, destination
                )
            self.assertFalse(destination.exists())
            self.assertFalse(destination.is_symlink())

    def test_future_path_guard_checks_plan_result_and_lease_without_writes(
        self,
    ) -> None:
        for relative in (
            FREEZER.PLAN_DIRECTORY,
            FREEZER.ALLOWED_OUTPUT_ROOT,
            FREEZER.EXECUTION_LEASE_PATH,
        ):
            for kind in ("directory", "file", "symlink"):
                with self.subTest(path=relative.as_posix(), kind=kind):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary).resolve()
                        before = set(root.rglob("*"))
                        FREEZER._require_future_paths_absent(root)
                        self.assertEqual(before, set(root.rglob("*")))
                        occupied = root.joinpath(*relative.parts)
                        occupied.parent.mkdir(parents=True, exist_ok=True)
                        if kind == "directory":
                            occupied.mkdir()
                        elif kind == "file":
                            occupied.write_bytes(b"occupied")
                        else:
                            occupied.symlink_to(root / "missing")
                        with self.assertRaises(FREEZER.PlanFreezeError):
                            FREEZER._require_future_paths_absent(root)

    def _failure_fixture(self, root: Path) -> None:
        records = self.registry["failure_evidence"]["files"]
        for record in records:
            source = (
                REPO_ROOT
                / FREEZER.V1_FAILURE_SCENE
                / PurePosixPath(record["path"])
            )
            destination = (
                root
                / FREEZER.V1_FAILURE_SCENE
                / PurePosixPath(record["path"])
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            destination.chmod(0o444)
        scene = root / FREEZER.V1_FAILURE_SCENE
        (scene / "reference").chmod(0o555)
        scene.chmod(0o555)

    @staticmethod
    def _fixture_committed_bytes(
        root: Path,
        commit: str,
        relative: PurePosixPath,
        label: str,
    ) -> bytes:
        del commit, label
        return root.joinpath(*relative.parts).read_bytes()

    def _verify_failure_fixture(
        self,
        root: Path,
        committed_reader: object | None = None,
    ) -> dict[str, object]:
        scene_relative = FREEZER.V1_FAILURE_SCENE.relative_to(
            FREEZER.V1_FAILURE_ROOT
        ).as_posix()
        expected_tree = {
            f"{scene_relative}/{record['path']}"
            for record in self.registry["failure_evidence"]["files"]
        }
        with (
            mock.patch.object(
                FREEZER,
                "_committed_tree_paths",
                return_value=expected_tree,
            ),
            mock.patch.object(
                FREEZER,
                "_committed_bytes",
                side_effect=(
                    self._fixture_committed_bytes
                    if committed_reader is None
                    else committed_reader
                ),
            ),
        ):
            return FREEZER._verify_sealed_failure(
                root,
                self.registry,
                (REPO_ROOT / FREEZER.V1_PLAN_PATH).read_bytes(),
            )

    def test_sealed_failure_fixture_passes_and_negatives_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self._failure_fixture(root)
            evidence = self._verify_failure_fixture(root)
            self.assertEqual(evidence["commit"], FREEZER.V1_FAILURE_COMMIT)
            self.assertEqual(len(evidence["files"]), 7)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self._failure_fixture(root)

            def drifted_evidence(
                fixture_root: Path,
                commit: str,
                relative: PurePosixPath,
                label: str,
            ) -> bytes:
                data = self._fixture_committed_bytes(
                    fixture_root, commit, relative, label
                )
                if relative.name == "config.yaml":
                    return data + b"x"
                return data

            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "bytes differ"
            ):
                self._verify_failure_fixture(root, drifted_evidence)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self._failure_fixture(root)
            extra = root / FREEZER.V1_FAILURE_ROOT / "perfect_maze"
            extra.mkdir()
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "extra or missing destination"
            ):
                self._verify_failure_fixture(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self._failure_fixture(root)
            scene = root / FREEZER.V1_FAILURE_SCENE
            scene.chmod(0o755)
            (scene / "extra.txt").write_bytes(b"extra")
            (scene / "extra.txt").chmod(0o444)
            scene.chmod(0o555)
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "extra or missing file"
            ):
                self._verify_failure_fixture(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self._failure_fixture(root)
            reference = root / FREEZER.V1_FAILURE_SCENE / "reference"
            reference.chmod(0o755)
            (reference / "stdout.txt").unlink()
            reference.chmod(0o555)
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "extra or missing file"
            ):
                self._verify_failure_fixture(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self._failure_fixture(root)
            path = root / FREEZER.V1_FAILURE_SCENE / "config.yaml"
            path.chmod(0o644)
            path.write_bytes(b"changed")
            with self.assertRaises(FREEZER.PlanFreezeError):
                self._verify_failure_fixture(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self._failure_fixture(root)
            reference = root / FREEZER.V1_FAILURE_SCENE / "reference"
            reference.chmod(0o755)
            stdout = reference / "stdout.txt"
            stdout.unlink()
            stdout.symlink_to(root / "missing")
            reference.chmod(0o555)
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "symlink"
            ):
                self._verify_failure_fixture(root)

    def test_zero_remote_gate_fails_before_other_git_checks(self) -> None:
        with mock.patch.object(
            FREEZER, "_git", return_value="origin\n"
        ):
            with self.assertRaisesRegex(
                FREEZER.PlanFreezeError, "zero Git remotes"
            ):
                FREEZER._verify_git_state(REPO_ROOT)

    @staticmethod
    def _checkpoint_snapshot() -> dict[str, tuple[object, ...]]:
        records: dict[str, tuple[object, ...]] = {}
        bases = (
            FREEZER.RECOVERY_REGISTRY_PATH,
            FREEZER.RECOVERY_ADDENDUM_PATH,
            FREEZER.V1_PLAN_DIRECTORY,
            FREEZER.PLAN_DIRECTORY,
            FREEZER.SIMULATOR_RESULT_ROOT,
            FREEZER.ARTIFACT_PATHS["controller"],
            FREEZER.CONTROLLER_TEST_PATH,
            FREEZER.ARTIFACT_PATHS["plan_freezer"],
            FREEZER.FREEZER_TEST_PATH,
        )
        for relative in bases:
            path = REPO_ROOT.joinpath(*relative.parts)
            candidates = (
                [path, *path.rglob("*")] if path.is_dir() else [path]
            )
            for candidate in candidates:
                key = candidate.relative_to(REPO_ROOT).as_posix()
                metadata = candidate.lstat()
                if stat.S_ISREG(metadata.st_mode):
                    records[key] = (
                        "file",
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_size,
                        metadata.st_mtime_ns,
                        hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    )
                elif stat.S_ISDIR(metadata.st_mode):
                    records[key] = (
                        "directory",
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_mtime_ns,
                    )
                elif stat.S_ISLNK(metadata.st_mode):
                    records[key] = ("symlink", os.readlink(candidate))
                else:
                    records[key] = ("special", metadata.st_mode)
        for relative in (
            FREEZER.PLAN_DIRECTORY,
            FREEZER.ALLOWED_OUTPUT_ROOT,
            FREEZER.EXECUTION_LEASE_PATH,
        ):
            path = REPO_ROOT.joinpath(*relative.parts)
            records[f"absence:{relative.as_posix()}"] = (
                path.exists(),
                path.is_symlink(),
            )
        return records

    def test_real_regenerated_bundle_matches_published_fixture(self) -> None:
        original_scan = FREEZER._scan_regular_tree
        legacy_result_root = REPO_ROOT.joinpath(
            *FREEZER.SIMULATOR_RESULT_ROOT.parts
        ).resolve()
        published_result_relative = FREEZER.ALLOWED_OUTPUT_ROOT.relative_to(
            FREEZER.SIMULATOR_RESULT_ROOT
        )
        published_result_key = published_result_relative.as_posix()
        published_result_prefix = published_result_key + "/"

        def pre_freeze_result_inventory(
            root: Path, label: str
        ) -> tuple[dict[str, os.stat_result], dict[str, os.stat_result]]:
            directories, files = original_scan(root, label)
            if root.resolve() != legacy_result_root:
                return directories, files
            return (
                {
                    key: metadata
                    for key, metadata in directories.items()
                    if key != published_result_key
                    and not key.startswith(published_result_prefix)
                },
                {
                    key: metadata
                    for key, metadata in files.items()
                    if not key.startswith(published_result_prefix)
                },
            )

        with mock.patch.object(
            FREEZER,
            "_scan_regular_tree",
            side_effect=pre_freeze_result_inventory,
        ):
            regenerated = FREEZER.build_bundle(
                REPO_ROOT, require_git_clean=False
            )

        self.assertEqual(regenerated.plan_bytes, self.bundle.plan_bytes)
        self.assertEqual(regenerated.plan, self.bundle.plan)
        self.assertEqual(
            set(regenerated.scenarios), set(self.bundle.scenarios)
        )
        for relative, expected in self.bundle.scenarios.items():
            with self.subTest(scenario=relative.as_posix()):
                self.assertEqual(regenerated.scenarios[relative], expected)

    def test_actual_post_freeze_check_only_fails_closed_without_writes(
        self,
    ) -> None:
        plan_root = REPO_ROOT.joinpath(*FREEZER.PLAN_DIRECTORY.parts)
        if not plan_root.is_dir():
            self.skipTest("published v2 plan is not present")
        before = self._checkpoint_snapshot()
        output = io.StringIO()
        errors = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            return_code = FREEZER.main(["--check-only"])
        after = self._checkpoint_snapshot()
        self.assertEqual(return_code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn(
            "v2 visual plan root must remain absent before freeze",
            errors.getvalue(),
        )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
