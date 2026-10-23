#!/usr/bin/env python3
"""Build the offline held-out safe-grid benchmark and five replay pages."""
from __future__ import annotations
import csv,hashlib,html,importlib.util,io,json,math,os,statistics,sys,types
from pathlib import Path,PurePosixPath
from typing import Any
sys.dont_write_bytecode=True
REPORT={"bytes":2026673,"path":"results/simulator-demo/v1/headless-scientific/epoch-5-validation/held-out-validation-raster-repair-session-001-analysis.json","sha256":"14caa13f43ca478e3bf1d05fbd499e5409de8ae8b6820c464f4e15c40bd07836"}
PLAN={"bytes":4618050,"path":"comparison/plans/simulator-demo/v1/headless-epoch-5-validation/plan.json","sha256":"b3a30c33581382014e8a0e35b1c9ce84f7e7de4daffddff18eefea0dd3e459e8"}
MANIFEST={"bytes":129929,"path":"comparison/environments/manifest.json","sha256":"2785458ea32028f8a59498650677d1aa4c9dfb749b5f1a80754577e1eb703589"}
SESSION=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-5-validation/held-out-validation-raster-repair-session-001")
SAFE_CELLS=(("open",8),("perfect_maze",2),("perfect_maze",4),("perfect_maze",6),("fractal_low",2),("fractal_medium",2),("fractal_high",2),("fractal_high",4))
REPLAYS=(("open",8,11),("perfect_maze",6,11),("fractal_low",2,11),("fractal_medium",2,11),("fractal_high",4,11))
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def canon(v:object)->bytes:return (json.dumps(v,allow_nan=False,separators=(",",":"),sort_keys=True)+"\n").encode()
def read(root:Path,i:dict,label:str)->bytes:
 b=(root/i["path"]).read_bytes()
 if len(b)!=i["bytes"] or sha(b)!=i["sha256"]:raise ValueError(f"{label} identity differs")
 return b
def load_viewer(root:Path):
 p=root/"scripts/comparison/build_replay_viewer.py";name="_laptop_bundle_viewer";m=types.ModuleType(name);m.__file__=str(p);sys.modules[name]=m;sys.path.insert(0,str(p.parent));exec(compile(p.read_bytes(),str(p),"exec"),m.__dict__);return m
def descriptors(entry:dict)->dict:
 mm=entry["effective_runtime_1000"]["map_metrics"]
 return {"obstacle_occupancy":mm["obstacle_occupancy"],"boundary_density":mm["boundary_density"],"boundary_box_counting":{"dimension":mm["boundary_box_counting"]["dimension"],"r_squared":mm["boundary_box_counting"]["r_squared"]},"free_space":{"component_count_4_connected":mm["free_space"]["component_count_4_connected"],"reachable_fraction":mm["free_space"]["reachable_fraction"]}}
def geometric(values:list[float])->float:return math.exp(statistics.fmean(math.log(v) for v in values))
def benchmark_rows(root:Path,report:dict,plan:dict)->list[dict[str,Any]]:
 cells={(r["family"],r["robot_count"]):r for r in report["analysis"]["timing_validation"]["cell_rows"]};pairs=report["analysis"]["timing_validation"]["pair_rows"];scenes={(s["family"],s["robot_count"],s["seed"]):s for s in plan["scenes"]};slots=plan["schedule"]["inventories"]["positions_replay"]["measured"];dest={(s["scene_id"],s["method"]):s["destination"] for s in slots}
 out=[]
 for family,count in SAFE_CELLS:
  cell=cells[(family,count)]
  if not cell["cell_pass"] or cell["task_valid_scene_count"]!=20:raise ValueError("safe benchmark cell no longer passes")
  cell_pairs=[r for r in pairs if r["family"]==family and r["robot_count"]==count]
  summaries=[]
  for seed in range(11,31):
   scene=scenes[(family,count,seed)];d=dest[(scene["scene_id"],"reference")];p=root/SESSION/"attempts"/d/"summary.json";s=json.loads(p.read_bytes());summaries.append(s)
  row={"all_goals_reached_scene_count":sum(s["all_goals_reached"] for s in summaries),"family":family,"geometric_mean_optimized_wall_seconds":geometric([r["optimized_wall_seconds"] for r in cell_pairs]),"geometric_mean_reference_wall_seconds":geometric([r["reference_wall_seconds"] for r in cell_pairs]),"geometric_mean_wall_speedup":cell["bootstrap"]["geometric_mean_wall_speedup"],"goal_completion_fraction":sum(s["goals_reached"] for s in summaries)/sum(s["goals_total"] for s in summaries),"iterations_per_scene":sorted({s["iterations"] for s in summaries}),"maximum_pair_collision_events":max(s["pair_collision_events"] for s in summaries),"median_total_path_length_m":statistics.median(s["total_path_length_m"] for s in summaries),"minimum_obstacle_clearance_m":min(s["minimum_obstacle_clearance_m"] for s in summaries),"minimum_pair_clearance_m":min(s["minimum_pair_clearance_m"] for s in summaries),"mission_count":20,"paired_timing_repetition_count":60,"robot_count":count,"speedup_lower_95":cell["bootstrap"]["lower_95"],"speedup_upper_95":cell["bootstrap"]["upper_95"]}
  out.append(row)
 return out
def csv_bytes(rows:list[dict[str,Any]])->bytes:
 fields=list(rows[0]);buf=io.StringIO(newline="");w=csv.DictWriter(buf,fieldnames=fields,lineterminator="\n");w.writeheader()
 for row in rows:w.writerow({k:json.dumps(v,separators=(",",":")) if isinstance(v,list) else v for k,v in row.items()})
 return buf.getvalue().encode()
def index_bytes(entries:list[dict],rows:list[dict])->bytes:
 links="\n".join(f'<li><a href="{html.escape(e["file"],quote=True)}">{html.escape(e["title"])}</a> — 200 synchronized frames, exact semantic match</li>' for e in entries)
 table="\n".join(f'<tr><td>{html.escape(r["family"])}</td><td>{r["robot_count"]}</td><td>{r["geometric_mean_wall_speedup"]:.3f}</td><td>{r["speedup_lower_95"]:.3f}</td><td>{r["minimum_pair_clearance_m"]:.3f}</td></tr>' for r in rows)
 return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>DANCeRS held-out laptop comparison</title><style>body{{max-width:980px;margin:40px auto;padding:0 20px;font:16px/1.5 system-ui;background:#0b1119;color:#e8eef7}}a{{color:#49b9ff}}table{{border-collapse:collapse;width:100%}}th,td{{padding:8px;border-bottom:1px solid #2b3b50;text-align:left}}.warn{{color:#f4bd4f}}</style></head><body><h1>DANCeRS held-out laptop comparison</h1><p>Reference and optimized source-pinned builds, fixed 100-step work, OMP=1. Replays are self-contained and require no server.</p><h2>Synchronized replays</h2><ul>{links}</ul><h2>Validated safe-grid benchmark</h2><table><thead><tr><th>Environment</th><th>N</th><th>Geometric speedup</th><th>95% lower</th><th>Minimum pair clearance (m)</th></tr></thead><tbody>{table}</tbody></table><p class="warn">Scope: fixed held-out suite only. Fractal-low N=4 and fractal-medium N=4 failed the task gate and are excluded. No capacity or population-wide claim.</p><p>See <a href="benchmark.json">benchmark.json</a>, <a href="benchmark.csv">benchmark.csv</a>, and <a href="manifest.json">manifest.json</a>.</p></body></html>'''.encode()
def build_bundle(repository_root:Path,output:Path)->dict:
 root=Path(os.path.abspath(repository_root));report=json.loads(read(root,REPORT,"analysis report"));plan=json.loads(read(root,PLAN,"plan"));environment=json.loads(read(root,MANIFEST,"environment manifest"));viewer=load_viewer(root)
 if output.exists() or output.is_symlink():raise FileExistsError(output)
 output.mkdir(parents=True,mode=0o755);rows=benchmark_rows(root,report,plan);entries=[];env={(e["family"],e["split"]):e for e in environment["environments"]};scenes={(s["family"],s["robot_count"],s["seed"]):s for s in plan["scenes"]};slots=plan["schedule"]["inventories"]["positions_replay"]["measured"];dest={(s["scene_id"],s["method"]):s["destination"] for s in slots}
 for family,count,seed in REPLAYS:
  scene=scenes[(family,count,seed)];paths={method:root/SESSION/"attempts"/dest[(scene["scene_id"],method)]/"trace.ndjson" for method in ("reference","optimized")};map_path=root/scene["map"]["path"];result=viewer.build_replay_viewer(paths["reference"],paths["optimized"],map_path,title=f"Held-out {family} N={count} seed {seed}",environment_family=family,environment_descriptors=descriptors(env[(family,"validation")]))
  if not result.exact_semantic_match or result.frame_count!=200:raise ValueError("replay semantic or frame gate differs")
  name=f"{family}-n{count}-seed{seed}.html";data=result.html_bytes;(output/name).write_bytes(data);entries.append({"bytes":len(data),"family":family,"file":name,"frame_count":result.frame_count,"optimized_trace_sha256":sha(paths["optimized"].read_bytes()),"reference_trace_sha256":sha(paths["reference"].read_bytes()),"robot_count":count,"seed":seed,"sha256":sha(data),"title":f"{family} N={count} seed {seed}"})
 benchmark={"classification":{"benchmark_scope":"eight-held-out-task-valid-fixed-suite-cells","capacity_claim_ready":False,"population_generalization_ready":False},"rows":rows,"schema_version":"dancers-laptop-safe-grid-benchmark-v1"};braw=canon(benchmark);craw=csv_bytes(rows);(output/"benchmark.json").write_bytes(braw);(output/"benchmark.csv").write_bytes(craw);iraw=index_bytes(entries,rows);(output/"index.html").write_bytes(iraw)
 manifest={"artifacts":entries+[ {"bytes":len(braw),"file":"benchmark.json","sha256":sha(braw)},{"bytes":len(craw),"file":"benchmark.csv","sha256":sha(craw)},{"bytes":len(iraw),"file":"index.html","sha256":sha(iraw)}],"report":REPORT,"schema_version":"dancers-laptop-comparison-bundle-v1"};mraw=canon(manifest);(output/"manifest.json").write_bytes(mraw);return manifest
def main()->int:
 if len(sys.argv)!=2:print("usage: build_laptop_comparison_bundle_v1.py OUTPUT_DIRECTORY",file=sys.stderr);return 2
 build_bundle(Path(__file__).resolve().parents[2],Path(sys.argv[1]));return 0
if __name__=="__main__":raise SystemExit(main())
