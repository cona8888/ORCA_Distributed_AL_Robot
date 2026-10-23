from pathlib import Path
import json,unittest
R=Path(__file__).resolve().parents[3]
class Tests(unittest.TestCase):
 def test_authority(self):
  v=json.loads((R/"docs/simulator/headless_timing_calibration_epoch_4_registration_v1.json").read_bytes());self.assertFalse(v["simulator_execution_authorized"]);self.assertFalse(v["result_write_authorized"]);self.assertEqual(v["expected"]["file_count"],1)
if __name__=="__main__":unittest.main()
