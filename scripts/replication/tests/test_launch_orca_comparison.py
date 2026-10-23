from __future__ import annotations

import ast
import hashlib
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts.replication import launch_orca_comparison as launcher


class OrcaExecutionLauncherTests(unittest.TestCase):
    def _decision_fixture(
        self, root: pathlib.Path
    ) -> tuple[pathlib.Path, str, pathlib.Path, str, pathlib.Path]:
        output = root / "dancers-orca-confirmatory-session-001"
        binding = launcher.admission.build_binding(
            launcher.admission.DEFAULT_CAPSULE_ROOT,
            launcher.admission.CAPSULE_MANIFEST_SHA256,
            output,
            "confirmatory-session-001",
        )
        authorization_raw = launcher.admission.canonical_json_bytes(
            launcher.admission.authorization_document(binding)
        )
        authorization_sha = hashlib.sha256(authorization_raw).hexdigest()
        authorization_path = root / "authorization.json"
        authorization_path.write_bytes(authorization_raw)
        admission_raw = launcher.admission.canonical_json_bytes(
            launcher.admission.admission_document(binding, authorization_sha)
        )
        admission_sha = hashlib.sha256(admission_raw).hexdigest()
        admission_path = root / "admission.json"
        admission_path.write_bytes(admission_raw)
        return (
            authorization_path,
            authorization_sha,
            admission_path,
            admission_sha,
            output,
        )

    def _launch_arguments(self, root: pathlib.Path) -> dict[str, object]:
        authorization, authorization_sha, decision, decision_sha, output = (
            self._decision_fixture(root)
        )
        return {
            "capsule_root": launcher.admission.DEFAULT_CAPSULE_ROOT,
            "expected_capsule_sha256": launcher.admission.CAPSULE_MANIFEST_SHA256,
            "output_root": output,
            "session_id": "confirmatory-session-001",
            "authorization_manifest": authorization,
            "expected_authorization_sha256": authorization_sha,
            "admission_manifest": decision,
            "expected_admission_sha256": decision_sha,
            "confirmation": launcher.EXACT_CONFIRMATION,
        }

    def test_exact_documents_form_one_exact_permit_and_one_runner_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            arguments = self._launch_arguments(root)
            receipt = {"execution_complete": True, "expected_processes": 363}
            with mock.patch.object(
                launcher.runner, "execute_authorized", return_value=receipt
            ) as execute:
                self.assertEqual(launcher.launch_authenticated(**arguments), receipt)
            execute.assert_called_once()
            call = execute.call_args.kwargs
            permit = call["permit"]
            self.assertTrue(permit.execution_authorized)
            self.assertEqual(permit.expected_processes, 363)
            self.assertFalse(permit.selective_retry_allowed)
            self.assertEqual(permit.output_root, arguments["output_root"])
            self.assertEqual(
                permit.authorization_manifest_sha256,
                arguments["expected_authorization_sha256"],
            )
            self.assertEqual(
                permit.admission_manifest_sha256,
                arguments["expected_admission_sha256"],
            )
            self.assertFalse(pathlib.Path(arguments["output_root"]).exists())

    def test_binding_authenticates_both_translation_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            arguments = self._launch_arguments(root)
            binding = launcher.admission.build_binding(
                arguments["capsule_root"],
                arguments["expected_capsule_sha256"],
                arguments["output_root"],
                arguments["session_id"],
            )
            sources = binding["source_contract"]
            self.assertEqual(
                set(sources),
                {
                    "admission_gate",
                    "amendment_4",
                    "amendment_5",
                    "amendment_6",
                    "amendment_7",
                    "amendment_8",
                    "amendment_9",
                    "execution_launcher",
                    "schema_version",
                },
            )
            for label in (
                "admission_gate",
                "amendment_4",
                "amendment_5",
                "amendment_6",
                "amendment_7",
                "amendment_8",
                "amendment_9",
                "execution_launcher",
            ):
                identity = sources[label]
                raw = pathlib.Path(identity["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), identity["sha256"])
            self.assertEqual(
                launcher.admission.sha256_bytes(
                    launcher.admission.canonical_json_bytes(sources)
                ),
                binding["source_contract_sha256"],
            )

    def test_wrong_confirmation_refuses_before_authentication_or_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            arguments = self._launch_arguments(root)
            arguments["confirmation"] = "run"
            with (
                mock.patch.object(launcher, "_authenticated_context") as context,
                mock.patch.object(launcher.runner, "execute_authorized") as execute,
            ):
                with self.assertRaisesRegex(launcher.LauncherError, "confirmation"):
                    launcher.launch_authenticated(**arguments)
            context.assert_not_called()
            execute.assert_not_called()
            self.assertFalse(pathlib.Path(arguments["output_root"]).exists())

    def test_bad_external_decision_digest_never_reaches_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            arguments = self._launch_arguments(root)
            arguments["expected_authorization_sha256"] = "0" * 64
            with mock.patch.object(launcher.runner, "execute_authorized") as execute:
                with self.assertRaisesRegex(
                    launcher.admission.AdmissionError, "external trust root"
                ):
                    launcher.launch_authenticated(**arguments)
            execute.assert_not_called()
            self.assertFalse(pathlib.Path(arguments["output_root"]).exists())

    def test_binding_drift_between_checks_never_reaches_runner(self) -> None:
        first = ({"binding": 1}, {}, pathlib.Path("/input"), mock.Mock())
        second = ({"binding": 2}, {}, pathlib.Path("/input"), mock.Mock())
        with (
            mock.patch.object(
                launcher, "_authenticated_context", side_effect=(first, second)
            ) as context,
            mock.patch.object(launcher.runner, "execute_authorized") as execute,
        ):
            with self.assertRaisesRegex(launcher.LauncherError, "changed"):
                launcher.launch_authenticated(
                    capsule_root=pathlib.Path("/capsule"),
                    expected_capsule_sha256="0" * 64,
                    output_root=pathlib.Path("/output"),
                    session_id="confirmatory-session-001",
                    authorization_manifest=pathlib.Path("/authorization"),
                    expected_authorization_sha256="1" * 64,
                    admission_manifest=pathlib.Path("/admission"),
                    expected_admission_sha256="2" * 64,
                    confirmation=launcher.EXACT_CONFIRMATION,
                )
        self.assertEqual(context.call_count, 2)
        execute.assert_not_called()

    def test_cli_exposes_only_complete_launch_without_retry_or_selection(self) -> None:
        parser = launcher.parser()
        launch = next(
            action for action in parser._actions if action.dest == "command"
        ).choices["launch"]
        destinations = {action.dest for action in launch._actions}
        self.assertTrue(
            {
                "authorization_manifest",
                "admission_manifest",
                "confirmation",
                "output_root",
                "session_id",
            }.issubset(destinations)
        )
        self.assertTrue(
            {"sequence", "retry", "resume", "overwrite"}.isdisjoint(destinations)
        )
        tree = ast.parse(pathlib.Path(launcher.__file__).read_text(encoding="utf-8"))
        runner_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "runner"
            and node.func.attr == "execute_authorized"
        ]
        self.assertEqual(len(runner_calls), 1)


if __name__ == "__main__":
    unittest.main()
