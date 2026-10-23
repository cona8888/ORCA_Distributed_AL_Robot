#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
from typing import Any
sys.dont_write_bytecode=True
A=PurePosixPath("docs/simulator/headless_timing_calibration_epoch_4_execution_v1.json");RUN={"bytes":6001,"path":"scripts/comparison/run_headless_timing_calibration_epoch_v1.py","sha256":"8c66ac596871a5a2aea5f1bafe4c66fa7ee9945a7452dbaed2590801175627e6"};AN={"bytes":1045,"path":"scripts/comparison/analyze_headless_timing_calibration_epoch_v1.py","sha256":"7fcc02bced0d0558cef17913c0b704ca7582179eff52913e5b040dcbf6c1b987"};PLAN={"bytes":1138070,"path":"comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration/plan.json","sha256":"dcc11359a9cb19188940ef295d887066ad8dee0fd35cca0179e539f2c0d1ae2b"};RESULT=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-4-timing-calibration");SESSION="paired-timing-calibration-session-001";N=610
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def canon(v:object)->bytes:return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def git(root:Path,*a:str)->bytes:
 r=subprocess.run(["git",*a],cwd=root,env={"LC_ALL":"C","PATH":os.environ.get("PATH","")},stdout=subprocess.PIPE,stderr=subprocess.PIPE); 
 if r.returncode:raise ValueError("git input check failed")
 return r.stdout
def load(root:Path,identity:dict[str,Any],name:str)->types.ModuleType:
 raw=root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
 if len(raw)!=identity["bytes"] or sha(raw)!=identity["sha256"]:raise ValueError(f"{name} identity differs")
 m=types.ModuleType(name);m.__file__=identity["path"];sys.modules[name]=m;exec(compile(raw,m.__file__,"exec"),m.__dict__);return m
def committed(plan:Path,root:Path)->None:
 rel=plan.relative_to(root).as_posix()
 if git(root,"show",f"HEAD:{rel}")!=plan.read_bytes():raise ValueError("timing plan not committed")
def execute(root:Path)->dict[str,Any]:
 root=root.resolve();raw=(root.joinpath(*A.parts)).read_bytes();v=json.loads(raw)
 if raw!=canon(v) or v.get("runner")!=RUN or v.get("analyzer")!=AN or v.get("plan")!=PLAN or v.get("expected_process_count")!=N or v.get("timing_execution_authorized") is not True or v.get("validation_execution_authorized") is not False or v.get("benchmark_execution_authorized") is not False:raise ValueError("timing execution authorization differs")
 if git(root,"status","--porcelain","--untracked-files=no"):raise ValueError("tracked repository differs")
 for rel in (A.as_posix(),RUN["path"],AN["path"],PLAN["path"],Path(__file__).resolve().relative_to(root).as_posix()):
  if git(root,"show",f"HEAD:{rel}")!=(root/rel).read_bytes():raise ValueError(f"{rel} not exact HEAD")
 runner=load(root,RUN,"_authorized_timing_runner");result=root.joinpath(*RESULT.parts)
 if result.exists() or result.is_symlink():raise ValueError("timing result root exists")
 result.mkdir(mode=0o755,parents=False)
 try:
  auth=runner.load_authenticated_plan(root/PLAN["path"],root,production=True,committed_input_checker=committed)
  if len(auth.schedule)!=N or auth.plan_sha256!=PLAN["sha256"]:raise ValueError("timing schedule differs")
  session=runner.execute_custodied_schedule(auth,result/SESSION)
 except BaseException:
  if result.exists() and not any(result.iterdir()):result.rmdir()
  raise
 return {"authorization_sha256":sha(canon(v)),"benchmark_execution_performed":False,"expected_process_count":N,"observed_attempt_count":session["attempt_count"],"operation":"execute-exact-paired-timing-calibration","schema_version":"dancers-headless-timing-calibration-execution-receipt-v1","session":(RESULT/SESSION).as_posix(),"session_aborted":session["aborted"],"timing_execution_performed":True,"validation_execution_performed":False}
def main()->int:
 try:r=execute(Path(__file__).resolve().parents[2])
 except BaseException as e:print(f"timing execution rejected: {type(e).__name__}: {e}",file=sys.stderr);return 1
 sys.stdout.buffer.write(canon(r));return 0
if __name__=="__main__":raise SystemExit(main())
