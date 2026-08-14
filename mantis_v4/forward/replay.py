"""Historical-data harness for live mechanics only; never tunes or scores policy."""
from __future__ import annotations
from collections.abc import Iterable
from .engine import ForwardEngine
from .models import LiveAssetState
class ForwardReplayHarness:
 def __init__(self,engine:ForwardEngine): self.engine=engine
 def run(self,states:Iterable[LiveAssetState]):
  ordered=list(states)
  if any(ordered[i].scan_timestamp>ordered[i+1].scan_timestamp for i in range(len(ordered)-1)):
   raise ValueError("replay states must be chronological")
  snapshots=[]
  for state in ordered: snapshots.append(self.engine.process(state))
  return snapshots
