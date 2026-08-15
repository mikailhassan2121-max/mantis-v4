"""Priority-ordered verified economics providers; proxy can never unlock EV."""
from __future__ import annotations
from abc import ABC,abstractmethod
from datetime import datetime
import json,math
from pathlib import Path
from typing import Optional,Sequence
from ..clock import parse_iso_utc
from ..config import WebullCredentials
from .models import ContractEconomics,FeeStatus,SlippageStatus
from .fees import WEBULL_EVENT_FEE_PROVENANCE,WEBULL_EVENT_OPENING_FEE

class EconomicsProvider(ABC):
    name="economics"; priority=100
    status="UNAVAILABLE"
    @abstractmethod
    def get_economics(self,asset:str,contract_id:str,now:datetime)->Optional[ContractEconomics]: ...

class WebullEconomicsProvider(EconomicsProvider):
    """Official seam. No credentials means deterministic clean degradation."""
    name="webull-official"; priority=10
    def __init__(self,credentials:WebullCredentials):
        self.credentials=credentials
        self.status="AUTH_NOT_CONFIGURED" if not credentials.is_configured else "SCHEMA_MAPPING_REQUIRES_LIVE_VALIDATION"
    def get_economics(self,asset,contract_id,now): return None

class ManualEconomicsProvider(EconomicsProvider):
    name="verified-manual"; priority=50
    def __init__(self,path:Path): self.path=Path(path); self.status="AVAILABLE" if self.path.exists() else "UNAVAILABLE"
    def _records(self):
        if not self.path.exists(): return []
        try:
            raw=json.loads(self.path.read_text(encoding="utf-8"))
            return raw.get("contracts",[]) if isinstance(raw,dict) else raw
        except (OSError,json.JSONDecodeError): self.status="INVALID"; return []
    def _parse(self,r:dict)->Optional[ContractEconomics]:
        required=("asset","contract_id","reference","resolution_time","yes_ask","yes_bid","no_ask","no_bid","payout","quote_timestamp","settlement_rule","source","verified")
        if any(k not in r for k in required) or r.get("verified") is not True: return None
        qt=parse_iso_utc(str(r["quote_timestamp"])); rt=parse_iso_utc(str(r["resolution_time"]));
        if qt is None or rt is None: return None
        try:
            vals={k:float(r[k]) for k in ("reference","yes_ask","yes_bid","no_ask","no_bid","payout")}
            if not all(math.isfinite(x) for x in vals.values()): return None
            fs=FeeStatus(str(r.get("fees_status","UNKNOWN_FEES")))
            ss=SlippageStatus(str(r.get("slippage_status","SLIPPAGE_NOT_MODELED")))
            return ContractEconomics(asset=str(r["asset"]),contract_id=str(r["contract_id"]),reference=vals["reference"],resolution_time=rt,
              settlement_rule=str(r["settlement_rule"]),yes_bid=vals["yes_bid"],yes_ask=vals["yes_ask"],no_bid=vals["no_bid"],no_ask=vals["no_ask"],payout=vals["payout"],quote_timestamp=qt,quote_source=str(r["source"]),reference_verified=True,quote_verified=True,
              yes_depth=float(r["yes_depth"]) if r.get("yes_depth") is not None else None,no_depth=float(r["no_depth"]) if r.get("no_depth") is not None else None,
              fee_per_contract=float(r["fee_per_contract"]) if r.get("fee_per_contract") is not None else float(WEBULL_EVENT_OPENING_FEE),fee_status=fs if r.get("fee_per_contract") is not None else FeeStatus.VERIFIED,
              fee_provenance=str(r.get("fee_provenance") or ("WEBULL_ORDER_PREVIEW" if r.get("fee_per_contract") is not None else WEBULL_EVENT_FEE_PROVENANCE)),
              slippage_per_contract=float(r["slippage_per_contract"]) if r.get("slippage_per_contract") is not None else None,slippage_status=ss)
        except (ValueError,TypeError): return None
    def get_economics(self,asset,contract_id,now):
        for r in self._records():
            if str(r.get("asset"))==asset and str(r.get("contract_id"))==contract_id: return self._parse(r)
        return None

class ProxyEconomicsProvider(EconomicsProvider):
    name="underlying-proxy"; priority=900; status="ECONOMICS_UNAVAILABLE"
    def get_economics(self,asset,contract_id,now): return None

class EconomicsProviderChain:
    def __init__(self,providers:Sequence[EconomicsProvider]): self.providers=sorted(providers,key=lambda p:p.priority); self.last_provider=None
    def get_economics(self,asset,contract_id,now):
        for p in self.providers:
            try: value=p.get_economics(asset,contract_id,now)
            except Exception: continue
            if value is not None: self.last_provider=p.name; return value
        self.last_provider=None; return None
