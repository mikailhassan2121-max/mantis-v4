# Phase 9 UI Architecture

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**  
> **OBSERVATION ONLY — NO AUTOMATED EXECUTION**

Phase 9 adds presentation. It changes no quantitative behaviour. The locked Phase 6 classification policy, the Phase 7 economic formulas, the Phase 8 append-only logging semantics and `mantis_15m_resolution_v3.py` are all byte-for-byte unchanged, and `tests/test_phase9_ui.py` asserts each of those independently.

## Framework decision

**Chosen: Rich 13+ (`rich.live.Live` driving a `rich.layout.Layout`).**

| Option | Verdict |
|---|---|
| **Rich** | Chosen. Already a declared dependency since Phase 2 (`requirements.txt` says "full UI lands in Phase 9"), so no new install for the operator. Synchronous render model: the scanner keeps a plain thread and the renderer is a second plain thread. Renders into a fixed-size `Layout`, handles terminal resize per frame, and degrades to ASCII on legacy consoles by itself. |
| **Textual** | Rejected. It is a strong TUI framework, but it owns an `asyncio` event loop. Adopting it means either running the 5-second scan inside that loop — where a slow provider call blocks the interface — or bridging threads to an event loop for no visible benefit. It is also a new dependency for a Windows operator we are explicitly trying to keep to `pip install -r requirements.txt`. The screen we need is a dense fixed dashboard, not an interactive widget tree. |
| **Web stack (FastAPI + browser)** | Rejected. The user asked for a dedicated command-center application. A browser UI adds a server, a port, a build step, and a second process to supervise, in exchange for styling we can already achieve. |
| **curses / blessed** | Rejected. Poor Windows story; Rich already sits on top of the same capability. |

The decision is reversible: `mantis_v4/ui/panels.py` holds the renderables and `mantis_v4/ui/dashboard.py` holds the layout and the loop. `mantis_v4/ui/state.py` — the actual read model — is framework-free.

## Module layout

```text
mantis_v4/ui/
  settings.py    PresentationConfig: file -> environment -> CLI
  theme.py       palette, glyphs, decision-state descriptors, branding, reason text
  state.py       CommandCenterState / AssetView / UiSnapshot   (no framework, no math)
  panels.py      Rich renderables, one function per panel
  dashboard.py   CommandCenter: layout assembly + isolated render thread
  startup.py     initialization sequence
  alerts.py      Severity levels and EventType -> presentation routing
  audio.py       non-blocking tone subsystem
  voice.py       non-blocking speech subsystem
  errors.py      operator-facing error blocks + diagnostic log
  demo.py        synthetic states, structurally unable to reach the forward logs
```

## Separation of concerns

```text
QUANT        compute_asset_state()          mantis_v4_live.py  (unchanged expressions)
PROVIDERS    market data + economics chain  mantis_v4/providers, mantis_v4/economics
STATE        ForwardEngine -> ForwardStore  mantis_v4/forward   (unchanged)
HOOKS        EventBus                       mantis_v4/forward/events.py
PRESENTATION CommandCenterState -> panels   mantis_v4/ui
AUDIO/VOICE  AudioEngine, VoiceEngine       mantis_v4/ui
```

The presentation layer consumes exactly one backend type — `LiveSnapshot` — plus the seven Phase 8 hooks. `view_from_snapshot` is field copying; there is no arithmetic in it. The only derived quantities anywhere in the UI are:

- the countdown, `window_end - now`, where `window_end` is the backend's own authoritative resolution timestamp;
- bar fill widths and percentage formatting;
- `AssetView.buffer_z`, a display normalisation of three published snapshot fields. It mirrors what `ForwardEngine` feeds to `RiskDiagnostics`, is shown only in the diagnostics panel, is read by no UI decision, and `test_buffer_z_matches_the_value_the_engine_gives_the_policy` pins it against the engine so the two cannot drift apart silently.

`test_ui_package_imports_no_decision_mathematics` enforces that no module under `mantis_v4/ui/` imports `..entry`, `..simulation`, `..models`, `..backtest` or `numpy`.

## Concurrency and isolation

The scanner owns the main thread. Four daemon threads exist beside it:

| Thread | Owns | Failure behaviour |
|---|---|---|
| `MANTIS-Render` | one `Live` screen, redraws at `UI_REFRESH_RATE` | every frame wrapped; after 5 consecutive failures the interface disables itself and the scan continues |
| `MANTIS-Audio` | bounded queue (8), tone playback | device errors counted; 3 failures mark the subsystem unavailable |
| `MANTIS-Voice` | bounded queue (4), PowerShell speech | same; a wedged synthesiser sheds the oldest line |
| `MANTIS-Forward` | periodic `forward_report` recomputation | wrapped; a slow report never touches the scan cadence |

Interaction rules:

1. The scan thread only **writes** to `CommandCenterState` under its lock; the render thread only **reads** an immutable `UiSnapshot` copy. No mutable object is shared.
2. Both queues drop the **oldest** pending item when full. Back-pressure never reaches the caller — `play()` and `say()` return a boolean and never block or raise.
3. `AlertRouter._safe_handle` wraps every hook handler, because `EventBus.emit` calls handlers inline on the scanner's thread. `test_alert_handler_failure_does_not_escape_to_the_engine` drives a state object whose every method raises and asserts the engine still writes its observation and entry rows.
4. `mantis_v4_live.py` wraps each asset's scan in `try/except`, converts the exception into an `ON_ERROR` hook plus a diagnostics-log entry, marks that asset `DATA HOLD`, and continues with the next asset.

## Performance

- Renders are frame-based `Live` updates, not `print` — no full-screen flicker.
- Default `UI_REFRESH_RATE` is 4 Hz: smooth enough for a seconds countdown, far below the cost of a redraw.
- In-memory event history is a bounded `deque` (`EVENT_LOG_LENGTH`, default 200) and per-asset probability history is capped at 60 points. The append-only JSONL logs on disk remain the complete record.
- The forward-validation summary is recomputed off-thread on an interval (default 60 s) rather than per scan, because it reads the whole observation file.
- Every panel column is `no_wrap` with ellipsis overflow. A wrapped value would silently double a panel's height and push content out of its fixed layout band.

## Terminal support

Target is 1920×1080 (roughly 200×50 cells). The layout adapts:

| Condition | Behaviour |
|---|---|
| ≥ 150 cols and ≥ 44 rows | five full asset cards, block countdown, three-panel top row |
| ≥ 116 cols | three-panel top row (decision / economics / provider) |
| < 116 cols | provider status moves to the bottom auxiliary slot rather than being clipped |
| short terminals | asset cards collapse to one compact row per asset; all five assets always stay visible, the decision band gives way first |
| < 96 × 30 | a `TERMINAL TOO SMALL` panel with the current and required sizes and the `--no-ui` hint. The scanner keeps running |
| legacy console / non-UTF code page | the whole visual language degrades to ASCII glyphs together |

`test_renders_across_a_range_of_terminal_sizes` renders at seven sizes from 96×30 to 400×90 and asserts no line exceeds the terminal width.
