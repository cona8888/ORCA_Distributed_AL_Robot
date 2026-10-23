from __future__ import annotations

import contextlib
import io
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.replication import prepare_orca_execution_decision_packet as packet


class OrcaExecutionDecisionPacketTests(unittest.TestCase):
    def test_create_and_external_check_are_canonical_and_nonexecuting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            root = parent / "packet"
            with (
                mock.patch.object(packet, "RESULT_ROOT", parent / "result"),
                mock.patch.object(subprocess, "Popen") as popen,
                mock.patch.object(subprocess, "run") as run,
            ):
                payloads, manifest = packet.build_packet()
                digest = packet.publish_packet(root, payloads, manifest)
                checked = packet.check_packet(root, digest)
            popen.assert_not_called()
            run.assert_not_called()
            self.assertEqual(checked, manifest)
            self.assertEqual(
                checked["classification"],
                {
                    "admission_granted": True,
                    "execution_authorized": True,
                    "launcher_invoked": False,
                    "publication_processes_executed": 0,
                    "publication_simulation_steps": 0,
                    "result_namespace": "absent",
                },
            )
            self.assertFalse((parent / "result").exists())

    def test_wrong_confirmation_refuses_before_build_or_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve() / "packet"
            with (
                mock.patch.object(packet, "build_packet") as build,
                mock.patch.object(packet, "publish_packet") as publish,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = packet.main(
                    ["create", "--packet-root", str(root), "--confirmation", "NO"]
                )
            self.assertEqual(status, 2)
            build.assert_not_called()
            publish.assert_not_called()
            self.assertFalse(root.exists())

    def test_tamper_extra_file_and_wrong_external_digest_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            with mock.patch.object(packet, "RESULT_ROOT", parent / "result"):
                payloads, manifest = packet.build_packet()
                original = parent / "original"
                digest = packet.publish_packet(original, payloads, manifest)
                with self.assertRaisesRegex(packet.DecisionPacketError, "trust root"):
                    packet.check_packet(original, "0" * 64)

                tampered = parent / "tampered"
                shutil.copytree(original, tampered)
                (tampered / "authorization.json").write_bytes(b"{}\n")
                with self.assertRaises(packet.DecisionPacketError):
                    packet.check_packet(tampered, digest)

                extra = parent / "extra"
                shutil.copytree(original, extra)
                (extra / "extra.txt").touch()
                with self.assertRaisesRegex(packet.DecisionPacketError, "inventory"):
                    packet.check_packet(extra, digest)

    def test_public_source_has_no_launcher_runner_or_subprocess_surface(self) -> None:
        source = pathlib.Path(packet.__file__).read_text(encoding="utf-8")
        self.assertNotIn("launch_orca_comparison", source)
        self.assertNotIn("run_orca_comparison", source)
        self.assertNotIn("execute_authorized(", source)
        self.assertNotIn("subprocess.", source)


if __name__ == "__main__":
    unittest.main()
