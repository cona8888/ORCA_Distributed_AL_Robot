from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'scripts/comparison/repair_headless_validation_raster_plan_v1.py';S=importlib.util.spec_from_file_location('repair',P);R=importlib.util.module_from_spec(S);S.loader.exec_module(R)
class RepairTests(unittest.TestCase):
 def test_live_superseding_plan_has_exact_repaired_rasters(self):
  raw=(ROOT/R.OLD['path']).read_bytes();new=json.loads(raw);self.assertEqual(hashlib.sha256(raw).hexdigest(),'b3a30c33581382014e8a0e35b1c9ce84f7e7de4daffddff18eefea0dd3e459e8');self.assertEqual(len(new['scenes']),200)
  for scene in new['scenes']:self.assertEqual(scene['effective_runtime_raster']['sha256'],R.RASTERS[scene['family']])
  self.assertTrue(new['status']['execution_ready']);self.assertTrue(new['classification']['validation_execution_authorized'])
  with self.assertRaisesRegex(ValueError,'old plan identity differs'):R.compile_repair(ROOT)
 def test_cli_is_nonauthorizing(self):self.assertEqual(R.main(),1)
if __name__=='__main__':unittest.main()
