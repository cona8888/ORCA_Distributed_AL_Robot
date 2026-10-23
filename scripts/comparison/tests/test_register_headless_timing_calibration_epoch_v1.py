from pathlib import Path
import importlib.util,sys,unittest
R=Path(__file__).resolve().parents[3];P=R/"scripts/comparison/register_headless_timing_calibration_epoch_v1.py";S=importlib.util.spec_from_file_location("_tr",P);M=importlib.util.module_from_spec(S);sys.modules[S.name]=M;S.loader.exec_module(M)
class Tests(unittest.TestCase):
 def test_prepare(self):
  c=M.prepare(R);self.assertTrue(c.value["classification"]["plan_registered"]);self.assertFalse(c.value["classification"]["timing_execution_authorized"]);self.assertEqual(len(c.value["scenes"]),100)
 def test_guard(self):
  with self.assertRaisesRegex(M.RegistrationError,"disabled"):M.main()
if __name__=="__main__":unittest.main()
