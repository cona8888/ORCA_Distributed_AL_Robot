from pathlib import Path
import json,unittest
R=Path(__file__).resolve().parents[3]
class Tests(unittest.TestCase):
 def test_authority(self):
  v=json.loads((R/"docs/simulator/headless_validation_epoch_5_execution_repair_3_v1.json").read_bytes());self.assertTrue(v["validation_execution_authorized"]);self.assertFalse(v["benchmark_execution_authorized"]);self.assertEqual(v["prior_attempts"]["task_trajectory_count"],0)
if __name__=="__main__":unittest.main()
