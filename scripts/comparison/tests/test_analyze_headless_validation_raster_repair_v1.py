from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'scripts/comparison/analyze_headless_validation_raster_repair_v1.py';S=importlib.util.spec_from_file_location('a',P);A=importlib.util.module_from_spec(S);S.loader.exec_module(A)
class Tests(unittest.TestCase):
 def test_plan_binding(self):
  m=A.load(ROOT);self.assertEqual(m.PLAN,A.PLAN);self.assertEqual(len(m.load_authenticated_plan(ROOT)['runner_plan'].schedule),2810)
 def test_guard(self):self.assertEqual(A.main(),1)
if __name__=='__main__':unittest.main()
