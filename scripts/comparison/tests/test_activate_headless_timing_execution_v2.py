from pathlib import Path
import json,unittest
R=Path(__file__).resolve().parents[3]
class Tests(unittest.TestCase):
 def test_repair(self):
  v=json.loads((R/"docs/simulator/headless_timing_calibration_epoch_4_execution_repair_v1.json").read_bytes());self.assertEqual(v["prior_attempt"]["simulator_process_count"],0);self.assertTrue(v["timing_execution_authorized"]);self.assertFalse(v["validation_execution_authorized"])
if __name__=="__main__":unittest.main()
