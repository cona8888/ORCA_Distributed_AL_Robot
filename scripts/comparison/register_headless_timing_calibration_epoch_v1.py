#!/usr/bin/env python3
"""Prepare the exact registered epoch-4 timing plan; publication disabled."""
from __future__ import annotations
from dataclasses import replace
import hashlib,json,os,sys,types
from pathlib import Path,PurePosixPath
from typing import Any
sys.dont_write_bytecode=True
PLAN_ROOT=PurePosixPath("comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration")
FREEZER={"bytes":10214,"path":"scripts/comparison/freeze_headless_timing_calibration_epoch_v1.py","sha256":"6eecbe99d59576f03bb6b893a0ff5b6a24da3aa72f977f8051dfe4b77ac4e5e5"}
class RegistrationError(ValueError):pass
def _sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def _load(root:Path)->types.ModuleType:
 raw=root.joinpath(*PurePosixPath(FREEZER["path"]).parts).read_bytes()
 if len(raw)!=FREEZER["bytes"] or _sha(raw)!=FREEZER["sha256"]:raise RegistrationError("timing freezer identity differs")
 m=types.ModuleType("_timing_reg_freezer");m.__file__=FREEZER["path"];sys.modules[m.__name__]=m;exec(compile(raw,m.__file__,"exec"),m.__dict__);return m
def prepare(repository_root:Path)->Any:
 root=Path(os.path.abspath(repository_root));m=_load(root);c=m.compile_epoch(root);v=json.loads(c.data)
 v["classification"]["plan_registered"]=True;v["operations"]["plan_written"]=True;v["status"]={"execution_ready":False,"registration_state":"registered-committed"}
 data=m.canonical_json_bytes(v);m.validate_epoch_bytes(data)
 return m.Compilation(v,data,_sha(data),(m.PlanFile(PurePosixPath("plan.json"),data,_sha(data),"plan"),))
def publish(repository_root:Path,c:Any)->dict[str,Any]:
 root=Path(os.path.abspath(repository_root)).joinpath(*PLAN_ROOT.parts)
 if root.exists() or root.is_symlink():raise RegistrationError("timing plan destination exists")
 root.mkdir(mode=0o755,parents=False);p=root/"plan.json"
 try:
  with p.open("xb") as f:f.write(c.data);f.flush();os.fsync(f.fileno())
  os.chmod(p,0o444);os.chmod(root,0o555)
 except BaseException:
  try:
   os.chmod(root,0o755);p.unlink(missing_ok=True);root.rmdir()
  except BaseException:pass
  raise
 return {"file_count":1,"plan_bytes":len(c.data),"plan_sha256":c.sha256,"total_bytes":len(c.data)}
def main()->int:raise RegistrationError("timing plan publication disabled pending one-use authorization")
if __name__=="__main__":raise SystemExit(main())
