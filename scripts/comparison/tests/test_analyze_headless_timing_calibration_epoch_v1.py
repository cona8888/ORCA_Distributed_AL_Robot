from pathlib import Path
import importlib.util,sys,unittest
R=Path(__file__).resolve().parents[3];P=R/"scripts/comparison/analyze_headless_timing_calibration_epoch_v1.py";S=importlib.util.spec_from_file_location("_ta",P);M=importlib.util.module_from_spec(S);sys.modules[S.name]=M;S.loader.exec_module(M)
class Tests(unittest.TestCase):
 def test_guard(self):
  self.assertTrue(all(v is False for v in M.record(R)["classification"].values()));self.assertEqual(M.main(),1)
  with self.assertRaisesRegex(ValueError,"disabled"):M.analyze()
if __name__=="__main__":unittest.main()
