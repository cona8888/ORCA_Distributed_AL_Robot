#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,sys,types
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
AUTH={"bytes":1619,"path":"docs/simulator/headless_validation_epoch_5_raster_repair_analysis_authorization_v1.json","sha256":"09014be643e1f1e2e370a4817de61e1c6b2a4b5e2bc5d71a67bad7a1d27e3685"}
def sha(b):return hashlib.sha256(b).hexdigest()
def read(root,i,label):
 b=(root/i["path"]).read_bytes()
 if len(b)!=i["bytes"] or sha(b)!=i["sha256"]:raise ValueError(f"{label} identity differs")
 return b
def main():
 root=Path(__file__).resolve().parents[2];raw=read(root,AUTH,"analysis authority");v=json.loads(raw)
 if raw!=(json.dumps(v,separators=(",",":"),sort_keys=True)+"\n").encode() or v["authority"]!={"analysis_authorized":True,"benchmark_claim_authorized":False,"capacity_claim_authorized":False,"execution_authorized":False,"result_publication_authorized":True}:raise SystemExit("analysis authority differs")
 for key in ("execution_receipt","plan"):read(root,v[key],key)
 for key in ("ledger","session_record"):read(root,v["session"][key],key)
 i={"bytes":v["analysis"]["source"]["bytes"],"path":v["analysis"]["path"],"sha256":v["analysis"]["source"]["sha256"]};source=read(root,i,"analyzer");m=types.ModuleType("_authorized_raster_repair_analysis");m.__file__=i["path"];sys.modules[m.__name__]=m;exec(compile(source,m.__file__,"exec"),m.__dict__)
 report=m.analyze_validation_session(root,root/v["session"]["path"],draws=v["analysis"]["bootstrap_draw_count"]);out=root/v["output"];data=m.canonical_json_bytes(report);fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o444)
 try:
  view=memoryview(data)
  while view:
   n=os.write(fd,view)
   if n<=0:raise OSError("write made no progress")
   view=view[n:]
  os.fsync(fd)
 except BaseException:
  try:os.close(fd)
  finally:out.unlink(missing_ok=True)
  raise
 os.close(fd);print(json.dumps({"analysis_bytes":len(data),"analysis_sha256":sha(data),"operation":"publish-validation-raster-repair-analysis","output":v["output"],"schema_version":"dancers-headless-validation-raster-repair-analysis-publication-receipt-v1","validation_outcome":report["status"]["validation_outcome"]},separators=(",",":"),sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
