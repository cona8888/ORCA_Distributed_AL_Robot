#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,subprocess,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
A="docs/simulator/headless_validation_epoch_5_registration_v1.json"
R={"bytes":2471,"path":"scripts/comparison/register_headless_validation_epoch_v1.py","sha256":"f01aea738ad98e3a8ab31cfe1cc9db0f6f511bb91105f62d094aaed69a560a2e"}
E={"file_count":513,"plan_bytes":4618054,"plan_path":"comparison/plans/simulator-demo/v1/headless-epoch-5-validation/plan.json","plan_sha256":"592c8ded608f4fbfcfb128d30877d5809d7be5b5b9e6a6504e78ff3cfa9552b5","total_bytes":5627959}
def canon(v):return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def main():
 root=Path(__file__).resolve().parents[2];araw=(root/A).read_bytes();v=json.loads(araw);raw=root.joinpath(*PurePosixPath(R["path"]).parts).read_bytes()
 if araw!=canon(v) or v.get("expected")!=E or v.get("registrar")!=R or v.get("one_use") is not True or v.get("simulator_execution_authorized") is not False or v.get("result_write_authorized") is not False or len(raw)!=R["bytes"] or hashlib.sha256(raw).hexdigest()!=R["sha256"]:raise SystemExit("validation registration authorization differs")
 status=subprocess.run(["git","status","--porcelain","--untracked-files=no"],cwd=root,stdout=subprocess.PIPE).stdout.decode().splitlines()
 if any(not line.startswith(" D comparison/plans/simulator-demo/v1/headless-epoch-5-validation/") for line in status):raise SystemExit("tracked tree differs outside superseded plan")
 m=types.ModuleType("_validation_registrar");m.__file__=R["path"];sys.modules[m.__name__]=m;exec(compile(raw,m.__file__,"exec",dont_inherit=True,optimize=0),m.__dict__);c=m.prepare_registered_compilation(root);o=m.publish_registered_create_exclusive(root,c)
 if {key:o[key] for key in E}!=E:raise SystemExit("validation registration output differs")
 sys.stdout.buffer.write(canon({"authorization_sha256":hashlib.sha256(araw).hexdigest(),"classification":{"result_write_performed":False,"simulator_invoked":False},"operation":"publish-exact-validation-plan-only","plan":E,"schema_version":"dancers-headless-validation-registration-receipt-v1"}));return 0
if __name__=="__main__":raise SystemExit(main())
