#!/usr/bin/env python3
"""Regressions for the closed Track-B implementation checkpoint verifier."""

from __future__ import annotations

import ast
import copy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
VERIFY_PATH = (
    ROOT / "scripts/comparison/verify_headless_scientific_implementation_v1.py"
)
REGISTRY_PATH = (
    ROOT / "docs/simulator/headless_scientific_implementation_checkpoint_v1.json"
)
EXPECTED_VERIFIER_BYTES = 99_588
EXPECTED_VERIFIER_SHA256 = "da38be03ca442ddd2556eda358a017b91d992c496575c367e2a7c9362903cec2"

SPEC = importlib.util.spec_from_file_location(
    "headless_scientific_implementation_v1", VERIFY_PATH
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class HeadlessScientificImplementationV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry_raw = REGISTRY_PATH.read_bytes()
        cls.registry = json.loads(cls.registry_raw)

    def test_01_frozen_identities_live_reconstruction_and_all_false_verdict(self) -> None:
        verifier_raw = VERIFY_PATH.read_bytes()
        self.assertEqual(len(verifier_raw), EXPECTED_VERIFIER_BYTES)
        self.assertEqual(
            hashlib.sha256(verifier_raw).hexdigest(), EXPECTED_VERIFIER_SHA256
        )
        self.assertEqual(len(self.registry_raw), VERIFY.EXPECTED_REGISTRY_BYTES)
        self.assertEqual(
            hashlib.sha256(self.registry_raw).hexdigest(),
            VERIFY.EXPECTED_REGISTRY_SHA256,
        )
        self.assertEqual(
            self.registry_raw, VERIFY.canonical_json_bytes(self.registry)
        )

        reads: list[str] = []
        probes: list[str] = []
        computed: dict[str, object] = {}
        real_read = VERIFY._read_regular_relative
        real_probe = VERIFY._assert_absent
        real_compute = VERIFY._compute_receipts

        def tracked_read(
            root: Path, relative: str, label: str, maximum: int
        ) -> bytes:
            reads.append(relative)
            return real_read(root, relative, label, maximum)

        def tracked_probe(root: Path, relative: str, label: str) -> None:
            probes.append(relative)
            real_probe(root, relative, label)

        def tracked_compute(
            record: dict[str, object], authenticated: dict[str, bytes]
        ) -> dict[str, object]:
            result = real_compute(record, authenticated)
            computed.update(result)
            return result

        output = io.StringIO()
        with mock.patch.object(
            VERIFY, "_read_regular_relative", side_effect=tracked_read
        ), mock.patch.object(
            VERIFY, "_assert_absent", side_effect=tracked_probe
        ), mock.patch.object(
            VERIFY, "_compute_receipts", side_effect=tracked_compute
        ), redirect_stdout(output):
            self.assertEqual(VERIFY.main([]), 0)

        verdict_raw = output.getvalue().encode("utf-8")
        verdict = json.loads(verdict_raw)
        self.assertEqual(verdict_raw, VERIFY.canonical_json_bytes(verdict))
        self.assertEqual(
            verdict["status"],
            "pass-closed-nonauthorizing-implementation-checkpoint",
        )
        self.assertEqual(verdict["bound_artifact_count"], 34)
        self.assertEqual(verdict["historical_pre_enable_blocker_count"], 7)
        self.assertEqual(verdict["pre_enable_blocker_count"], 1)
        self.assertEqual(verdict["pre_enable_status"], "blocked")
        self.assertEqual(verdict["qualified_closed_blocker_count"], 8)
        self.assertEqual(verdict["successor_pre_enable_blocker_count"], 2)
        self.assertEqual(verdict["total_pre_enable_blocker_count"], 9)
        self.assertEqual(verdict["process_count"], 3520)
        self.assertEqual(verdict["operations_scope"], VERIFY.OPERATIONS_SCOPE)
        self.assertTrue(
            VERIFY._json_exact(
                verdict["engineering_attempt_history"],
                VERIFY.ENGINEERING_ATTEMPT_HISTORY,
            )
        )
        self.assertEqual(
            verdict["verifier_read_surface"],
            {
                "legacy_v1_existing_artifact_count": 6,
                "legacy_v1_existing_artifacts_read": True,
                "new_successor_build_namespace_read": True,
                "purpose": VERIFY.VERIFIER_READ_SURFACE["purpose"],
                "successor_sealed_artifact_count": 63,
            },
        )
        for section in ("authorizations", "claims", "operations"):
            self.assertTrue(verdict[section])
            for name, value in verdict[section].items():
                self.assertIs(value, False, f"{section}.{name}")
        self.assertTrue(VERIFY._json_exact(computed, self.registry["receipts"]))
        self.assertTrue(
            VERIFY._json_exact(
                self.registry["pre_enable_blockers"], VERIFY.PRE_ENABLE_BLOCKERS
            )
        )
        self.assertTrue(
            VERIFY._json_exact(
                self.registry["pre_enable_verdict"], VERIFY.PRE_ENABLE_VERDICT
            )
        )
        self.assertTrue(
            VERIFY._json_exact(
                self.registry["engineering_boundary"],
                VERIFY.ENGINEERING_BOUNDARY,
            )
        )
        self.assertTrue(
            VERIFY._json_exact(
                self.registry["engineering_attempt_history"],
                VERIFY.ENGINEERING_ATTEMPT_HISTORY,
            )
        )
        self.assertTrue(
            VERIFY._json_exact(
                self.registry["engineering_success_receipt"],
                VERIFY.ENGINEERING_SUCCESS_RECEIPT,
            )
        )
        self.assertEqual(
            self.registry["engineering_attempt_history"]["attempt_count"], 4
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][0][
                "effects"
            ]["build_invoked"],
            True,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][1][
                "effects"
            ]["descriptor_test_product_compiled_and_linked"],
            True,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][1][
                "effects"
            ]["product_executed"],
            False,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][2][
                "repair"
            ]["rerun_after_repair"],
            False,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][2][
                "effects"
            ]["prior_lc_data_in_code_gate_passed"],
            True,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][2][
                "effects"
            ]["runtime_static_attestation_passed"],
            False,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][3][
                "effects"
            ]["all_eight_runtime_product_passes_compiled_linked_and_statically_attested"],
            True,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][3][
                "effects"
            ]["shim_pass1_static_attestation_passed"],
            False,
        )
        self.assertIs(
            self.registry["engineering_attempt_history"]["attempts"][3][
                "repair"
            ]["rerun_after_repair"],
            False,
        )
        self.assertTrue(
            VERIFY._json_exact(
                self.registry["receipts"]["scope"], VERIFY.RECEIPT_SCOPE
            )
        )
        self.assertEqual(self.registry["operations_scope"], VERIFY.OPERATIONS_SCOPE)
        self.assertTrue(
            VERIFY._json_exact(
                self.registry["verifier_read_surface"],
                VERIFY.VERIFIER_READ_SURFACE,
            )
        )
        self.assertEqual(len(self.registry["pre_enable_blockers"]), 9)
        statuses = [
            blocker["status"]
            for blocker in self.registry["pre_enable_blockers"].values()
        ]
        self.assertEqual(statuses.count("closed-blocks-enablement"), 1)
        self.assertEqual(statuses.count("qualified-closed"), 8)
        for blocker in (
            "analyzer_session_reader",
            "descendant_new_session_escape",
            "lease_output_pathname_custody",
            "post_reap_pid_pgid_reuse",
            "prelaunch_use_time_identity",
            "runtime_artifact_root",
            "spawn_to_identity_async_gap",
            "launcher_activation_receipt",
        ):
            self.assertEqual(
                self.registry["pre_enable_blockers"][blocker]["status"],
                "qualified-closed",
                blocker,
            )
        for blocker in ("freezer_plan_v2_integration",):
            self.assertEqual(
                self.registry["pre_enable_blockers"][blocker]["status"],
                "closed-blocks-enablement",
                blocker,
            )
        self.assertEqual(
            reads,
            [VERIFY.REGISTRY_PATH]
            + [VERIFY.BINDING_PATHS[name] for name in sorted(VERIFY.BINDING_PATHS)],
        )
        self.assertEqual(
            probes,
            [
                VERIFY.PLAN_ROOT,
                VERIFY.RESULT_ROOT,
                VERIFY.LEASE_PATH,
            ],
        )
        self.assertFalse(any(path.startswith("results/") for path in reads))
        # The outer reader covers the registry and exact bindings. Nested build
        # reads are performed by the authenticated static verifier and attesters.
        self.assertFalse(any(path.startswith("build/") for path in reads))

    def test_02_strict_canonical_bounded_json(self) -> None:
        invalid = {
            "duplicate": b'{"a":1,"a":2}\n',
            "nan": b'{"a":NaN}\n',
            "overflow": b'{"a":1e999}\n',
            "utf8": b'{"a":"\xff"}\n',
        }
        for label, raw in invalid.items():
            with self.subTest(label=label), self.assertRaises(
                VERIFY.ImplementationCheckpointError
            ):
                VERIFY._strict_json(raw, label)
        nested: object = None
        for _ in range(VERIFY.MAX_JSON_DEPTH + 2):
            nested = [nested]
        with self.assertRaisesRegex(
            VERIFY.ImplementationCheckpointError, "depth"
        ):
            VERIFY._check_json_tree(nested, "nested")
        pretty = json.dumps(self.registry, indent=2).encode("utf-8")
        with self.assertRaisesRegex(
            VERIFY.ImplementationCheckpointError, "canonical JSON"
        ):
            VERIFY._parse_registry_bytes(pretty)
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._parse_registry_bytes(self.registry_raw[:-1])

    def test_03_all_authority_claim_and_operation_mutations_fail(self) -> None:
        for section in ("authorizations", "claims", "operations"):
            for name in self.registry[section]:
                changed = copy.deepcopy(self.registry)
                changed[section][name] = True
                with self.subTest(section=section, name=name), self.assertRaises(
                    VERIFY.ImplementationCheckpointError
                ):
                    VERIFY._shape(changed)
                changed[section][name] = 0
                with self.assertRaises(VERIFY.ImplementationCheckpointError):
                    VERIFY._shape(changed)

    def test_04_design_namespace_basis_and_receipt_mutations_fail(self) -> None:
        mutations = [
            lambda value: value["basis"].__setitem__("base_head", "0" * 40),
            lambda value: value["basis"].__setitem__(
                "implementation_ancestor", "0" * 40
            ),
            lambda value: value["classification"].__setitem__(
                "candidate_status", "registered"
            ),
            lambda value: value["classification"].__setitem__(
                "engineering_builder_status", "build-complete"
            ),
            lambda value: value["engineering_boundary"].__setitem__(
                "build_materialized", False
            ),
            lambda value: value["engineering_boundary"]["accepted_p2s"].__setitem__(
                "attester_self_identity", "self-authenticating"
            ),
            lambda value: value["engineering_boundary"].__setitem__(
                "failed_attempt_status", "successful-build"
            ),
            lambda value: value["engineering_attempt_history"].__setitem__(
                "attempt_count", 2
            ),
            lambda value: value["engineering_attempt_history"]["attempts"][0][
                "attempt"
            ]["attempted_builder"].__setitem__("sha256", "0" * 64),
            lambda value: value["engineering_attempt_history"]["attempts"][0][
                "effects"
            ].__setitem__("compile_invoked", False),
            lambda value: value["engineering_attempt_history"]["attempts"][1][
                "attempt"
            ]["attempted_design"].__setitem__("sha256", "0" * 64),
            lambda value: value["engineering_attempt_history"]["attempts"][1][
                "effects"
            ].__setitem__("descriptor_test_product_executed", True),
            lambda value: value["engineering_attempt_history"]["attempts"][1][
                "effects"
            ].__setitem__("static_attestation_passed", True),
            lambda value: value["engineering_attempt_history"]["attempts"][1][
                "repair"
            ].__setitem__("rerun_after_repair", True),
            lambda value: value["engineering_attempt_history"]["attempts"][1][
                "rollback"
            ].__setitem__("build_root_absent", False),
            lambda value: value["engineering_attempt_history"]["attempts"][2][
                "attempt"
            ]["attempted_builder"].__setitem__("sha256", "0" * 64),
            lambda value: value["engineering_attempt_history"]["attempts"][2][
                "effects"
            ].__setitem__("runtime_product_executed", True),
            lambda value: value["engineering_attempt_history"]["attempts"][2][
                "effects"
            ].__setitem__("runtime_static_attestation_passed", True),
            lambda value: value["engineering_attempt_history"]["attempts"][2][
                "repair"
            ].__setitem__("rerun_after_repair", True),
            lambda value: value["engineering_attempt_history"]["attempts"][2][
                "rollback"
            ].__setitem__("staging_fully_removed", False),
            lambda value: value["engineering_attempt_history"]["attempts"][3][
                "attempt"
            ]["attempted_builder"].__setitem__("sha256", "0" * 64),
            lambda value: value["engineering_attempt_history"]["attempts"][3][
                "effects"
            ].__setitem__("product_executed", True),
            lambda value: value["engineering_attempt_history"]["attempts"][3][
                "effects"
            ].__setitem__("shim_pass1_static_attestation_passed", True),
            lambda value: value["engineering_attempt_history"]["attempts"][3][
                "repair"
            ].__setitem__("rerun_after_repair", True),
            lambda value: value["engineering_attempt_history"]["attempts"][3][
                "rollback"
            ].__setitem__("staging_fully_removed", False),
            lambda value: value["engineering_success_receipt"].__setitem__(
                "status", "scientific-build"
            ),
            lambda value: value["engineering_success_receipt"]["manifest"].__setitem__(
                "sha256", "0" * 64
            ),
            lambda value: value["engineering_success_receipt"]["effects"].__setitem__(
                "product_executed_by_builder", True
            ),
            lambda value: value.__setitem__("operations_scope", "all-reads"),
            lambda value: value["verifier_read_surface"].__setitem__(
                "legacy_v1_existing_artifacts_read", False
            ),
            lambda value: value["verifier_read_surface"].__setitem__(
                "new_successor_build_namespace_read", False
            ),
            lambda value: value["verifier_read_surface"]["artifacts"][0].__setitem__(
                "sha256", "0" * 64
            ),
            lambda value: value["namespace_lock"].__setitem__(
                "plan_expected_state", "present"
            ),
            lambda value: value["namespace_lock"].__setitem__(
                "build_expected_state", "present"
            ),
            lambda value: value["namespace_lock"].__setitem__(
                "lease_path", "results/other/.epoch-lease"
            ),
            lambda value: value["pre_enable_blockers"][
                "runtime_artifact_root"
            ].__setitem__("current_proof", "pathname-only"),
            lambda value: value["pre_enable_blockers"][
                "runtime_artifact_root"
            ].__setitem__(
                "qualification_scope",
                "method-specific-PROJECT_ROOT",
            ),
            lambda value: value["pre_enable_blockers"][
                "analyzer_session_reader"
            ].__setitem__("status", "closed-blocks-enablement"),
            lambda value: value["pre_enable_blockers"][
                "analyzer_session_reader"
            ].__setitem__("current_proof", "pathname-read"),
            lambda value: value["pre_enable_verdict"].__setitem__(
                "active_blocker_count", 4
            ),
            lambda value: value["pre_enable_verdict"].__setitem__(
                "historical_blocker_count", 8
            ),
            lambda value: value["pre_enable_verdict"].__setitem__(
                "qualified_closed_count", 5
            ),
            lambda value: value["pre_enable_verdict"].__setitem__(
                "successor_blocker_count", 3
            ),
            lambda value: value["pre_enable_verdict"].__setitem__(
                "total_blocker_count", 8
            ),
            lambda value: value["pre_enable_verdict"].__setitem__(
                "status", "ready"
            ),
            lambda value: value["design"]["robot_counts"].reverse(),
            lambda value: value["design"]["mission_seeds"].append(11),
            lambda value: value["design"].__setitem__("scene_count", 249),
            lambda value: value["design"].__setitem__("process_count", 3519),
            lambda value: value["receipts"]["mission_compilation"].__setitem__(
                "compiled_file_count", 301
            ),
            lambda value: value["receipts"]["binary_attestation"].__setitem__(
                "binary_count", 3
            ),
            lambda value: value["receipts"]["binary_attestation_v2"].__setitem__(
                "product_count", 3
            ),
            lambda value: value["receipts"]["host_activation"].__setitem__(
                "status", "failed"
            ),
            lambda value: value["receipts"]["runtime_build_verification"].__setitem__(
                "static_read_only_verification", False
            ),
            lambda value: value["receipts"]["epoch_candidate"].__setitem__(
                "process_count", 3519
            ),
            lambda value: value["receipts"]["scope"].__setitem__(
                "runtime_build_v2_attestation", "present"
            ),
            lambda value: value["receipts"]["scope"].__setitem__(
                "legacy_v1_read_artifact_count", 5
            ),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.registry)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(
                VERIFY.ImplementationCheckpointError
            ):
                VERIFY._shape(changed)
        for blocker, expected in VERIFY.PRE_ENABLE_BLOCKERS.items():
            for field, expected_value in expected.items():
                changed = copy.deepcopy(self.registry)
                changed["pre_enable_blockers"][blocker][field] = (
                    "mutated-" + expected_value
                )
                with self.subTest(blocker=blocker, field=field), self.assertRaises(
                    VERIFY.ImplementationCheckpointError
                ):
                    VERIFY._shape(changed)

    def test_05_any_registry_byte_mutation_requires_refreeze(self) -> None:
        mutations = [
            lambda value: value["bindings"]["runner_source"].__setitem__(
                "sha256", "0" * 64
            ),
            lambda value: value["receipts"]["epoch_candidate"].__setitem__(
                "sha256", "f" * 64
            ),
            lambda value: value["receipts"]["mission_compilation"].__setitem__(
                "total_compiled_bytes",
                value["receipts"]["mission_compilation"]["total_compiled_bytes"] + 1,
            ),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.registry)
            mutation(changed)
            raw = VERIFY.canonical_json_bytes(changed)
            with self.subTest(index=index), self.assertRaisesRegex(
                VERIFY.ImplementationCheckpointError, "frozen byte identity"
            ):
                VERIFY._parse_registry_bytes(raw)

    def test_06_science_guards_legacy_disables_and_builder_gate_are_closed(self) -> None:
        authenticated = {
            name: (ROOT / path).read_bytes()
            for name, path in VERIFY.BINDING_PATHS.items()
        }
        VERIFY._verify_runtime_build_design(authenticated["runtime_build_design"])
        VERIFY._verify_component_guards(authenticated)

        runner = authenticated["runner_source"]
        changed_return = runner.replace(
            b'    return 1\n\n    """INERT PRE-ACTIVATION REFERENCE',
            b'    return 0\n\n    """INERT PRE-ACTIVATION REFERENCE',
            1,
        )
        injected_call = runner.replace(
            b"    _ = args\n",
            b"    _ = args\n    execute_schedule()\n",
            1,
        )
        conditional = runner.replace(
            b'    return 1\n\n    """INERT PRE-ACTIVATION REFERENCE',
            b'    if args.preflight_only:\n        return 1\n\n    """INERT PRE-ACTIVATION REFERENCE',
            1,
        )
        for label, changed in {
            "return": changed_return,
            "capability": injected_call,
            "conditional": conditional,
        }.items():
            self.assertNotEqual(changed, runner)
            with self.subTest(label=label), self.assertRaises(
                VERIFY.ImplementationCheckpointError
            ):
                VERIFY._verify_runner_guard(changed)

        for label, before, after in (
            (
                "legacy-environment",
                b"pathname attempt environments are disabled",
                b"pathname attempt environments are enabled",
            ),
            (
                "legacy-process",
                b"legacy measured-process execution is disabled",
                b"legacy measured-process execution is enabled",
            ),
            (
                "legacy-schedule",
                b"legacy pathname schedule execution is disabled",
                b"legacy pathname schedule execution is enabled",
            ),
            (
                "legacy-inert-tail",
                b"INERT LEGACY REFERENCE",
                b"ACTIVE LEGACY REFERENCE",
            ),
        ):
            changed = runner.replace(before, after, 1)
            self.assertNotEqual(changed, runner, label)
            with self.subTest(label=label), self.assertRaises(
                VERIFY.ImplementationCheckpointError
            ):
                VERIFY._verify_runner_guard(changed)

        builder = authenticated["runtime_builder_source"]
        ungated_builder = builder.replace(
            b"    if not args.execute_engineering_build:\n",
            b"    if False:\n",
            1,
        )
        self.assertNotEqual(ungated_builder, builder)
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_engineering_builder_gate(ungated_builder)

        build_verifier = authenticated["runtime_build_verifier_source"]
        verifier_without_static_call = build_verifier.replace(
            b"        verdict = verify_build(",
            b"        verdict = object(",
            1,
        )
        self.assertNotEqual(verifier_without_static_call, build_verifier)
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_static_build_surfaces(
                verifier_without_static_call,
                authenticated["binary_attester_v2_source"],
            )

        design = json.loads(authenticated["runtime_build_design"])
        design["classification"]["build_executed"] = True
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_runtime_build_design(
                json.dumps(design).encode("utf-8")
            )
        diagnostic_mutation = json.loads(authenticated["runtime_build_design"])
        diagnostic_mutation["build"]["compiler_driver_diagnostic_policy"] = (
            "ignore-all-driver-diagnostics"
        )
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_runtime_build_design(
                json.dumps(diagnostic_mutation).encode("utf-8")
            )
        flag_mutation = json.loads(authenticated["runtime_build_design"])
        flag_mutation["build"]["cmake_arguments"][-1] = flag_mutation["build"][
            "cmake_arguments"
        ][-1].replace(" -Qunused-arguments", "")
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_runtime_build_design(
                json.dumps(flag_mutation).encode("utf-8")
            )
        data_in_code_mutation = json.loads(authenticated["runtime_build_design"])
        data_in_code_mutation["build"]["data_in_code_policy"] = (
            "reject every empty LC_DATA_IN_CODE encoding"
        )
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_runtime_build_design(
                json.dumps(data_in_code_mutation).encode("utf-8")
            )
        chained_fixups_mutation = json.loads(authenticated["runtime_build_design"])
        chained_fixups_mutation["build"]["chained_fixups_policy"] = (
            "treat the leading sentinel as a hidden gap"
        )
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_runtime_build_design(
                json.dumps(chained_fixups_mutation).encode("utf-8")
            )

        categorical_mutations = {
            "mission": (
                VERIFY._verify_mission_guards,
                authenticated["mission_compiler_source"],
                b'    """Reject every mission-bank publication at this checkpoint."""\n\n',
            ),
            "attester": (
                VERIFY._verify_attester_guards,
                authenticated["binary_attester_source"],
                b'    """Reject every binary-attestation publication at this checkpoint."""\n\n',
            ),
            "freezer": (
                VERIFY._verify_freezer_guards,
                authenticated["freezer_source"],
                b'    """Reject every candidate publication at the code-only checkpoint."""\n\n',
            ),
            "analyzer": (
                VERIFY._verify_analyzer_guards,
                authenticated["analyzer_source"],
                b'    """Remain categorically nonmutating until a publisher is qualified."""\n\n',
            ),
        }
        for label, (verify_guard, source, marker) in categorical_mutations.items():
            changed = source.replace(marker, marker + b"    return None\n", 1)
            self.assertNotEqual(changed, source, label)
            with self.subTest(label=label), self.assertRaises(
                VERIFY.ImplementationCheckpointError
            ):
                verify_guard(changed)

        mission_alias = authenticated["mission_compiler_source"].replace(
            b"publish_mission_banks = publish_compilation_create_exclusive",
            b"publish_mission_banks = compile_mission_banks",
            1,
        )
        freezer_alias = authenticated["freezer_source"].replace(
            b"publish_candidate = publish_candidate_create_exclusive",
            b"publish_candidate = compile_epoch",
            1,
        )
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_mission_guards(mission_alias)
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._verify_freezer_guards(freezer_alias)

        main_guard_mutations = {
            "mission-main": (
                VERIFY._verify_mission_guards,
                authenticated["mission_compiler_source"].replace(
                    b"    return 1\n\n    try:\n",
                    b"    return 0\n\n    try:\n",
                    1,
                ),
                authenticated["mission_compiler_source"],
            ),
            "attester-main": (
                VERIFY._verify_attester_guards,
                authenticated["binary_attester_source"].replace(
                    b"    return 2\n\n    try:\n",
                    b"    return 0\n\n    try:\n",
                    1,
                ),
                authenticated["binary_attester_source"],
            ),
            "analyzer-main": (
                VERIFY._verify_analyzer_guards,
                authenticated["analyzer_source"].replace(
                    b"    return 1\n\n\nif __name__",
                    b"    return 0\n\n\nif __name__",
                    1,
                ),
                authenticated["analyzer_source"],
            ),
        }
        for label, (verify_guard, changed, original) in main_guard_mutations.items():
            self.assertNotEqual(changed, original, label)
            with self.subTest(label=label), self.assertRaises(
                VERIFY.ImplementationCheckpointError
            ):
                verify_guard(changed)

        modules = {
            label: VERIFY._exec_authenticated(
                label,
                VERIFY.BINDING_PATHS[binding],
                authenticated[binding],
            )
            for label, binding in {
                "mission": "mission_compiler_source",
                "attester": "binary_attester_source",
                "freezer": "freezer_source",
                "analyzer": "analyzer_source",
            }.items()
        }
        nested_reads: list[str] = []
        real_nested_reader = modules["attester"]._read_stable_regular

        def tracked_nested_reader(
            path: Path,
            *,
            maximum_bytes: int,
            label: str,
            executable: bool,
        ) -> object:
            nested_reads.append(path.relative_to(ROOT).as_posix())
            return real_nested_reader(
                path,
                maximum_bytes=maximum_bytes,
                label=label,
                executable=executable,
            )

        with mock.patch.object(
            modules["attester"],
            "_read_stable_regular",
            side_effect=tracked_nested_reader,
        ):
            legacy_attestation = VERIFY._build_disclosed_legacy_v1_attestation(
                modules["attester"]
            )
        self.assertEqual(
            nested_reads,
            [item["path"] for item in VERIFY.VERIFIER_READ_SURFACE["artifacts"]],
        )
        self.assertEqual(len(legacy_attestation["binaries"]), 4)
        real_build_attestation = modules["attester"].build_attestation

        def build_with_out_of_order_nested_read(**kwargs: object) -> object:
            modules["attester"]._read_stable_regular(
                ROOT / "pixi.lock",
                maximum_bytes=modules["attester"].MAX_LOCK_BYTES,
                label="undisclosed out-of-order nested read",
                executable=False,
            )
            return real_build_attestation(**kwargs)

        with mock.patch.object(
            modules["attester"],
            "build_attestation",
            side_effect=build_with_out_of_order_nested_read,
        ), self.assertRaisesRegex(
            VERIFY.ImplementationCheckpointError, "nested read surface"
        ):
            VERIFY._build_disclosed_legacy_v1_attestation(modules["attester"])
        bombs = [
            mock.patch.object(
                modules["mission"],
                "validate_compilation",
                side_effect=AssertionError("mission validation reached"),
            ),
            mock.patch.object(
                modules["attester"],
                "_absolute_root",
                side_effect=AssertionError("attester repository read reached"),
            ),
            mock.patch.object(
                modules["attester"],
                "build_attestation",
                side_effect=AssertionError("attester read reached"),
            ),
            mock.patch.object(
                modules["freezer"],
                "validate_compilation",
                side_effect=AssertionError("freezer validation reached"),
            ),
            mock.patch.object(
                modules["analyzer"],
                "validate_analysis_report",
                side_effect=AssertionError("analyzer validation reached"),
            ),
        ]
        with bombs[0], bombs[1], bombs[2], bombs[3], bombs[4]:
            VERIFY._exercise_publication_guards(**modules)

    def test_07_registry_excludes_self_cycle_and_binds_expanded_components(self) -> None:
        self.assertEqual(set(self.registry["bindings"]), set(VERIFY.BINDING_PATHS))
        self.assertNotIn(
            VERIFY_PATH.relative_to(ROOT).as_posix(),
            {
                identity["path"]
                for identity in self.registry["bindings"].values()
            },
        )
        for stem in (
            "analyzer",
            "binary_attester",
            "binary_attester_v2",
            "freezer",
            "geometry",
            "mission_compiler",
            "runner",
            "runtime_build_verifier",
            "runtime_builder",
        ):
            self.assertIn(stem + "_source", self.registry["bindings"])
            self.assertIn(stem + "_test", self.registry["bindings"])
        for name in (
            "descriptor_io_patch",
            "runtime_build_design",
            "runtime_build_fixture",
            "spawn_shim_c_source",
            "spawn_shim_header",
            "spawn_shim_native_target",
            "spawn_shim_test",
        ):
            self.assertIn(name, self.registry["bindings"])
        for name, path in VERIFY.BINDING_PATHS.items():
            identity = self.registry["bindings"][name]
            raw = (ROOT / path).read_bytes()
            self.assertEqual(identity["bytes"], len(raw), name)
            self.assertEqual(
                identity["sha256"], hashlib.sha256(raw).hexdigest(), name
            )

    def test_08_descriptor_reader_and_absence_probe_reject_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "safe").mkdir()
            data = b"authenticated\n"
            (root / "safe/data.bin").write_bytes(data)
            self.assertEqual(
                VERIFY._read_regular_relative(
                    root, "safe/data.bin", "data", len(data)
                ),
                data,
            )
            VERIFY._assert_absent(root, "safe/missing/leaf", "missing")
            (root / "safe/present").write_bytes(b"x")
            with self.assertRaisesRegex(
                VERIFY.ImplementationCheckpointError, "must remain absent"
            ):
                VERIFY._assert_absent(root, "safe/present", "present")
            os.symlink("safe", root / "alias")
            with self.assertRaises(VERIFY.ImplementationCheckpointError):
                VERIFY._read_regular_relative(
                    root, "alias/data.bin", "alias", len(data)
                )
            os.link(root / "safe/data.bin", root / "safe/hardlink.bin")
            with self.assertRaises(VERIFY.ImplementationCheckpointError):
                VERIFY._read_regular_relative(
                    root, "safe/data.bin", "hardlinked", len(data)
                )
        for path in ("../outside", "/absolute", "results\\alias"):
            with self.subTest(path=path), self.assertRaises(
                VERIFY.ImplementationCheckpointError
            ):
                VERIFY._assert_absent(ROOT, path, "invalid")

    def test_09_verifier_has_no_process_network_or_write_surface(self) -> None:
        source = VERIFY_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imported
            & {
                "asyncio",
                "http",
                "importlib",
                "requests",
                "shutil",
                "socket",
                "subprocess",
                "urllib",
            }
        )
        forbidden_attributes = {
            "chmod",
            "chown",
            "copy",
            "copy2",
            "glob",
            "iterdir",
            "listdir",
            "makedirs",
            "mkdir",
            "popen",
            "remove",
            "rename",
            "replace",
            "rglob",
            "rmdir",
            "scandir",
            "symlink_to",
            "system",
            "touch",
            "unlink",
            "walk",
            "write",
            "write_bytes",
            "write_text",
        }
        forbidden_names = {"eval", "open", "__import__"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "ast"
                ):
                    continue
                self.assertNotIn(node.func.attr, forbidden_attributes)
            elif isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, forbidden_names)
        self.assertNotIn("O_WRONLY", source)
        self.assertNotIn("O_RDWR", source)
        self.assertNotIn("O_CREAT", source)
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("compile", calls)
        self.assertIn("exec", calls)

    def test_10_cli_and_read_bounds_are_closed(self) -> None:
        with self.assertRaisesRegex(
            VERIFY.ImplementationCheckpointError, "accepts no arguments"
        ):
            VERIFY.main(["--plan", "anything"])
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._read_regular_relative(
                ROOT, VERIFY.REGISTRY_PATH, "registry", 0
            )
        with self.assertRaises(VERIFY.ImplementationCheckpointError):
            VERIFY._read_regular_relative(
                ROOT,
                VERIFY.REGISTRY_PATH,
                "registry",
                VERIFY.MAX_BOUND_BYTES + 1,
            )


if __name__ == "__main__":
    unittest.main()
