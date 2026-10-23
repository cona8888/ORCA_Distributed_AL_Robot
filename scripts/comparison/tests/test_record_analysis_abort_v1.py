from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.comparison import record_analysis_abort_v1 as RECORDER


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


class AbortFixture:
    def __init__(self, directory: Path) -> None:
        self.repository = (directory / "repository").resolve()
        self.repository.mkdir()
        git(self.repository, "init", "-q")

        self.analyzer = self.repository / RECORDER.ANALYZER_RELATIVE
        self.write(self.analyzer, b"# exact fixture analyzer\n")
        self.analyzer_sha256 = digest(self.analyzer)
        self.analyzer_bytes = self.analyzer.stat().st_size

        self.prior_root = self.repository / RECORDER.PRIOR_FAILED_RELATIVE
        self.prior_root.mkdir(parents=True)
        self.prior_pairs = self.write(
            self.prior_root / "pairs.jsonl", b""
        )
        self.prior_runs = self.write(
            self.prior_root / "runs.jsonl", b""
        )

        self.session = self.repository / RECORDER.SESSION_RELATIVE
        self.session.mkdir(parents=True)
        plan = {
            "schema_version": "dancers-laptop-comparison-plan-v3",
            "amendment_id": RECORDER.AMENDMENT_ID,
            "execution_epoch_id": RECORDER.EXECUTION_EPOCH_ID,
            "cohort_id": RECORDER.COHORT_ID,
            "mode": "fidelity",
            "result_root": str(self.session),
            "analyzer_sha256": self.analyzer_sha256,
            "artifacts": {
                "analysis_revision": {
                    "path": str(self.analyzer),
                    "sha256": self.analyzer_sha256,
                },
                "prior_failed_pairs": {
                    "path": str(self.prior_pairs),
                    "sha256": RECORDER.EMPTY_SHA256,
                },
                "prior_failed_runs": {
                    "path": str(self.prior_runs),
                    "sha256": RECORDER.EMPTY_SHA256,
                },
            },
        }
        self.write_json(self.session / "frozen_plan.json", plan)
        plan_sha256 = digest(self.session / "frozen_plan.json")
        preflight = {
            "schema_version": "dancers-laptop-comparison-plan-v3",
            "amendment_id": RECORDER.AMENDMENT_ID,
            "execution_epoch_id": RECORDER.EXECUTION_EPOCH_ID,
            "mode": "fidelity",
            "plan_sha256": plan_sha256,
            "artifacts": {
                "analysis_revision": {
                    "path": str(self.analyzer),
                    "sha256": self.analyzer_sha256,
                    "bytes": self.analyzer_bytes,
                },
                "prior_failed_pairs": {
                    "path": str(self.prior_pairs),
                    "sha256": RECORDER.EMPTY_SHA256,
                    "bytes": 0,
                },
                "prior_failed_runs": {
                    "path": str(self.prior_runs),
                    "sha256": RECORDER.EMPTY_SHA256,
                    "bytes": 0,
                },
            },
        }
        self.write_json(self.session / "preflight.json", preflight)
        self.write_json(
            self.session / "session.json",
            {
                "schema_version": "dancers-laptop-comparison-session-v3",
                "execution_epoch_id": RECORDER.EXECUTION_EPOCH_ID,
                "cohort_id": RECORDER.COHORT_ID,
                "result_root": str(self.session),
                "attempt_count": 40,
                "pair_count": 20,
                "planned_method_attempt_slots": 40,
                "executed_method_attempt_slots": 40,
                "completed_method_attempt_slots": 40,
                "valid_method_attempt_slots": 40,
                "planned_pair_blocks": 20,
                "executed_pair_blocks": 20,
                "valid_pair_blocks": 0,
                "all_attempts_completed": True,
                "all_pairs_valid": False,
                "stopped_early": False,
            },
        )
        self.write_json(self.session / "run_manifest.json", {"fixture": True})
        self.write(
            self.session / "runs.jsonl",
            b"".join(
                RECORDER.canonical_json_bytes({"attempt": index})
                for index in range(40)
            ),
        )
        self.write(self.session / "runs.csv", b"fixture_header\n")
        self.write(
            self.session / "pairs.jsonl",
            b"".join(
                RECORDER.canonical_json_bytes({"block": index})
                for index in range(20)
            ),
        )

        attempts = self.session / "attempts"
        attempts.mkdir()
        sequence = 0
        for count in (192, 256):
            for seed in range(1, 11):
                for method in ("reference", "optimized"):
                    sequence += 1
                    name = (
                        f"{sequence:06d}__fractal_high-calibration-"
                        f"seed-{seed}-n-{count}__rep-001__{method}"
                    )
                    attempt = attempts / name
                    attempt.mkdir()
                    for filename in RECORDER.ATTEMPT_FILENAMES:
                        self.write(
                            attempt / filename,
                            f"{name}/{filename}\n".encode("utf-8"),
                        )

        blocks = self.session / "blocks"
        blocks.mkdir()
        for count in (192, 256):
            for seed in range(1, 11):
                name = (
                    f"fractal_high-calibration-seed-{seed}-n-{count}"
                    "__rep-001"
                )
                block = blocks / name
                block.mkdir()
                for filename in RECORDER.BLOCK_FILENAMES:
                    self.write(
                        block / filename,
                        f"{name}/{filename}\n".encode("utf-8"),
                    )

        self.core = {
            filename: {
                "bytes": (self.session / filename).stat().st_size,
                "sha256": digest(self.session / filename),
            }
            for filename in RECORDER.EXPECTED_CORE_ARTIFACTS
        }
        entries = self.raw_inventory()
        self.inventory_sha256 = hashlib.sha256(
            RECORDER.canonical_json_bytes(entries)
        ).hexdigest()

        git(self.repository, "add", ".")
        git(
            self.repository,
            "-c",
            "user.name=Abort Fixture",
            "-c",
            "user.email=abort@example.invalid",
            "commit",
            "-q",
            "-m",
            "sealed fixture evidence",
        )
        self.head = git(self.repository, "rev-parse", "HEAD")

    @staticmethod
    def write(path: Path, data: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.resolve()

    @staticmethod
    def write_json(path: Path, value: object) -> Path:
        return AbortFixture.write(
            path, RECORDER.canonical_json_bytes(value)
        )

    def raw_inventory(self) -> list[dict[str, object]]:
        entries = []
        for path in sorted(
            self.session.rglob("*"),
            key=lambda item: item.relative_to(self.session).as_posix(),
        ):
            if path.is_file() and not path.is_symlink():
                entries.append(
                    {
                        "path": path.relative_to(self.session).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": digest(path),
                    }
                )
        return entries

    def constants(self):
        return mock.patch.multiple(
            RECORDER,
            EXPECTED_EVIDENCE_COMMIT=self.head,
            EXPECTED_ANALYZER_SHA256=self.analyzer_sha256,
            EXPECTED_ANALYZER_BYTES=self.analyzer_bytes,
            EXPECTED_CORE_ARTIFACTS=self.core,
            EXPECTED_INVENTORY_SHA256=self.inventory_sha256,
        )

    @property
    def analysis(self) -> Path:
        return self.repository / RECORDER.ANALYSIS_RELATIVE

    @property
    def receipt(self) -> Path:
        return self.repository / RECORDER.RECEIPT_RELATIVE

    @property
    def abort(self) -> Path:
        return self.repository / RECORDER.ABORT_RELATIVE


class RecordAnalysisAbortV1Tests(unittest.TestCase):
    def test_success_is_deterministic_exclusive_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = AbortFixture(Path(temporary))
            before = {
                path.relative_to(fixture.session): (
                    digest(path),
                    path.stat().st_mode,
                    path.stat().st_mtime_ns,
                )
                for path in fixture.session.rglob("*")
                if path.is_file()
            }
            with fixture.constants():
                first, first_path = RECORDER.build_analysis_abort_record(
                    fixture.repository
                )
                second, second_path = RECORDER.build_analysis_abort_record(
                    fixture.repository
                )
                self.assertEqual(first, second)
                self.assertEqual(first_path, fixture.abort)
                self.assertEqual(second_path, fixture.abort)
                result = RECORDER.record_analysis_abort(fixture.repository)

            self.assertEqual(result.path, fixture.abort)
            self.assertEqual(
                result.inventory_sha256, fixture.inventory_sha256
            )
            self.assertEqual(
                result.sha256,
                hashlib.sha256(
                    RECORDER.canonical_json_bytes(first)
                ).hexdigest(),
            )
            self.assertEqual(
                fixture.abort.read_bytes(),
                RECORDER.canonical_json_bytes(first),
            )
            self.assertEqual(
                stat.S_IMODE(fixture.abort.stat().st_mode), 0o444
            )
            self.assertFalse(fixture.analysis.exists())
            self.assertFalse(fixture.receipt.exists())
            self.assertFalse(first["receipt_issued"])
            self.assertTrue(first["timing_locked"])
            self.assertEqual(first["sealed_session"]["file_count"], 327)
            self.assertEqual(first["sealed_session"]["attempt_count"], 40)
            self.assertEqual(first["sealed_session"]["block_count"], 20)
            self.assertEqual(first["sealed_session"]["symlink_count"], 0)
            self.assertEqual(
                first["analysis_failure"]["message"],
                RECORDER.EXACT_ANALYSIS_ERROR,
            )
            self.assertEqual(
                set(first["core_artifacts"]),
                set(RECORDER.EXPECTED_CORE_ARTIFACTS),
            )
            for record in first["prior_empty_ledgers"].values():
                self.assertEqual(record["bytes"], 0)
                self.assertEqual(record["sha256"], RECORDER.EMPTY_SHA256)

            after = {
                path.relative_to(fixture.session): (
                    digest(path),
                    path.stat().st_mode,
                    path.stat().st_mtime_ns,
                )
                for path in fixture.session.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)
            original = fixture.abort.read_bytes()
            with fixture.constants(), self.assertRaises(FileExistsError):
                RECORDER.record_analysis_abort(fixture.repository)
            self.assertEqual(fixture.abort.read_bytes(), original)

    def test_refuses_either_planned_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = AbortFixture(Path(temporary))
            for label, path in (
                ("analysis", fixture.analysis),
                ("receipt", fixture.receipt),
            ):
                with self.subTest(output=label):
                    path.write_bytes(b"must not overwrite\n")
                    with fixture.constants(), self.assertRaises(
                        FileExistsError
                    ):
                        RECORDER.record_analysis_abort(fixture.repository)
                    self.assertFalse(fixture.abort.exists())
                    self.assertEqual(path.read_bytes(), b"must not overwrite\n")
                    path.unlink()

    def test_requires_exact_evidence_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = AbortFixture(Path(temporary))
            with mock.patch.object(
                RECORDER,
                "EXPECTED_EVIDENCE_COMMIT",
                "0" * 40,
            ), self.assertRaisesRegex(
                RECORDER.AnalysisAbortError, "HEAD must equal"
            ):
                RECORDER.record_analysis_abort(fixture.repository)
            self.assertFalse(fixture.abort.exists())

    def test_rejects_symlink_and_wrong_file_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = AbortFixture(Path(temporary))
            link = fixture.session / "unexpected-link"
            link.symlink_to(fixture.session / "session.json")
            with fixture.constants(), self.assertRaisesRegex(
                RECORDER.AnalysisAbortError, "symbolic link"
            ):
                RECORDER.record_analysis_abort(fixture.repository)
            link.unlink()

            victim = next(
                (fixture.session / "attempts").glob("*/stderr.log")
            )
            victim.unlink()
            with fixture.constants(), self.assertRaisesRegex(
                RECORDER.AnalysisAbortError, "326"
            ):
                RECORDER.record_analysis_abort(fixture.repository)
            self.assertFalse(fixture.abort.exists())

    def test_rejects_nonempty_prior_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = AbortFixture(Path(temporary))
            fixture.prior_pairs.write_bytes(b"not empty\n")
            with fixture.constants(), self.assertRaisesRegex(
                RECORDER.AnalysisAbortError,
                "must remain the registered empty ledger",
            ):
                RECORDER.record_analysis_abort(fixture.repository)
            self.assertFalse(fixture.abort.exists())

    def test_rejects_core_or_analyzer_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = AbortFixture(Path(temporary))
            core = fixture.session / "runs.csv"
            core.write_bytes(b"changed\n")
            with fixture.constants(), self.assertRaisesRegex(
                RECORDER.AnalysisAbortError,
                "inventory SHA-256 changed|core artifact changed",
            ):
                RECORDER.record_analysis_abort(fixture.repository)
            self.assertFalse(fixture.abort.exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = AbortFixture(Path(temporary))
            fixture.analyzer.write_bytes(b"changed analyzer\n")
            with fixture.constants(), self.assertRaisesRegex(
                RECORDER.AnalysisAbortError, "analyzer bytes changed"
            ):
                RECORDER.record_analysis_abort(fixture.repository)
            self.assertFalse(fixture.abort.exists())


if __name__ == "__main__":
    unittest.main()
