from __future__ import annotations

import ast
import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from scripts.comparison import analyze_results_v4 as analysis
from scripts.comparison.tests import test_analyze_results_v3 as inherited_v3


class AnalysisRemediationContractTests(unittest.TestCase):
    def test_registered_contract_is_analysis_only_and_distinct(self) -> None:
        self.assertEqual(
            analysis.ANALYSIS_SCHEMA,
            "dancers-comparison-analysis-v4",
        )
        self.assertEqual(
            analysis.SOURCE_ANALYSIS_SCHEMA,
            "dancers-comparison-analysis-v3",
        )
        self.assertEqual(
            analysis.ANALYSIS_ID,
            (
                "fh-lower-count-fidelity-2026-07-28-rerun-1-"
                "analysis-remediation-1"
            ),
        )
        self.assertEqual(
            analysis.ANALYSIS_ROLE,
            "retrospective-analysis-only",
        )
        self.assertTrue(analysis.OUTCOME_KNOWN_AT_FREEZE)
        self.assertEqual(
            analysis.APPLIED_REPAIRS,
            (
                "exact-empty-prior-ledger-allowlist",
                "canonical-private-evidence-root",
            ),
        )
        self.assertFalse(analysis.RECEIPT_EMISSION_PERMITTED)
        self.assertFalse(analysis.TIMING_UNLOCK_PERMITTED)
        self.assertEqual(
            analysis.registered_output_path(),
            (
                analysis.registered_session_path().parent
                / (
                    "fractal-high-lower-count-fidelity-rerun-1-"
                    "analysis-remediation-1.json"
                )
            ),
        )
        self.assertFalse(hasattr(analysis, "fidelity_gate_receipt"))
        self.assertEqual(
            tuple(path.name for path in analysis.prohibited_output_paths()),
            (
                (
                    "fractal-high-lower-count-fidelity-rerun-1-"
                    "analysis.json"
                ),
                (
                    "fractal-high-lower-count-fidelity-rerun-1-"
                    "gate-receipt.json"
                ),
            ),
        )
        for path in analysis.prohibited_output_paths():
            self.assertFalse(path.exists())
            self.assertFalse(path.is_symlink())

    def test_command_line_cannot_accept_a_receipt_output(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            analysis.parse_args(
                [
                    "--session",
                    str(analysis.registered_session_path()),
                    "--output",
                    str(analysis.registered_output_path()),
                    "--fidelity-gate-receipt-output",
                    "/tmp/forbidden.json",
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_analyzer_bindings_separate_v3_execution_from_v4_analysis(
        self,
    ) -> None:
        bindings = analysis.verified_analyzer_bindings()
        execution = bindings["execution_analyzer"]
        revision = bindings["analysis_revision"]
        self.assertEqual(
            execution["sha256"],
            (
                "61a6bf537c6376ab4780df6fdb15f01b9385417abdffa85d"
                "190dee6f4ae19e97"
            ),
        )
        self.assertEqual(
            execution["path"],
            "scripts/comparison/analyze_results_v3.py",
        )
        self.assertEqual(
            revision["path"],
            "scripts/comparison/analyze_results_v4.py",
        )
        self.assertEqual(
            revision["sha256"],
            analysis.sha256_file(
                analysis._repository_root() / revision["path"]
            ),
        )
        self.assertNotEqual(execution["path"], revision["path"])

    def test_changed_execution_analyzer_binding_is_rejected(self) -> None:
        with (
            mock.patch.object(
                analysis,
                "EXECUTION_ANALYZER_SHA256",
                "0" * 64,
            ),
            self.assertRaises(analysis.AnalysisError),
        ):
            analysis.verified_analyzer_bindings()

    def test_frozen_protocol_and_abort_closure_is_authenticated(self) -> None:
        closure = analysis.verified_protocol_closure()
        self.assertEqual(
            closure["protocol_checkpoint_commit"],
            "67890bbb7e6295e74eded311432514254ccc559c",
        )
        self.assertEqual(
            closure["analysis_abort_checkpoint_commit"],
            "94258e5f112dd23ab7b4c3c269b42cafa5ed50af",
        )
        self.assertEqual(
            closure["source_evidence_commit"],
            "a1a83693a16b74449aad6e5d2fc9ec7708fff461",
        )
        self.assertEqual(len(closure["files"]), 7)
        self.assertEqual(
            closure["files"]["analysis-abort record"]["sha256"],
            analysis.ANALYSIS_ABORT_SHA256,
        )
        self.assertEqual(
            closure["files"]["analysis-remediation addendum"]["sha256"],
            analysis.PROTOCOL_ADDENDUM_SHA256,
        )

    def test_changed_protocol_binding_is_rejected(self) -> None:
        with (
            mock.patch.object(
                analysis,
                "PROTOCOL_ADDENDUM_SHA256",
                "0" * 64,
            ),
            self.assertRaises(analysis.AnalysisError),
        ):
            analysis.verified_protocol_closure()

    def test_result_rejects_mutated_analyzer_paths_or_hashes(self) -> None:
        combined = {
            "execution_epoch_id": "epoch",
            "cohort_id": "cohort",
            "design_role": "role",
            "prior_failed_cohort": {},
            "input_sessions": [],
            "fidelity_gate": {},
            "timing_screen": None,
        }
        bindings = analysis.verified_analyzer_bindings()
        mutations = (
            ("execution_analyzer", "path", "/tmp/analyze_results_v3.py"),
            ("execution_analyzer", "sha256", "0" * 64),
            ("analysis_revision", "path", "/tmp/analyze_results_v4.py"),
            ("analysis_revision", "sha256", "0" * 64),
        )
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                hostile = copy.deepcopy(bindings)
                hostile[section][field] = value
                with self.assertRaises(analysis.AnalysisError):
                    analysis.build_analysis_result(combined, hostile)

    def test_analyzer_is_a_standalone_v3_source_derivative(self) -> None:
        self.assertFalse(hasattr(analysis, "_v3"))
        self.assertFalse(hasattr(analysis, "_derived_v3_namespace"))
        self.assertEqual(
            analysis._validate_attested_artifact.__module__,
            analysis.__name__,
        )
        self.assertEqual(
            analysis._EvidenceGuard.__module__,
            analysis.__name__,
        )
        source = Path(analysis.__file__).read_text()
        self.assertNotIn("FunctionType", source)
        self.assertNotIn("import analyze_results_v3", source)

    def test_shared_v3_functions_are_ast_identical_outside_scope(self) -> None:
        repository = analysis._repository_root()
        v3_tree = ast.parse(
            (
                repository / "scripts/comparison/analyze_results_v3.py"
            ).read_text()
        )
        v4_tree = ast.parse(Path(analysis.__file__).read_text())

        def functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
            return {
                node.name: node
                for node in tree.body
                if isinstance(node, ast.FunctionDef)
            }

        v3_functions = functions(v3_tree)
        v4_functions = functions(v4_tree)
        differing = {
            name
            for name in v3_functions.keys() & v4_functions.keys()
            if ast.dump(v3_functions[name], include_attributes=False)
            != ast.dump(v4_functions[name], include_attributes=False)
        }
        self.assertEqual(
            differing,
            {
                "_validate_attested_artifact",
                "validate_session",
                "parse_args",
                "main",
            },
        )
        self.assertEqual(
            set(v4_functions) - set(v3_functions),
            {
                "_git_output",
                "_require_prohibited_outputs_absent",
                "_validated_output_path",
                "_verified_real_file",
                "_verify_committed_file",
                "build_analysis_result",
                "prohibited_output_paths",
                "registered_output_path",
                "registered_session_path",
                "verified_analyzer_bindings",
                "verified_protocol_closure",
            },
        )
        self.assertEqual(
            set(v3_functions) - set(v4_functions),
            {"fidelity_gate_receipt"},
        )
        v3_classes = {
            node.name: node
            for node in v3_tree.body
            if isinstance(node, ast.ClassDef)
        }
        v4_classes = {
            node.name: node
            for node in v4_tree.body
            if isinstance(node, ast.ClassDef)
        }
        self.assertEqual(set(v3_classes), set(v4_classes))
        self.assertEqual(
            {
                name
                for name in v3_classes
                if ast.dump(v3_classes[name], include_attributes=False)
                != ast.dump(v4_classes[name], include_attributes=False)
            },
            {"_EvidenceGuard"},
        )
        v3_guard_methods = {
            node.name: node
            for node in v3_classes["_EvidenceGuard"].body
            if isinstance(node, ast.FunctionDef)
        }
        v4_guard_methods = {
            node.name: node
            for node in v4_classes["_EvidenceGuard"].body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(set(v3_guard_methods), set(v4_guard_methods))
        self.assertEqual(
            {
                name
                for name in v3_guard_methods
                if ast.dump(
                    v3_guard_methods[name], include_attributes=False
                )
                != ast.dump(
                    v4_guard_methods[name], include_attributes=False
                )
            },
            {"__init__"},
        )

    def test_publication_rejects_a_prohibited_original_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prohibited = (Path(temporary) / "original.json").resolve()
            prohibited.write_bytes(b"must remain absent")
            with (
                mock.patch.object(
                    analysis,
                    "prohibited_output_paths",
                    return_value=(prohibited,),
                ),
                self.assertRaises(analysis.AnalysisError),
            ):
                analysis._validated_output_path(
                    analysis.registered_output_path()
                )

    def test_unexpected_gate_pass_stops_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = (Path(temporary) / "analysis.json").resolve()
            validated = {
                "_analyzer_bindings": {},
                "_protocol_closure": {},
            }
            combined = {"fidelity_gate": {"gate_pass": True}}
            result = {"fidelity_gate": {"gate_pass": True}}
            with (
                mock.patch.object(
                    analysis,
                    "_validated_output_path",
                    return_value=output,
                ),
                mock.patch.object(
                    analysis,
                    "validate_session",
                    return_value=validated,
                ),
                mock.patch.object(
                    analysis,
                    "analyze_validated_sessions",
                    return_value=combined,
                ),
                mock.patch.object(
                    analysis,
                    "build_analysis_result",
                    return_value=result,
                ),
                mock.patch.object(
                    analysis,
                    "verified_protocol_closure",
                    return_value={},
                ),
                mock.patch.object(
                    analysis,
                    "verified_analyzer_bindings",
                    return_value={},
                ),
                mock.patch.object(
                    analysis,
                    "_require_prohibited_outputs_absent",
                ),
            ):
                status = analysis.main(
                    [
                        "--session",
                        "/unused/session",
                        "--output",
                        "/unused/output",
                    ]
                )
            self.assertEqual(status, 2)
            self.assertFalse(output.exists())

    def test_late_prohibited_output_stops_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = (Path(temporary) / "analysis.json").resolve()
            validated = {
                "_analyzer_bindings": {},
                "_protocol_closure": {},
            }

            def validate_output(_: Path) -> Path:
                analysis._require_prohibited_outputs_absent()
                return output

            absence_checks = mock.Mock(
                side_effect=[
                    None,
                    analysis.AnalysisError("late prohibited output"),
                ]
            )
            with (
                mock.patch.object(
                    analysis,
                    "_validated_output_path",
                    side_effect=validate_output,
                ),
                mock.patch.object(
                    analysis,
                    "validate_session",
                    return_value=validated,
                ),
                mock.patch.object(
                    analysis,
                    "analyze_validated_sessions",
                    return_value={"fidelity_gate": {"gate_pass": False}},
                ),
                mock.patch.object(
                    analysis,
                    "build_analysis_result",
                    return_value={"fidelity_gate": {"gate_pass": False}},
                ),
                mock.patch.object(
                    analysis,
                    "verified_protocol_closure",
                    return_value={},
                ),
                mock.patch.object(
                    analysis,
                    "verified_analyzer_bindings",
                    return_value={},
                ),
                mock.patch.object(
                    analysis,
                    "_require_prohibited_outputs_absent",
                    absence_checks,
                ),
            ):
                status = analysis.main(
                    [
                        "--session",
                        "/unused/session",
                        "--output",
                        "/unused/output",
                    ]
                )
            self.assertEqual(status, 2)
            self.assertEqual(absence_checks.call_count, 2)
            self.assertFalse(output.exists())


class EmptyAttestationRemediationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = analysis.registered_session_path()
        cls.plan = json.loads((root / "frozen_plan.json").read_text())
        cls.preflight = json.loads((root / "preflight.json").read_text())

    def _validate_registered(self, artifact_name: str) -> None:
        planned = self.plan["artifacts"][artifact_name]
        attested = self.preflight["artifacts"][artifact_name]
        analysis._validate_attested_artifact(
            attested,
            expected_path=Path(planned["path"]),
            expected_sha256=planned["sha256"],
            label=f"preflight.json.artifacts.{artifact_name}",
        )

    def test_only_two_registered_empty_ledgers_are_accepted(self) -> None:
        self._validate_registered("prior_failed_runs")
        self._validate_registered("prior_failed_pairs")

    def test_mutated_registered_empty_attestation_is_rejected(self) -> None:
        for artifact_name, field, value in (
            ("prior_failed_runs", "bytes", 1),
            ("prior_failed_runs", "sha256", "0" * 64),
            ("prior_failed_pairs", "path", "/tmp/not-registered.jsonl"),
        ):
            with self.subTest(artifact_name=artifact_name, field=field):
                planned = self.plan["artifacts"][artifact_name]
                hostile = dict(
                    self.preflight["artifacts"][artifact_name]
                )
                hostile[field] = value
                with self.assertRaises(analysis.AnalysisError):
                    analysis._validate_attested_artifact(
                        hostile,
                        expected_path=Path(planned["path"]),
                        expected_sha256=planned["sha256"],
                        label=(
                            "preflight.json.artifacts."
                            f"{artifact_name}"
                        ),
                    )

    def test_wrong_expected_path_or_hash_is_rejected(self) -> None:
        artifact_name = "prior_failed_runs"
        planned = self.plan["artifacts"][artifact_name]
        attested = self.preflight["artifacts"][artifact_name]
        label = f"preflight.json.artifacts.{artifact_name}"
        with self.assertRaises(analysis.AnalysisError):
            analysis._validate_attested_artifact(
                attested,
                expected_path=Path("/tmp/not-registered.jsonl"),
                expected_sha256=planned["sha256"],
                label=label,
            )
        with self.assertRaises(analysis.AnalysisError):
            analysis._validate_attested_artifact(
                attested,
                expected_path=Path(planned["path"]),
                expected_sha256="0" * 64,
                label=label,
            )

    def test_every_other_zero_byte_artifact_keeps_v3_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = (Path(temporary) / "other.json").resolve()
            empty.write_bytes(b"")
            record = {
                "path": str(empty),
                "sha256": analysis.EMPTY_SHA256,
                "bytes": 0,
            }
            with self.assertRaises(analysis.AnalysisError):
                analysis._validate_attested_artifact(
                    record,
                    expected_path=empty,
                    expected_sha256=analysis.EMPTY_SHA256,
                    label="preflight.json.artifacts.environment_manifest",
                )

    def test_allowlisted_attestation_still_requires_actual_empty_bytes(
        self,
    ) -> None:
        label = "preflight.json.artifacts.prior_failed_runs"
        with tempfile.TemporaryDirectory() as temporary:
            hostile = (Path(temporary) / "runs.jsonl").resolve()
            hostile.write_bytes(b"x")
            record = {
                "path": str(hostile),
                "sha256": analysis.EMPTY_SHA256,
                "bytes": 0,
            }
            registered = {label: ("prior_failed_runs", hostile)}
            with (
                mock.patch.object(
                    analysis,
                    "_EMPTY_ATTESTATION_LABELS",
                    registered,
                ),
                self.assertRaises(analysis.AnalysisError),
            ):
                analysis._validate_attested_artifact(
                    record,
                    expected_path=hostile,
                    expected_sha256=analysis.EMPTY_SHA256,
                    label=label,
                )


class CanonicalEvidenceGuardTests(unittest.TestCase):
    def test_macos_alias_is_reproduced_and_private_paths_are_canonical(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = (Path(temporary) / "evidence.bin").resolve()
            source.write_bytes(b"authenticated evidence")
            lexical_names: list[str] = []
            temporary_directory = analysis.tempfile.TemporaryDirectory

            def capture_lexical_name(*args: object, **kwargs: object) -> object:
                created = temporary_directory(*args, **kwargs)
                lexical_names.append(created.name)
                return created

            with mock.patch.object(
                analysis.tempfile,
                "TemporaryDirectory",
                side_effect=capture_lexical_name,
            ):
                guard = analysis._EvidenceGuard()
            try:
                self.assertEqual(len(lexical_names), 1)
                lexical_root = Path(lexical_names[0])
                private_root = Path(guard._temporary.name)
                self.assertNotEqual(lexical_root, private_root)
                self.assertTrue(str(lexical_root).startswith("/var/"))
                self.assertTrue(str(private_root).startswith("/private/var/"))
                self.assertEqual(
                    private_root,
                    lexical_root.resolve(strict=True),
                )
                self.assertEqual(
                    private_root,
                    private_root.resolve(strict=True),
                )
                snapshot = guard.capture(source, "test evidence")
                self.assertEqual(
                    snapshot.private_path,
                    snapshot.private_path.resolve(strict=True),
                )
                guard.reverify()
            finally:
                guard.close()


class InheritedV3RegressionTests(unittest.TestCase):
    def test_registered_fidelity_gate_semantics_are_unchanged(self) -> None:
        attempts = inherited_v3.fidelity_attempts()
        pairs = inherited_v3.fidelity_pairs()
        gate = analysis.registered_fidelity_gate(attempts, pairs)
        self.assertTrue(gate["gate_pass"])
        self.assertEqual(gate["completed_attempt_count"], 40)
        self.assertEqual(gate["valid_pair_count"], 20)
        self.assertEqual(gate["host_valid_pair_count"], 20)
        self.assertEqual(gate["frames_compared_per_pair"], 200)

        attempts[0]["process_control_valid"] = False
        pairs[0]["frames_compared"] = 199
        failed = analysis.registered_fidelity_gate(attempts, pairs)
        self.assertFalse(failed["gate_pass"])
        self.assertIn(
            "attempt-000:process_control_valid",
            failed["attempt_failures"],
        )
        self.assertIn(
            "pair-000:frames_compared:199",
            failed["pair_failures"],
        )

    def test_all_non_envelope_v3_regressions_pass_against_v4(self) -> None:
        excluded_methods = {
            "test_any_bad_attempt_or_pair_fails_the_complete_gate",
            "test_complete_gate_and_receipt",
            "test_failed_gate_writes_analysis_but_not_receipt",
            "test_receipt_rejects_a_self_contradictory_gate",
            "test_receipt_rejects_epoch_pooling_and_prior_failure_drift",
        }

        def flatten(
            suite: unittest.TestSuite,
        ) -> list[unittest.TestCase]:
            tests: list[unittest.TestCase] = []
            for item in suite:
                if isinstance(item, unittest.TestSuite):
                    tests.extend(flatten(item))
                else:
                    assert isinstance(item, unittest.TestCase)
                    tests.append(item)
            return tests

        inherited_suite = unittest.defaultTestLoader.loadTestsFromModule(
            inherited_v3
        )
        selected = [
            test
            for test in flatten(inherited_suite)
            if test._testMethodName not in excluded_methods
        ]
        self.assertEqual(len(selected), 35)
        original_analysis = inherited_v3.analysis
        result = unittest.TestResult()
        try:
            inherited_v3.analysis = analysis
            unittest.TestSuite(selected).run(result)
        finally:
            inherited_v3.analysis = original_analysis
        if result.failures or result.errors:
            details = "\n".join(
                f"{test.id()}: {traceback}"
                for test, traceback in result.failures + result.errors
            )
            self.fail(f"inherited v3 regression failure:\n{details}")
        self.assertEqual(result.testsRun, 35)


class SealedSessionRegressionTests(unittest.TestCase):
    def test_full_real_session_validates_with_only_the_two_repairs(
        self,
    ) -> None:
        validated = analysis.validate_session(
            analysis.registered_session_path()
        )
        self.assertEqual(
            analysis.validate_session.__module__,
            analysis.__name__,
        )
        guard = validated["_evidence_guard"]
        try:
            combined = analysis.analyze_validated_sessions([validated])
            gate = combined["fidelity_gate"]
            self.assertIsNotNone(gate)
            assert gate is not None
            self.assertEqual(gate["planned_method_attempt_slots"], 40)
            self.assertEqual(gate["executed_attempt_count"], 40)
            self.assertEqual(gate["completed_attempt_count"], 40)
            self.assertEqual(gate["planned_pair_blocks"], 20)
            self.assertEqual(gate["host_valid_pair_count"], 20)
            self.assertEqual(gate["valid_pair_count"], 0)
            self.assertEqual(gate["frames_compared_per_pair"], 200)
            self.assertFalse(gate["gate_pass"])
            self.assertEqual(len(gate["attempt_failures"]), 40)
            self.assertEqual(len(gate["pair_failures"]), 40)
            self.assertEqual(
                Counter(
                    failure.rsplit(":", 1)[-1]
                    for failure in gate["attempt_failures"]
                ),
                Counter({"task_valid": 40}),
            )
            self.assertEqual(
                Counter(
                    failure.rsplit(":", 1)[-1]
                    for failure in gate["pair_failures"]
                ),
                Counter({"pair_valid": 20, "task_valid": 20}),
            )
            self.assertIsNone(combined["timing_screen"])

            result = analysis.build_analysis_result(
                combined,
                validated["_analyzer_bindings"],
            )
            self.assertEqual(
                set(result),
                {
                    "schema_version",
                    "analysis_id",
                    "analysis_role",
                    "outcome_known_at_freeze",
                    "applied_repairs",
                    "source_analysis_schema",
                    "execution_analyzer",
                    "analysis_revision",
                    "analysis_revision_sha256",
                    "receipt_emission_permitted",
                    "timing_unlock_permitted",
                    "amendment_id",
                    "execution_epoch_id",
                    "cohort_id",
                    "design_role",
                    "prior_failed_cohort",
                    "input_sessions",
                    "fidelity_gate",
                    "timing_screen",
                },
            )
            self.assertEqual(
                result["schema_version"],
                "dancers-comparison-analysis-v4",
            )
            self.assertEqual(result["analysis_id"], analysis.ANALYSIS_ID)
            self.assertEqual(
                result["analysis_role"],
                "retrospective-analysis-only",
            )
            self.assertTrue(result["outcome_known_at_freeze"])
            self.assertEqual(
                result["applied_repairs"],
                [
                    "exact-empty-prior-ledger-allowlist",
                    "canonical-private-evidence-root",
                ],
            )
            self.assertEqual(
                result["source_analysis_schema"],
                "dancers-comparison-analysis-v3",
            )
            self.assertFalse(result["receipt_emission_permitted"])
            self.assertFalse(result["timing_unlock_permitted"])
            self.assertNotIn("receipt", result)
            self.assertNotIn("fidelity_gate_receipt", result)
            self.assertEqual(
                result["analysis_revision_sha256"],
                result["analysis_revision"]["sha256"],
            )
            self.assertEqual(
                result["execution_analyzer"]["sha256"],
                analysis.EXECUTION_ANALYZER_SHA256,
            )
            self.assertEqual(
                json.loads(analysis.canonical_json_bytes(result)),
                result,
            )

            self.assertGreater(len(guard._snapshots), 0)
            for snapshot in guard._snapshots:
                self.assertEqual(
                    snapshot.private_path,
                    snapshot.private_path.resolve(strict=True),
                )
            guard.reverify()
        finally:
            guard.close()


if __name__ == "__main__":
    unittest.main()
