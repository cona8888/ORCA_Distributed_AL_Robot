#!/usr/bin/env python3
"""Regressions for the nonauthorizing runtime activation qualifier."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/qualify_headless_runtime_activation_v1.py"
RECEIPT = ROOT / "comparison/headless-runtime-rebuild-v1/activation_receipt_v1.json"
SPEC = importlib.util.spec_from_file_location("activation_v1", SOURCE)
assert SPEC is not None and SPEC.loader is not None
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)


class RuntimeActivationV1Tests(unittest.TestCase):
    def test_01_bound_sources_and_frozen_receipt(self) -> None:
        runner_path, runner_raw = Q._read_exact_source(
            ROOT, Q.RUNNER_PATH, Q.RUNNER_BYTES, Q.RUNNER_SHA256
        )
        attester_path, attester_raw = Q._read_exact_source(
            ROOT, Q.ATTESTER_PATH, Q.ATTESTER_BYTES, Q.ATTESTER_SHA256
        )
        self.assertEqual(runner_path, ROOT / Q.RUNNER_PATH)
        self.assertEqual(attester_path, ROOT / Q.ATTESTER_PATH)
        self.assertEqual(hashlib.sha256(runner_raw).hexdigest(), Q.RUNNER_SHA256)
        self.assertEqual(hashlib.sha256(attester_raw).hexdigest(), Q.ATTESTER_SHA256)
        raw = RECEIPT.read_bytes()
        value = json.loads(raw)
        self.assertEqual(raw, Q._canonical(value))
        self.assertEqual(
            value["status"],
            "pass-host-bound-runtime-root-activation-nonauthorizing",
        )
        self.assertEqual([item["product_id"] for item in value["products"]], list(Q.PRODUCT_IDS))
        self.assertEqual({item["returncode"] for item in value["products"]}, {0})
        self.assertEqual({item["stderr_bytes"] for item in value["products"]}, {0})
        self.assertEqual({item["stdout_bytes"] for item in value["products"]}, {321})
        self.assertEqual(
            {item["stdout_sha256"] for item in value["products"]},
            {"11dc8c1c6c94141c0740fd980899c58da52b852f0b044584df9983b59b79b9fa"},
        )
        for key in (
            "calibration_execution_authorized",
            "plan_frozen",
            "scientific_evidence",
            "scientific_evidence_eligible",
            "validation_execution_authorized",
        ):
            self.assertIs(value["classification"][key], False)

    def test_02_cli_rejects_before_qualification_without_gate(self) -> None:
        error = io.StringIO()
        with mock.patch.object(Q, "qualify", side_effect=AssertionError("qualification called")), contextlib.redirect_stderr(error):
            self.assertEqual(Q.main(["--build-root", "/private/tmp/missing"]), 2)
        self.assertIn("explicit engineering activation gate", error.getvalue())

    def test_03_probe_environment_is_exact_and_descriptor_mode_stays_clean(self) -> None:
        runner_path, runner_raw = Q._read_exact_source(
            ROOT, Q.RUNNER_PATH, Q.RUNNER_BYTES, Q.RUNNER_SHA256
        )
        runner = Q._load_exact("_activation_test_runner", runner_path, runner_raw)
        runtime = runner.DescriptorRuntime(100, 1, "none", 1, 1, 100)
        ordinary = runner.clean_descriptor_environment(runtime)
        probe = runner.clean_descriptor_environment(runtime, "runtime-root-probe")
        self.assertNotIn(runner.RUNTIME_ROOT_PROBE_ENV_KEY, ordinary)
        self.assertEqual(
            probe[runner.RUNTIME_ROOT_PROBE_ENV_KEY],
            runner.RUNTIME_ROOT_PROBE_ENV_VALUE,
        )
        changed = dict(probe)
        changed[runner.RUNTIME_ROOT_PROBE_ENV_KEY] = "wrong"
        self.assertNotEqual(changed, probe)
        with self.assertRaisesRegex(runner.RunnerError, "execution mode"):
            runner.clean_descriptor_environment(runtime, "scientific")

    def test_04_success_output_is_canonical_and_no_publisher_exists(self) -> None:
        receipt = json.loads(RECEIPT.read_bytes())
        output = io.BytesIO()

        class _Stdout:
            buffer = output

        with mock.patch.object(Q, "qualify", return_value=receipt), mock.patch.object(Q.sys, "stdout", _Stdout()):
            self.assertEqual(
                Q.main(
                    [
                        "--build-root",
                        "/private/tmp/authenticated-build",
                        "--execute-engineering-activation-smoke",
                    ]
                ),
                0,
            )
        self.assertEqual(output.getvalue(), Q._canonical(receipt))
        self.assertFalse(hasattr(Q, "publish"))


if __name__ == "__main__":
    unittest.main()
