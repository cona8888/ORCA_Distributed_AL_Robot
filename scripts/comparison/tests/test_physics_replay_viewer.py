from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import html
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "comparison"
    / "build_physics_replay_viewer.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_physics_replay_viewer", MODULE_PATH
)
assert SPEC and SPEC.loader
VIEWER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VIEWER
SPEC.loader.exec_module(VIEWER)


def _embedded_bundle(html_bytes: bytes) -> dict:
    text = html_bytes.decode("utf-8")
    match = re.search(
        r'<template id="replayPayload">([A-Za-z0-9+/=]+)</template>',
        text,
    )
    assert match is not None
    return json.loads(base64.b64decode(match.group(1), validate=True))


def _run_driver(
    driver: Path,
    output: Path,
    *,
    detail: str,
    threads: int,
    cwd: Path,
) -> None:
    environment = os.environ.copy()
    environment["OMP_NUM_THREADS"] = str(threads)
    environment["OMP_DYNAMIC"] = "FALSE"
    completed = subprocess.run(
        [str(driver), str(output), detail, "success"],
        cwd=cwd,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"analytic evidence driver failed: {completed.stderr}"
        )
    if completed.stdout != "analytic physics evidence: PASS\n" or completed.stderr:
        raise RuntimeError("analytic evidence driver lifecycle was not clean")


class PhysicsReplayViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_driver = os.environ.get("DANCERS_PHYSICS_EVIDENCE_ANALYTIC_BINARY")
        if not raw_driver:
            raise unittest.SkipTest(
                "DANCERS_PHYSICS_EVIDENCE_ANALYTIC_BINARY is not configured"
            )
        cls.driver = Path(raw_driver).resolve(strict=True)
        private_tmp = Path("/private/tmp") if Path("/private/tmp").is_dir() else None
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="dancers-physics-replay-viewer-test-",
            dir=private_tmp,
        )
        cls.root = Path(cls.temporary.name).resolve(strict=True)
        os.chmod(cls.root, 0o700)
        cls.left = cls.root / "run-a.jsonl"
        cls.right = cls.root / "run-b.jsonl"
        cls.digest = cls.root / "digest.jsonl"
        _run_driver(cls.driver, cls.left, detail="replay", threads=1, cwd=cls.root)
        _run_driver(cls.driver, cls.right, detail="replay", threads=2, cwd=cls.root)
        _run_driver(cls.driver, cls.digest, detail="digest", threads=1, cwd=cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _build(self, **overrides: object) -> object:
        arguments = {
            "title": "Analytic synchronized replay",
            "left_label": "Run A",
            "right_label": "Run B",
            "allow_analytic_fallback": True,
        }
        arguments.update(overrides)
        return VIEWER.build_physics_replay_viewer(
            self.left,
            self.right,
            **arguments,
        )

    def test_deterministic_verified_analytic_viewer_contract(self) -> None:
        first = self._build()
        second = self._build()
        self.assertEqual(first.html_bytes, second.html_bytes)
        self.assertEqual(first.frame_count, 21)
        self.assertEqual(first.robot_samples, 126)
        self.assertGreater(first.contact_items, 0)
        self.assertLessEqual(first.output_bytes, 64 * 1024 * 1024)

        payload = _embedded_bundle(first.html_bytes)
        self.assertEqual(
            payload["schema"], "dancers-planar-physics-replay-bundle-v1"
        )
        self.assertEqual(payload["left"]["display_only_label"], "Run A")
        self.assertEqual(payload["right"]["display_only_label"], "Run B")
        self.assertFalse(payload["classification"]["scientific_evidence"])
        self.assertFalse(payload["classification"]["timing_eligible"])
        self.assertFalse(
            payload["classification"]["display_labels_authenticate_methods"]
        )
        self.assertFalse(payload["verification"]["solver_launched_by_exporter"])
        self.assertFalse(payload["scene"]["raster"]["available"])
        self.assertEqual(
            [frame["time_ms"] for frame in payload["left"]["frames"]],
            list(range(0, 201, 10)),
        )
        self.assertIsNone(payload["left"]["frames"][0]["held_command_tick"])
        self.assertEqual(
            [frame["held_command_tick"] for frame in payload["left"]["frames"][1:]],
            [0] * 10 + [1] * 10,
        )
        self.assertEqual(payload["left"]["frames"][10]["route_boundary_index"], 1)
        self.assertEqual(payload["left"]["frames"][20]["route_boundary_index"], 2)

        text = first.html_bytes.decode("utf-8")
        for required in (
            'id="playPause"',
            'id="stepBack"',
            'id="stepForward"',
            'id="timeline"',
            'id="speed"',
            'id="splitMode"',
            'id="overlayMode"',
            'id="showRoutes"',
            'id="showTrails"',
            'id="showVectors"',
            'id="showContacts"',
            "OFFLINE PHYSICS REPLAY",
            "NOT LIVE SOLVER TIMING",
            "RENDERING EXCLUDED FROM COMPUTE",
            "MODEL DIAGNOSTIC — NOT PHYSICAL SAFETY",
            "VIEWER NEVER LAUNCHES SOLVER",
            "ANALYTIC FIXTURE — SIDECARS UNAVAILABLE",
            "controller boundary",
            "held_command_tick",
            "command_frames",
            "contact keys for ${bundle.left.display_only_label}: ${leftContact.keySummary}",
            "contact keys for ${bundle.right.display_only_label}: ${rightContact.keySummary}",
        ):
            self.assertIn(required, text)
        self.assertNotRegex(text, r"(?:https?|wss?)://")
        self.assertNotRegex(
            text,
            r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|Worker)\s*\(",
        )
        self.assertNotRegex(text, r"\sstyle=")
        self.assertNotIn(str(self.root), text)

    def test_csp_hashes_exact_embedded_style_and_script(self) -> None:
        text = self._build().html_bytes.decode("utf-8")
        csp_match = re.search(
            r'http-equiv="Content-Security-Policy" content="([^"]+)"', text
        )
        style_match = re.search(r"<style>(.*?)</style>", text, re.DOTALL)
        script_match = re.search(r"<script>(.*?)</script>", text, re.DOTALL)
        self.assertIsNotNone(csp_match)
        self.assertIsNotNone(style_match)
        self.assertIsNotNone(script_match)
        csp = html.unescape(csp_match.group(1))
        for directive in (
            "default-src 'none'",
            "connect-src 'none'",
            "frame-src 'none'",
            "object-src 'none'",
            "worker-src 'none'",
        ):
            self.assertIn(directive, csp)
        style_token = "sha256-" + base64.b64encode(
            hashlib.sha256(style_match.group(1).encode("utf-8")).digest()
        ).decode("ascii")
        script_token = "sha256-" + base64.b64encode(
            hashlib.sha256(script_match.group(1).encode("utf-8")).digest()
        ).decode("ascii")
        self.assertIn(f"style-src '{style_token}'", csp)
        self.assertIn(f"script-src '{script_token}'", csp)
        self.assertNotRegex(text, r"__[A-Z][A-Z0-9_]*__")
        self.assertNotIn("light-dark(", style_match.group(1))
        self.assertNotRegex(
            style_match.group(1),
            r"\.hud span:nth-child\(odd\)\s*\{[^}]*display\s*:\s*none",
        )

    def test_fail_closed_input_and_bundle_policy(self) -> None:
        with self.assertRaises(VIEWER.replay_export.ReplayBundleError):
            VIEWER.build_physics_replay_viewer(
                self.left,
                self.right,
                allow_analytic_fallback=False,
            )
        with self.assertRaises(VIEWER.replay_export.ReplayBundleError):
            VIEWER.build_physics_replay_viewer(
                self.digest,
                self.right,
                allow_analytic_fallback=True,
            )

        result = self._build()
        bundle = _embedded_bundle(result.html_bytes)
        changed = copy.deepcopy(bundle)
        changed["classification"]["scientific_evidence"] = True
        with self.assertRaises(VIEWER.PhysicsReplayViewerError):
            VIEWER.render_physics_replay_bundle(
                changed,
                allow_analytic_fallback=True,
            )
        changed = copy.deepcopy(bundle)
        changed["left"]["frames"][7]["time_ms"] += 1
        with self.assertRaises(VIEWER.PhysicsReplayViewerError):
            VIEWER.render_physics_replay_bundle(
                changed,
                allow_analytic_fallback=True,
            )
        with self.assertRaises(VIEWER.PhysicsReplayViewerError):
            VIEWER.render_physics_replay_bundle(
                bundle,
                allow_analytic_fallback=True,
                max_bundle_bytes=100,
            )
        with self.assertRaises(VIEWER.PhysicsReplayViewerError):
            VIEWER.render_physics_replay_bundle(
                bundle,
                allow_analytic_fallback=True,
                max_html_bytes=result.output_bytes - 1,
            )
        raised_limits = {
            "max_frames": VIEWER.DEFAULT_MAX_FRAMES + 1,
            "max_bundle_bytes": VIEWER.DEFAULT_MAX_BUNDLE_BYTES + 1,
            "max_html_bytes": VIEWER.DEFAULT_MAX_HTML_BYTES + 1,
            "max_robot_samples": VIEWER.DEFAULT_MAX_ROBOT_SAMPLES + 1,
            "max_contact_items": VIEWER.DEFAULT_MAX_CONTACT_ITEMS + 1,
        }
        for name, value in raised_limits.items():
            with self.subTest(limit=name), self.assertRaises(
                VIEWER.PhysicsReplayViewerError
            ):
                VIEWER.render_physics_replay_bundle(
                    bundle,
                    allow_analytic_fallback=True,
                    **{name: value},
                )
            with self.subTest(build_limit=name), self.assertRaises(
                VIEWER.PhysicsReplayViewerError
            ):
                VIEWER.build_physics_replay_viewer(
                    self.left,
                    self.right,
                    allow_analytic_fallback=True,
                    **{name: value},
                )

    def test_create_exclusive_private_html_publication(self) -> None:
        output = self.root / "viewer.html"
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = [
            str(self.left),
            str(self.right),
            str(output),
            "--analytic-fixture-only",
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(VIEWER.main(arguments), 0)
        self.assertEqual(stderr.getvalue(), "")
        initial = output.read_bytes()
        status = output.stat()
        self.assertTrue(stat.S_ISREG(status.st_mode))
        self.assertEqual(stat.S_IMODE(status.st_mode), 0o600)
        self.assertLessEqual(len(initial), 64 * 1024 * 1024)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(VIEWER.main(arguments), 2)
        self.assertEqual(output.read_bytes(), initial)
        self.assertIn("output already exists", stderr.getvalue())

        with self.assertRaises(VIEWER.PhysicsReplayViewerError):
            VIEWER._secure_output_parent(
                REPOSITORY_ROOT / "docs" / "simulator" / "forbidden-viewer.html",
                REPOSITORY_ROOT,
            )
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o720)
        os.chmod(unsafe, 0o720)
        with self.assertRaises(VIEWER.PhysicsReplayViewerError):
            VIEWER._secure_output_parent(
                unsafe / "viewer.html",
                REPOSITORY_ROOT,
            )

    def test_builder_has_no_independent_evidence_or_process_path(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("import export_physics_replay_bundle_v1", source)
        self.assertNotIn("import physics_evidence_v1", source)
        self.assertNotIn("import compare_physics_evidence_v1", source)
        for forbidden in (
            "import subprocess",
            "os.system",
            "os.popen",
            "subprocess.run",
            "subprocess.Popen",
            "posix_spawn",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
