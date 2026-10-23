#!/usr/bin/env python3
"""Authenticate the epoch-4 timing-only plan; public execution disabled."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Callable, Mapping


sys.dont_write_bytecode = True

SCHEMA="dancers-headless-timing-calibration-epoch-plan-v1"
TRACK_ID="simulator-demo-v1-headless-epoch-1"
PHASE="calibration"
RUN_CLASSES=("headless_timing",)
PLAN_ROOT=PurePosixPath("comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration")
RESULT_ROOT=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-4-timing-calibration")
BASE={"bytes":314378,"path":"scripts/comparison/run_headless_epoch_v1.py","sha256":"262eda91d5bbe40a0516bcb12e3091ddb320ca997112053ac97651cf5ab88768"}
FREEZER={"bytes":10214,"path":"scripts/comparison/freeze_headless_timing_calibration_epoch_v1.py","sha256":"6eecbe99d59576f03bb6b893a0ff5b6a24da3aa72f977f8051dfe4b77ac4e5e5"}
_CACHE:dict[str,types.ModuleType]={}

class TimingRunnerError(ValueError): pass

def _sha(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()

def _load(root:Path,identity:Mapping[str,Any],name:str)->types.ModuleType:
    raw=root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    if len(raw)!=identity["bytes"] or _sha(raw)!=identity["sha256"]: raise TimingRunnerError(f"{name} identity differs")
    m=types.ModuleType(name);m.__file__=identity["path"];sys.modules[name]=m
    exec(compile(raw,m.__file__,"exec",dont_inherit=True,optimize=0),m.__dict__);return m

def _flatten(plan:Mapping[str,Any])->list[tuple[Mapping[str,Any],int]]:
    s=plan["schedule"]
    if set(s)!={"execution_order","inventories","prior_task_evidence_gate","warmups"} or s["execution_order"]!=["warmups","headless_timing"] or s["prior_task_evidence_gate"]!={"authenticated":True,"scene_count":100}: raise TimingRunnerError("timing schedule differs")
    w=s["warmups"]; inv=s["inventories"]["headless_timing"]
    if w["process_count"]!=10 or len(w["slots"])!=10 or inv["measured_process_count"]!=600 or len(inv["measured"])!=600: raise TimingRunnerError("timing denominator differs")
    return [(x,i) for i,x in enumerate(w["slots"])]+[(x,i) for i,x in enumerate(inv["measured"])]

def _base(root:Path)->types.ModuleType:
    key=os.fspath(root)
    if key in _CACHE:return _CACHE[key]
    m=_load(root,BASE,"_dancers_timing_runner_base")
    m.EPOCH_SCHEMA=SCHEMA;m.TRACK_ID=TRACK_ID;m.PHASE=PHASE;m.RUN_CLASSES=RUN_CLASSES;m.PLAN_ROOT=PLAN_ROOT;m.RESULT_ROOT=RESULT_ROOT;m._flatten_schedule=_flatten
    _CACHE[key]=m;return m

def load_authenticated_plan(plan_path:Path,repository_root:Path,*,production:bool,committed_input_checker:Callable[[Path,Path],None]|None=None)->Any:
    root=Path(os.path.abspath(repository_root)); absolute=Path(os.path.abspath(plan_path))
    raw=absolute.read_bytes(); freezer=_load(root,FREEZER,"_dancers_timing_plan_freezer"); value=freezer.validate_epoch_bytes(raw)
    if absolute!=root.joinpath(*PLAN_ROOT.parts,"plan.json"): raise TimingRunnerError("timing plan path differs")
    for identity in value["inputs"].values():
        rows=identity if type(identity) is list else [identity]
        for row in rows:
            observed=root.joinpath(*PurePosixPath(row["path"]).parts).read_bytes()
            if len(observed)!=row["bytes"] or _sha(observed)!=row["sha256"]: raise TimingRunnerError("timing input identity differs")
    runtime_parent=json.loads(root.joinpath(*PurePosixPath(value["inputs"]["runtime_parent"]["path"]).parts).read_bytes())
    value["input_records"]["activation_receipt"]=runtime_parent["input_records"]["activation_receipt"]
    base=_base(root)
    parse_value=json.loads(json.dumps(value))
    originals={}
    for scene in parse_value["scenes"]:
        family=scene["family"];count=scene["robot_count"];seed=scene["seed"]
        originals[scene["scene_id"]]=(base._identity_from_record(scene["config"],"timing config"),base._identity_from_record(scene["scenario"],"timing scenario"))
        scene["scenario"]["path"]=(PLAN_ROOT/"missions"/family/f"seed-{seed:04d}"/f"n-{count}"/"scenario.json").as_posix()
        scene["config"]["path"]=(PLAN_ROOT/"configs"/family/f"n-{count}"/f"seed-{seed:02d}"/"config.yaml").as_posix()
    for slot,_index in _flatten(parse_value):
        scene=next(row for row in parse_value["scenes"] if row["scene_id"]==slot["scene_id"])
        slot["config"]=dict(scene["config"])
    schedule=tuple(replace(item,config=originals[item.scene_id][0],scenario=originals[item.scene_id][1]) for item in base._parse_schedule(parse_value))
    for attempt in schedule:
        for identity in (attempt.binary,attempt.config,attempt.map_artifact,attempt.scenario): base._verify_identity(root,identity,"timing scheduled artifact")
    if production:
        if value["status"].get("registration_state")!="registered-committed" or value["classification"].get("plan_registered") is not True or value["classification"].get("timing_execution_authorized") is not False or committed_input_checker is None: raise TimingRunnerError("registered timing plan state differs")
        committed_input_checker(absolute,root)
    return base.AuthenticatedPlan(root,absolute,raw,_sha(raw),value,schedule,RESULT_ROOT)

def execute_custodied_schedule(plan:Any,session_path:Path,**kwargs:Any)->dict[str,Any]:
    base=_base(plan.repository_root); original=base._execute_attempt
    def admitted(*args:Any,**inner:Any)->dict[str,Any]:
        attempt=args[1]
        if attempt.run_class=="headless_timing" and attempt.attempt_kind=="measured":
            inner["digest_success"].add((attempt.raw["input_id"],attempt.method,attempt.repetition))
            inner["positions_success"].add((attempt.raw["input_id"],attempt.method))
        return original(*args,**inner)
    base._execute_attempt=admitted
    try:return base.execute_custodied_schedule(plan,session_path,**kwargs)
    finally:base._execute_attempt=original

def main()->int:
    print("timing runner remains nonauthorizing; no plan read, host probe, output, or process is permitted",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
