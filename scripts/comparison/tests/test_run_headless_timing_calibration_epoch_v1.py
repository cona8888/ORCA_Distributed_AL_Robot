from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[3];P=ROOT/"scripts/comparison/run_headless_timing_calibration_epoch_v1.py"
S=importlib.util.spec_from_file_location("_test_timing_runner",P);assert S and S.loader;M=importlib.util.module_from_spec(S);sys.modules[S.name]=M;S.loader.exec_module(M)
class Tests(unittest.TestCase):
 def test_candidate_auth_and_public_guard(self):
  import importlib.util as iu
  f=ROOT/"scripts/comparison/freeze_headless_timing_calibration_epoch_v1.py";s=iu.spec_from_file_location("_f",f);m=iu.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);c=m.compile_epoch(ROOT)
  temp=ROOT/"comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration"
  self.assertEqual(len(c.value["scenes"]),100);self.assertEqual(M.main(),1)
 def test_flatten_denominator(self):
  import importlib.util as iu
  f=ROOT/"scripts/comparison/freeze_headless_timing_calibration_epoch_v1.py";s=iu.spec_from_file_location("_f2",f);m=iu.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);c=m.compile_epoch(ROOT)
  self.assertEqual(len(M._flatten(c.value)),610)
 def test_registered_plan_authenticates_with_external_task_artifacts(self):
  plan=ROOT/"comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration/plan.json"
  authenticated=M.load_authenticated_plan(plan,ROOT,production=True,committed_input_checker=lambda _p,_r:None)
  self.assertEqual(len(authenticated.schedule),610)
if __name__=="__main__":unittest.main()
