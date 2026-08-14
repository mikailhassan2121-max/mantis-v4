from dataclasses import dataclass
from enum import Enum
from typing import Any,Callable
class EventType(str,Enum):
 ENTRY_YES="ON_ENTRY_YES"; ENTRY_NO="ON_ENTRY_NO"; WAIT="ON_WAIT"; DATA_HOLD="ON_DATA_HOLD"
 ROLLOVER="ON_CONTRACT_ROLLOVER"; RESOLUTION="ON_RESOLUTION"; ERROR="ON_ERROR"
@dataclass(frozen=True)
class AppEvent: type:EventType; timestamp_utc:str; payload:dict[str,Any]
class EventBus:
 def __init__(self): self._handlers={x:[] for x in EventType}
 def subscribe(self,event:EventType,handler:Callable[[AppEvent],None]): self._handlers[event].append(handler)
 def emit(self,event:AppEvent):
  for handler in tuple(self._handlers[event.type]): handler(event)
