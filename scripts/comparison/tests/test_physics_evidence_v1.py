from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "comparison" / "physics_evidence_v1.py"
SPEC = importlib.util.spec_from_file_location("physics_evidence_v1", MODULE_PATH)
assert SPEC and SPEC.loader
EVIDENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVIDENCE
SPEC.loader.exec_module(EVIDENCE)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def domain_hash(domain: str, records: list[dict[str, object]]) -> str:
    digest = hashlib.sha256(domain.encode("ascii") + b"\0")
    for record in records:
        digest.update(canonical(record) + b"\n")
    return digest.hexdigest()


def one_hash(domain: str, value: dict[str, object]) -> str:
    return domain_hash(domain, [value])


def find_analytic_binary() -> Path | None:
    configured = os.environ.get("DANCERS_PHYSICS_EVIDENCE_ANALYTIC_BINARY")
    candidates = [
        Path(configured) if configured else None,
        REPO_ROOT / "build" / "pixi" / "dancers-physics-evidence-analytic-test",
        REPO_ROOT / "build" / "dancers-physics-evidence-analytic-test",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def read_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_records(path: Path, records: list[dict[str, object]]) -> None:
    path.write_bytes(b"".join(canonical(record) + b"\n" for record in records))


def resign_payload_and_terminal(records: list[dict[str, object]]) -> None:
    terminal = records[-1]
    payload = b"".join(canonical(record) + b"\n" for record in records[:-1])
    payload_digest = hashlib.sha256(
        EVIDENCE.PAYLOAD_DOMAIN.encode("ascii") + b"\0" + payload
    ).hexdigest()
    terminal["preterminal_payload_bytes"] = len(payload)
    terminal["preterminal_payload_sha256"] = payload_digest
    terminal_without_hash = {
        key: terminal[key]
        for key in list(terminal)[:-1]
    }
    terminal["terminal_semantic_sha256"] = one_hash(
        EVIDENCE.TERMINAL_DOMAIN, terminal_without_hash
    )


def resign_mission_terminal(records: list[dict[str, object]]) -> None:
    header = records[0]
    inputs = header["initialization"]["common_scene"]["inputs"]
    terminal = records[-1]
    summary = terminal["summary"]
    terminal_frame_sha = one_hash(EVIDENCE.EVENT_DOMAIN, summary)
    terminal["mission_terminal_frame_sha256"] = terminal_frame_sha
    mission_refs: list[dict[str, object]] = []
    for event in records[1:-1]:
        if event["event"] == "boundary":
            mission_refs.append(
                {
                    "record": "mission_boundary_frame_ref",
                    "boundary": event["boundary"],
                    "simulated_time_ms": event["simulated_time_ms"],
                    "item_count": inputs["robot_count"],
                    "frame_sha256": event["mission_frame_sha256"],
                }
            )
    mission_refs.extend(
        [
            {"record": "mission_terminal_frame_ref", "frame_sha256": terminal_frame_sha},
            {
                "record": "mission_end",
                "boundary_frame_count": inputs["controller_ticks"] + 1,
                "terminal_frame_count": 1,
            },
        ]
    )
    terminal["domain_digests"]["canonical_mission_summary_sha256"] = domain_hash(
        EVIDENCE.MISSION_DOMAIN, mission_refs
    )


def resign_command_event(
    records: list[dict[str, object]],
    event_index: int,
) -> None:
    event = records[event_index]
    frame = {
        key: event[key]
        for key in ("event", "tick", "simulated_time_ms", "record_count", "records")
    }
    event["command_frame_sha256"] = one_hash(EVIDENCE.EVENT_DOMAIN, frame)
    semantic = {key: event[key] for key in list(event)[:-1]}
    event["semantic_sha256"] = one_hash(EVIDENCE.EVENT_DOMAIN, semantic)
    header = records[0]
    inputs = header["initialization"]["common_scene"]["inputs"]
    command_refs = [
        {
            "record": "command_frame_ref",
            "tick": candidate["tick"],
            "simulated_time_ms": candidate["simulated_time_ms"],
            "item_count": candidate["record_count"],
            "frame_sha256": candidate["command_frame_sha256"],
        }
        for candidate in records[1:-1]
        if candidate["event"] == "commands"
    ]
    command_refs.append(
        {
            "record": "commands_end",
            "frame_count": inputs["controller_ticks"],
            "item_count": inputs["controller_ticks"] * inputs["robot_count"],
        }
    )
    command_sha = domain_hash(EVIDENCE.COMMAND_DOMAIN, command_refs)
    terminal = records[-1]
    terminal["summary"]["stream_digests"]["canonical_command_sha256"] = command_sha
    terminal["domain_digests"]["canonical_command_sha256"] = command_sha
    resign_mission_terminal(records)
    resign_payload_and_terminal(records)


def resign_digest_command_event(
    records: list[dict[str, object]],
    event_index: int,
) -> None:
    header = records[0]
    inputs = header["initialization"]["common_scene"]["inputs"]
    command_refs = [
        {
            "record": "command_frame_ref",
            "tick": candidate["tick"],
            "simulated_time_ms": candidate["simulated_time_ms"],
            "item_count": candidate["record_count"],
            "frame_sha256": candidate["command_frame_sha256"],
        }
        for candidate in records[1:-1]
        if candidate["event"] == "commands"
    ]
    command_refs.append(
        {
            "record": "commands_end",
            "frame_count": inputs["controller_ticks"],
            "item_count": inputs["controller_ticks"] * inputs["robot_count"],
        }
    )
    command_sha = domain_hash(EVIDENCE.COMMAND_DOMAIN, command_refs)
    terminal = records[-1]
    terminal["summary"]["stream_digests"]["canonical_command_sha256"] = command_sha
    terminal["domain_digests"]["canonical_command_sha256"] = command_sha
    resign_mission_terminal(records)
    resign_payload_and_terminal(records)


def resign_contact_event(
    records: list[dict[str, object]],
    event_index: int,
) -> None:
    event = records[event_index]
    event["contact_frame_sha256"] = one_hash(
        EVIDENCE.EVENT_DOMAIN, event["contact_frame"]
    )
    semantic = {key: event[key] for key in list(event)[:-1]}
    event["semantic_sha256"] = one_hash(EVIDENCE.EVENT_DOMAIN, semantic)
    terminal = records[-1]
    contact_refs: list[dict[str, object]] = []
    boundary0 = records[1]
    contact_refs.append(
        {
            "record": "clearance_frame_ref",
            "sample_index": 0,
            "simulated_time_ms": 0,
            "frame_sha256": boundary0["clearance_frame_sha256"],
        }
    )
    for candidate in records[1:-1]:
        if candidate["event"] != "substep":
            continue
        contact_refs.extend(
            [
                {
                    "record": "contact_frame_ref",
                    "engine_step": candidate["engine_step"],
                    "simulated_time_ms": candidate["simulated_time_ms"],
                    "item_count": candidate["contact_count"],
                    "frame_sha256": candidate["contact_frame_sha256"],
                },
                {
                    "record": "clearance_frame_ref",
                    "sample_index": candidate["engine_step"],
                    "simulated_time_ms": candidate["simulated_time_ms"],
                    "frame_sha256": candidate["clearance_frame_sha256"],
                },
            ]
        )
    counts = terminal["summary"]["event_counts"]
    contact_refs.append(
        {
            "record": "contacts_and_clearance_end",
            "contact_frame_count": counts["contact_frames"],
            "contact_item_count": counts["contact_items"],
            "clearance_frame_count": counts["clearance_samples"],
        }
    )
    contact_sha = domain_hash(EVIDENCE.CONTACT_DOMAIN, contact_refs)
    terminal["summary"]["stream_digests"][
        "canonical_contact_and_clearance_sha256"
    ] = contact_sha
    terminal["domain_digests"][
        "canonical_contact_and_clearance_sha256"
    ] = contact_sha
    resign_mission_terminal(records)
    resign_payload_and_terminal(records)


def resign_header_scene(records: list[dict[str, object]]) -> None:
    header = records[0]
    initialization = header["initialization"]
    common_scene_sha = one_hash(EVIDENCE.COMMON_SCENE_DOMAIN, initialization["common_scene"])
    initialization_sha = one_hash(EVIDENCE.INITIALIZATION_DOMAIN, initialization)
    header["semantic_sha256"] = initialization_sha
    terminal = records[-1]
    terminal["initialization_semantic_sha256"] = initialization_sha
    terminal["summary"]["common_scene_semantic_sha256"] = common_scene_sha
    resign_mission_terminal(records)
    resign_payload_and_terminal(records)


def resign_replay_event_only(records: list[dict[str, object]], event_index: int) -> None:
    event = records[event_index]
    semantic = {key: event[key] for key in list(event)[:-1]}
    event["semantic_sha256"] = one_hash(EVIDENCE.EVENT_DOMAIN, semantic)
    resign_payload_and_terminal(records)


class PhysicsEvidenceParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        binary = find_analytic_binary()
        if binary is None:
            raise unittest.SkipTest("analytic physics-evidence test binary is not built")
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="physics-evidence-parser-", dir=REPO_ROOT.parent
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

    def mutated_path(self, name: str) -> Path:
        return self.directory / name

    def assert_rejected(self, path: Path, fragment: str | None = None) -> None:
        with self.assertRaises(EVIDENCE.EvidenceFormatError) as raised:
            EVIDENCE.parse_physics_evidence_v1(path)
        if fragment is not None:
            self.assertIn(fragment, str(raised.exception))

    def test_live_digest_and_replay_verify_with_projection_identical_domains(self) -> None:
        digest = EVIDENCE.parse_physics_evidence_v1(self.digest_path)
        replay = EVIDENCE.parse_physics_evidence_v1(self.replay_path)
        self.assertEqual(digest.controller_ticks, 2)
        self.assertEqual(digest.physics_steps, 20)
        self.assertEqual(len(digest.event_locators), 25)
        self.assertEqual(digest.domain_digests, replay.domain_digests)
        self.assertEqual(
            [item.semantic_sha256 for item in digest.event_locators],
            [item.semantic_sha256 for item in replay.event_locators],
        )
        self.assertFalse(replay.scientific_evidence)
        self.assertFalse(replay.execution_plan_bound)
        self.assertEqual(replay.verification_level, "replay-full-leaf-and-formula")

    def test_signed_zero_is_preserved_in_authenticated_input_and_normalized_in_body0(self) -> None:
        trace = EVIDENCE.parse_physics_evidence_v1(self.replay_path)
        mission = trace.header["initialization"]["common_scene"]["inputs"]["missions"][2]
        self.assertEqual(
            mission["authenticated_initial_r6_before_zero_normalization"][4:],
            ["8000000000000000", "8000000000000000"],
        )
        boundary0 = EVIDENCE.read_verified_event(trace, 0)
        self.assertEqual(
            boundary0["body_state_frame"]["bodies"][2]["r6"][3:],
            ["0000000000000000"] * 3,
        )
        self.assertFalse(EVIDENCE._float_close_bits("8000000000000000", 0.0))

    def test_duplicate_key_and_wrong_key_order_are_rejected(self) -> None:
        raw_lines = self.digest_path.read_bytes().splitlines(keepends=True)
        duplicate = list(raw_lines)
        duplicate[1] = duplicate[1].replace(
            b'{"event":"boundary"',
            b'{"event":"boundary","event":"boundary"',
            1,
        )
        duplicate_path = self.mutated_path("duplicate.jsonl")
        duplicate_path.write_bytes(b"".join(duplicate))
        self.assert_rejected(duplicate_path, "duplicate object key")

        records = read_records(self.digest_path)
        boundary = records[1]
        records[1] = {
            "boundary": boundary["boundary"],
            "event": boundary["event"],
            **{key: boundary[key] for key in list(boundary)[2:]},
        }
        order_path = self.mutated_path("wrong-order.jsonl")
        write_records(order_path, records)
        self.assert_rejected(order_path, "object keys/order differ")

    def test_nonfinite_f64_and_oversized_integer_fail_after_coherent_rehash(self) -> None:
        records = read_records(self.replay_path)
        command_index = next(
            index for index, event in enumerate(records) if event["event"] == "commands"
        )
        records[command_index]["records"][0]["planner_raw_requested_pose"][0] = "7ff0000000000000"
        resign_command_event(records, command_index)
        nonfinite = self.mutated_path("coherent-nonfinite.jsonl")
        write_records(nonfinite, records)
        self.assert_rejected(nonfinite, "NaN and infinity")

        records = read_records(self.replay_path)
        records[command_index]["records"][0]["remaining_waypoint_count"] = 1 << 64
        resign_command_event(records, command_index)
        oversized = self.mutated_path("coherent-oversized.jsonl")
        write_records(oversized, records)
        self.assert_rejected(oversized, "unsigned 64-bit")

    def test_cumulative_float_and_final_goal_zero_rules_survive_coherent_hashes(self) -> None:
        records = read_records(self.replay_path)
        substep_index = next(
            index for index, event in enumerate(records) if event["event"] == "substep"
        )
        original = records[substep_index]["cumulative_engine_metrics"]["total_force_magnitude_n_s"]
        records[substep_index]["cumulative_engine_metrics"]["total_force_magnitude_n_s"] = (
            f"{int(original, 16) + 1:016x}"
        )
        resign_replay_event_only(records, substep_index)
        cumulative = self.mutated_path("coherent-cumulative-float.jsonl")
        write_records(cumulative, records)
        self.assert_rejected(cumulative, "cumulative floating engine total")

        records = read_records(self.replay_path)
        goal_command_index = [
            index for index, event in enumerate(records) if event["event"] == "commands"
        ][1]
        goal_record = records[goal_command_index]["records"][0]
        self.assertTrue(goal_record["final_goal_reached"])
        goal_record["adapter_raw_velocity_command"][0] = "3ff0000000000000"
        goal_record["adapter_clamped_velocity_command"][0] = "3ff0000000000000"
        resign_command_event(records, goal_command_index)
        goal_path = self.mutated_path("coherent-goal-command.jsonl")
        write_records(goal_path, records)
        self.assert_rejected(goal_path, "final-goal command")

    def test_negative_zero_engine_metric_fails_after_coherent_rehash(self) -> None:
        records = read_records(self.replay_path)
        substep_index = next(
            index
            for index, event in enumerate(records)
            if event["event"] == "substep"
            and event["step_engine_metrics"]["total_pair_normal_impulse_n_s"]
            == "0000000000000000"
        )
        records[substep_index]["step_engine_metrics"][
            "total_pair_normal_impulse_n_s"
        ] = "8000000000000000"
        resign_replay_event_only(records, substep_index)
        negative_zero = self.mutated_path("coherent-negative-zero-metric.jsonl")
        write_records(negative_zero, records)
        self.assert_rejected(negative_zero, "canonical +0")

    def test_digest_terminal_nonnegative_field_rejects_negative_zero_after_rehash(self) -> None:
        records = read_records(self.digest_path)
        records[-1]["summary"]["derived_metrics_v1"][
            "linear_command_tracking_error_rms_m_s"
        ] = "8000000000000000"
        resign_mission_terminal(records)
        resign_payload_and_terminal(records)
        negative_zero = self.mutated_path(
            "coherent-digest-terminal-negative-zero.jsonl"
        )
        write_records(negative_zero, records)
        self.assert_rejected(negative_zero, "canonical +0")

    def test_adapter_conversion_and_clamp_reject_coherently_rehashed_command_words(self) -> None:
        raw, clamped = EVIDENCE._reconstruct_adapter_command(
            [
                EVIDENCE.f64_bits(1.0),
                EVIDENCE.f64_bits(0.0),
                EVIDENCE.f64_bits(1.0),
            ],
            [EVIDENCE.f64_bits(0.0)] * 6,
            False,
        )
        self.assertEqual(raw[0], EVIDENCE.f64_bits(10.0))
        self.assertEqual(clamped[0], EVIDENCE.f64_bits(2.0))
        self.assertEqual(
            EVIDENCE._reconstruct_adapter_command(
                [EVIDENCE.f64_bits(99.0)] * 3,
                [EVIDENCE.f64_bits(0.0)] * 6,
                True,
            ),
            (["0000000000000000"] * 3, ["0000000000000000"] * 3),
        )

        records = read_records(self.replay_path)
        command_index = next(
            index for index, event in enumerate(records) if event["event"] == "commands"
        )
        command = records[command_index]["records"][0]
        changed_word = f"{int(command['adapter_raw_velocity_command'][0], 16) ^ 1:016x}"
        command["adapter_raw_velocity_command"][0] = changed_word
        command["adapter_clamped_velocity_command"][0] = changed_word
        resign_command_event(records, command_index)
        changed = self.mutated_path("coherent-adapter-command.jsonl")
        write_records(changed, records)
        self.assert_rejected(changed, "adapter raw velocity command")

    def test_negative_contact_magnitude_fails_after_coherent_rehash(self) -> None:
        records = read_records(self.replay_path)
        substep_index = next(
            index
            for index, event in enumerate(records)
            if event["event"] == "substep" and event["contact_count"] > 0
        )
        records[substep_index]["contact_frame"]["contacts"][0][
            "penetration_before_m"
        ] = EVIDENCE.f64_bits(-1.0)
        resign_contact_event(records, substep_index)
        changed = self.mutated_path("coherent-negative-contact.jsonl")
        write_records(changed, records)
        self.assert_rejected(changed, "physically nonnegative")

    def test_truncation_trailing_payload_and_terminal_tamper_are_rejected(self) -> None:
        raw = self.digest_path.read_bytes()
        truncation = self.mutated_path("truncated.jsonl")
        truncation.write_bytes(raw.rsplit(b"\n", 2)[0] + b"\n")
        self.assert_rejected(truncation, "unexpected end of file")

        trailing = self.mutated_path("trailing.jsonl")
        trailing.write_bytes(raw + b"{}\n")
        self.assert_rejected(trailing, "trailing")

        records = read_records(self.digest_path)
        records[2]["semantic_sha256"] = "0" * 64
        payload = self.mutated_path("payload-tamper.jsonl")
        write_records(payload, records)
        self.assert_rejected(payload, "preterminal payload SHA-256")

        records = read_records(self.digest_path)
        records[-1]["terminal_semantic_sha256"] = "0" * 64
        terminal = self.mutated_path("terminal-tamper.jsonl")
        write_records(terminal, records)
        self.assert_rejected(terminal, "terminal semantic SHA-256")

    def test_final_symlink_is_rejected_before_reading(self) -> None:
        link = self.mutated_path("evidence-link.jsonl")
        link.symlink_to(self.digest_path)
        self.assert_rejected(link, "securely open")

    def test_reread_rejects_ctime_change_even_when_mtime_and_size_are_restored(self) -> None:
        copied = self.mutated_path("ctime-replay.jsonl")
        shutil.copyfile(self.replay_path, copied)
        trace = EVIDENCE.parse_physics_evidence_v1(copied)
        original = copied.stat()
        os.chmod(copied, 0o600)
        os.utime(copied, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaises(EVIDENCE.EvidenceFormatError) as raised:
            EVIDENCE.read_verified_event(trace, 0)
        self.assertIn("changed since verification", str(raised.exception))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO support is required")
    def test_fifo_is_rejected_without_blocking_before_fstat(self) -> None:
        fifo = self.mutated_path("hostile-input.fifo")
        os.mkfifo(fifo)
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(fifo)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("regular file", completed.stderr)


if __name__ == "__main__":
    unittest.main()
