#!/usr/bin/env python3
"""Bind the frozen validation analyzer to the raster-repair plan identity."""
from __future__ import annotations
import hashlib,os,sys,types
from pathlib import Path,PurePosixPath
from typing import Any
sys.dont_write_bytecode=True
BASE={"bytes":18359,"path":"scripts/comparison/analyze_headless_validation_epoch_v2.py","sha256":"65921e7c50236b00a7d78cbe7f8425a2ca2359356a3810191f0d42d3df1a75ff"}
PLAN={"bytes":4618050,"path":"comparison/plans/simulator-demo/v1/headless-epoch-5-validation/plan.json","sha256":"b3a30c33581382014e8a0e35b1c9ce84f7e7de4daffddff18eefea0dd3e459e8"}
def sha(b):return hashlib.sha256(b).hexdigest()
def load(root:Path):
 b=(root/BASE["path"]).read_bytes()
 if len(b)!=BASE["bytes"] or sha(b)!=BASE["sha256"]:raise ValueError("validation analyzer identity differs")
 m=types.ModuleType("_raster_repair_analysis");m.__file__=BASE["path"];sys.modules[m.__name__]=m;exec(compile(b,m.__file__,"exec"),m.__dict__);m.PLAN=dict(PLAN);return m
def analyze_validation_session(repository_root:Path,session_path:Path,*,draws:int=100000)->dict[str,Any]:
 root=Path(os.path.abspath(repository_root));return load(root).analyze_validation_session(root,session_path,draws=draws)
def canonical_json_bytes(value:object)->bytes:return load(Path(__file__).resolve().parents[2]).canonical_json_bytes(value)
def main()->int:print("raster-repair validation analysis requires committed authorization",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
