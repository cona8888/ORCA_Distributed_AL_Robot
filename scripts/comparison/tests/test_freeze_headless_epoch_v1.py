from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.comparison import attest_headless_binaries_v1 as ATTEST
from scripts.comparison import freeze_headless_epoch_v1 as FREEZER
from scripts.comparison import generate_headless_mission_banks_v1 as MISSIONS


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_PATH = REPOSITORY_ROOT / "scripts/comparison/freeze_headless_epoch_v1.py"


def epoch_design(
    counts: tuple[int, ...] = FREEZER.ROBOT_COUNTS,
) -> dict[str, object]:
    return {
        "family_grids": [
            {"counts": list(counts), "family": family, "n0": counts[0]}
            for family in FREEZER.FAMILIES
        ],
        "normalized_config": copy.deepcopy(FREEZER.NORMALIZED_CONFIG),
        "order_seed": "12" * 32,
        "phase": FREEZER.PHASE,
        "sampling_structure": copy.deepcopy(FREEZER.SAMPLING_STRUCTURE),
        "schema_version": FREEZER.DESIGN_SCHEMA,
        "seed_roles": copy.deepcopy(FREEZER.SEED_ROLES),
        "timeout_seconds": 300,
        "track_id": FREEZER.TRACK_ID,
    }


def mission_design(
    counts: tuple[int, ...] = FREEZER.ROBOT_COUNTS,
) -> dict[str, object]:
    return {
        "phase": FREEZER.PHASE,
        "requests": [
            {"counts": list(counts), "family": family, "seed": seed}
            for family in FREEZER.FAMILIES
            for seed in FREEZER.SEEDS
        ],
        "schema_version": MISSIONS.DESIGN_SCHEMA,
    }


def component_record(
    schema: str,
    contract: dict[str, object],
    relative_path: str,
    *,
    phase: bool = False,
) -> dict[str, object]:
    path = REPOSITORY_ROOT / relative_path
    data = path.read_bytes() if path.is_file() else relative_path.encode("ascii")
    value: dict[str, object] = {
        "classification": copy.deepcopy(FREEZER.COMPONENT_CLASSIFICATION),
        "contract": copy.deepcopy(contract),
        "identity": {
            "bytes": len(data),
            "path": relative_path,
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "schema_version": schema,
        "track_id": FREEZER.TRACK_ID,
    }
    if phase:
        value["phase"] = FREEZER.PHASE
    return value


def component_records() -> dict[str, dict[str, object]]:
    return {
        "protocol_record": component_record(
            FREEZER.PROTOCOL_RECORD_SCHEMA,
            FREEZER.PROTOCOL_CONTRACT,
            FREEZER.PROTOCOL_SOURCE_PATH,
            phase=True,
        ),
        "runner_record": component_record(
            FREEZER.RUNNER_RECORD_SCHEMA,
            FREEZER.RUNNER_CONTRACT,
            "scripts/comparison/run_headless_epoch_v1.py",
        ),
        "analyzer_record": component_record(
            FREEZER.ANALYZER_RECORD_SCHEMA,
            FREEZER.ANALYZER_CONTRACT,
            "scripts/comparison/analyze_headless_epoch_v1.py",
        ),
        "geometry_record": component_record(
            FREEZER.GEOMETRY_RECORD_SCHEMA,
            FREEZER.GEOMETRY_CONTRACT,
            "scripts/comparison/headless_geometry_v1.py",
        ),
        "freezer_record": component_record(
            FREEZER.FREEZER_RECORD_SCHEMA,
            FREEZER.FREEZER_CONTRACT,
            "scripts/comparison/freeze_headless_epoch_v1.py",
        ),
        "mission_compiler_record": component_record(
            FREEZER.MISSION_COMPILER_RECORD_SCHEMA,
            FREEZER.MISSION_COMPILER_CONTRACT,
            "scripts/comparison/generate_headless_mission_banks_v1.py",
        ),
        "binary_attester_record": component_record(
            FREEZER.BINARY_ATTESTER_RECORD_SCHEMA,
            FREEZER.BINARY_ATTESTER_CONTRACT,
            "scripts/comparison/attest_headless_binaries_v1.py",
        ),
    }


class HeadlessEpochFreezerV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.design = epoch_design()
        cls.missions = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, mission_design()
        ).manifest
        cls.attestation = ATTEST.build_attestation(
            repository_root=REPOSITORY_ROOT,
            observed_utc="2026-08-11T12:00:00Z",
        )
        cls.components = component_records()
        cls.compilation = cls.compile()

    @classmethod
    def compile(
        cls,
        *,
        design: dict[str, object] | None = None,
        missions: dict[str, object] | None = None,
        attestation: dict[str, object] | None = None,
        components: dict[str, dict[str, object]] | None = None,
    ) -> FREEZER.EpochCompilation:
        return FREEZER.compile_epoch(
            design=copy.deepcopy(cls.design if design is None else design),
            mission_receipt=copy.deepcopy(
                cls.missions if missions is None else missions
            ),
            binary_attestation=copy.deepcopy(
                cls.attestation if attestation is None else attestation
            ),
            **copy.deepcopy(cls.components if components is None else components),
        )

    def assert_raw_rejected(self, value: dict[str, object]) -> None:
        with self.assertRaises(FREEZER.EpochFreezeError):
            FREEZER.validate_epoch_bytes(FREEZER.canonical_json_bytes(value))

    def test_complete_counts_balance_phase_order_and_false_claims(self) -> None:
        value = self.compilation.value
        self.assertEqual(len(value["scenes"]), 250)
        self.assertEqual(len(value["config_artifacts"]), 250)
        schedule = value["schedule"]
        self.assertEqual(schedule["warmups"]["process_count"], 20)
        self.assertEqual(
            schedule["execution_order"],
            ["warmups", "digest_fidelity", "positions_replay", "headless_timing"],
        )
        self.assertEqual(
            {
                run_class: inventory["measured_process_count"]
                for run_class, inventory in schedule["inventories"].items()
            },
            {
                "digest_fidelity": 1_500,
                "positions_replay": 500,
                "headless_timing": 1_500,
            },
        )
        self.assertTrue(all(flag is False for flag in value["classification"].values()))
        self.assertTrue(all(flag is False for flag in value["operations"].values()))
        self.assertEqual(value["failure_policy"], FREEZER.FAILURE_POLICY)
        for row in schedule["balance"]["per_family_count"]:
            self.assertEqual((row["aba_scene_count"], row["bab_scene_count"]), (5, 5))
            self.assertEqual(
                row["measured_method_pair_counts"],
                {"digest_fidelity": 30, "positions_replay": 10, "headless_timing": 30},
            )
        FREEZER.validate_compilation(self.compilation)

    def test_live_binary_attestation_identity_is_the_pinned_input(self) -> None:
        raw = ATTEST.canonical_json_bytes(self.attestation)
        self.assertEqual(len(raw), 6_784)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "ce9934dd74602e23863162d1e8b89756a9be55fa592b1f3595fa9584b0998136",
        )

    def test_deterministic_bytes_and_canonical_strict_json(self) -> None:
        repeated = self.compile()
        self.assertEqual(repeated.data, self.compilation.data)
        self.assertEqual(repeated.sha256, self.compilation.sha256)
        self.assertEqual(
            FREEZER.validate_epoch_bytes(self.compilation.data),
            self.compilation.value,
        )
        for raw in (
            b'{"a":1,"a":2}\n',
            b'{"x":NaN}\n',
            json.dumps(self.compilation.value).encode("utf-8"),
        ):
            with self.assertRaises(FREEZER.EpochFreezeError):
                FREEZER.validate_epoch_bytes(raw)

    def test_scene_seed_sampling_and_rendered_config_bindings(self) -> None:
        value = self.compilation.value
        for scene, artifact in zip(value["scenes"], value["config_artifacts"]):
            self.assertEqual(scene["sampling_unit"]["count_relationship"], "nested-prefix")
            self.assertEqual(
                scene["seed_roles"]["mission_selection"]["active_via_distinct_scenario_bytes"],
                True,
            )
            self.assertEqual(
                scene["seed_roles"]["runtime_rng"]["activity_assumed"], False
            )
            data = artifact["content_utf8"].encode("utf-8")
            self.assertEqual(scene["config"]["bytes"], len(data))
            self.assertEqual(scene["config"]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(
                scene["effective_runtime_raster"]["sha256"],
                FREEZER.EFFECTIVE_RUNTIME_RASTERS[scene["family"]],
            )
            self.assertEqual(scene["effective_runtime_raster"]["bytes"], 1_000_000)
            self.assertTrue(scene["config"]["path"].startswith(FREEZER.FUTURE_PLAN_ROOT + "/configs/"))
            self.assertTrue(scene["scenario"]["path"].startswith(FREEZER.FUTURE_PLAN_ROOT + "/missions/"))
            self.assertIn("MAX_TIME: 100\n", artifact["content_utf8"])
            self.assertNotIn("MAX_TIME: 100.0", artifact["content_utf8"])

    def test_exact_run_class_joins_observers_and_warmup_uniqueness(self) -> None:
        schedule = self.compilation.value["schedule"]
        joins: dict[str, set[tuple[object, ...]]] = {}
        for run_class, inventory in schedule["inventories"].items():
            joins[run_class] = {
                (
                    slot["scene_id"],
                    slot["input_id"],
                    slot["repetition"],
                    slot["pattern"],
                    slot["pair_position"],
                    slot["method"],
                )
                for slot in inventory["measured"]
            }
            expected_binary_suffix = "_timing" if run_class == "headless_timing" else "_fidelity"
            self.assertTrue(
                all(slot["binary_key"].endswith(expected_binary_suffix) for slot in inventory["measured"])
            )
            self.assertTrue(
                all(slot["timed"] is (run_class == "headless_timing") for slot in inventory["measured"])
            )
        self.assertEqual(joins["digest_fidelity"], joins["headless_timing"])
        self.assertEqual(
            joins["positions_replay"],
            {item for item in joins["digest_fidelity"] if item[2] == 1},
        )
        warmups = schedule["warmups"]["slots"]
        self.assertEqual(
            len({(slot["binary_key"], slot["map_sha256"]) for slot in warmups}),
            20,
        )

    def test_design_grid_n0_timeout_types_and_prohibited_phase(self) -> None:
        mutations: list[dict[str, object]] = []
        for mutate in (
            lambda d: d["family_grids"][0]["counts"].append(4),
            lambda d: d["family_grids"][0].__setitem__("counts", [8, 16]),
            lambda d: d["family_grids"][0].__setitem__("n0", 4),
            lambda d: d.__setitem__("timeout_seconds", True),
            lambda d: d.__setitem__("phase", "validation"),
            lambda d: d["family_grids"].pop(),
            lambda d: d["sampling_structure"].__setitem__("independent_scene_assumption", True),
            lambda d: d["seed_roles"]["runtime_rng"].__setitem__("activity_assumed", True),
        ):
            candidate = copy.deepcopy(self.design)
            mutate(candidate)
            mutations.append(candidate)
        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(FREEZER.EpochFreezeError):
                    self.compile(design=candidate)

    def test_mission_incomplete_duplicate_hash_path_and_type_mutations(self) -> None:
        mutations = []
        for mutate in (
            lambda m: m["artifacts"].pop(),
            lambda m: m["artifacts"][1].__setitem__("seed", 1),
            lambda m: m["artifacts"][1].__setitem__("bank_id", m["artifacts"][0]["bank_id"]),
            lambda m: m.__setitem__(
                "design", {"bytes": 1, "path": "design.json", "sha256": "0" * 64}
            ),
            lambda m: m["artifacts"][0]["counts"][1].__setitem__("effective_scenario_sha256", m["artifacts"][1]["counts"][1]["effective_scenario_sha256"]),
            lambda m: m["artifacts"][0]["counts"][0]["scenario"].__setitem__("path", "../scenario.json"),
            lambda m: m["artifacts"][0].__setitem__("generator_seed_uint64", True),
            lambda m: m["artifacts"][0]["seed_roles"]["runtime_rng"].__setitem__("activity_assumed", True),
            lambda m: m["artifacts"][0]["sampling_structure"].__setitem__("independent_scene_assumption", True),
        ):
            candidate = copy.deepcopy(self.missions)
            mutate(candidate)
            mutations.append(candidate)
        for candidate in mutations:
            with self.subTest():
                with self.assertRaises(FREEZER.EpochFreezeError):
                    self.compile(missions=candidate)

    def test_binary_observer_run_class_path_hash_and_type_mutations(self) -> None:
        mutations = []
        for mutate in (
            lambda a: a["binaries"]["reference_fidelity"].__setitem__("run_classes", ["digest_fidelity"]),
            lambda a: a["binaries"]["reference_timing"]["observer_marker_counts"].__setitem__(FREEZER.OBSERVER_MARKER_NAMES[0], 1),
            lambda a: a["binaries"]["reference_fidelity"].__setitem__("observer_compiled", False),
            lambda a: a["binaries"]["reference_timing"].__setitem__("path", a["binaries"]["reference_fidelity"]["path"]),
            lambda a: a["binaries"]["optimized_timing"].__setitem__("bytes", True),
            lambda a: a["binaries"]["optimized_timing"].__setitem__("sha256", "0" * 63),
            lambda a: a["pixi_lock"].__setitem__("sha256", "0" * 64),
            lambda a: a["source_identity"]["reference"].__setitem__("tree", "0" * 40),
            lambda a: a.__setitem__("observed_utc", "2026-02-31T12:00:00Z"),
        ):
            candidate = copy.deepcopy(self.attestation)
            mutate(candidate)
            mutations.append(candidate)
        for candidate in mutations:
            with self.subTest():
                with self.assertRaises(FREEZER.EpochFreezeError):
                    self.compile(attestation=candidate)

    def test_all_generator_component_records_are_embedded_locatable_and_strict(self) -> None:
        expected = {
            "analyzer",
            "binary_attester",
            "binary_attestation",
            "freezer",
            "geometry",
            "mission_compiler",
            "mission_receipt",
            "protocol",
            "runner",
        }
        records = self.compilation.value["input_records"]
        self.assertEqual(set(records), expected)
        for name, record in records.items():
            raw = FREEZER.canonical_json_bytes(record["content"])
            self.assertEqual(record["bytes"], len(raw))
            self.assertEqual(record["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertTrue(record["path"].startswith(FREEZER.FUTURE_PLAN_ROOT + "/records/"))
        self.assertEqual(
            records["protocol"]["content"]["identity"]["path"],
            FREEZER.PROTOCOL_SOURCE_PATH,
        )
        self.assertNotEqual(
            records["protocol"]["content"]["identity"],
            records["freezer"]["content"]["identity"],
        )
        for key in self.components:
            candidate = copy.deepcopy(self.components)
            candidate[key]["classification"]["execution_authorized"] = True
            with self.subTest(key=key):
                with self.assertRaises(FREEZER.EpochFreezeError):
                    self.compile(components=candidate)
        path_mutation = copy.deepcopy(self.components)
        path_mutation["geometry_record"]["identity"]["path"] = (
            "scripts/comparison/analyze_headless_epoch_v1.py"
        )
        with self.assertRaises(FREEZER.EpochFreezeError):
            self.compile(components=path_mutation)

    def test_raw_mutations_of_schedule_config_inputs_and_claims_rejected(self) -> None:
        mutations = []
        for mutate in (
            lambda v: v["schedule"]["inventories"]["positions_replay"]["measured"].pop(),
            lambda v: v["schedule"]["execution_order"].reverse(),
            lambda v: v["schedule"]["timing_gate"].__setitem__("runtime_override_permitted", True),
            lambda v: v["schedule"]["warmups"]["slots"][0].__setitem__("binary_key", "optimized_fidelity"),
            lambda v: v["config_artifacts"][0].__setitem__("content_utf8", v["config_artifacts"][0]["content_utf8"] + "# mutation\n"),
            lambda v: v["scenes"][0].__setitem__("input_id", "0" * 64),
            lambda v: v["scenes"][0]["effective_runtime_raster"].__setitem__("sha256", "0" * 64),
            lambda v: v["scenes"][0]["seed_roles"]["runtime_rng"].__setitem__("activity_assumed", True),
            lambda v: v["input_records"]["freezer"].__setitem__("path", "records/freezer.json"),
            lambda v: v["input_records"]["geometry"]["content"]["contract"].__setitem__("positions_trace_required", False),
            lambda v: v["classification"].__setitem__("scientific_evidence", True),
            lambda v: v["failure_policy"].__setitem__("adaptive_stopping", True),
            lambda v: v["failure_policy"].__setitem__("legacy_evidence_permitted", True),
        ):
            candidate = copy.deepcopy(self.compilation.value)
            mutate(candidate)
            mutations.append(candidate)
        for candidate in mutations:
            with self.subTest():
                self.assert_raw_rejected(candidate)

    def test_candidate_publication_is_categorically_disabled_before_any_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(os.path.realpath(temporary))
            output = root / "candidate.json"
            forbidden = REPOSITORY_ROOT / "comparison/plans/headless-candidate-forbidden.json"
            self.assertFalse(forbidden.exists())
            for selected, repository in (
                (output, REPOSITORY_ROOT),
                (forbidden, REPOSITORY_ROOT),
                (forbidden, root),
            ):
                with self.subTest(selected=selected, repository=repository):
                    with mock.patch.object(
                        FREEZER,
                        "validate_compilation",
                        side_effect=AssertionError("validation capability reached"),
                    ) as validate_bomb, mock.patch.object(
                        FREEZER.os,
                        "open",
                        side_effect=AssertionError("open capability reached"),
                    ) as open_bomb:
                        with self.assertRaisesRegex(
                            FREEZER.EpochFreezeError,
                            "categorically disabled",
                        ):
                            FREEZER.publish_candidate_create_exclusive(
                                self.compilation,
                                output_path=selected,
                                repository_root=repository,
                            )
                    validate_bomb.assert_not_called()
                    open_bomb.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(forbidden.exists())

    def test_parent_close_failure_cannot_be_reached_by_disabled_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(os.path.realpath(temporary)) / "close-failure.json"
            with mock.patch.object(
                FREEZER.os,
                "close",
                side_effect=AssertionError("close capability reached"),
            ) as close_bomb:
                with self.assertRaisesRegex(
                    FREEZER.EpochFreezeError, "categorically disabled"
                ):
                    FREEZER.publish_candidate_create_exclusive(
                        self.compilation,
                        output_path=output,
                        repository_root=REPOSITORY_ROOT,
                    )
            close_bomb.assert_not_called()
            self.assertFalse(output.exists())

    def test_output_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(os.path.realpath(temporary))
            target = root / "target.json"
            target.write_bytes(b"untouched")
            output = root / "candidate.json"
            output.symlink_to(target.name)
            with self.assertRaisesRegex(
                FREEZER.EpochFreezeError, "categorically disabled"
            ):
                FREEZER.publish_candidate_create_exclusive(
                    self.compilation,
                    output_path=output,
                    repository_root=REPOSITORY_ROOT,
                )
            self.assertEqual(target.read_bytes(), b"untouched")

    def test_output_ancestor_swap_cannot_redirect_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(os.path.realpath(temporary))
            alias = root / "alias"
            parent = alias / "parent"
            parent.mkdir(parents=True)
            protected = root / "protected"
            (protected / "parent").mkdir(parents=True)
            output = parent / "candidate.json"
            with mock.patch.object(
                FREEZER,
                "_publication_path",
                side_effect=AssertionError("path policy capability reached"),
            ) as policy_bomb:
                with self.assertRaisesRegex(
                    FREEZER.EpochFreezeError, "categorically disabled"
                ):
                    FREEZER.publish_candidate_create_exclusive(
                        self.compilation,
                        output_path=output,
                        repository_root=REPOSITORY_ROOT,
                    )
            policy_bomb.assert_not_called()
            self.assertFalse((protected / "parent" / "candidate.json").exists())
            self.assertFalse(output.exists())

    def test_deep_json_and_huge_numeric_values_fail_closed(self) -> None:
        deep = b"[" * 100_000 + b"0" + b"]" * 100_000
        with self.assertRaises(FREEZER.EpochFreezeError):
            FREEZER.strict_json_bytes(deep, "deep mutation")
        candidate = copy.deepcopy(self.missions)
        candidate["artifacts"][0]["static_inter_route_distance"][
            "threshold_m"
        ] = 10**4000
        with self.assertRaises(FREEZER.EpochFreezeError):
            self.compile(missions=candidate)

    def test_static_nonexecuting_surface_and_no_default_plan_or_result_write(self) -> None:
        source = SOURCE_PATH.read_text("utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue(
            imports.isdisjoint(
                {"socket", "subprocess", "urllib", "requests", "http", "shutil"}
            )
        )
        forbidden_calls = {
            "system",
            "popen",
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
            "execv",
            "execve",
        }
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertFalse(
            [
                node
                for node in calls
                if isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
            ]
        )
        self.assertNotIn("argparse", imports)
        self.assertNotIn("if __name__", source)
        self.assertEqual(source.count("os.write("), 1)
        self.assertNotIn("comparison/results", source)
        duplicate_literals = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            ]
            if len(keys) != len(set(keys)):
                duplicate_literals.append(node.lineno)
        self.assertEqual(duplicate_literals, [])


if __name__ == "__main__":
    unittest.main()
