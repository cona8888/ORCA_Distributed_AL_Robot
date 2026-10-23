from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.replication import prepare_orca_execution_admission as admission


class OrcaExecutionAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capsule = admission.DEFAULT_CAPSULE_ROOT

    def _binding(self, root: pathlib.Path) -> dict[str, object]:
        return admission.build_binding(
            self.capsule,
            admission.CAPSULE_MANIFEST_SHA256,
            root / "dancers-orca-confirmatory-session-001",
            "confirmatory-session-001",
        )

    def test_describe_authenticates_exact_binding_without_write_or_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            output = root / "dancers-orca-confirmatory-session-001"
            stdout = io.BytesIO()
            wrapped = io.TextIOWrapper(stdout, encoding="utf-8")
            with (
                mock.patch.object(subprocess, "Popen") as popen,
                mock.patch.object(subprocess, "run") as run,
                mock.patch.object(admission.sys, "stdout", wrapped),
            ):
                self.assertEqual(
                    admission.main(
                        [
                            "describe",
                            "--output-root",
                            str(output),
                            "--session-id",
                            "confirmatory-session-001",
                        ]
                    ),
                    0,
                )
                wrapped.flush()
            popen.assert_not_called()
            run.assert_not_called()
            self.assertFalse(output.exists())
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["classification"]["execution_authorized"])
            self.assertEqual(result["binding"]["expected_processes"], 363)
            self.assertEqual(result["binding"]["output_root"], str(output))

    def test_affirmative_decisions_can_be_authenticated_but_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            binding = self._binding(root)
            authorization = admission.canonical_json_bytes(
                admission.authorization_document(binding)
            )
            authorization_sha = hashlib.sha256(authorization).hexdigest()
            authorization_path = root / "authorization.json"
            authorization_path.write_bytes(authorization)
            decision = admission.canonical_json_bytes(
                admission.admission_document(binding, authorization_sha)
            )
            decision_sha = hashlib.sha256(decision).hexdigest()
            decision_path = root / "admission.json"
            decision_path.write_bytes(decision)
            result = admission.verify_decisions(
                binding,
                authorization_path,
                authorization_sha,
                decision_path,
                decision_sha,
            )
            self.assertTrue(result["decisions_authenticated"])
            self.assertFalse(result["execution_authorized"])
            self.assertEqual(result["status"], "admitted_without_execution")
            self.assertFalse(pathlib.Path(binding["output_root"]).exists())

    def test_self_declared_or_tampered_decisions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            binding = self._binding(root)
            authorization = admission.authorization_document(binding)
            authorization["expected_processes"] = 1
            raw = admission.canonical_json_bytes(authorization)
            path = root / "authorization.json"
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            with self.assertRaisesRegex(admission.AdmissionError, "exact contract"):
                admission.verify_decisions(
                    binding,
                    path,
                    digest,
                    path,
                    digest,
                )
            with self.assertRaisesRegex(admission.AdmissionError, "trust root"):
                admission._strict_json_file(path, "0" * 64, "authorization manifest")

    def test_output_session_and_capsule_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            existing = root / "existing"
            existing.mkdir()
            cases = (
                (
                    existing,
                    "confirmatory-session-001",
                    admission.CAPSULE_MANIFEST_SHA256,
                    "absent",
                ),
                (root / "new", "BAD SESSION", admission.CAPSULE_MANIFEST_SHA256, "session id"),
                (root / "new", "confirmatory-session-001", "0" * 64, "capsule digest"),
            )
            for output, session, digest, message in cases:
                with self.subTest(message=message), self.assertRaisesRegex(
                    admission.AdmissionError, message
                ):
                    admission.build_binding(self.capsule, digest, output, session)

    def test_public_cli_has_no_execute_command_or_runner_call(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            admission.parser().parse_args(["execute"])
        source = pathlib.Path(admission.__file__).read_text(encoding="utf-8")
        self.assertNotIn("runner.execute_authorized(", source)
        self.assertNotIn("ExecutionPermit(", source)
        self.assertNotIn("subprocess.", source)


if __name__ == "__main__":
    unittest.main()
