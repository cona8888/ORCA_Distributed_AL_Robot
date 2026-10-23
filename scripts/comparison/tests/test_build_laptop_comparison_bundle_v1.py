from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'scripts/comparison/build_laptop_comparison_bundle_v1.py';S=importlib.util.spec_from_file_location('bundle',P);B=importlib.util.module_from_spec(S);S.loader.exec_module(B)
class Tests(unittest.TestCase):
 def test_frozen_safe_grid_and_replay_inventory(self):
  self.assertEqual(len(B.SAFE_CELLS),8);self.assertEqual(len(B.REPLAYS),5);self.assertEqual({x[0] for x in B.REPLAYS},{'open','perfect_maze','fractal_low','fractal_medium','fractal_high'})
 def test_index_is_offline_and_scope_labeled(self):
  row={'family':'open','robot_count':8,'geometric_mean_wall_speedup':1.3,'speedup_lower_95':1.2,'minimum_pair_clearance_m':1.0};raw=B.index_bytes([{'file':'open.html','title':'open'}],[row]);self.assertNotIn(b'http:',raw);self.assertNotIn(b'https:',raw);self.assertIn(b'No capacity or population-wide claim',raw)
if __name__=='__main__':unittest.main()
