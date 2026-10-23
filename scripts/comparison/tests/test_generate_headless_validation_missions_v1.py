#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/generate_headless_validation_missions_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_validation_missions", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ValidationMissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = MODULE.compile_validation_missions(ROOT)

    def test_exact_inventory_and_freshness(self) -> None:
        compilation = self.compilation
        self.assertEqual(compilation.manifest["inventory"], {
            "bank_count": 100,
            "file_count_including_manifest": 302,
            "scene_count": 200,
        })
        self.assertEqual(len(compilation.files), 302)
        scenarios = [item.sha256 for item in compilation.files if item.kind == "scenario"]
        self.assertEqual(len(scenarios), 200)
        self.assertEqual(len(set(scenarios)), 200)
        prior = set()
        for identity in MODULE.PRIOR_PLANS:
            plan = MODULE._load_source(ROOT)._parse(
                MODULE._read(ROOT, identity, "prior"), "prior"
            )
            prior.update(scene["effective_scenario_sha256"] for scene in plan["scenes"])
        self.assertTrue(set(scenarios).isdisjoint(prior))

    def test_repeat_is_byte_identical(self) -> None:
        second = MODULE.compile_validation_missions(ROOT)
        self.assertEqual(self.compilation.manifest_bytes, second.manifest_bytes)
        self.assertEqual(
            [(item.relative_path, item.data) for item in self.compilation.files],
            [(item.relative_path, item.data) for item in second.files],
        )

    def test_domains_and_seed_range_are_exact(self) -> None:
        self.assertEqual(MODULE.SEEDS, tuple(range(11, 31)))
        self.assertEqual(self.compilation.manifest["derivation"], {
            "count_enters_root": False,
            "epoch_id": MODULE.EPOCH_ID,
            "root_domain_hex": MODULE.ROOT_DOMAIN.hex(),
            "stream_domain_hex": MODULE.STREAM_DOMAIN.hex(),
        })

    def test_publication_is_disabled(self) -> None:
        with self.assertRaisesRegex(MODULE.ValidationMissionError, "disabled"):
            MODULE.main()


if __name__ == "__main__":
    unittest.main()
