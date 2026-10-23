from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/analyze_headless_epoch_v2.py"
SPEC = importlib.util.spec_from_file_location("_headless_analysis_v2_test", SOURCE)
assert SPEC is not None and SPEC.loader is not None
V2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V2)


class HeadlessAnalysisRemediationTests(unittest.TestCase):
    def test_addendum_and_all_bound_inputs_are_exact(self) -> None:
        addendum = V2.load_addendum(ROOT)
        V2._verify_bindings(ROOT, addendum)

    def test_exact_repair_accepts_a_real_absolute_posix_path(self) -> None:
        namespace = V2._load_original_analyzer(ROOT, V2.load_addendum(ROOT))
        concrete = Path(os.path.realpath(tempfile.gettempdir()))
        self.assertIs(type(concrete), namespace["Path"])
        with self.assertRaises(KeyError) as captured:
            namespace["analyze_fidelity_and_task"](
                {"plan": {}, "repository_root": concrete},
                {"session_root": concrete},
            )
        self.assertEqual(captured.exception.args, ("geometry_namespace",))

    def test_preflight_never_analyzes_or_writes(self) -> None:
        with mock.patch.object(V2, "run_analysis", side_effect=AssertionError), mock.patch.object(
            V2, "publish_create_exclusive", side_effect=AssertionError
        ):
            self.assertEqual(V2.main(["--preflight-only"]), 0)

    def test_no_default_execution_mode(self) -> None:
        with self.assertRaises(SystemExit):
            V2.main([])


if __name__ == "__main__":
    unittest.main()
