"""Central, provenance-labelled Webull event-contract opening fee schedule."""
from decimal import Decimal

WEBULL_EVENT_EXCHANGE_FEE = Decimal("0.01")
WEBULL_EVENT_FIRM_FEE = Decimal("0.01")
WEBULL_EVENT_OPENING_FEE = WEBULL_EVENT_EXCHANGE_FEE + WEBULL_EVENT_FIRM_FEE
WEBULL_EVENT_FEE_PROVENANCE = "WEBULL_OFFICIAL_FEE_SCHEDULE"
WEBULL_EVENT_FEE_SOURCE = "https://developer.webull.com/apis/docs/trade-api/event-contract/"

