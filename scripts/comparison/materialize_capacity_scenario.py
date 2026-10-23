#!/usr/bin/env python3
"""Materialize one registered exact-N capacity scenario without overwriting.

This utility performs geometry-only prefix extraction.  It never launches a
simulator.  The requested count must be present in the frozen manifest, the
bank and materializer hashes must match, and the derived bytes must match the
pre-registered SHA-256 before an exclusive-create output is attempted.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from generate_environments import (
    CAPACITY_MATERIALIZER_RELATIVE_PATH,
    canonical_json_bytes,
    capacity_scenario_prefix,
    sha256_bytes,
    validate_capacity_scenario,
)


def _verified_manifest(environment_root: Path) -> dict:
    manifest_path = environment_root / "manifest.json"
    sidecar_path = environment_root / "manifest.sha256"
    manifest_data = manifest_path.read_bytes()
    expected = sidecar_path.read_text(encoding="ascii").split()[0]
    actual = sha256_bytes(manifest_data)
    if actual != expected:
        raise ValueError(
            f"environment manifest hash mismatch: {actual} != {expected}"
        )
    manifest = json.loads(manifest_data)
    registered = manifest["capacity_suite"]["prefix_materializer"]
    if registered["path"] != CAPACITY_MATERIALIZER_RELATIVE_PATH:
        raise ValueError("manifest registered a different prefix materializer")
    actual_materializer = sha256_bytes(Path(__file__).read_bytes())
    if actual_materializer != registered["sha256"]:
        raise ValueError("capacity prefix materializer hash mismatch")
    if not registered.get("exclusive_create_no_overwrite"):
        raise ValueError("manifest does not require exclusive-create output")
    return manifest


def _case_record(manifest: dict, family: str, split: str) -> dict:
    matches = [
        case
        for case in manifest["environments"]
        if case["family"] == family and case["split"] == split
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one registered case for {family}/{split}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _verified_bank(environment_root: Path, case: dict) -> dict:
    root = environment_root.resolve()
    record = case["capacity"]["bank"]
    bank_path = (environment_root / record["path"]).resolve()
    try:
        bank_path.relative_to(root)
    except ValueError as error:
        raise ValueError("capacity bank path escapes environment root") from error
    data = bank_path.read_bytes()
    if len(data) != record["bytes"]:
        raise ValueError("capacity bank byte count mismatch")
    if sha256_bytes(data) != record["sha256"]:
        raise ValueError("capacity bank hash mismatch")
    bank = json.loads(data)
    maximum = case["capacity"]["family_frozen_max_robot_count"]
    validate_capacity_scenario(bank, maximum)
    if bank["metadata"]["capacity_bank_id"] != case["capacity"]["bank_id"]:
        raise ValueError("capacity bank identity mismatch")
    return bank


def materialize_registered_prefix(
    environment_root: Path,
    family: str,
    split: str,
    robot_count: int,
    output: Path,
) -> str:
    manifest = _verified_manifest(environment_root)
    case = _case_record(manifest, family, split)
    maximum = case["capacity"]["family_frozen_max_robot_count"]
    if robot_count > maximum:
        raise ValueError(
            f"requested N={robot_count} exceeds frozen {family} ceiling "
            f"N={maximum}"
        )
    registered = {
        record["robot_count"]: record
        for record in case["capacity"]["prefix_scenarios"]
    }
    if robot_count not in registered:
        allowed = ", ".join(str(count) for count in sorted(registered))
        raise ValueError(
            f"requested N={robot_count} is not registered; allowed: {allowed}"
        )

    bank = _verified_bank(environment_root, case)
    scenario = capacity_scenario_prefix(bank, robot_count)
    validate_capacity_scenario(scenario, robot_count)
    data = canonical_json_bytes(scenario)
    expected = registered[robot_count]
    actual_hash = sha256_bytes(data)
    if len(data) != expected["bytes"] or actual_hash != expected["sha256"]:
        raise ValueError("derived exact-N prefix does not match registered bytes")

    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o444)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise FileExistsError(
                f"refusing to overwrite existing output: {output}"
            ) from error
        raise
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(output, 0o444)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return actual_hash


def default_environment_root() -> Path:
    return Path(__file__).resolve().parents[2] / "comparison" / "environments"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument(
        "--split", required=True, choices=("calibration", "validation")
    )
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--environment-root",
        type=Path,
        default=default_environment_root(),
    )
    args = parser.parse_args(argv)
    try:
        digest = materialize_registered_prefix(
            args.environment_root,
            args.family,
            args.split,
            args.count,
            args.output,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"capacity materialization failed: {error}", file=sys.stderr)
        return 1
    print(f"{digest}  {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
