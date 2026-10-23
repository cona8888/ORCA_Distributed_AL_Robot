#!/usr/bin/env python3
"""Contract checks for the prospective bounded-open split amendment v1."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = (
    REPO_ROOT / "docs/comparison/bounded_open_split_amendment_v1.json"
)
REGISTRY_BYTES = 7005
REGISTRY_SHA256 = (
    "42525ab9b5b66754789c371b8a31783ac41ee0c38d694e5ebaa0b3985f3fa260"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_exact_registry(raw: bytes) -> dict[str, object]:
    if len(raw) != REGISTRY_BYTES:
        raise ValueError("bounded-open registry byte count differs")
    if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
        raise ValueError("bounded-open registry SHA-256 differs")
    value = json.loads(raw)
    canonical = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if raw != canonical:
        raise ValueError("bounded-open registry is not canonical JSON")
    return value


class BoundedOpenSplitAmendmentV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry_bytes = REGISTRY_PATH.read_bytes()
        cls.registry = load_exact_registry(cls.registry_bytes)
        environment_path = REPO_ROOT / cls.registry["base_artifacts"][
            "environment_manifest"
        ]["path"]
        experiment_path = REPO_ROOT / cls.registry["base_artifacts"][
            "experiment_manifest"
        ]["path"]
        cls.environment = json.loads(environment_path.read_bytes())
        cls.experiment = json.loads(experiment_path.read_bytes())

    def test_registry_is_canonical_json(self) -> None:
        self.assertEqual(len(self.registry_bytes), REGISTRY_BYTES)
        self.assertEqual(
            hashlib.sha256(self.registry_bytes).hexdigest(), REGISTRY_SHA256
        )
        self.assertEqual(load_exact_registry(self.registry_bytes), self.registry)

    def test_exact_registry_identity_rejects_authority_and_scope_mutations(
        self,
    ) -> None:
        mutants: list[dict[str, object]] = []

        authority = copy.deepcopy(self.registry)
        authority["scientific_role"] = "validation-authorizing"
        mutants.append(authority)

        status = copy.deepcopy(self.registry)
        status["status"] = "validation-authorized"
        mutants.append(status)

        scope = copy.deepcopy(self.registry)
        scope["bounded_open_exception"]["exception_scope"] = "all families"
        mutants.append(scope)

        requirements = copy.deepcopy(self.registry)
        requirements["validation_lock"]["requirements"] = []
        mutants.append(requirements)

        for index, mutant in enumerate(mutants):
            raw = (json.dumps(mutant, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            with self.subTest(mutant=index):
                with self.assertRaises(ValueError):
                    load_exact_registry(raw)

    def test_contract_scope_and_validation_literals_are_exact(self) -> None:
        self.assertEqual(
            set(self.registry),
            {
                "amendment_id",
                "amendment_protocol",
                "base_artifacts",
                "bounded_open_exception",
                "non_open_map_disjointness",
                "schema_version",
                "scientific_role",
                "split_independence",
                "status",
                "validation_lock",
                "validation_outcomes_accessed_for_authorship",
            },
        )
        self.assertEqual(
            self.registry["schema_version"],
            "dancers-bounded-open-split-amendment-v1",
        )
        self.assertEqual(
            self.registry["scientific_role"],
            "prospective-narrow-split-policy-clarification",
        )
        self.assertEqual(
            self.registry["status"], "prospective-before-validation-access"
        )

        exception = self.registry["bounded_open_exception"]
        self.assertEqual(
            exception["exception_scope"],
            "the two registered bounded-open safety-border raster paths only",
        )
        self.assertEqual(exception["allowed_family"], "open")
        self.assertFalse(exception["open_map_generalization_claim_permitted"])
        self.assertFalse(
            exception["substitute_or_additional_shared_raster_permitted"]
        )

        lock = self.registry["validation_lock"]
        self.assertEqual(
            lock["requirements"],
            [
                "complete passing canonical receipts for every registered calibration fidelity gate",
                "complete eligible timing evidence under fidelity, host, resource, and task-validity gates for every family decision",
                "exclusive canonical capacity-decision artifact for every family binding all eligible calibration evidence",
                "calibration decisions and manifests committed before validation seed 11 is run",
                "prospectively frozen validation plan authenticating this and every other applicable amendment",
            ],
        )
        self.assertFalse(
            lock["access_to_held_out_outcomes_authorized_by_this_amendment"]
        )
        self.assertFalse(lock["validation_plan_authorized_by_this_amendment"])
        self.assertFalse(lock["validation_run_authorized_by_this_amendment"])

    def test_base_and_amendment_hash_bindings(self) -> None:
        bindings = [
            self.registry["amendment_protocol"],
            *self.registry["base_artifacts"].values(),
        ]
        for binding in bindings:
            path = REPO_ROOT / binding["path"]
            with self.subTest(path=binding["path"]):
                self.assertTrue(path.is_file())
                self.assertEqual(sha256(path), binding["sha256"])

    def test_open_exception_matches_registered_inputs(self) -> None:
        entries = {
            entry["split"]: entry
            for entry in self.environment["environments"]
            if entry["family"] == "open"
        }
        self.assertEqual(set(entries), {"calibration", "validation"})

        exception = self.registry["bounded_open_exception"]
        self.assertEqual(exception["allowed_family"], "open")
        self.assertTrue(exception["exact_path_byte_count_and_sha256_required"])
        self.assertFalse(exception["substitute_or_additional_shared_raster_permitted"])
        self.assertFalse(exception["open_map_generalization_claim_permitted"])

        for split, entry in entries.items():
            registered = exception[split]
            for field in ("map", "scenario"):
                manifest_artifact = entry[field]
                amendment_artifact = registered[field]
                expected_path = f"comparison/environments/{manifest_artifact['path']}"
                with self.subTest(split=split, field=field):
                    self.assertEqual(amendment_artifact["path"], expected_path)
                    self.assertEqual(
                        amendment_artifact["bytes"], manifest_artifact["bytes"]
                    )
                    self.assertEqual(
                        amendment_artifact["sha256"], manifest_artifact["sha256"]
                    )
                    path = REPO_ROOT / amendment_artifact["path"]
                    self.assertEqual(path.stat().st_size, amendment_artifact["bytes"])
                    self.assertEqual(sha256(path), amendment_artifact["sha256"])

            bank = registered["capacity_bank"]
            manifest_bank = entry["capacity"]["bank"]
            self.assertEqual(
                bank["path"], f"comparison/environments/{manifest_bank['path']}"
            )
            self.assertEqual(bank["bytes"], manifest_bank["bytes"])
            self.assertEqual(bank["sha256"], manifest_bank["sha256"])
            self.assertEqual(bank["bank_id"], entry["capacity"]["bank_id"])
            bank_path = REPO_ROOT / bank["path"]
            self.assertEqual(bank_path.stat().st_size, bank["bytes"])
            self.assertEqual(sha256(bank_path), bank["sha256"])
            self.assertEqual(registered["map_seed"], entry["map_seed"])
            self.assertEqual(registered["mission_seed"], entry["mission_seed"])

        calibration = exception["calibration"]
        validation = exception["validation"]
        self.assertEqual(
            calibration["map"]["sha256"], exception["shared_map_sha256"]
        )
        self.assertEqual(
            validation["map"]["sha256"], exception["shared_map_sha256"]
        )
        self.assertEqual(
            (REPO_ROOT / calibration["map"]["path"]).read_bytes(),
            (REPO_ROOT / validation["map"]["path"]).read_bytes(),
        )
        self.assertNotEqual(calibration["map_seed"], validation["map_seed"])
        self.assertNotEqual(calibration["mission_seed"], validation["mission_seed"])
        self.assertNotEqual(
            calibration["scenario"]["sha256"], validation["scenario"]["sha256"]
        )
        self.assertNotEqual(
            calibration["capacity_bank"]["sha256"],
            validation["capacity_bank"]["sha256"],
        )
        self.assertNotEqual(
            calibration["capacity_bank"]["bank_id"],
            validation["capacity_bank"]["bank_id"],
        )

        prefixes = {
            split: {
                item["robot_count"]: item["sha256"]
                for item in entry["capacity"]["prefix_scenarios"]
            }
            for split, entry in entries.items()
        }
        self.assertEqual(set(prefixes["calibration"]), set(prefixes["validation"]))
        for count in prefixes["calibration"]:
            with self.subTest(robot_count=count):
                self.assertNotEqual(
                    prefixes["calibration"][count], prefixes["validation"][count]
                )

    def test_non_open_maps_remain_exact_and_split_disjoint(self) -> None:
        registered_pairs = self.registry["non_open_map_disjointness"][
            "registered_pairs"
        ]
        self.assertTrue(self.registry["non_open_map_disjointness"]["required"])
        self.assertEqual(
            set(registered_pairs),
            {"perfect_maze", "fractal_low", "fractal_medium", "fractal_high"},
        )
        entries = {
            (entry["family"], entry["split"]): entry
            for entry in self.environment["environments"]
        }
        for family, pair in registered_pairs.items():
            for split in ("calibration", "validation"):
                manifest_map = entries[(family, split)]["map"]
                amendment_map = pair[split]
                self.assertEqual(
                    amendment_map["path"],
                    f"comparison/environments/{manifest_map['path']}",
                )
                self.assertEqual(amendment_map["bytes"], manifest_map["bytes"])
                self.assertEqual(amendment_map["sha256"], manifest_map["sha256"])
                path = REPO_ROOT / amendment_map["path"]
                self.assertEqual(path.stat().st_size, amendment_map["bytes"])
                self.assertEqual(sha256(path), amendment_map["sha256"])
            self.assertNotEqual(
                pair["calibration"]["sha256"], pair["validation"]["sha256"]
            )

    def test_phase_seeds_are_exact_and_pairwise_disjoint(self) -> None:
        split = self.registry["split_independence"]
        expected = self.experiment["randomization"]
        phases = {
            "calibration": split["calibration_run_seeds"],
            "validation": split["validation_run_seeds"],
            "audit": split["audit_run_seeds"],
        }
        for phase, seeds in phases.items():
            self.assertEqual(seeds, expected[f"{phase}_seeds"])
        for left, left_seeds in phases.items():
            for right, right_seeds in phases.items():
                if left < right:
                    self.assertTrue(set(left_seeds).isdisjoint(right_seeds))
        self.assertTrue(split["phase_run_seed_sets_must_be_pairwise_disjoint"])
        self.assertTrue(split["fixed_mission_scenario_bytes_must_differ"])
        self.assertTrue(split["capacity_bank_bytes_must_differ"])
        self.assertTrue(split["capacity_bank_ids_must_differ"])
        self.assertTrue(split["corresponding_capacity_prefix_sha256_must_differ"])

    def test_amendment_does_not_unlock_validation(self) -> None:
        lock = self.registry["validation_lock"]
        self.assertFalse(lock["access_to_held_out_outcomes_authorized_by_this_amendment"])
        self.assertFalse(lock["validation_plan_authorized_by_this_amendment"])
        self.assertFalse(lock["validation_run_authorized_by_this_amendment"])
        self.assertTrue(lock["all_applicable_calibration_and_decision_gates_must_pass"])
        self.assertTrue(
            lock["failed_missing_incomplete_or_unauthenticated_gate_blocks_validation"]
        )
        self.assertFalse(self.registry["validation_outcomes_accessed_for_authorship"])


if __name__ == "__main__":
    unittest.main()
