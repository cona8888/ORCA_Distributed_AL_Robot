#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
A=PurePosixPath("docs/simulator/headless_validation_epoch_5_execution_diagnostic_v1.json");RUN={"bytes":7515,"path":"scripts/comparison/run_headless_validation_epoch_v1.py","sha256":"b4e372fa56134eafba267c04892bdf8d901c253ea92593dc7173e41519eed4c8"};PLAN={"bytes":4617990,"path":"comparison/plans/simulator-demo/v1/headless-epoch-5-validation/plan.json","sha256":"fa0e8487c815ddb4d72d655a39abf727e36453e6ebcc0c8e5e738a2b7b6b15c8"};RESULT=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-5-validation");SESSION="held-out-validation-session-001"
def sha(b):return hashlib.sha256(b).hexdigest()
def canon(v):return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def git(root,*args):
 r=subprocess.run(["git",*args],cwd=root,env={"LC_ALL":"C","PATH":os.environ.get("PATH","")},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if r.returncode:raise ValueError("git check failed")
 return r.stdout
def terminal(label):raise RuntimeError(label)
def main():
 root=Path(__file__).resolve().parents[2];araw=root.joinpath(*A.parts).read_bytes();v=json.loads(araw);rr=(root/RUN["path"]).read_bytes()
 if araw!=canon(v) or v.get("runner")!=RUN or v.get("validation_execution_authorized") is not True or len(rr)!=RUN["bytes"] or sha(rr)!=RUN["sha256"]:raise SystemExit("validation diagnostic authority differs")
 if git(root,"status","--porcelain","--untracked-files=no"):raise SystemExit("tracked tree differs")
 for rel in (A.as_posix(),RUN["path"],PLAN["path"],Path(__file__).resolve().relative_to(root).as_posix()):
  if git(root,"show",f"HEAD:{rel}")!=(root/rel).read_bytes():raise SystemExit(f"{rel} not exact HEAD")
 m=types.ModuleType("_diagnostic_validation_runner");m.__file__=RUN["path"];sys.modules[m.__name__]=m;exec(compile(rr,m.__file__,"exec",dont_inherit=True,optimize=0),m.__dict__);out=root.joinpath(*RESULT.parts)
 if out.exists() or out.is_symlink():raise SystemExit("validation result root exists")
 out.mkdir(mode=0o755,parents=False)
 try:
  p=m.load_authenticated_plan(root/PLAN["path"],root,production=False);m.execute_custodied_schedule(p,out/SESSION,ambiguity_terminal=terminal)
 except BaseException as error:
  print(f"validation diagnostic: {type(error).__name__}: {error}",file=sys.stderr);return 1
 return 0
if __name__=="__main__":raise SystemExit(main())
