from pathlib import Path
import importlib.util,sys,unittest
R=Path(__file__).resolve().parents[3];P=R/"scripts/comparison/analyze_headless_timing_calibration_epoch_v2.py";S=importlib.util.spec_from_file_location("_tav2",P);M=importlib.util.module_from_spec(S);sys.modules[S.name]=M;S.loader.exec_module(M)
class Tests(unittest.TestCase):
 def test_real_session(self):
  v=M.analyze(R);self.assertEqual(v["analysis"]["pair_count"],300);self.assertEqual(v["analysis"]["warmup_count"],10);self.assertEqual(len(v["analysis"]["group_rows"]),10);self.assertFalse(v["classification"]["capacity_claim_ready"])
 def test_guard(self):self.assertEqual(M.main(),1)
if __name__=="__main__":unittest.main()
