from pathlib import Path
import json,unittest
R=Path(__file__).resolve().parents[3]
class Tests(unittest.TestCase):
 def test_authority(self):
  v=json.loads((R/"docs/simulator/headless_timing_calibration_epoch_4_execution_v1.json").read_bytes());self.assertTrue(v["timing_execution_authorized"]);self.assertFalse(v["validation_execution_authorized"]);self.assertFalse(v["benchmark_execution_authorized"]);self.assertEqual(v["expected_process_count"],610)
if __name__=="__main__":unittest.main()
