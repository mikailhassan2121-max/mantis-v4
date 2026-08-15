"""Append-only JSONL source of truth with restart recovery and no update path."""
from __future__ import annotations
import csv,json,os,threading
from pathlib import Path
from typing import Iterable
class ForwardStore:
 FILES={"observations":"observations.jsonl","entries":"entries.jsonl","resolutions":"resolutions.jsonl","provider_health":"provider_health.jsonl","runs":"runs.jsonl","window_events":"window_events.jsonl","session_events":"session_events.jsonl"}
 # Existing Phase 8 rows are implicit version 1. This registry documents that
 # contract without rewriting historical append-only files.
 SCHEMA_VERSIONS={name:1 for name in FILES}
 def __init__(self,directory:Path):
  self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True); self._lock=threading.RLock()
  self._ids={name:self._load_ids(name) for name in self.FILES}
  self._entry_keys={(x.get("contract_id"),x.get("asset")) for x in self.read("entries")}
  self._resolved_keys={(x.get("contract_id"),x.get("asset")) for x in self.read("resolutions")}
  self._observed_contracts={}
  for row in self.read("observations"): self._observed_contracts.setdefault((row.get("contract_id"),row.get("asset")),row)
 def path(self,name): return self.directory/self.FILES[name]
 def read(self,name)->list[dict]:
  if name not in self.FILES: raise KeyError(f"unknown forward record type {name!r}")
  with self._lock:
   out=[]; p=self.path(name)
   if not p.exists(): return out
   lines=p.read_text(encoding="utf-8").splitlines()
   for lineno,line in enumerate(lines,1):
     if not line.strip(): continue
     try: value=json.loads(line)
     except json.JSONDecodeError:
      if lineno==len(lines): break # recover a truncated final append only
      raise ValueError(f"malformed {p.name} line {lineno}")
     if not isinstance(value,dict): raise ValueError(f"malformed {p.name} line {lineno}")
     out.append(value)
   return out
 def _load_ids(self,name):
  keys={"observations":"observation_id","entries":"entry_id","resolutions":"resolution_id","runs":"run_id","provider_health":"health_id","window_events":"window_event_id","session_events":"session_event_id"}
  return {str(x.get(keys[name])) for x in self.read(name) if x.get(keys[name]) is not None}
 def append(self,name,record:dict,id_field:str)->bool:
  if name not in self.FILES: raise KeyError(f"unknown forward record type {name!r}")
  with self._lock:
   identifier=str(record[id_field])
   if identifier in self._ids[name]: return False
   payload=json.dumps(record,separators=(",",":"),sort_keys=True,allow_nan=False)
   with self.path(name).open("a",encoding="utf-8",newline="\n") as f:
    f.write(payload+"\n"); f.flush(); os.fsync(f.fileno())
   self._ids[name].add(identifier)
   key=(record.get("contract_id"),record.get("asset"))
   if name=="entries": self._entry_keys.add(key)
   if name=="resolutions": self._resolved_keys.add(key)
   if name=="observations": self._observed_contracts.setdefault(key,record)
   return True
 def has_entry(self,contract_id,asset):
  with self._lock: return (contract_id,asset) in self._entry_keys
 def has_resolution(self,contract_id,asset):
  with self._lock: return (contract_id,asset) in self._resolved_keys
 def unresolved_contracts(self,asset=None):
  with self._lock:
   return [row for key,row in self._observed_contracts.items() if key not in self._resolved_keys and (asset is None or key[1]==asset)]
 def unresolved_entries(self):
  with self._lock:
   return [x for x in self.read("entries") if (x.get("contract_id"),x.get("asset")) not in self._resolved_keys]
