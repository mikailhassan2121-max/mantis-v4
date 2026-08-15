# Phase 10 — Production Hardening Audit

Phase 10 begins from clean commit `2c68155`. This phase changes runtime and
operator infrastructure only. Phase 6 classification mathematics, Phase 7
economics formulas, Phase 8 forward semantics, the accepted Phase 9 visual
design, and `mantis_15m_resolution_v3.py` are protected.

## Baseline inventory

### Entry points

- `mantis_v4_live.py` — live observer, demo, one-shot and alert-test modes.
- `mantis_v4_backtest.py`, `mantis_v4_phase4.py`, `mantis_v4_phase5.py`,
  `mantis_v4_phase6.py` — offline research utilities; not production workers.
- `mantis_15m_resolution_v4.py` — earlier V4 application entry point.

### Runtime workers and resources

| Owner | Resource | Baseline lifecycle |
|---|---|---|
| live runner | scanner | main thread; `KeyboardInterrupt` reaches `finally` |
| command center | Rich renderer | bounded daemon thread; explicit `stop()` |
| web server | HTTP acceptor | daemon thread; explicit `shutdown/server_close` |
| web server | SSE publisher | daemon thread; bounded per-client backlog |
| `ThreadingHTTPServer` | request/SSE clients | daemon request threads; disconnect unregisters client |
| audio | cue worker | bounded queue, daemon, explicit `stop()` |
| voice | speech worker | bounded queue, daemon, subprocess timeout, explicit `stop()` |
| live runner | forward reporter | daemon thread with stop event, but no retained thread/join |
| browser shell | Chromium child | launched once, but ownership was not retained for shutdown |

### Persistent writes

- `data/forward/*.jsonl`: append-only observations, entries, resolutions,
  provider health and run metadata. Each append flushes and `fsync`s.
- `data/logs/mantis_ui_diagnostics.log`: technical tracebacks; unbounded at baseline.
- `data/ui-profile/`: dedicated Chromium profile/session state.
- Legacy recording/backtest commands can write SQLite, CSV, datasets and model
  artifacts, but the production live runner does not open those paths.

### Configuration precedence

- Quant/runtime: dataclass defaults → `config/mantis_v4.local.json` → environment.
- Presentation: dataclass defaults → `config/mantis_v4.ui.json` → environment → CLI.
- Webull secrets: `WEBULL_APP_KEY` and `WEBULL_APP_SECRET`; repr/status paths redact values.

### Baseline recovery and isolation

- Forward JSONL reads ignore only a malformed final line; malformed interior rows fail closed.
- Deterministic IDs prevent duplicate observations/entries after restart.
- Browser/SSE/audio/voice failures are presentation-isolated.
- Provider retry queues and UI event queues are bounded.

## Gaps selected for Phase 10

1. Joinable reporter/browser ownership and concise, deterministic shutdown status.
2. Rotating diagnostic log with no rotation of raw forward JSONL.
3. Safe `--health-check` and `--self-test` modes that cannot touch real forward data.
4. Stronger config types/ranges/assets/timezone/path validation and small runtime profiles.
5. Browser duplicate-launch prevention and explicit browser-child cleanup.
6. Deterministic accelerated soak/failure-injection coverage.
7. Release/install scripts, runtime-file documentation and beginner launch path.

Phase 10 will not add order routes, broker mutations, strategy tuning, or remote
server access.

## Implemented hardening

- joinable forward reporter handle and explicit ownership for renderer, HTTP,
  SSE, audio, voice and the dedicated Chromium child;
- concise Ctrl+C/normal-exit subsystem report;
- localhost host validation, sixteen-client SSE ceiling and bounded frame queues;
- browser URL validation, single-window guard and intentional-close behavior;
- thread-safe forward store with cached contract-entry/resolution keys; raw
  JSONL remains append-only with flush + fsync and no automatic rotation;
- persistence `OSError` changes the system to DATA HOLD and stops the scanner;
- rotating technical diagnostic log (2 MB, three backups by default);
- stricter asset, timezone, type, range and path validation;
- `default`, `quiet` and `diagnostic` presentation/runtime profiles;
- redacted `--health-check`, temp-only `--self-test`, and isolated soak harness;
- provider cooldown after exhausted network retries;
- MANTIS 4.10.0 in package, UI and diagnostics. Phase 8 run records retain
  `MANTIS_V4_PHASE8` as frozen provenance rather than being rewritten.

## Performance and boundedness findings

The accelerated test executes 200 scans across 100 contract windows in a few
seconds, with one entry and resolution per contract, no unresolved or duplicate
entries, no worker-thread growth, and under 64 MB peak traced allocation. The
default utility run covers 500 scans across 250 windows.

Presentation serialization and SSE remain off the scanner thread. Each SSE
client holds at most eight frames and total SSE clients are capped at sixteen.
Audio/voice queues and retained diagnostic histories were already bounded.

Forward-report generation still reads the full historical observation sample
periodically. This is intentional for statistical reporting and may eventually
need incremental aggregation for multi-year samples; it does not accumulate a
resident list between report cycles.

## Schema compatibility

The five Phase 8 JSONL record families are registered as schema version 1:
observations, entries, resolutions, provider health and runs. Existing rows are
implicitly version 1. Phase 10 does not inject new fields or rewrite historical
rows. A future incompatible schema must use a new explicit version and a
read-compatible migration tool rather than editing files in place.
