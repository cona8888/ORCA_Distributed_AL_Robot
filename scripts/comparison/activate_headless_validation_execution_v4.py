#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
A=PurePosixPath("docs/simulator/headless_validation_epoch_5_execution_repair_3_v1.json");RUN={"bytes":8790,"path":"scripts/comparison/run_headless_validation_epoch_v1.py","sha256":"54fb03019e60cb1452c1bf7f298e1166161691b8400f8329ec8f41920a5ae942"};PLAN={"bytes":4618054,"path":"comparison/plans/simulator-demo/v1/headless-epoch-5-validation/plan.json","sha256":"f2f97036e137b5bcf0fcd8f07a9679433babef366c2b69ca7592ca9aec231414"};RESULT=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-5-validation");SESSION="held-out-validation-session-001";N=2810
def sha(b):return hashlib.sha256(b).hexdigest()
def canon(v):return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def terminal(label):raise RuntimeError(label)
def git(root,*args):
 r=subprocess.run(["git",*args],cwd=root,env={"LC_ALL":"C","PATH":os.environ.get("PATH","")},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if r.returncode:raise ValueError("git check failed")
 return r.stdout
def main():
 root=Path(__file__).resolve().parents[2];araw=root.joinpath(*A.parts).read_bytes();v=json.loads(araw);rr=(root/RUN["path"]).read_bytes()
 if araw!=canon(v) or v.get("runner")!=RUN or v.get("plan")!=PLAN or v.get("expected_process_count")!=N or v.get("validation_execution_authorized") is not True or v.get("benchmark_execution_authorized") is not False or v.get("prior_attempts")!={"count":4,"task_trajectory_count":0,"timing_process_count":0} or len(rr)!=RUN["bytes"] or sha(rr)!=RUN["sha256"]:raise SystemExit("validation RNG-amended execution authority differs")
 if git(root,"status","--porcelain","--untracked-files=no"):raise SystemExit("tracked tree differs")
 for rel in (A.as_posix(),RUN["path"],PLAN["path"],Path(__file__).resolve().relative_to(root).as_posix()):
  if git(root,"show",f"HEAD:{rel}")!=(root/rel).read_bytes():raise SystemExit(f"{rel} not exact HEAD")
 m=types.ModuleType("_validation_rng_amended_runner");m.__file__=RUN["path"];sys.modules[m.__name__]=m;exec(compile(rr,m.__file__,"exec",dont_inherit=True,optimize=0),m.__dict__);out=root.joinpath(*RESULT.parts)
 if out.exists() or out.is_symlink():raise SystemExit("validation result root exists")
 out.mkdir(mode=0o755,parents=False)
 try:
  p=m.load_authenticated_plan(root/PLAN["path"],root,production=False)
  if len(p.schedule)!=N or p.plan_sha256!=PLAN["sha256"]:raise ValueError("validation schedule differs")
  s=m.execute_custodied_schedule(p,out/SESSION,ambiguity_terminal=terminal)
 except BaseException:
  if out.exists() and not any(out.iterdir()):out.rmdir()
  raise
 sys.stdout.buffer.write(canon({"authorization_sha256":sha(araw),"benchmark_execution_performed":False,"expected_process_count":N,"observed_attempt_count":s["attempt_count"],"operation":"execute-runtime-rng-amended-held-out-validation","schema_version":"dancers-headless-validation-execution-receipt-v3","session":(RESULT/SESSION).as_posix(),"session_aborted":s["aborted"],"validation_execution_performed":True}));return 0
if __name__=="__main__":raise SystemExit(main())
