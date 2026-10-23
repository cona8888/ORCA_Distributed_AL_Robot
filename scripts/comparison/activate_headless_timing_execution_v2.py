#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
AUTH=PurePosixPath("docs/simulator/headless_timing_calibration_epoch_4_execution_repair_v1.json");RUN={"bytes":6227,"path":"scripts/comparison/run_headless_timing_calibration_epoch_v1.py","sha256":"fbb654e91a01d1e3617c393fc4541536f941a1a936f75030822666dc22e78203"};PLAN={"bytes":1138070,"path":"comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration/plan.json","sha256":"dcc11359a9cb19188940ef295d887066ad8dee0fd35cca0179e539f2c0d1ae2b"};RESULT=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-4-timing-calibration");SESSION="paired-timing-calibration-session-001"
def sha(b):return hashlib.sha256(b).hexdigest()
def canon(v):return (json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode()
def git(root,*a):
 r=subprocess.run(["git",*a],cwd=root,env={"LC_ALL":"C","PATH":os.environ.get("PATH","")},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if r.returncode:raise ValueError("git check failed")
 return r.stdout
def main():
 root=Path(__file__).resolve().parents[2];raw=(root.joinpath(*AUTH.parts)).read_bytes();v=json.loads(raw);rr=(root/RUN["path"]).read_bytes()
 original=v.get("original_authorization",{});original_raw=root.joinpath(*PurePosixPath(original.get("path","")).parts).read_bytes()
 if raw!=canon(v) or v.get("runner")!=RUN or v.get("expected_process_count")!=610 or v.get("prior_attempt")!={"failure":"InfrastructureAbort: runtime activation receipt is absent","result_namespace_state":"absent","simulator_process_count":0} or v.get("timing_execution_authorized") is not True or v.get("validation_execution_authorized") is not False or len(rr)!=RUN["bytes"] or sha(rr)!=RUN["sha256"] or len(original_raw)!=original.get("bytes") or sha(original_raw)!=original.get("sha256"):raise SystemExit("timing retry authorization differs")
 if git(root,"status","--porcelain","--untracked-files=no"):raise SystemExit("tracked tree differs")
 for rel in (AUTH.as_posix(),RUN["path"],PLAN["path"],Path(__file__).resolve().relative_to(root).as_posix()):
  if git(root,"show",f"HEAD:{rel}")!=(root/rel).read_bytes():raise SystemExit(f"{rel} not exact HEAD")
 m=types.ModuleType("_timing_runner_retry");m.__file__=RUN["path"];sys.modules[m.__name__]=m;exec(compile(rr,m.__file__,"exec"),m.__dict__);out=root.joinpath(*RESULT.parts)
 if out.exists() or out.is_symlink():raise SystemExit("timing result root exists")
 out.mkdir(mode=0o755,parents=False)
 try:
  def check(p,r):
   rel=p.relative_to(r).as_posix()
   if git(r,"show",f"HEAD:{rel}")!=p.read_bytes():raise ValueError("plan not committed")
  a=m.load_authenticated_plan(root/PLAN["path"],root,production=True,committed_input_checker=check)
  s=m.execute_custodied_schedule(a,out/SESSION)
 except BaseException:
  if out.exists() and not any(out.iterdir()):out.rmdir()
  raise
 receipt={"benchmark_execution_performed":False,"expected_process_count":610,"observed_attempt_count":s["attempt_count"],"operation":"execute-repaired-exact-paired-timing-calibration","repair_authorization_sha256":sha(raw),"schema_version":"dancers-headless-timing-calibration-execution-receipt-v2","session":(RESULT/SESSION).as_posix(),"session_aborted":s["aborted"],"timing_execution_performed":True,"validation_execution_performed":False};sys.stdout.buffer.write(canon(receipt));return 0
if __name__=="__main__":raise SystemExit(main())
