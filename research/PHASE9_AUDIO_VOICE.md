# Phase 9 Audio and Voice

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**  
> **OBSERVATION ONLY — NO AUTOMATED EXECUTION**

Audio and voice are advisory notifications. Neither can start, stop, alter or delay a scan, and neither carries any trading action.

## Alert severity

| Level | Meaning | Default treatment |
|---|---|---|
| `INFO` | routine state, no operator action | log only, plus one soft tone for rollover |
| `NOTICE` | something concluded | log + short tone + announcement |
| `ACTION` | an actionable advisory | log + distinct tone + announcement |
| `WARNING` | degraded capability or held data | log + warning tone + announcement |
| `CRITICAL` | provider or system failure | log + urgent tone + announcement |

## Hook routing

`mantis_v4/ui/alerts.py` declares the whole policy in one table, so it is auditable in one place.

| Phase 8 hook | Severity | Tone | Spoken by default |
|---|---|---|---|
| `ON_CONTRACT_ROLLOVER` | INFO | `rollover` | no |
| `ON_RESOLUTION` | NOTICE | `resolution` | yes |
| `ON_ENTRY_YES` | ACTION | `enter_yes` | yes |
| `ON_ENTRY_NO` | ACTION | `enter_no` | yes |
| `ON_WAIT` | INFO | — (silent) | no |
| `ON_DATA_HOLD` | WARNING | `data_hold` | yes |
| `ON_ERROR` | CRITICAL | `error` | yes |

Provider-health degradation is routed separately as a `WARNING` with the `provider_failure` tone, because it is not a decision event.

Hooks feed visual state, the event log, audio and voice. They contain no trading logic and they write nothing to the forward store.

## Tone design

Cues are distinguished by **shape**, not by loudness. Each is under half a second.

| Cue | Sequence (Hz × ms) | Shape |
|---|---|---|
| `enter_yes` | 784×90, 1047×130 | ascending pair — action, positive |
| `enter_no` | 784×90, 523×130 | descending pair — action, distinct |
| `data_hold` | 330×120, 330×120 | flat low double — technical |
| `rollover` | 587×70 | single soft mid — informational |
| `resolution` | 659×70, 784×110 | short two-step — notice |
| `provider_failure` | 440×110, 349×160 | falling pair — warning |
| `error` | 262×130, 262×130, 262×200 | low triple — critical |

`WAIT` is silent by design. It is the most common state; a tone on every wait would be the definition of alarm fatigue. It still appears in the event log, and repeated identical wait reasons for one asset collapse to a single line.

## Anti-fatigue policy

- `AUDIO_MIN_INTERVAL_SECONDS` (default 15) throttles per cue **and** asset, so a provider outage across five assets cannot produce a stream of beeps.
- `VOICE_MIN_INTERVAL_SECONDS` (default 30) does the same for speech.
- Rollover is not spoken by default: it happens four times an hour per asset.
- The engine already emits at most one entry event per asset per contract, so `ENTER` announcements are naturally rare.

## Audio subsystem

`mantis_v4/ui/audio.py`. One daemon thread owns playback; callers only enqueue.

- Backend is `winsound.Beep` on Windows, with a terminal-bell fallback elsewhere and an injectable backend for tests.
- The queue holds 8 cues and drops the **oldest** pending cue when full. `play()` returns a boolean and never blocks or raises.
- Three consecutive device failures mark the subsystem unavailable; the provider panel then shows audio as off and the scan is unaffected.
- `winsound.Beep` has no amplitude control, so `MASTER_VOLUME` modulates tone duration and gates playback entirely at zero, rather than pretending to set a level.
- `AUDIO_ENABLED=false` substitutes `NullAudioEngine`, which is guaranteed silent and starts no thread at all.

## Voice subsystem

`mantis_v4/ui/voice.py`. The Windows `System.Speech` synthesiser through a short-lived PowerShell process — the mechanism V3 used, with its operational problems fixed.

- One daemon thread owns synthesis. `say()` enqueues and returns immediately; `test_voice_never_blocks_the_caller` asserts six queued lines return in under half a second while the synthesiser is deliberately stalled.
- The queue holds 4 lines and sheds the oldest, so a synthesiser mid-sentence cannot slow the scanner.
- Text is restricted to `[A-Za-z0-9 ,.\-%:]` before it reaches PowerShell, so an asset name from configuration cannot become a command. Quotes, semicolons and `$` are stripped.
- Three consecutive synthesis failures disable voice and surface it as a degraded status; nothing raises into the quant loop.
- `VOICE_RATE` and `VOICE_VOLUME` map to `SpeechSynthesizer.Rate` and `.Volume`; `MASTER_VOLUME` scales the latter.

### What is spoken

Only actionable events. Metrics are never read aloud.

```text
"MANTIS. Bitcoin. Enter Yes."
"MANTIS. Ethereum. Enter No."
"Data hold. Bitcoin market data stale."
"Bitcoin contract resolved. Prediction correct."
"New Bitcoin contract window."          (rollover, off by default)
"MANTIS system error. MARKET DATA PROVIDER."
```

Spoken asset names: BTC → Bitcoin, ETH → Ethereum, SOL → Solana, XRP → X R P, ADA → Cardano.

## Settings

All in `PresentationConfig` (`config/mantis_v4.ui.json`, `MANTIS_UI_*` environment variables, or CLI flags):

```text
AUDIO_ENABLED               MASTER_VOLUME              AUDIO_MIN_INTERVAL_SECONDS
AUDIO_ENTER_YES             AUDIO_ENTER_NO             AUDIO_WAIT (default false)
AUDIO_DATA_HOLD             AUDIO_ROLLOVER             AUDIO_RESOLUTION
AUDIO_ERROR

VOICE_ENABLED               VOICE_RATE                 VOICE_VOLUME
VOICE_ENTER                 VOICE_DATA_HOLD            VOICE_ROLLOVER (default false)
VOICE_RESOLUTION            VOICE_ERROR                VOICE_MIN_INTERVAL_SECONDS
```

## Test mode

```powershell
python mantis_v4_live.py --test-alerts
```

Plays every cue and speaks every announcement in sequence, with a printed transcript.

It is safe by construction, not by convention: `run_test_alerts` receives only an audio engine, a voice engine and a config. `mantis_v4_live.run_test_alerts` returns before `MantisConfig`, `ForwardStore` or `ForwardEngine` are ever constructed, and `test_alert_test_module_cannot_reach_the_store` parses the function's AST to prove it references no store, engine or asset-state name. Test events therefore cannot appear in the forward-validation logs.
