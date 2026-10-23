from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/"scripts/comparison/freeze_headless_timing_calibration_epoch_v1.py"
S=importlib.util.spec_from_file_location("_test_timing_freezer",P); assert S and S.loader
M=importlib.util.module_from_spec(S);sys.modules[S.name]=M;S.loader.exec_module(M)

class Tests(unittest.TestCase):
    def test_exact_timing_plan(self):
        a=M.compile_epoch(ROOT); b=M.compile_epoch(ROOT)
        self.assertEqual(a.data,b.data); self.assertEqual(len(a.value["scenes"]),100)
        self.assertEqual(a.value["schedule"]["warmups"]["process_count"],10)
        self.assertEqual(a.value["schedule"]["inventories"]["headless_timing"]["measured_process_count"],600)
        self.assertFalse(a.value["classification"]["timing_execution_authorized"])
    def test_publication_disabled(self):
        with self.assertRaisesRegex(M.TimingFreezeError,"disabled"): M.main()
if __name__=="__main__": unittest.main()
