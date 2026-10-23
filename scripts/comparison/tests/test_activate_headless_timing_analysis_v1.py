from pathlib import Path
import json,unittest
R=Path(__file__).resolve().parents[3]
class Tests(unittest.TestCase):
 def test_authority(self):
  v=json.loads((R/"docs/simulator/headless_timing_calibration_epoch_4_analysis_v1.json").read_bytes());self.assertFalse(v["scientific_claim_authorized"]);self.assertFalse(v["validation_claim_authorized"]);self.assertFalse(v["benchmark_claim_authorized"])
if __name__=="__main__":unittest.main()
