#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


F2 = load("_test_freezer_v2", "scripts/comparison/freeze_headless_epoch_v2.py")
F1 = load("_test_freezer_v1", "scripts/comparison/freeze_headless_epoch_v1.py")
MISSION = load(
    "_test_mission_v1", "scripts/comparison/generate_headless_mission_banks_v1.py"
)
ATTESTER1 = load(
    "_test_attester_v1", "scripts/comparison/attest_headless_binaries_v1.py"
)
ATTESTER2 = load(
    "_test_attester_v2", "scripts/comparison/attest_headless_binaries_v2.py"
)


def identity(relative: str) -> dict[str, object]:
    raw = (ROOT / relative).read_bytes()
    return {
        "bytes": len(raw),
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def component(schema, contract, relative, *, phase=False):
    value = {
        "classification": dict(F1.COMPONENT_CLASSIFICATION),
        "contract": dict(contract),
        "identity": identity(relative),
        "schema_version": schema,
        "track_id": F1.TRACK_ID,
    }
    if phase:
        value["phase"] = "calibration"
    return value


def successor_component(schema, contract, relative):
    return {
        "classification": dict(F2.COMPONENT_CLASSIFICATION),
        "contract": dict(contract),
        "identity": identity(relative),
        "schema_version": schema,
        "track_id": F2.TRACK_ID,
    }


class FreezerV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mission_design = {
            "phase": "calibration",
            "requests": [
                {"counts": [8, 12, 16, 24, 32], "family": family, "seed": seed}
                for family in F1.FAMILIES
                for seed in F1.SEEDS
            ],
            "schema_version": "dancers-headless-mission-design-v1",
        }
        missions = MISSION.compile_mission_banks(ROOT, mission_design)
        design = {
            "family_grids": [
                {"counts": [8, 12, 16, 24, 32], "family": family, "n0": 8}
                for family in F1.FAMILIES
            ],
            "normalized_config": F1.NORMALIZED_CONFIG,
            "order_seed": "12" * 32,
            "phase": "calibration",
            "sampling_structure": F1.SAMPLING_STRUCTURE,
            "schema_version": F1.DESIGN_SCHEMA,
            "seed_roles": F1.SEED_ROLES,
            "timeout_seconds": 300,
            "track_id": F1.TRACK_ID,
        }
        attestation1 = ATTESTER1.build_attestation(
            repository_root=ROOT, observed_utc="2026-08-11T12:00:00Z"
        )
        records = {
            "protocol_record": component(
                F1.PROTOCOL_RECORD_SCHEMA,
                F1.PROTOCOL_CONTRACT,
                F1.PROTOCOL_SOURCE_PATH,
                phase=True,
            ),
            "runner_record": component(
                F1.RUNNER_RECORD_SCHEMA,
                F1.RUNNER_CONTRACT,
                "scripts/comparison/run_headless_epoch_v1.py",
            ),
            "analyzer_record": component(
                F1.ANALYZER_RECORD_SCHEMA,
                F1.ANALYZER_CONTRACT,
                "scripts/comparison/analyze_headless_epoch_v1.py",
            ),
            "geometry_record": component(
                F1.GEOMETRY_RECORD_SCHEMA,
                F1.GEOMETRY_CONTRACT,
                "scripts/comparison/headless_geometry_v1.py",
            ),
            "freezer_record": component(
                F1.FREEZER_RECORD_SCHEMA,
                F1.FREEZER_CONTRACT,
                "scripts/comparison/freeze_headless_epoch_v1.py",
            ),
            "mission_compiler_record": component(
                F1.MISSION_COMPILER_RECORD_SCHEMA,
                F1.MISSION_COMPILER_CONTRACT,
                "scripts/comparison/generate_headless_mission_banks_v1.py",
            ),
            "binary_attester_record": component(
                F1.BINARY_ATTESTER_RECORD_SCHEMA,
                F1.BINARY_ATTESTER_CONTRACT,
                "scripts/comparison/attest_headless_binaries_v1.py",
            ),
        }
        cls.base = F1.compile_epoch(
            design=design,
            mission_receipt=missions.manifest,
            binary_attestation=attestation1,
            **records,
        ).value
        build = ROOT / "build/comparison/headless-runtime-v1/826835e100dfbbae4fc558f83e85aa07019ca366ca5df417e3ea5d81a31d02b3"
        cls.attestation2 = ATTESTER2.attest_build(ROOT, build)
        cls.activation = json.loads(
            (ROOT / "comparison/headless-runtime-rebuild-v1/activation_receipt_v1.json").read_bytes()
        )
        cls.compilation = F2.compile_registered_plan(
            base_epoch=cls.base,
            runtime_attestation=cls.attestation2,
            activation_receipt=cls.activation,
            successor_freezer_record=F2.build_freezer_record(ROOT),
            runtime_attester_record=successor_component(
                F2.RUNTIME_ATTESTER_RECORD_SCHEMA,
                F2.RUNTIME_ATTESTER_CONTRACT,
                F2.RUNTIME_ATTESTER_SOURCE_PATH.as_posix(),
            ),
            activation_qualifier_record=successor_component(
                F2.ACTIVATION_QUALIFIER_RECORD_SCHEMA,
                F2.ACTIVATION_QUALIFIER_CONTRACT,
                F2.ACTIVATION_QUALIFIER_SOURCE_PATH.as_posix(),
            ),
        )

    def test_01_registered_matrix_and_denominator(self):
        value = self.compilation.value
        self.assertEqual(value["schema_version"], F2.EPOCH_SCHEMA)
        self.assertEqual(len(value["scenes"]), 250)
        self.assertEqual(len(value["input_records"]), 14)
        self.assertEqual(len(value["schedule"]["warmups"]["slots"]), 20)
        measured = sum(
            item["measured_process_count"]
            for item in value["schedule"]["inventories"].values()
        )
        self.assertEqual(measured + 20, 3520)
        self.assertEqual(F2.validate_epoch_bytes(self.compilation.data), value)

    def test_02_runtime_binary_and_activation_are_bound(self):
        slot = self.compilation.value["schedule"]["inventories"][
            "digest_fidelity"
        ]["measured"][0]
        product = self.attestation2["products"]["reference-fidelity"]["binary"]
        self.assertEqual(slot["binary_sha256"], product["sha256"])
        self.assertIn("runtime_attestation", self.compilation.value["input_records"])
        self.assertIn("activation_receipt", self.compilation.value["input_records"])

    def test_03_mutated_activation_fails(self):
        value = copy.deepcopy(self.compilation.value)
        content = value["input_records"]["activation_receipt"]["content"]
        content["products"][0]["returncode"] = 1
        envelope = F2._envelope(
            "activation-receipt", content, F2.ACTIVATION_SCHEMA
        )
        value["input_records"]["activation_receipt"] = envelope
        with self.assertRaises(F2.EpochFreezeV2Error):
            F2.validate_epoch_bytes(F2.canonical_json_bytes(value))

    def test_04_mutated_slot_fails(self):
        value = copy.deepcopy(self.compilation.value)
        value["schedule"]["inventories"]["headless_timing"]["measured"][0][
            "binary_sha256"
        ] = "0" * 64
        with self.assertRaises(F2.EpochFreezeV2Error):
            F2.validate_epoch_bytes(F2.canonical_json_bytes(value))

    def test_05_publication_guards(self):
        with self.assertRaises(F2.EpochFreezeV2Error):
            F2.publish_registered_plan(object())
        with self.assertRaises(F2.EpochFreezeV2Error):
            F2.main()


if __name__ == "__main__":
    unittest.main()
