from __future__ import annotations

import contextlib
import io
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts.replication import prepare_orca_outcome_packet as packet


def fake_analysis() -> dict[str, object]:
    behavior = {
        metric: {
            "difference_direction": "DANCeRS-minus-ORCA",
            "estimable": True,
            "mean_difference": float(index),
            "median_difference": float(index) + 0.5,
            "mean_difference_interval": {
                "lower_95": float(index) - 1.0,
                "upper_95": float(index) + 1.0,
            },
        }
        for index, metric in enumerate(packet.analyzer.BEHAVIOR_DIFFERENCE_METRICS)
    }
    resources = {
        metric: {
            "estimable": True,
            "geometric_mean_ratio": 1.2,
            "lower_95": 1.1,
            "registered_gate_passed": True,
            "upper_95": 1.3,
        }
        for metric in packet.analyzer.RESOURCE_RATIO_METRICS
    }
    return {
        "behavior": {
            "open": {"metric_summaries": behavior},
            "scene_pairs": [{"estimable": True} for _ in range(23)],
        },
        "dancers_fidelity": {"all_scenes_passed": True},
        "integrity": {
            "admission_manifest_sha256": packet.ADMISSION_SHA256,
            "authorization_manifest_sha256": packet.AUTHORIZATION_SHA256,
            "input_manifest_sha256": packet.INPUT_MANIFEST_SHA256,
            "plan_sha256": packet.PLAN_SHA256,
            "schedule_sha256": packet.SCHEDULE_SHA256,
            "sealed_session_valid": True,
        },
        "orca_repeatability": {
            "all_main_passed": True,
            "all_sensitivity_passed": True,
            "rows": [{} for _ in range(26)],
        },
        "resources": {"open": {"metric_summaries": resources}},
        "schema_version": "dancers-orca-analysis-v1",
        "study_id": packet.STUDY_ID,
    }


class OrcaOutcomePacketTests(unittest.TestCase):
    def test_projection_includes_every_registered_metric_and_refuses_dominance(self) -> None:
        outcome = packet.outcome_document(fake_analysis())
        self.assertEqual(
            set(outcome["behavior_open_fixed_world"]),
            set(packet.analyzer.BEHAVIOR_DIFFERENCE_METRICS),
        )
        self.assertEqual(
            set(outcome["resource_open_fixed_world"]),
            set(packet.analyzer.RESOURCE_RATIO_METRICS),
        )
        self.assertFalse(
            outcome["claim_boundary"]["blanket_dancers_superiority_to_orca_supported"]
        )
        self.assertTrue(all(outcome["confirmatory_gates"].values()))

    def test_create_requires_exact_confirmation_before_analysis(self) -> None:
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

    def test_publish_and_check_bind_full_analysis_to_compact_projection(self) -> None:
        analysis = fake_analysis()
        analysis_raw = packet.canonical_json_bytes(analysis, pretty=True)
        outcome_raw = packet.canonical_json_bytes(
            packet.outcome_document(analysis), pretty=True
        )
        payloads = {"analysis.json": analysis_raw, "outcome.json": outcome_raw}
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve() / "packet"
            with (
                mock.patch.object(packet, "EXPECTED_ANALYSIS_BYTES", len(analysis_raw)),
                mock.patch.object(
                    packet, "EXPECTED_ANALYSIS_SHA256", packet.sha256_bytes(analysis_raw)
                ),
            ):
                manifest = {
                    "analysis": {
                        "bytes": len(analysis_raw),
                        "sha256": packet.sha256_bytes(analysis_raw),
                    },
                    "authentication": {"rule": packet.AUTHENTICATION_RULE},
                    "decision_packet_manifest_sha256": (
                        packet.DECISION_PACKET_MANIFEST_SHA256
                    ),
                    "execution_capsule_manifest_sha256": (
                        packet.CAPSULE_MANIFEST_SHA256
                    ),
                    "files": [
                        packet._payload_identity(name, payloads[name])
                        for name in packet.PAYLOADS
                    ],
                    "packet_id": packet.PACKET_ID,
                    "schema_version": packet.SCHEMA,
                    "session_files": packet._validate_live_session(),
                    "session_id": packet.SESSION_ID,
                    "sources": {
                        "analyzer": packet._source_identity(packet.ANALYZER_SOURCE),
                        "outcome_packet_generator": packet._source_identity(
                            packet.GENERATOR_SOURCE
                        ),
                    },
                    "study_id": packet.STUDY_ID,
                }
                digest = packet.publish_packet(root, payloads, manifest)
                checked = packet.check_packet(root, digest)
            self.assertEqual(checked, manifest)

    def test_public_source_has_no_execution_or_subprocess_surface(self) -> None:
        source = pathlib.Path(packet.__file__).read_text(encoding="utf-8")
        self.assertNotIn("launch_orca_comparison", source)
        self.assertNotIn("run_orca_comparison", source)
        self.assertNotIn("execute_authorized(", source)
        self.assertNotIn("ExecutionPermit(", source)
        self.assertNotIn("subprocess.", source)


if __name__ == "__main__":
    unittest.main()
