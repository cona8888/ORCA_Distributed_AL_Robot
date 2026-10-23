#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
from typing import Any
sys.dont_write_bytecode=True
A=PurePosixPath("docs/simulator/headless_validation_epoch_5_execution_v1.json")
RUN={"bytes":7515,"path":"scripts/comparison/run_headless_validation_epoch_v1.py","sha256":"b4e372fa56134eafba267c04892bdf8d901c253ea92593dc7173e41519eed4c8"}
AN={"bytes":1927,"path":"scripts/comparison/analyze_headless_validation_epoch_v1.py","sha256":"a175309ca3fa873bef478d963f9278031182b3868f0b8c21b4621d3a6c186d7e"}
PLAN={"bytes":4617990,"path":"comparison/plans/simulator-demo/v1/headless-epoch-5-validation/plan.json","sha256":"fa0e8487c815ddb4d72d655a39abf727e36453e6ebcc0c8e5e738a2b7b6b15c8"}
RESULT=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-5-validation");SESSION="held-out-validation-session-001";N=2810
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def canon(v:object)->bytes:return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def git(root:Path,*args:str)->bytes:
 r=subprocess.run(["git",*args],cwd=root,env={"LC_ALL":"C","PATH":os.environ.get("PATH","")},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if r.returncode:raise ValueError("git input check failed")
 return r.stdout
def load(root:Path,identity:dict[str,Any])->types.ModuleType:
 raw=root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
 if len(raw)!=identity["bytes"] or sha(raw)!=identity["sha256"]:raise ValueError("validation runner identity differs")
 m=types.ModuleType("_authorized_validation_runner");m.__file__=identity["path"];sys.modules[m.__name__]=m;exec(compile(raw,m.__file__,"exec",dont_inherit=True,optimize=0),m.__dict__);return m
def execute(root:Path)->dict[str,Any]:
 root=root.resolve();araw=root.joinpath(*A.parts).read_bytes();v=json.loads(araw)
 if araw!=canon(v) or v.get("runner")!=RUN or v.get("analyzer")!=AN or v.get("plan")!=PLAN or v.get("expected_process_count")!=N or v.get("validation_execution_authorized") is not True or v.get("timing_execution_authorized") is not False or v.get("benchmark_execution_authorized") is not False:raise ValueError("validation execution authorization differs")
 if git(root,"status","--porcelain","--untracked-files=no"):raise ValueError("tracked repository differs")
 for rel in (A.as_posix(),RUN["path"],AN["path"],PLAN["path"],Path(__file__).resolve().relative_to(root).as_posix()):
  if git(root,"show",f"HEAD:{rel}")!=(root/rel).read_bytes():raise ValueError(f"{rel} not exact HEAD")
 runner=load(root,RUN);out=root.joinpath(*RESULT.parts)
 if out.exists() or out.is_symlink():raise ValueError("validation result root exists")
 out.mkdir(mode=0o755,parents=False)
 try:
  auth=runner.load_authenticated_plan(root/PLAN["path"],root,production=False)
  if len(auth.schedule)!=N or auth.plan_sha256!=PLAN["sha256"] or auth.plan["classification"]["plan_registered"] is not True or auth.plan["status"]["registration_state"]!="registered-committed":raise ValueError("registered validation plan differs")
  session=runner.execute_custodied_schedule(auth,out/SESSION)
 except BaseException:
  if out.exists() and not any(out.iterdir()):out.rmdir()
  raise
 return {"authorization_sha256":sha(araw),"benchmark_execution_performed":False,"expected_process_count":N,"observed_attempt_count":session["attempt_count"],"operation":"execute-exact-held-out-validation","schema_version":"dancers-headless-validation-execution-receipt-v1","session":(RESULT/SESSION).as_posix(),"session_aborted":session["aborted"],"validation_execution_performed":True}
def main()->int:
 try:r=execute(Path(__file__).resolve().parents[2])
 except BaseException as error:print(f"validation execution rejected: {type(error).__name__}: {error}",file=sys.stderr);return 1
 sys.stdout.buffer.write(canon(r));return 0
if __name__=="__main__":raise SystemExit(main())
