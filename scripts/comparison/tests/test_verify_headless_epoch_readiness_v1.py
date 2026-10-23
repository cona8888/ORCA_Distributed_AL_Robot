#!/usr/bin/env python3
"""In-process regressions for the nonauthorizing Track-B readiness verifier."""

from __future__ import annotations

import ast
import copy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
VERIFY_PATH = ROOT / "scripts/comparison/verify_headless_epoch_readiness_v1.py"
RECORD_PATH = ROOT / "docs/simulator/headless_epoch_readiness_v1.json"
EXPECTED_VERIFIER_BYTES = 22760
EXPECTED_VERIFIER_SHA256 = "7c5548ab5b12f3f44d5fff4d269466a8bc09d1b8448ab5cebb83b47b9d8dd5dd"

SPEC = importlib.util.spec_from_file_location("headless_epoch_readiness_v1", VERIFY_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFY
SPEC.loader.exec_module(VERIFY)


class HeadlessEpochReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.record_raw = RECORD_PATH.read_bytes()
        cls.record = json.loads(cls.record_raw)
        cls.protocol_raw = (
            ROOT / cls.record["bindings"]["protocol_registry"]["path"]
        ).read_bytes()
        cls.protocol = json.loads(cls.protocol_raw)
        cls.markdown_raw = (
            ROOT / cls.record["bindings"]["protocol_markdown"]["path"]
        ).read_bytes()
        cls.build = json.loads((
            ROOT / cls.record["bindings"]["build_manifest"]["path"]
        ).read_bytes())
        cls.environment = json.loads((
            ROOT / cls.record["bindings"]["environment_manifest"]["path"]
        ).read_bytes())

    def test_01_frozen_identities_and_live_locked_verdict(self) -> None:
        verifier_raw = VERIFY_PATH.read_bytes()
        self.assertEqual(len(verifier_raw), EXPECTED_VERIFIER_BYTES)
        self.assertEqual(hashlib.sha256(verifier_raw).hexdigest(), EXPECTED_VERIFIER_SHA256)
        self.assertEqual(len(self.record_raw), VERIFY.EXPECTED_RECORD_BYTES)
        self.assertEqual(
            hashlib.sha256(self.record_raw).hexdigest(),
            VERIFY.EXPECTED_RECORD_SHA256,
        )
        self.assertEqual(self.record_raw, VERIFY.canonical_json_bytes(self.record))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(VERIFY.main([]), 0)
        verdict_raw = output.getvalue().encode("utf-8")
        verdict = json.loads(verdict_raw)
        self.assertEqual(verdict_raw, VERIFY.canonical_json_bytes(verdict))
        self.assertEqual(verdict["status"], "pass-locked-readiness")
        self.assertEqual(verdict["bound_identity_count"], 5)
        self.assertEqual(verdict["missing_required_freezes"], list(VERIFY.FREEZES))
        for key in (
            "benchmark_claim_ready",
            "current_evidence_admissible_for_track_b",
            "execution_authorized",
            "timing_unlock",
            "validation_unlock",
        ):
            self.assertIs(verdict[key], False, key)

    def test_02_strict_and_canonical_registry_parser(self) -> None:
        for label, raw in {
            "duplicate": b'{"schema_version":"x","schema_version":"y"}\n',
            "nan": b'{"value":NaN}\n',
            "invalid_utf8": b'{"value":"\xff"}\n',
        }.items():
            with self.subTest(label=label), self.assertRaises(VERIFY.ReadinessError):
                VERIFY._strict_json(raw, label)
        pretty = json.dumps(self.record, indent=2).encode("utf-8")
        with self.assertRaisesRegex(VERIFY.ReadinessError, "canonical JSON"):
            VERIFY._parse_record_bytes(pretty)
        without_lf = self.record_raw[:-1]
        with self.assertRaises(VERIFY.ReadinessError):
            VERIFY._parse_record_bytes(without_lf)

    def test_03_nonauthorization_and_shape_mutations(self) -> None:
        mutations = [
            lambda value: value.__setitem__("execution_authorized", True),
            lambda value: value.__setitem__("timing_unlock", True),
            lambda value: value.__setitem__("validation_unlock", True),
            lambda value: value.__setitem__("benchmark_claim_ready", True),
            lambda value: value["basis"].__setitem__("checkpoint_commit", "0" * 40),
            lambda value: value["classification"].__setitem__("documentation_only", False),
            lambda value: value["classification"].__setitem__("scientific_evidence", True),
            lambda value: value["classification"].__setitem__("documentation_only", 1),
            lambda value: value["classification"].__setitem__("scientific_evidence", 0),
            lambda value: value["epoch"].__setitem__("execution_status", "ready"),
            lambda value: value["epoch"].__setitem__("plan_namespace_expected_state", "present"),
            lambda value: value["epoch"].__setitem__("result_root", "results/other"),
            lambda value: value["evidence_boundary"].__setitem__("rerun_1_reuse_permitted", True),
            lambda value: value["evidence_boundary"].__setitem__("rerun_1_reuse_permitted", 0),
            lambda value: value["evidence_boundary"].__setitem__("build_manifest_calibration_permitted", 1),
            lambda value: value["evidence_boundary"].__setitem__("build_manifest_validation_permitted", True),
            lambda value: value["evidence_boundary"].__setitem__("build_manifest_validation_permitted", 0),
            lambda value: value["evidence_boundary"].__setitem__("visual_positions_replay_timing_evidence_permitted", True),
            lambda value: value["required_pre_execution_freezes"].__setitem__("all_satisfied", True),
            lambda value: value["required_pre_execution_freezes"]["missing"].pop(),
            lambda value: value["required_pre_execution_freezes"]["registered_in_headless_plan_namespace"].append("runner"),
            lambda value: value["source_pins"].__setitem__("reference_source_commit", "not-an-oid"),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(VERIFY.ReadinessError):
                VERIFY._shape(changed)

    def test_04_every_registry_byte_mutation_requires_refreeze(self) -> None:
        mutations = [
            lambda value: value["bindings"]["build_manifest"].__setitem__("sha256", "0" * 64),
            lambda value: value["bindings"]["environment_manifest"].__setitem__("bytes", 1),
            lambda value: value["bindings"]["pixi_lock"].__setitem__("sha256", "f" * 64),
            lambda value: value["source_pins"].__setitem__("optimized_source_commit", "1" * 40),
            lambda value: value["evidence_boundary"].__setitem__(
                "current_evidence_conclusion", "admissible"
            ),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            raw = VERIFY.canonical_json_bytes(changed)
            with self.subTest(index=index), self.assertRaisesRegex(
                VERIFY.ReadinessError, "frozen byte identity"
            ):
                VERIFY._parse_record_bytes(raw)

    def test_05_protocol_cross_binding_mutations(self) -> None:
        mutations = [
            lambda value: value["status"].__setitem__("execution_authorized_by_this_checkpoint", True),
            lambda value: value["headless_scientific"].__setitem__("execution_status", "ready"),
            lambda value: value["headless_scientific"]["required_pre_execution_freezes"].pop(),
            lambda value: value["headless_scientific"].__setitem__("rerun_1_reuse_permitted", True),
            lambda value: value["headless_scientific"]["run_classes"]["positions_replay"].__setitem__("timed", True),
            lambda value: value["output_namespaces"].__setitem__("headless_plan_root", "comparison/plans/other"),
            lambda value: value["prior_evidence_policy"].__setitem__("admission_rule", "reuse allowed"),
            lambda value: value["claim_boundaries"]["visual_diagnostic_prohibited"].remove("speedup"),
            lambda value: value["identities"].__setitem__("reference_source_commit", "0" * 40),
            lambda value: value["identities"]["build_manifest"].__setitem__("sha256", "0" * 64),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.protocol)
            with self.subTest(index=index), self.assertRaises(VERIFY.ReadinessError):
                mutation(changed)
                VERIFY._verify_contract(
                    self.record,
                    changed,
                    VERIFY.canonical_json_bytes(changed),
                )

    def test_06_build_and_environment_cross_binding_mutations(self) -> None:
        build_mutations = [
            lambda value: value.__setitem__("status", "benchmark-ready"),
            lambda value: value["repository"].__setitem__("reference_pin", "0" * 40),
            lambda value: value["repository"].__setitem__("optimized_applied_head", "0" * 40),
            lambda value: value["toolchain"].__setitem__("pixi_lock_sha256", "0" * 64),
            lambda value: value["builds"]["reference_timing"].__setitem__("algorithm_revision", "0" * 40),
            lambda value: value["builds"]["optimized_fidelity"].__setitem__("applied_head", "0" * 40),
            lambda value: value["builds"]["reference_fidelity"].__setitem__("binary_sha256", "0" * 64),
            lambda value: value["gates"].__setitem__("calibration_permitted", False),
            lambda value: value["gates"].__setitem__("validation_permitted", True),
        ]
        for index, mutation in enumerate(build_mutations):
            changed = copy.deepcopy(self.build)
            mutation(changed)
            with self.subTest(kind="build", index=index), self.assertRaises(VERIFY.ReadinessError):
                VERIFY._verify_build_manifest(self.record, changed, self.protocol)

        environment_mutations = [
            lambda value: value.__setitem__("schema_version", 2),
            lambda value: value.__setitem__("schema_version", True),
            lambda value: value["families"].reverse(),
            lambda value: value["generator"].__setitem__("stdlib_only", False),
        ]
        for index, mutation in enumerate(environment_mutations):
            changed = copy.deepcopy(self.environment)
            mutation(changed)
            with self.subTest(kind="environment", index=index), self.assertRaises(VERIFY.ReadinessError):
                VERIFY._verify_environment_manifest(changed, self.protocol)
        changed_contract = copy.deepcopy(self.protocol)
        changed_contract["environment_design"]["families"].reverse()
        with self.assertRaises(VERIFY.ReadinessError):
            VERIFY._verify_environment_manifest(self.environment, changed_contract)

    def test_07_markdown_and_namespace_locks(self) -> None:
        VERIFY._verify_protocol_markdown(self.markdown_raw)
        for excerpt in (
            b"## Track B: future headless scientific epoch",
            b"Rerun-1 is inadmissible.",
            b"Validation and audit traces remain unopened",
        ):
            changed = self.markdown_raw.replace(excerpt, b"X" * len(excerpt), 1)
            with self.subTest(excerpt=excerpt), self.assertRaises(VERIFY.ReadinessError):
                VERIFY._verify_protocol_markdown(changed)
        VERIFY._assert_namespace_absent(ROOT, VERIFY.PLAN_ROOT, "plan")
        VERIFY._assert_namespace_absent(ROOT, VERIFY.RESULT_ROOT, "result")
        with self.assertRaisesRegex(VERIFY.ReadinessError, "must remain absent"):
            VERIFY._assert_namespace_absent(
                ROOT,
                "docs/simulator/SIMULATOR_CONTRACT_V1.md",
                "existing bound file",
            )
        for path in ("../outside", "/absolute/path", "results\\other"):
            with self.subTest(path=path), self.assertRaises(VERIFY.ReadinessError):
                VERIFY._assert_namespace_absent(ROOT, path, "invalid path")

    def test_08_live_read_and_probe_surface_is_exact(self) -> None:
        reads: list[str] = []
        probes: list[str] = []
        real_read = VERIFY._read_regular
        real_probe = VERIFY._assert_namespace_absent

        def tracked_read(path: Path, maximum: int = VERIFY.MAX_BOUND_BYTES) -> bytes:
            reads.append(Path(path).relative_to(ROOT).as_posix())
            return real_read(path, maximum)

        def tracked_probe(root: Path, relative: str, label: str) -> None:
            probes.append(relative)
            real_probe(root, relative, label)

        with mock.patch.object(VERIFY, "_read_regular", side_effect=tracked_read), mock.patch.object(
            VERIFY, "_assert_namespace_absent", side_effect=tracked_probe
        ):
            verdict = VERIFY.verify()
        self.assertEqual(verdict["status"], "pass-locked-readiness")
        self.assertEqual(reads, [
            "docs/simulator/headless_epoch_readiness_v1.json",
            "comparison/build_manifest.json",
            "comparison/environments/manifest.json",
            "pixi.lock",
            "docs/simulator/SIMULATOR_CONTRACT_V1.md",
            "docs/simulator/simulator_contract_v1.json",
        ])
        self.assertEqual(probes, [VERIFY.PLAN_ROOT, VERIFY.RESULT_ROOT])
        self.assertFalse(any(path.startswith("results/") for path in reads))

    def test_09_source_has_no_execution_network_enumeration_or_write_surface(self) -> None:
        source = VERIFY_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_imports = {
            "asyncio", "http", "importlib", "requests", "shutil", "socket",
            "subprocess", "urllib",
        }
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(imported & forbidden_imports)
        self.assertNotIn("Simulator", source)
        self.assertNotIn("/validation/", source)
        self.assertNotIn("/audit/", source)

        forbidden_attributes = {
            "chmod", "chown", "copy", "copy2", "exec", "glob", "hardlink_to",
            "iterdir", "link", "listdir", "makedirs", "mkdir", "popen", "remove",
            "rename", "replace", "rglob", "rmdir", "scandir", "symlink_to", "system",
            "touch", "unlink", "walk", "write", "write_bytes", "write_text",
        }
        forbidden_names = {"compile", "eval", "exec", "open", "__import__"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden_attributes)
                if node.func.attr == "open":
                    self.assertIsInstance(node.func.value, ast.Name)
                    self.assertEqual(node.func.value.id, "os")
                    flags = ast.unparse(node.args[1])
                    self.assertEqual(flags, "flags")
                    self.assertIn(
                        'flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)',
                        source,
                    )
                    self.assertNotIn("O_WRONLY", source)
                    self.assertNotIn("O_RDWR", source)
                    self.assertNotIn("O_CREAT", source)
            elif isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, forbidden_names)

    def test_10_reader_bounds_and_cli_argument_lock(self) -> None:
        protocol_path = ROOT / self.record["bindings"]["protocol_registry"]["path"]
        self.assertEqual(VERIFY._read_regular(protocol_path), self.protocol_raw)
        with self.assertRaises(VERIFY.ReadinessError):
            VERIFY._read_regular(protocol_path, 0)
        with self.assertRaises(VERIFY.ReadinessError):
            VERIFY._read_regular(protocol_path, VERIFY.MAX_BOUND_BYTES + 1)
        with self.assertRaisesRegex(VERIFY.ReadinessError, "accepts no arguments"):
            VERIFY.main(["--execute"])


if __name__ == "__main__":
    unittest.main()
