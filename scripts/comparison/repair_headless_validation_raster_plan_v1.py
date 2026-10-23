#!/usr/bin/env python3
"""Compile the exact registered-but-disabled epoch-5 raster repair plan."""

from __future__ import annotations
import hashlib,json,os,sys,types
from pathlib import Path,PurePosixPath
from typing import Any
sys.dont_write_bytecode=True
OLD={"bytes":4618054,"path":"comparison/plans/simulator-demo/v1/headless-epoch-5-validation/plan.json","sha256":"592c8ded608f4fbfcfb128d30877d5809d7be5b5b9e6a6504e78ff3cfa9552b5"}
FREEZER={"bytes":19606,"path":"scripts/comparison/freeze_headless_validation_epoch_v1.py","sha256":"825fd22e81e57c1d65e09ac33b4363798ebcd02297ef9643022d8aeacb895213"}
DESIGN={"bytes":1427,"path":"docs/simulator/headless_validation_epoch_5_raster_repair_design_v1.json","sha256":"6e182865bcb29153d56b2b39e97ade37b02f987241b70f2489e84c05df32c0e3"}
RASTERS={"fractal_high":"416150375eee6c2e1ccb8bba198f0b872d50c5ff58c51b99b249142b93f5738f","fractal_low":"a33ae302104a0deeb524bd69ec78edeaff2ab81ec107cf3ff9f0def9ba82c355","fractal_medium":"c4ae3c3b14c0cd7b831dc603aa7d7ea613c4435ca97ff5eafef1b24bcb603980","open":"c77b708cec16c2c0403d3c137b5ed4d134ff1955e7c76dcd5ff45303c5591a83","perfect_maze":"66b7af44df433d960c4684ea80d7b7946fb50d0957627d0992e89005592b0dbd"}
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def canon(v:object)->bytes:return (json.dumps(v,allow_nan=False,separators=(",",":"),sort_keys=True)+"\n").encode()
def read(root:Path,i:dict,label:str)->bytes:
 b=(root/i["path"]).read_bytes()
 if len(b)!=i["bytes"] or sha(b)!=i["sha256"]:raise ValueError(f"{label} identity differs")
 return b
def semantic(domain:str,v:dict)->str:return sha(domain.encode()+b"\0"+canon(v))
def compile_repair(root:Path)->bytes:
 root=Path(os.path.abspath(root));old=json.loads(read(root,OLD,"old plan"));json.loads(read(root,DESIGN,"repair design"))
 by={}
 for scene in old["scenes"]:
  raster=RASTERS[scene["family"]];scene["effective_runtime_raster"]["sha256"]=raster
  payload={"controller_timesteps":100,"effective_runtime_raster_sha256":raster,"effective_scenario_sha256":scene["effective_scenario_sha256"],"family":scene["family"],"map_sha256":scene["map"]["sha256"],"mission_bank_id":scene["sampling_unit"]["mission_bank_id"],"mission_generator_seed_uint64":scene["seed_roles"]["mission_selection"]["generator_seed_uint64"],"mission_selection_seed":scene["seed"],"normalized_config_sha256":scene["normalized_config_sha256"],"phase":"validation","rendered_config_sha256":scene["config"]["sha256"],"robot_count":scene["robot_count"],"runtime_rng_seed":scene["seed_roles"]["runtime_rng"]["runtime_rng_seed"],"scenario_sha256":scene["scenario"]["sha256"],"timestep_seconds":0.1}
  scene["input_id"]=semantic("dancers-headless-validation-exact-input-v1",payload);by[scene["scene_id"]]=scene
 for slot in old["schedule"]["warmups"]["slots"]:
  scene=by[slot["scene_id"]];slot["input_id"]=scene["input_id"];slot["effective_runtime_raster_sha256"]=RASTERS[scene["family"]]
 for inventory in old["schedule"]["inventories"].values():
  for slot in inventory["measured"]:
   scene=by[slot["scene_id"]];slot["input_id"]=scene["input_id"];slot["effective_runtime_raster_sha256"]=RASTERS[scene["family"]]
 old["classification"].update({"execution_authorized":False,"scientific_evidence_eligible":False,"validation_execution_authorized":False,"validation_results_present":False})
 old["status"]={"execution_ready":False,"registration_state":"registered-committed"}
 raw=canon(old);fraw=read(root,FREEZER,"validation freezer");m=types.ModuleType("_repair_freezer");m.__file__=FREEZER["path"];sys.modules[m.__name__]=m;exec(compile(fraw,m.__file__,"exec"),m.__dict__);m.validate_epoch_bytes(raw)
 return raw
def main()->int:
 print("raster repair compiler is nonauthorizing; no plan write or execution is permitted",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
