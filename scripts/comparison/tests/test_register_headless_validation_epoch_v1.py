#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/register_headless_validation_epoch_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_validation_registrar", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ValidationRegistrarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = MODULE.prepare_registered_compilation(ROOT)

    def test_registered_candidate_is_exact_but_execution_locked(self) -> None:
        value = self.compilation.value
        self.assertTrue(value["classification"]["plan_registered"])
        self.assertFalse(value["classification"]["validation_execution_authorized"])
        self.assertFalse(value["status"]["execution_ready"])
        self.assertEqual(len(self.compilation.files), 513)

    def test_publication_is_disabled(self) -> None:
        with self.assertRaisesRegex(MODULE.RegistrationError, "disabled"):
            MODULE.main()


if __name__ == "__main__":
    unittest.main()
