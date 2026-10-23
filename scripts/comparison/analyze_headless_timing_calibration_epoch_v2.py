#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,math,os,statistics,sys,types
from pathlib import Path,PurePosixPath
from typing import Any
sys.dont_write_bytecode=True
PLAN={"bytes":1138070,"path":"comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration/plan.json","sha256":"dcc11359a9cb19188940ef295d887066ad8dee0fd35cca0179e539f2c0d1ae2b"};RUN={"bytes":6227,"path":"scripts/comparison/run_headless_timing_calibration_epoch_v1.py","sha256":"fbb654e91a01d1e3617c393fc4541536f941a1a936f75030822666dc22e78203"};SESSION={"bytes":531678,"path":"results/simulator-demo/v1/headless-scientific/epoch-4-timing-calibration/paired-timing-calibration-session-001/session.json","sha256":"da71648be5692d4d980e6997b5c821b0a9c3036a8db4780d64145c5f5181e93e"};LEDGER={"bytes":324745,"path":"results/simulator-demo/v1/headless-scientific/epoch-4-timing-calibration/paired-timing-calibration-session-001/ledger.ndjson","sha256":"54ca41b4ae2ecce0ab47b9eb21dd846f396e217ef112be63fd262b6a78b9153f"}
def sha(b):return hashlib.sha256(b).hexdigest()
def read(root,i):
 b=root.joinpath(*PurePosixPath(i["path"]).parts).read_bytes()
 if len(b)!=i["bytes"] or sha(b)!=i["sha256"]:raise ValueError("timing evidence identity differs")
 return b
def canon(v):return (json.dumps(v,allow_nan=False,separators=(",",":"),sort_keys=True)+"\n").encode()
def load_runner(root):
 b=read(root,RUN);m=types.ModuleType("_timing_analysis_runner");m.__file__=RUN["path"];sys.modules[m.__name__]=m;exec(compile(b,m.__file__,"exec"),m.__dict__);return m
def analyze(repository_root:Path)->dict[str,Any]:
 root=Path(os.path.abspath(repository_root));runner=load_runner(root);plan=runner.load_authenticated_plan(root/PLAN["path"],root,production=False);session=json.loads(read(root,SESSION));read(root,LEDGER)
 if session["aborted"] is not False or session["attempt_count"]!=610 or len(session["results"])!=610:raise ValueError("timing session denominator differs")
 pairs={};warmups=0
 for attempt,result in zip(plan.schedule,session["results"]):
  if result["sequence"]!=attempt.sequence or result["attempt_id"]!=attempt.attempt_id or result["category"]!="completed":raise ValueError("timing result join differs")
  if attempt.attempt_kind=="warmup":warmups+=1;continue
  m=result["measurement"]
  if m["returncode"]!=0 or m["timed_out"] or not m["process_group_contained"]:raise ValueError("timing measurement failed")
  key=(attempt.scene_id,attempt.repetition);pairs.setdefault(key,{"family":attempt.raw["family"],"robot_count":attempt.raw["robot_count"],"seed":attempt.raw["seed"],"scene_id":attempt.scene_id,"repetition":attempt.repetition})[attempt.method]=m
 rows=[]
 for key,row in sorted(pairs.items()):
  if set(row)<={"family","robot_count","seed","scene_id","repetition"}:raise ValueError("timing pair incomplete")
  ref=row["reference"];opt=row["optimized"];speed=ref["wall_seconds"]/opt["wall_seconds"]
  rows.append({"family":row["family"],"robot_count":row["robot_count"],"seed":row["seed"],"scene_id":row["scene_id"],"repetition":row["repetition"],"reference_wall_seconds":ref["wall_seconds"],"optimized_wall_seconds":opt["wall_seconds"],"wall_speedup":speed,"reference_cpu_seconds":ref["user_cpu_seconds"]+ref["system_cpu_seconds"],"optimized_cpu_seconds":opt["user_cpu_seconds"]+opt["system_cpu_seconds"],"reference_peak_rss_mib":ref["peak_rss_mib"],"optimized_peak_rss_mib":opt["peak_rss_mib"]})
 groups=[]
 for family in ("open","perfect_maze","fractal_low","fractal_medium","fractal_high"):
  for count in sorted({r["robot_count"] for r in rows if r["family"]==family}):
   x=[r["wall_speedup"] for r in rows if r["family"]==family and r["robot_count"]==count]
   groups.append({"family":family,"robot_count":count,"pair_count":len(x),"median_wall_speedup":statistics.median(x),"geometric_mean_wall_speedup":math.exp(sum(math.log(v) for v in x)/len(x)),"minimum_wall_speedup":min(x),"maximum_wall_speedup":max(x)})
 return {"analysis":{"group_rows":groups,"pair_count":len(rows),"pair_rows":rows,"warmup_count":warmups},"classification":{"benchmark_claim_ready":False,"capacity_claim_ready":False,"scientific_evidence":False,"timing_calibration_evidence":True,"validation_claim_ready":False},"plan":PLAN,"schema_version":"dancers-headless-timing-calibration-analysis-v1","status":{"analysis_completed":True,"validation_unlock":False}}
def main():print("timing analysis publication requires committed authorization",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
