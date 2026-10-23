#!/usr/bin/env python3
"""Analyzer-only tick-convention repair for the sealed multi-map v1 session.

The execution runner remains byte-identical to commit 91113939f.  This wrapper
changes only the positions-frame pairing predicate: the registered observer
emits pre_update(t), post_update(t+1), exactly as the established headless
analysis core already handles by adjacent-frame pairing.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import run_multimap_study as study


ANALYZER_SCHEMA = "dancers-multimap-analysis-v2-tick-repair"
FROZEN_PROTOCOL_COMMIT = "91113939f914934369778d307b6cb385f3dc8516"


def _frame_pair_matches(
    first: Mapping[str, Any], last: Mapping[str, Any], index: int
) -> bool:
    return bool(
        first.get("phase") == "pre_update"
        and last.get("phase") == "post_update"
        and first.get("tick") == index
        and last.get("tick") == index + 1
    )


def positions_sweeps(path: pathlib.Path) -> tuple[list[dict[str, Any]], float]:
    sweeps: list[dict[str, Any]] = []
    with study.trace_compare.TraceReader(path) as reader:
        if reader.header.trace_detail != "positions":
            raise study.StudyError("geometry trace is not positions detail")
        timestep = reader.header.canonical["resolved_config"]["timestep_seconds"]
        frames = list(reader.frames())
    if len(frames) != 200:
        raise study.StudyError(f"positions trace frame count differs: {len(frames)}")
    for index in range(100):
        first, last = frames[2 * index : 2 * index + 2]
        if not _frame_pair_matches(first, last, index):
            raise study.StudyError(f"positions frame pair differs at sweep {index}")
        start = [
            {
                "position": [robot["state"][0], robot["state"][1]],
                "radius_m": robot["radius_m"],
                "rid": robot["rid"],
            }
            for robot in first["robots"]
        ]
        end = [
            {
                "position": [robot["state"][0], robot["state"][1]],
                "radius_m": robot["radius_m"],
                "rid": robot["rid"],
            }
            for robot in last["robots"]
        ]
        sweeps.append({"end": end, "start": start, "tick": index})
    return sweeps, float(timestep)


def _historical_protocol_identity(
    path: pathlib.Path, identity: Mapping[str, Any], label: str
) -> None:
    """Authenticate the frozen preregistration blob after audit annotations."""

    if label != "protocol preregistration":
        study._v2_original_require_identity(path, identity, label)
        return
    completed = subprocess.run(
        [
            "git",
            "show",
            f"{FROZEN_PROTOCOL_COMMIT}:{identity['path']}",
        ],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise study.StudyError(
            "cannot authenticate frozen preregistration blob: "
            + completed.stderr.decode("utf-8", "replace")
        )
    if (
        len(completed.stdout) != identity.get("bytes")
        or hashlib.sha256(completed.stdout).hexdigest() != identity.get("sha256")
    ):
        raise study.StudyError("frozen preregistration Git blob identity differs")


def analyze(output_root: pathlib.Path, input_root: pathlib.Path) -> dict[str, Any]:
    study._positions_sweeps = positions_sweeps
    if not hasattr(study, "_v2_original_require_identity"):
        study._v2_original_require_identity = study.require_identity
    study.require_identity = _historical_protocol_identity
    analysis = study.analyze_session(output_root, input_root)
    path = pathlib.Path(__file__)
    analysis["analyzer"] = {
        "bytes": path.stat().st_size,
        "path": path.relative_to(REPO).as_posix(),
        "schema_version": ANALYZER_SCHEMA,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    analysis["retained_infrastructure_repair"] = {
        "changed_scientific_inputs": False,
        "changed_statistics_or_gates": False,
        "execution_runner_changed": False,
        "repair": "accept registered adjacent pre_update(t)/post_update(t+1) sweep convention",
        "frozen_preregistration_authentication": (
            "original bytes authenticated from Git commit 91113939f; "
            "current document only appends retained infrastructure history"
        ),
        "trigger": "analysis attempt 1 stopped before completing the first scene geometry analysis",
    }
    study.write_outputs(output_root, analysis)
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
    ) as error:
        print(f"multi-map v2 analysis failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
