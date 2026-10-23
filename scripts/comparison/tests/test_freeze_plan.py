from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.comparison import freeze_plan as FREEZER
from scripts.comparison import freeze_capacity_decision as CAPACITY_DECISION
from scripts.comparison import run_paired as RUNNER


ROOT = Path(__file__).resolve().parents[3]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(source: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), *arguments],
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


class BuildFixture:
    """Minimal real Git/build identities; its binaries are never executed."""

    def __init__(self, directory: Path) -> None:
        self.repository = directory / "repository"
        self.repository.mkdir()
        self.analysis_revision = directory / "analysis_revision.py"
        self.analysis_revision.write_text(
            'ANALYSIS_SCHEMA = "test-only-frozen-analysis-v1"\n',
            encoding="utf-8",
        )
        (self.repository / "pixi.lock").write_bytes(
            (ROOT / "pixi.lock").read_bytes()
        )
        self.config_template = (
            self.repository
            / "config"
            / "comparison"
            / "laptop_benchmark.yaml"
        )
        self.config_template.parent.mkdir(parents=True)
        self.config_template.write_bytes(
            (
                ROOT / "config" / "comparison" / "laptop_benchmark.yaml"
            ).read_bytes()
        )
        records: dict[str, dict] = {}
        identities: dict[str, tuple[str, str]] = {}
        self.sources: dict[str, Path] = {}
        for method in ("reference", "optimized"):
            source = (
                self.repository
                / "registered-sources"
                / "trace-v3"
                / method
            )
            source.mkdir(parents=True)
            self.sources[method] = source
            git(source, "init", "-q")
            (source / "identity.txt").write_text(
                f"{method} test identity\n", encoding="utf-8"
            )
            git(source, "add", "identity.txt")
            git(
                source,
                "-c",
                "user.name=Plan Freezer Test",
                "-c",
                "user.email=plan-freezer@example.invalid",
                "commit",
                "-q",
                "-m",
                "fixture identity",
            )
            identities[method] = (
                git(source, "rev-parse", "HEAD"),
                git(source, "rev-parse", "HEAD^{tree}"),
            )
            for mode in ("timing", "fidelity"):
                binary = (
                    self.repository
                    / "build"
                    / "comparison"
                    / "build"
                    / f"{method}-{mode}"
                    / "dancers"
                )
                binary.parent.mkdir(parents=True)
                binary.write_text(
                    f"fixture {method} {mode}; never execute\n",
                    encoding="utf-8",
                )
                binary.chmod(0o755)
                auxiliary_paths = {
                    "cmake_cache_sha256": binary.parent / "CMakeCache.txt",
                    "compile_commands_sha256": (
                        binary.parent / "compile_commands.json"
                    ),
                    "clean_build_log_sha256": binary.parent / "build.log",
                }
                for field, path in auxiliary_paths.items():
                    path.write_text(
                        f"fixture {method} {mode} {field}\n",
                        encoding="utf-8",
                    )
                records[f"{method}_{mode}"] = {
                    "algorithm_revision": (
                        FREEZER.REFERENCE_PIN
                        if method == "reference"
                        else FREEZER.OPTIMIZED_PIN
                    ),
                    "applied_head": identities[method][0],
                    "observer_compiled": mode == "fidelity",
                    "source_dir": str(source.relative_to(self.repository)),
                    "binary_path": str(binary.relative_to(self.repository)),
                    "binary_sha256": digest(binary),
                    "cmake_cache_sha256": digest(
                        auxiliary_paths["cmake_cache_sha256"]
                    ),
                    "compile_commands_sha256": digest(
                        auxiliary_paths["compile_commands_sha256"]
                    ),
                    "clean_build_log_sha256": digest(
                        auxiliary_paths["clean_build_log_sha256"]
                    ),
                    "registered_tests": "3/3 passed",
                }

        manifest = {
            "schema_version": FREEZER.BUILD_MANIFEST_SCHEMA,
            "repository": {
                "reference_pin": FREEZER.REFERENCE_PIN,
                "optimized_pin": FREEZER.OPTIMIZED_PIN,
                "reference_applied_head": identities["reference"][0],
                "reference_tree": identities["reference"][1],
                "optimized_applied_head": identities["optimized"][0],
                "optimized_tree": identities["optimized"][1],
            },
            "toolchain": {
                "pixi_lock_sha256": digest(self.repository / "pixi.lock")
            },
            "builds": records,
            "gates": {
                "dual_clean_builds": "pass",
                "common_registered_tests_all_four_builds": "pass",
                "timing_observer_compile_absence": "pass",
                "smoke_semantic_trace_identity": "pass",
                "calibration_permitted": True,
                "validation_permitted": True,
                "audit_permitted": True,
            },
        }
        build_manifest = self.repository / "comparison" / "build_manifest.json"
        build_manifest.parent.mkdir()
        build_manifest.write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        experiment = json.loads(
            (
                ROOT / "docs" / "comparison" / "experiment_manifest.json"
            ).read_text(encoding="utf-8")
        )
        experiment["software_versions"]["analysis_revision"] = digest(
            self.analysis_revision
        )
        experiment["environment_and_map"]["map_manifest_sha256"] = digest(
            ROOT / "comparison" / "environments" / "manifest.json"
        )
        experiment["hardware_versions"]["power_requirement"] = (
            FREEZER.POWER_MODE
        )
        experiment["hardware_versions"]["thermal_policy"] = (
            FREEZER.THERMAL_POLICY
        )
        self.experiment_manifest = directory / "experiment_manifest.json"
        self.experiment_manifest.write_text(
            json.dumps(experiment, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def write_capacity_decision(
    directory: Path, fixture: BuildFixture
) -> Path:
    grids = {
        "open": [500, 1000, 1500, 1935],
        "perfect_maze": [500, 578],
        "fractal_low": [500, 1000, 1395],
        "fractal_medium": [500, 1000, 1385],
        "fractal_high": [384, 471],
    }
    environments = []
    timeout_derivation = {}
    for family in CAPACITY_DECISION.FAMILIES:
        grid = grids[family]
        candidate_index = 1
        environments.append(
            {
                "environment": family,
                "reference_anchor_n": grid[0],
                "grid": grid,
                "candidate_n_star": grid[candidate_index],
                "next_grid_point": (
                    grid[candidate_index + 1]
                    if candidate_index + 1 < len(grid)
                    else None
                ),
                "validation_timing_counts": grid,
                "validation_fidelity_counts": grid,
                "validation_timeout_seconds": 300.0,
            }
        )
        timeout_derivation[family] = {
            "calibration_plan_timeout_seconds": 300.0,
            "maximum_completed_measured_wall_seconds": 10.0,
            "three_times_wall_ceiled_to_ten_seconds": 30.0,
        }
    value = {
        "schema_version": CAPACITY_DECISION.DECISION_SCHEMA,
        "status": CAPACITY_DECISION.DECISION_STATUS,
        "selection_rule_id": CAPACITY_DECISION.SELECTION_RULE_ID,
        "provenance": {
            "calibration_analysis": {
                "path": str(fixture.analysis_revision.resolve()),
                "sha256": digest(fixture.analysis_revision),
            },
            "analyzer": {
                "path": str(fixture.analysis_revision.resolve()),
                "sha256": digest(fixture.analysis_revision),
            },
            "experiment_manifest": {
                "path": str(fixture.experiment_manifest.resolve()),
                "sha256": digest(fixture.experiment_manifest),
            },
            "environment_manifest": {
                "path": str(
                    (
                        ROOT
                        / "comparison"
                        / "environments"
                        / "manifest.json"
                    ).resolve()
                ),
                "sha256": digest(
                    ROOT / "comparison" / "environments" / "manifest.json"
                ),
            },
            "calibration_sessions": [
                {
                    "session_id": "test-only-calibration-session",
                    "path": str(directory.resolve()),
                    "mode": "timing",
                    "hashes": {
                        "session_json_sha256": (
                            digest(fixture.analysis_revision)
                        ),
                        "run_manifest_json_sha256": (
                            digest(fixture.analysis_revision)
                        ),
                        "frozen_plan_sha256": (
                            digest(fixture.analysis_revision)
                        ),
                        "runs_jsonl_sha256": (
                            digest(fixture.analysis_revision)
                        ),
                        "pairs_jsonl_sha256": (
                            digest(fixture.analysis_revision)
                        ),
                        "capacity_json_sha256": (
                            digest(fixture.analysis_revision)
                        ),
                    },
                }
            ],
            "timeout_derivation": timeout_derivation,
        },
        "seeds": {
            "validation": list(range(11, 31)),
            "audit": list(range(31, 51)),
        },
        "environments": environments,
    }
    path = directory / "capacity-decision.json"
    path.write_bytes(CAPACITY_DECISION.canonical_json_bytes(value))
    path.chmod(0o444)
    return path


class PlanFreezerTests(unittest.TestCase):
    def freeze(
        self,
        directory: Path,
        fixture: BuildFixture,
        name: str,
        *,
        mode: str = "fidelity",
        seeds: tuple[int, ...] = tuple(range(1, 11)),
        counts: tuple[int, ...] = (8,),
        capacity: bool = False,
        phase: str = "calibration",
        timeout_seconds: float = 300.0,
        capacity_decision: Path | None = None,
        trace_detail: str | None = None,
    ) -> FREEZER.FrozenPlan:
        return FREEZER.freeze_plan(
            phase=phase,
            mode=mode,
            family="open",
            seeds=seeds,
            counts=counts,
            repetitions=1 if mode == "fidelity" else 3,
            timeout_seconds=timeout_seconds,
            minimum_free_disk_bytes=FREEZER.MINIMUM_FREE_DISK_BYTES,
            maximum_peak_rss_mib=FREEZER.MAXIMUM_PEAK_RSS_MIB,
            power_mode=FREEZER.POWER_MODE,
            thermal_policy=FREEZER.THERMAL_POLICY,
            output_root=directory / name,
            config_template=fixture.config_template,
            analysis_revision=fixture.analysis_revision,
            capacity=capacity,
            capacity_decision=capacity_decision,
            trace_detail=trace_detail,
            repository_root=fixture.repository,
            environment_root=ROOT / "comparison" / "environments",
            protocol_path=(
                ROOT / "docs" / "comparison" / "EXPERIMENT_PROTOCOL.md"
            ),
            experiment_manifest_path=(
                fixture.experiment_manifest
            ),
            runner_path=ROOT / "scripts" / "comparison" / "run_paired.py",
        )

    def test_fidelity_plan_is_runner_compatible_and_hash_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BuildFixture(root)
            git_calls: list[tuple[str, ...]] = []
            real_git = FREEZER._git

            def recording_git(source: Path, *arguments: str) -> str:
                git_calls.append(("git", "-C", str(source), *arguments))
                return real_git(source, *arguments)

            with mock.patch.object(
                FREEZER, "_git", side_effect=recording_git
            ):
                frozen = self.freeze(root, fixture, "fidelity")

            self.assertTrue(git_calls)
            self.assertTrue(all(command[0] == "git" for command in git_calls))
            raw = json.loads(frozen.plan_path.read_text(encoding="utf-8"))
            loaded = RUNNER.load_plan(frozen.plan_path)
            cases, _ = RUNNER._verify_environment_manifest(loaded)
            registry_records = [
                RUNNER._validate_pair_artifacts(loaded, pair, cases)
                for pair in loaded.pairs
            ]
            self.assertEqual(loaded.mode, "fidelity")
            self.assertEqual(loaded.trace_detail, "digest")
            self.assertEqual(
                {
                    method: Path(record["source_dir"])
                    for method, record in raw["methods"].items()
                },
                {
                    method: source.resolve()
                    for method, source in fixture.sources.items()
                },
            )
            self.assertEqual(len(loaded.pairs), 10)
            self.assertEqual(frozen.plan_sha256, digest(frozen.plan_path))
            self.assertEqual(
                raw["task_validity_predicate_id"],
                "fixed-horizon-safe-navigation-v1",
            )
            self.assertEqual(raw["timestep_seconds"], 0.1)
            self.assertEqual(raw["idle_cooldown_seconds"], 300)
            self.assertEqual(
                raw["minimum_free_disk_bytes"], 21_474_836_480
            )
            self.assertEqual(raw["maximum_peak_rss_mib"], 49_152.0)
            self.assertEqual(raw["power_mode"], "AC Power")
            self.assertEqual(raw["thermal_policy"], FREEZER.THERMAL_POLICY)
            self.assertTrue(
                all(
                    record["registry"]["registration"] == "exact-N-prefix"
                    for record in registry_records
                )
            )
            self.assertEqual(
                raw["artifacts"]["environment_manifest"]["sha256"],
                digest(ROOT / "comparison" / "environments" / "manifest.json"),
            )
            self.assertTrue(
                all(
                    Path(item["path"]).is_absolute()
                    for item in raw["artifacts"].values()
                )
            )
            self.assertTrue(
                all(
                    Path(method["binary"]["path"]).is_relative_to(
                        fixture.repository.resolve()
                    )
                    for method in raw["methods"].values()
                )
            )
            environment_manifest = json.loads(
                (
                    ROOT / "comparison" / "environments" / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            open_calibration = next(
                item
                for item in environment_manifest["environments"]
                if item["family"] == "open"
                and item["split"] == "calibration"
            )
            registered_n8 = next(
                item
                for item in open_calibration["capacity"][
                    "prefix_scenarios"
                ]
                if item["robot_count"] == 8
            )
            self.assertEqual(
                raw["pairs"][0]["scenario"]["sha256"],
                registered_n8["sha256"],
            )
            for scenario in frozen.scenario_paths:
                self.assertTrue(scenario.is_absolute())
                self.assertEqual(
                    json.loads(scenario.read_text(encoding="utf-8"))[
                        "num_robots"
                    ],
                    8,
                )
                self.assertFalse(scenario.stat().st_mode & 0o222)

    def test_all_plan_modes_reject_non_digest_trace_detail(self) -> None:
        for mode in ("fidelity", "timing"):
            for trace_detail in ("full", "positions"):
                with self.subTest(mode=mode, trace_detail=trace_detail):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        fixture = BuildFixture(root)
                        output = root / f"{mode}-{trace_detail}"
                        with self.assertRaisesRegex(
                            FREEZER.FreezeError,
                            rf"{mode} plans must use digest trace detail",
                        ):
                            self.freeze(
                                root,
                                fixture,
                                output.name,
                                mode=mode,
                                trace_detail=trace_detail,
                            )
                        self.assertFalse(output.exists())

    def test_source_dir_registration_fails_closed(self) -> None:
        cases = (
            ("missing-field", None, "non-empty repository-relative"),
            ("absolute", "ABSOLUTE", "repository-relative"),
            ("outside", "../outside", "without '\\.\\.'"),
            ("missing-directory", "registered-sources/missing", "is missing"),
            ("symlink", "SYMLINK", "symbolic link"),
        )
        for name, replacement, expected_error in cases:
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    fixture = BuildFixture(root)
                    manifest_path = (
                        fixture.repository
                        / "comparison"
                        / "build_manifest.json"
                    )
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    build = manifest["builds"]["reference_fidelity"]
                    if replacement is None:
                        del build["source_dir"]
                    elif replacement == "ABSOLUTE":
                        build["source_dir"] = str(
                            fixture.sources["reference"].resolve()
                        )
                    elif replacement == "SYMLINK":
                        link = (
                            fixture.repository
                            / "registered-sources"
                            / "reference-link"
                        )
                        link.symlink_to(
                            fixture.sources["reference"],
                            target_is_directory=True,
                        )
                        build["source_dir"] = str(
                            link.relative_to(fixture.repository)
                        )
                    else:
                        build["source_dir"] = replacement
                    manifest_path.write_text(
                        json.dumps(manifest, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

                    output = root / f"invalid-{name}"
                    with self.assertRaisesRegex(
                        FREEZER.FreezeError, expected_error
                    ):
                        self.freeze(root, fixture, output.name)
                    self.assertFalse(output.exists())

    def test_timing_capacity_grid_uses_same_seed_set_at_each_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BuildFixture(root)
            frozen = self.freeze(
                root,
                fixture,
                "capacity",
                mode="timing",
                seeds=tuple(range(1, 11)),
                counts=(500, 1000, 1500, 1935),
                capacity=True,
            )
            raw = json.loads(frozen.plan_path.read_text(encoding="utf-8"))
            loaded = RUNNER.load_plan(frozen.plan_path)
            cases, _ = RUNNER._verify_environment_manifest(loaded)
            self.assertTrue(
                all(
                    RUNNER._validate_pair_artifacts(
                        loaded, pair, cases
                    )["registry"]["registration"]
                    == "exact-N-prefix"
                    for pair in loaded.pairs
                )
            )
            self.assertEqual(
                raw["capacity_search"],
                {
                    "environment": "open",
                    "grid": [500, 1000, 1500, 1935],
                    "max_points": 4,
                    "reference_anchor_n": 500,
                },
            )
            self.assertIsNotNone(loaded.capacity)
            by_count: dict[int, set[int]] = {}
            for pair in raw["pairs"]:
                by_count.setdefault(pair["robot_count"], set()).add(
                    pair["seed"]
                )
            self.assertEqual(
                by_count,
                {
                    500: set(range(1, 11)),
                    1000: set(range(1, 11)),
                    1500: set(range(1, 11)),
                    1935: set(range(1, 11)),
                },
            )
            self.assertEqual(len(frozen.scenario_paths), 4)

    def test_seed_count_and_capacity_policies_fail_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BuildFixture(root)
            common = {
                "phase": "validation",
                "mode": "timing",
                "family": "fractal_high",
                "repetitions": 3,
                "timeout_seconds": 30.0,
                "minimum_free_disk_bytes": (
                    FREEZER.MINIMUM_FREE_DISK_BYTES
                ),
                "maximum_peak_rss_mib": FREEZER.MAXIMUM_PEAK_RSS_MIB,
                "power_mode": FREEZER.POWER_MODE,
                "thermal_policy": FREEZER.THERMAL_POLICY,
                "config_template": fixture.config_template,
                "analysis_revision": fixture.analysis_revision,
                "repository_root": fixture.repository,
                "environment_root": ROOT / "comparison" / "environments",
                "protocol_path": (
                    ROOT / "docs" / "comparison" / "EXPERIMENT_PROTOCOL.md"
                ),
                "experiment_manifest_path": (
                    fixture.experiment_manifest
                ),
                "runner_path": (
                    ROOT / "scripts" / "comparison" / "run_paired.py"
                ),
            }
            invalid_seed_output = root / "invalid-seed"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "complete seed set 11-30"
            ):
                FREEZER.freeze_plan(
                    **common,
                    seeds=tuple(range(1, 11)),
                    counts=(8,),
                    output_root=invalid_seed_output,
                )
            self.assertFalse(invalid_seed_output.exists())

            over_ceiling_output = root / "over-ceiling"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "exceeds frozen"
            ):
                FREEZER.freeze_plan(
                    **common,
                    seeds=tuple(range(11, 31)),
                    counts=(500,),
                    output_root=over_ceiling_output,
                )
            self.assertFalse(over_ceiling_output.exists())

            unregistered_output = root / "unregistered"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "not a registered exact-N"
            ):
                FREEZER.freeze_plan(
                    **common,
                    seeds=tuple(range(11, 31)),
                    counts=(9,),
                    output_root=unregistered_output,
                )
            self.assertFalse(unregistered_output.exists())

            capacity_output = root / "validation-capacity"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "calibration-only"
            ):
                FREEZER.freeze_plan(
                    **common,
                    seeds=tuple(range(11, 31)),
                    counts=(8, 12),
                    output_root=capacity_output,
                    capacity=True,
                )
            self.assertFalse(capacity_output.exists())

    def test_audit_plan_uses_complete_audit_seeds_and_validation_geometry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BuildFixture(root)
            decision = write_capacity_decision(root, fixture)
            frozen = self.freeze(
                root,
                fixture,
                "audit",
                phase="audit",
                seeds=tuple(range(31, 51)),
                counts=(500, 1000, 1500, 1935),
                capacity_decision=decision,
            )
            raw = json.loads(frozen.plan_path.read_text(encoding="utf-8"))
            self.assertEqual(raw["phase"], "audit")
            self.assertEqual(
                [pair["seed"] for pair in raw["pairs"][:20]],
                list(range(31, 51)),
            )
            self.assertEqual(
                raw["capacity_evaluation"],
                {
                    "environment": "open",
                    "role": "fidelity-grid",
                    "reference_anchor_n": 500,
                    "candidate_n_star": 1000,
                    "next_grid_point": 1500,
                    "grid": [500, 1000, 1500, 1935],
                },
            )
            self.assertEqual(
                raw["artifacts"]["capacity_decision"],
                {
                    "path": str(decision.resolve()),
                    "sha256": digest(decision),
                },
            )
            scenario = json.loads(
                frozen.scenario_paths[0].read_text(encoding="utf-8")
            )
            self.assertEqual(scenario["metadata"]["split"], "validation")
            self.assertEqual(scenario["num_robots"], 500)

    def test_validation_timing_plan_must_match_decision_grid_and_timeout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BuildFixture(root)
            decision = write_capacity_decision(root, fixture)
            frozen = self.freeze(
                root,
                fixture,
                "validation",
                phase="validation",
                mode="timing",
                seeds=tuple(range(11, 31)),
                counts=(500, 1000, 1500, 1935),
                timeout_seconds=300.0,
                capacity_decision=decision,
            )
            raw = json.loads(frozen.plan_path.read_text(encoding="utf-8"))
            self.assertEqual(
                raw["capacity_evaluation"]["role"], "timing-grid"
            )
            self.assertNotIn("capacity_search", raw)
            missing = root / "missing-decision"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "require a frozen"
            ):
                self.freeze(
                    root,
                    fixture,
                    "missing-decision",
                    phase="validation",
                    mode="timing",
                    seeds=tuple(range(11, 31)),
                    counts=(500, 1000, 1500, 1935),
                    timeout_seconds=300.0,
                )
            self.assertFalse(missing.exists())
            wrong = root / "wrong-grid"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "full grid"
            ):
                self.freeze(
                    root,
                    fixture,
                    "wrong-grid",
                    phase="validation",
                    mode="timing",
                    seeds=tuple(range(11, 31)),
                    counts=(500, 1000),
                    timeout_seconds=300.0,
                    capacity_decision=decision,
                )
            self.assertFalse(wrong.exists())
            wrong_timeout = root / "wrong-timeout"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "frozen capacity decision"
            ):
                self.freeze(
                    root,
                    fixture,
                    "wrong-timeout",
                    phase="validation",
                    mode="timing",
                    seeds=tuple(range(11, 31)),
                    counts=(500, 1000, 1500, 1935),
                    timeout_seconds=310.0,
                    capacity_decision=decision,
                )
            self.assertFalse(wrong_timeout.exists())

            build_path = (
                fixture.repository
                / "comparison"
                / "build_manifest.json"
            )
            build_manifest = json.loads(
                build_path.read_text(encoding="utf-8")
            )
            build_manifest["gates"]["validation_permitted"] = False
            build_path.write_text(
                json.dumps(build_manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            locked = root / "validation-locked"
            with self.assertRaisesRegex(
                FREEZER.FreezeError, "validation_permitted"
            ):
                self.freeze(
                    root,
                    fixture,
                    "validation-locked",
                    phase="validation",
                    mode="timing",
                    seeds=tuple(range(11, 31)),
                    counts=(500, 1000, 1500, 1935),
                    timeout_seconds=300.0,
                    capacity_decision=decision,
                )
            self.assertFalse(locked.exists())

    def test_experiment_artifact_drift_fails_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BuildFixture(root)
            experiment = json.loads(
                fixture.experiment_manifest.read_text(encoding="utf-8")
            )
            experiment["software_versions"]["analysis_revision"] = "0" * 64
            fixture.experiment_manifest.write_text(
                json.dumps(experiment, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            output = root / "drifted"
            with self.assertRaisesRegex(
                FREEZER.FreezeError,
                "analysis revision disagrees",
            ):
                self.freeze(root, fixture, "drifted")
            self.assertFalse(output.exists())

    def test_exclusive_create_preserves_first_frozen_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BuildFixture(root)
            frozen = self.freeze(root, fixture, "exclusive")
            before = frozen.plan_path.read_bytes()
            with self.assertRaisesRegex(
                FileExistsError, "refusing to overwrite"
            ):
                self.freeze(root, fixture, "exclusive")
            self.assertEqual(frozen.plan_path.read_bytes(), before)

            second = self.freeze(root, fixture, "second")
            first_raw = json.loads(before)
            second_raw = json.loads(
                second.plan_path.read_text(encoding="utf-8")
            )
            self.assertEqual(first_raw["order_seed"], second_raw["order_seed"])
            self.assertEqual(
                digest(frozen.scenario_paths[0]),
                digest(second.scenario_paths[0]),
            )

    def test_cli_integer_set_is_strict_and_deterministic(self) -> None:
        self.assertEqual(
            FREEZER.parse_integer_set("1,3-5,8"),
            (1, 3, 4, 5, 8),
        )
        for invalid in ("", "3-1", "1,1", "1,,2", "one"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(Exception):
                    FREEZER.parse_integer_set(invalid)


if __name__ == "__main__":
    unittest.main()
