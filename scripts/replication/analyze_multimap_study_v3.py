#!/usr/bin/env python3
"""Final analyzer repair for the sealed DANCeRS multi-map v1 session.

Version 2 repaired the registered trace tick convention.  Version 3 also
derives task validity from exact summary counters and contact-free independent
geometry, instead of copying the geometry helper's deliberately locked
``task_valid=False`` authorization field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import analyze_multimap_study_v2 as v2
from scripts.replication import run_multimap_study as study


ANALYZER_SCHEMA = "dancers-multimap-analysis-v3-task-validity-repair"
SUMMARY_EXPECTED = {
    "final_tick": 100,
    "frames_recorded": 200,
    "invalid_geometry_evaluations": 0,
    "invalid_robot_frames": 0,
    "iterations": 200,
    "nonfinite_numeric_observations": 0,
    "nonfinite_state_observations": 0,
    "obstacle_collision_events": 0,
    "obstacle_collision_exposure_seconds": 0.0,
    "pair_collision_events": 0,
    "pair_collision_exposure_seconds": 0.0,
    "run_valid": True,
    "termination_reason": "maximum_time",
    "trace_detail": "positions",
}


def derive_scene_task_validity(
    summary: Mapping[str, Any], geometry_row: Mapping[str, Any], robot_count: int
) -> dict[str, Any]:
    reasons: list[str] = []
    expected = {
        **SUMMARY_EXPECTED,
        "final_robot_count": robot_count,
        "maximum_robot_count": robot_count,
    }
    for field, value in expected.items():
        if field not in summary or type(summary[field]) is not type(value) or summary[field] != value:
            reasons.append(f"summary-{field}-differs")
    for field in ("minimum_pair_clearance_m", "minimum_obstacle_clearance_m"):
        value = summary.get(field)
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            reasons.append(f"summary-{field}-nonfinite")
        elif float(value) <= 1e-12:
            reasons.append(f"summary-{field}-not-above-safety-epsilon")
    if geometry_row.get("geometry_contact_free") is not True:
        reasons.append("independent-geometry-not-contact-free")
    geometry_reasons = geometry_row.get("geometry_failure_reasons")
    if not isinstance(geometry_reasons, list) or any(type(item) is not str for item in geometry_reasons):
        reasons.append("independent-geometry-failure-reasons-malformed")
    else:
        reasons.extend(f"independent-{item}" for item in geometry_reasons)
    unique = sorted(set(reasons))
    return {"task_failure_reasons": unique, "task_valid": not unique}


def correct_task_rows(
    analysis: dict[str, Any], output_root: pathlib.Path, records: Sequence[Mapping[str, Any]]
) -> None:
    positions = {
        record["scene_id"]: record
        for record in records
        if record["phase"] == "measured"
        and record["run_class"] == "positions_fidelity"
        and record["method"] == "reference"
    }
    rows = analysis["fidelity"]["task_rows"]
    if len(rows) != 200 or len(positions) != 200:
        raise study.StudyError("task-validity correction inventory differs")
    for row in rows:
        record = positions.get(row["scene_id"])
        if record is None:
            raise study.StudyError(f"positions summary missing for {row['scene_id']}")
        summary = json.loads((output_root / record["summary"]["path"]).read_bytes())
        derived = derive_scene_task_validity(summary, row, int(record["robot_count"]))
        row.update(derived)
    analysis["fidelity"]["task_valid_scene_count"] = sum(row["task_valid"] for row in rows)
    analysis["fidelity"]["task_invalid_scene_count"] = sum(not row["task_valid"] for row in rows)


def analyze(output_root: pathlib.Path, input_root: pathlib.Path) -> dict[str, Any]:
    study._positions_sweeps = v2.positions_sweeps
    if not hasattr(study, "_v2_original_require_identity"):
        study._v2_original_require_identity = study.require_identity
    study.require_identity = v2._historical_protocol_identity
    original_writer = study.write_outputs
    study.write_outputs = lambda _root, _analysis: None
    try:
        analysis = study.analyze_session(output_root, input_root)
    finally:
        study.write_outputs = original_writer
    _inputs, _schedule, records = study.read_session(output_root)
    correct_task_rows(analysis, output_root, records)
    path = pathlib.Path(__file__)
    analysis["analyzer"] = {
        "bytes": path.stat().st_size,
        "path": path.relative_to(REPO).as_posix(),
        "schema_version": ANALYZER_SCHEMA,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    analysis["retained_infrastructure_repairs"] = [
        {
            "attempt": 1,
            "change": "accept registered adjacent pre_update(t)/post_update(t+1) sweep convention",
            "changed_scientific_inputs_or_statistics": False,
        },
        {
            "attempt": 2,
            "change": "authenticate original preregistration bytes from frozen Git commit after audit annotation",
            "changed_scientific_inputs_or_statistics": False,
        },
        {
            "attempt": 3,
            "change": "derive task validity from exact summary counters and contact-free geometry; never copy helper authorization lock",
            "changed_resource_results_or_statistics": False,
        },
    ]
    analysis.pop("retained_infrastructure_repair", None)
    original_writer(output_root, analysis)
    return analysis


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=pathlib.Path, required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        analyze(args.output_root.resolve(), args.input_root.resolve())
        return 0
    except (
        study.StudyError,
        study.inputs_module.InputError,
        study.geometry.GeometryError,
        study.trace_compare.TraceFormatError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as error:
        print(f"multi-map v3 analysis failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
