from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPORT_PATH = Path(__file__).with_name("test_physics_evidence_v1.py")
SUPPORT_SPEC = importlib.util.spec_from_file_location(
    "physics_evidence_test_support", SUPPORT_PATH
)
assert SUPPORT_SPEC and SUPPORT_SPEC.loader
SUPPORT = importlib.util.module_from_spec(SUPPORT_SPEC)
sys.modules[SUPPORT_SPEC.name] = SUPPORT
SUPPORT_SPEC.loader.exec_module(SUPPORT)

COMPARATOR_PATH = (
    REPO_ROOT / "scripts" / "comparison" / "compare_physics_evidence_v1.py"
)
COMPARATOR_SPEC = importlib.util.spec_from_file_location(
    "compare_physics_evidence_v1", COMPARATOR_PATH
)
assert COMPARATOR_SPEC and COMPARATOR_SPEC.loader
COMPARE = importlib.util.module_from_spec(COMPARATOR_SPEC)
sys.modules[COMPARATOR_SPEC.name] = COMPARE
COMPARATOR_SPEC.loader.exec_module(COMPARE)


class PhysicsEvidenceComparatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        binary = SUPPORT.find_analytic_binary()
        if binary is None:
            raise unittest.SkipTest("analytic physics-evidence test binary is not built")
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="physics-evidence-comparator-", dir=REPO_ROOT.parent
        )
        cls.directory = Path(cls._temporary.name)
        cls.digest_path = cls.directory / "digest.jsonl"
        cls.replay_path = cls.directory / "replay.jsonl"
        for output, detail in (
            (cls.digest_path, "digest"),
            (cls.replay_path, "replay"),
        ):
            subprocess.run(
                [str(binary), str(output), detail, "success"],
                cwd=REPO_ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "_temporary"):
            cls._temporary.cleanup()

    def test_digest_and_replay_are_semantically_equal_but_not_scientific_pair(self) -> None:
        result = COMPARE.compare_physics_evidence_v1(
            self.digest_path, self.replay_path
        )
        self.assertTrue(result.equal)
        self.assertEqual(result.events_compared, 25)
        report = result.public_report()
        self.assertTrue(report["cross_projection"])
        self.assertFalse(report["classification"]["scientific_pair_eligible"])
        self.assertFalse(
            report["classification"]["reference_optimized_claim_authenticated"]
        )

    def test_valid_replay_mismatch_resolves_exact_first_command_leaf(self) -> None:
        records = SUPPORT.read_records(self.replay_path)
        command_index = next(
            index for index, event in enumerate(records) if event["event"] == "commands"
        )
        word = records[command_index]["records"][0]["planner_raw_requested_pose"][0]
        records[command_index]["records"][0]["planner_raw_requested_pose"][0] = (
            f"{int(word, 16) ^ 1:016x}"
        )
        SUPPORT.resign_command_event(records, command_index)
        changed = self.directory / "changed-replay.jsonl"
        SUPPORT.write_records(changed, records)

        # The adversarial artifact is valid in isolation; this is a semantic
        # mismatch rather than invalid-input handling.
        SUPPORT.EVIDENCE.parse_physics_evidence_v1(changed)
        result = COMPARE.compare_physics_evidence_v1(self.replay_path, changed)
        self.assertFalse(result.equal)
        mismatch = result.first_discordance
        assert mismatch is not None
        self.assertEqual(mismatch.event, "commands")
        self.assertEqual(mismatch.tick, 0)
        self.assertEqual(mismatch.primary_domain, "canonical_command_sha256")
        self.assertEqual(mismatch.resolution, "replay-leaf")
        self.assertEqual(
            mismatch.leaf_path,
            "$.command[0].planner_raw_requested_pose[0]",
        )

    def test_valid_digest_mismatch_is_frame_localized_without_guessing_robot(self) -> None:
        records = SUPPORT.read_records(self.digest_path)
        command_index = next(
            index for index, event in enumerate(records) if event["event"] == "commands"
        )
        records[command_index]["command_frame_sha256"] = "f" * 64
        SUPPORT.resign_digest_command_event(records, command_index)
        changed = self.directory / "changed-digest.jsonl"
        SUPPORT.write_records(changed, records)
        SUPPORT.EVIDENCE.parse_physics_evidence_v1(changed)

        result = COMPARE.compare_physics_evidence_v1(self.digest_path, changed)
        mismatch = result.first_discordance
        assert mismatch is not None
        self.assertEqual(mismatch.event, "commands")
        self.assertEqual(mismatch.resolution, "frame-only")
        self.assertIsNone(mismatch.leaf_path)
        self.assertEqual(mismatch.coincident_components, ("command",))

    def test_common_scene_change_is_invalid_pair_not_behavior_divergence(self) -> None:
        records = SUPPORT.read_records(self.replay_path)
        records[0]["initialization"]["common_scene"]["inputs"][
            "mission_id"
        ] += "-different"
        SUPPORT.resign_header_scene(records)
        changed = self.directory / "different-scene.jsonl"
        SUPPORT.write_records(changed, records)
        SUPPORT.EVIDENCE.parse_physics_evidence_v1(changed)
        with self.assertRaises(COMPARE.EvidencePairError) as raised:
            COMPARE.compare_physics_evidence_v1(self.replay_path, changed)
        self.assertIn("common-scene", str(raised.exception))

    def test_malformed_suffix_is_rejected_after_an_early_mismatch(self) -> None:
        records = SUPPORT.read_records(self.replay_path)
        command_index = next(
            index for index, event in enumerate(records) if event["event"] == "commands"
        )
        word = records[command_index]["records"][0]["planner_raw_requested_pose"][0]
        records[command_index]["records"][0]["planner_raw_requested_pose"][0] = (
            f"{int(word, 16) ^ 1:016x}"
        )
        SUPPORT.resign_command_event(records, command_index)
        malformed = self.directory / "early-mismatch-bad-suffix.jsonl"
        SUPPORT.write_records(malformed, records[:-1])
        with self.assertRaises(SUPPORT.EVIDENCE.EvidenceFormatError) as raised:
            COMPARE.compare_physics_evidence_v1(self.replay_path, malformed)
        self.assertIn("unexpected end of file", str(raised.exception))

    def test_cli_exit_conventions_are_zero_equal_one_mismatch_two_invalid(self) -> None:
        equal = subprocess.run(
            [sys.executable, str(COMPARATOR_PATH), str(self.digest_path), str(self.replay_path)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(equal.returncode, 0, equal.stderr)

        records = SUPPORT.read_records(self.digest_path)
        command_index = next(
            index for index, event in enumerate(records) if event["event"] == "commands"
        )
        records[command_index]["command_frame_sha256"] = "e" * 64
        SUPPORT.resign_digest_command_event(records, command_index)
        mismatch_path = self.directory / "cli-mismatch.jsonl"
        SUPPORT.write_records(mismatch_path, records)
        mismatch = subprocess.run(
            [sys.executable, str(COMPARATOR_PATH), str(self.digest_path), str(mismatch_path)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(mismatch.returncode, 1, mismatch.stderr)

        invalid_path = self.directory / "cli-invalid.jsonl"
        invalid_path.write_bytes(b"{}\n")
        invalid = subprocess.run(
            [sys.executable, str(COMPARATOR_PATH), str(self.digest_path), str(invalid_path)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()
