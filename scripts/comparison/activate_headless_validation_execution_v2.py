#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
AUTH=PurePosixPath("docs/simulator/headless_validation_epoch_5_execution_repair_v1.json")
DRIVER={"bytes":3920,"path":"scripts/comparison/activate_headless_validation_execution_v1.py","sha256":"7d995bf62862cc0085271ab4cd7ba104dd2181129d65b72a515609ae9144b97c"}
def sha(b):return hashlib.sha256(b).hexdigest()
def canon(v):return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def git(root,*args):
 r=subprocess.run(["git",*args],cwd=root,env={"LC_ALL":"C","PATH":os.environ.get("PATH","")},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if r.returncode:raise ValueError("git check failed")
 return r.stdout
def main():
 root=Path(__file__).resolve().parents[2];raw=root.joinpath(*AUTH.parts).read_bytes();v=json.loads(raw);driver=root.joinpath(*PurePosixPath(DRIVER["path"]).parts).read_bytes()
 if raw!=canon(v) or v.get("original_driver")!=DRIVER or v.get("expected_process_count")!=2810 or v.get("validation_execution_authorized") is not True or v.get("prior_attempt")!={"completed_process_count":0,"leftover_process_count":0,"result_namespace_state":"absent","timing_process_count":0} or len(driver)!=DRIVER["bytes"] or sha(driver)!=DRIVER["sha256"]:raise SystemExit("validation retry authorization differs")
 if git(root,"status","--porcelain","--untracked-files=no"):raise SystemExit("tracked tree differs")
 for rel in (AUTH.as_posix(),DRIVER["path"],Path(__file__).resolve().relative_to(root).as_posix()):
  if git(root,"show",f"HEAD:{rel}")!=(root/rel).read_bytes():raise SystemExit(f"{rel} not exact HEAD")
 m=types.ModuleType("_validation_execution_v1");m.__file__=DRIVER["path"];sys.modules[m.__name__]=m;exec(compile(driver,m.__file__,"exec",dont_inherit=True,optimize=0),m.__dict__);receipt=m.execute(root);receipt["operation"]="retry-exact-held-out-validation-after-custody-diagnosis";receipt["repair_authorization_sha256"]=sha(raw);sys.stdout.buffer.write(canon(receipt));return 0
if __name__=="__main__":raise SystemExit(main())
