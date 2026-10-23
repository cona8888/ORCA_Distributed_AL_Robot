#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,subprocess,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
A=PurePosixPath("docs/simulator/headless_timing_calibration_epoch_4_analysis_v1.json");AN={"bytes":4511,"path":"scripts/comparison/analyze_headless_timing_calibration_epoch_v2.py","sha256":"101546697196b1d534dd2facf6858b7a4adb09dea8fbb964e354675777e3f9df"};OUT=PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-4-timing-calibration/paired-timing-calibration-session-001-analysis.json")
def sha(b):return hashlib.sha256(b).hexdigest()
def canon(v):return (json.dumps(v,allow_nan=False,separators=(",",":"),sort_keys=True)+"\n").encode()
def main():
 root=Path(__file__).resolve().parents[2];raw=(root.joinpath(*A.parts)).read_bytes();v=json.loads(raw);source=(root/AN["path"]).read_bytes()
 if raw!=canon(v) or v.get("analyzer")!=AN or v.get("output")!=OUT.as_posix() or v.get("one_use") is not True or v.get("scientific_claim_authorized") is not False or v.get("validation_claim_authorized") is not False or v.get("benchmark_claim_authorized") is not False or len(source)!=AN["bytes"] or sha(source)!=AN["sha256"]:raise SystemExit("timing analysis authorization differs")
 if subprocess.run(["git","status","--porcelain","--untracked-files=no"],cwd=root,stdout=subprocess.PIPE).stdout:raise SystemExit("tracked tree differs")
 for rel in (A.as_posix(),AN["path"],Path(__file__).resolve().relative_to(root).as_posix()):
  if subprocess.run(["git","show",f"HEAD:{rel}"],cwd=root,stdout=subprocess.PIPE).stdout!=(root/rel).read_bytes():raise SystemExit(f"{rel} not exact HEAD")
 m=types.ModuleType("_authorized_timing_analysis");m.__file__=AN["path"];sys.modules[m.__name__]=m;exec(compile(source,m.__file__,"exec"),m.__dict__);report=m.analyze(root);report_raw=canon(report);path=root.joinpath(*OUT.parts)
 if path.exists() or path.is_symlink():raise SystemExit("timing analysis output exists")
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o444)
 try:os.write(fd,report_raw);os.fsync(fd)
 finally:os.close(fd)
 sys.stdout.buffer.write(canon({"analysis_authorization_sha256":sha(raw),"benchmark_claim_ready":False,"output":{"bytes":len(report_raw),"path":OUT.as_posix(),"sha256":sha(report_raw)},"schema_version":"dancers-headless-timing-calibration-analysis-receipt-v1","scientific_evidence":False,"validation_claim_ready":False}));return 0
if __name__=="__main__":raise SystemExit(main())
