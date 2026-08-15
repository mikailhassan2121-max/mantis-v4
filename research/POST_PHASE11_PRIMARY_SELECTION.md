# Post-Phase-11 Primary Selection Policy

Normal live surveillance is exactly BTC, ETH, SOL and XRP. ADA remains valid historical research
data but is not a live candidate. Frozen policy `H_p0.95_l0.90_f50_d.05_t300` still qualifies each
asset independently. `PRIMARY_SELECTOR_V1` then requires executable side-specific economics and
chooses at most one candidate using conservative edge, net EV, conservative probability, point
probability, fragility, disagreement, crossing probability, crossings, and deterministic asset order.

The official Webull Event Contract documentation, verified 2026-08-15, states a $0.01 exchange fee
plus $0.01 firm fee per contract on opening and closing transactions. MANTIS hold-to-resolution
economics therefore applies one $0.02 opening transaction fee. It does not invent a closing fee for
automatic settlement. Source: https://developer.webull.com/apis/docs/trade-api/event-contract/

The centralized provenance is `WEBULL_OFFICIAL_FEE_SCHEDULE`. A future authenticated order preview
may supersede the schedule for that transaction, with separate provenance. Current Webull economics
provider code has no validated authenticated event-instrument/book mapping and returns no quote.
Without a fresh verified YES/NO ask, the selector reports `QUOTE_UNAVAILABLE` and emits no trade.

Per-asset observations and qualifications remain append-only. Operator actions are separately stored
in `primary_selections.jsonl` under policy `PRIMARY_SELECTOR_V1`; prior Phase 11 records are not
relabelled or combined with the new selector policy. This remains advisory and has no order endpoint.
