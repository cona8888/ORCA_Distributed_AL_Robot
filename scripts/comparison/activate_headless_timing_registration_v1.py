#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
A="docs/simulator/headless_timing_calibration_epoch_4_registration_v1.json";R={"bytes":2311,"path":"scripts/comparison/register_headless_timing_calibration_epoch_v1.py","sha256":"3d6cffc409279e6c381bd0eb1b7ee88ee1d4448d81e3aa473e7f635aae8f0b25"};E={"file_count":1,"plan_bytes":1138070,"plan_sha256":"dcc11359a9cb19188940ef295d887066ad8dee0fd35cca0179e539f2c0d1ae2b","total_bytes":1138070}
def canon(v):return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def main():
 root=Path(__file__).resolve().parents[2];v=json.loads((root/A).read_bytes());raw=(root/R["path"]).read_bytes()
 if (root/A).read_bytes()!=canon(v) or v.get("expected")!=E or v.get("registrar")!=R or v.get("one_use") is not True or v.get("simulator_execution_authorized") is not False or v.get("result_write_authorized") is not False or len(raw)!=R["bytes"] or hashlib.sha256(raw).hexdigest()!=R["sha256"]:raise SystemExit("timing registration authorization differs")
 if subprocess.run(["git","status","--porcelain","--untracked-files=no"],cwd=root,stdout=subprocess.PIPE).stdout:raise SystemExit("tracked tree differs")
 m=types.ModuleType("_timing_reg");m.__file__=R["path"];sys.modules[m.__name__]=m;exec(compile(raw,m.__file__,"exec"),m.__dict__);c=m.prepare(root);o=m.publish(root,c)
 if o!=E:raise SystemExit("timing registration output differs")
 sys.stdout.buffer.write(canon({"authorization_sha256":hashlib.sha256(canon(v)).hexdigest(),"classification":{"result_write_performed":False,"simulator_invoked":False},"operation":"publish-exact-timing-plan-only","plan":E,"schema_version":"dancers-headless-timing-registration-receipt-v1"}));return 0
if __name__=="__main__":raise SystemExit(main())
