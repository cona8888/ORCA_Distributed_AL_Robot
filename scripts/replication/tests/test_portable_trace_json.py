from __future__ import annotations

import hashlib
import json
import pathlib
import struct
import tempfile
import unittest

from scripts.comparison import trace_compare
from scripts.replication import analyze_orca_comparison as analyzer
from scripts.replication import run_orca_comparison as runner


def _f32_record(value: float) -> dict[str, object]:
    native = struct.unpack(">f", struct.pack(">f", value))[0]
    return {"hex": struct.pack(">f", native).hex(), "value": native}


def _f32_point(x: float, y: float) -> dict[str, object]:
    left = _f32_record(x)
    right = _f32_record(y)
    return {
        "hex": [left["hex"], right["hex"]],
        "value": [left["value"], right["value"]],
    }


class PortableTraceJsonTests(unittest.TestCase):
    def test_cpp_and_python_round_trip_spellings_are_both_admitted(self) -> None:
        cpp = (
            b'{"a":-19.871109008789063,"b":2.6011731222885772,'
            b'"c":4.1986163208717417e-20,"inside":"2.6011731222885772"}\n'
        )
        python = (
            b'{"a":-19.871109008789062,"b":2.601173122288577,'
            b'"c":4.1986163208717416e-20,"inside":"2.6011731222885772"}\n'
        )
        cpp_value = trace_compare.parse_portable_trace_json_line(cpp)
        python_value = trace_compare.parse_portable_trace_json_line(python)
        for key in ("a", "b", "c"):
            self.assertEqual(cpp_value[key].hex(), python_value[key].hex())
        self.assertEqual(cpp_value["inside"], "2.6011731222885772")

    def test_nonnumeric_canonical_and_strict_json_tampering_still_fails(self) -> None:
        rejected = {
            "whitespace": b'{"a": 1}\n',
            "unsorted-keys": b'{"b":1,"a":2}\n',
            "duplicate-key": b'{"a":1,"a":1}\n',
            "nan": b'{"a":NaN}\n',
            "infinity": b'{"a":Infinity}\n',
            "overflow-to-infinity": b'{"a":1e400}\n',
            "missing-lf": b'{"a":1}',
            "noncanonical-string-escape": b'{"a":"\\u0062"}\n',
        }
        for label, raw in rejected.items():
            with self.subTest(label=label), self.assertRaises(
                trace_compare.PortableTraceJsonError
            ):
                trace_compare.parse_portable_trace_json_line(raw)

    def test_runner_accepts_cpp_decimal_but_rejects_hex_tamper(self) -> None:
        row = {
            "cadence": "main",
            "common_sample_count": 1,
            "method": "orca",
            "native_sample_stride": 1,
            "robot_count": 1,
            "scenario_sha256": "1" * 64,
            "scene_id": "portable-json-regression",
            "scene_set": "open",
            "sequence": 0,
        }
        header = {
            "input": {
                "cadence": row["cadence"],
                "input_manifest_sha256": runner.INPUT_MANIFEST_SHA256,
                "scenario_sha256": row["scenario_sha256"],
                "scene_id": row["scene_id"],
                "scene_set": row["scene_set"],
                "schedule_sha256": runner.SCHEDULE_SHA256,
                "study_id": runner.STUDY_ID,
            },
            "numeric_identity": (
                "native IEEE-754 binary32 values plus 8-digit hex bits"
            ),
            "observation_rule": (
                "initial state plus post-doStep states; integer common ticks define time"
            ),
            "parameter_audit": {
                "accepted": {"num_agents": 1, "num_obstacle_vertices": 0},
                "verified_through_public_getters": True,
            },
            "schema_version": "dancers-orca-trace-v1",
            "source": runner.ORCA_SOURCE,
            "type": "header",
        }
        robot = {
            "executed_velocity": _f32_point(0.0, 0.0),
            "first_goal_native_step": None,
            "goal": _f32_point(4.0, 0.0),
            "goal_distance_m": 23.871109008789062,
            "goal_latched": False,
            "position": _f32_point(-19.871109008789062, 0.0),
            "preferred_velocity": _f32_point(0.0, 0.0),
            "radius": _f32_record(1.0),
            "rid": 0,
        }
        frame = {
            "common_tick": 0,
            "native_step": 0,
            "nominal_time_seconds": 0.0,
            "robots": [robot],
            "type": "frame",
        }
        canonical = runner.canonical_json_bytes(header) + runner.canonical_json_bytes(
            frame
        )
        cpp_spelling = canonical.replace(
            b"-19.871109008789062", b"-19.871109008789063"
        )
        self.assertNotEqual(canonical, cpp_spelling)
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "trace.ndjson"
            path.write_bytes(cpp_spelling)
            runner._validate_trace_structure(row, path)

            tampered = json.loads(json.dumps(frame))
            tampered["robots"][0]["position"]["hex"][0] = "00000000"
            path.write_bytes(
                runner.canonical_json_bytes(header)
                + runner.canonical_json_bytes(tampered).replace(
                    b"-19.871109008789062", b"-19.871109008789063"
                )
            )
            with self.assertRaisesRegex(runner.RunnerError, "robot trace differs"):
                runner._validate_trace_structure(row, path)

            missing_field = json.loads(json.dumps(frame))
            del missing_field["robots"][0]["goal_latched"]
            path.write_bytes(
                runner.canonical_json_bytes(header)
                + runner.canonical_json_bytes(missing_field)
            )
            with self.assertRaisesRegex(runner.RunnerError, "robot trace differs"):
                runner._validate_trace_structure(row, path)

            path.write_bytes(runner.canonical_json_bytes(header))
            with self.assertRaisesRegex(runner.RunnerError, "frame count differs"):
                runner._validate_trace_structure(row, path)

    def test_analyzer_uses_the_same_portable_trace_parser(self) -> None:
        raw = (
            b'{"type":"header","value":2.6011731222885772}\n'
            b'{"type":"frame","value":-0.7844561125634399}\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary).resolve() / "trace.ndjson"
            path.write_bytes(raw)
            header, frames, observed = analyzer._trace_records(path, "test trace")
            self.assertEqual(observed, raw)
            self.assertEqual(header["value"].hex(), float("2.601173122288577").hex())
            self.assertEqual(frames[0]["value"].hex(), float("-0.78445611256344").hex())

            path.write_bytes(b'{"value":1,"type":"header"}\n')
            with self.assertRaisesRegex(analyzer.AnalysisError, "portable trace"):
                analyzer._trace_records(path, "test trace")

    def test_session_001_root_ledger_is_immutable(self) -> None:
        root = runner.REPO / "results/publication/dancers-orca-v1-session-001"
        expected = {
            "input_manifest.json": runner.INPUT_MANIFEST_SHA256,
            "plan.json": "abea19e3e4ec5274aea80bebfd521444cc485e757731b522884bbaefcb77e7e7",
            "records.ndjson": "bcf2387e5c493c4324fc5119e54dcf07bc38bbcbf30516d224e7b481c80dd61e",
            "schedule.json": runner.SCHEDULE_SHA256,
            "session.json": "807619831db0c4cc2c2c2fc05598c6ac92f0ef2228b34c4008434c8ec4566cef",
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha256((root / name).read_bytes()).hexdigest(), digest
                )


if __name__ == "__main__":
    unittest.main()
