# Phase 9 Operator Guide

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**  
> **OBSERVATION ONLY — NO AUTOMATED EXECUTION**

MANTIS observes and advises. It never places an order, never connects a broker for execution, and never claims you traded. Every decision on screen is yours to act on or ignore.

---

## 1. Starting MANTIS on Windows, from zero

You need this once. After that, step 5 is the whole routine.

**1. Open PowerShell in the project folder.**

Open File Explorer and navigate to the `mantis-v4` folder — the one containing `mantis_v4_live.py`. Click once in the address bar at the top so the path highlights, type `powershell`, and press Enter. A blue-black window opens, already in the right folder.

**2. Check Python is installed.**

```powershell
python --version
```

You should see `Python 3.11` or newer. If instead the Microsoft Store opens or you get "not recognized", install Python from <https://www.python.org/downloads/windows/> and tick **Add python.exe to PATH** on the first screen of the installer. Then close and reopen PowerShell.

**3. Install the dependencies. Once, not every time.**

```powershell
python -m pip install -r requirements.txt
```

This takes a minute or two and prints a lot. `Successfully installed …` at the end means it worked.

**4. Make the window big.**

The command center wants at least **96 columns × 30 rows**, and looks best full-screen on a 1920×1080 display. Maximise the PowerShell window. If the text is large, press `Ctrl` and `-` a few times to shrink it. If the window is too small MANTIS tells you the current and required size instead of crashing.

**5. Start it.**

```powershell
python mantis_v4_live.py
```

You get the initialization sequence, then the full command center. It refreshes continuously.

**6. Stop it.**

Press `Ctrl` and `C` together. MANTIS closes the screen and prints:

```text
MANTIS STOPPED | forward records are append-only and intact
```

Your records are safe. Stopping mid-scan cannot corrupt them — every record is flushed and fsynced as it is written, and a half-written final line is recognised and ignored on the next start.

---

## 2. Trying it without waiting for the market

**See the whole interface immediately, with clearly-labelled fake data:**

```powershell
python mantis_v4_live.py --demo
```

Every frame is stamped `DEMO / SYNTHETIC DATA — NOT A LIVE SIGNAL` and every card is marked `SYNTHETIC`. It cycles through all the decision states so you can see what each looks like before it matters. Demo states are never written to the forward logs.

**Hear every alert sound and announcement:**

```powershell
python mantis_v4_live.py --test-alerts
```

Plays each tone and speaks each announcement in turn, printing what it is doing. No signals are produced and nothing is written to the forward logs.

**Take one look and exit:**

```powershell
python mantis_v4_live.py --once
```

Runs a single scan, prints one frame, and stops.

---

## 3. Command-line flags

| Flag | Effect |
|---|---|
| *(none)* | full command center, continuous |
| `--once` | one scan, print one frame, exit |
| `--no-ui` | plain text output instead of the command center |
| `--no-audio` | no alert tones |
| `--no-voice` | no spoken announcements |
| `--no-startup` | skip the initialization sequence |
| `--diagnostics` | show the advanced diagnostics panel |
| `--focus ASSET` | which asset holds the primary decision area by default |
| `--demo` | synthetic-data interface, prominently labelled |
| `--test-alerts` | preview every tone and announcement, then exit |
| `--forward-dir PATH` | where forward records are written (default `data/forward`) |
| `--manual-economics PATH` | verified manual contract economics file |

Combine freely: `python mantis_v4_live.py --no-voice --diagnostics`.

---

## 4. Turning voice and sound on and off

**Just for this run:** use `--no-voice` or `--no-audio`.

**Permanently:** create `config/mantis_v4.ui.json` next to `config/mantis_v4.ui.json.example`, and put in only the settings you want to change:

```json
{
  "voice_enabled": false,
  "master_volume": 0.4,
  "audio_rollover": false
}
```

MANTIS refuses to start if that file contains a setting name it does not recognise, rather than silently ignoring your change.

**For one session, from PowerShell:**

```powershell
$env:MANTIS_UI_VOICE_ENABLED = "0"
python mantis_v4_live.py
```

Every setting has an environment variable: the name in capitals with `MANTIS_UI_` in front.

### All presentation settings

```text
UI_ENABLED                   UI_REFRESH_RATE              STARTUP_ANIMATION
SHOW_ADVANCED_DIAGNOSTICS    DEFAULT_FOCUSED_ASSET        EVENT_LOG_LENGTH
EVENT_LOG_VISIBLE_ROWS       MINIMUM_TERMINAL_WIDTH       MINIMUM_TERMINAL_HEIGHT
SHOW_FORWARD_VALIDATION      FORWARD_REPORT_INTERVAL_SECONDS

AUDIO_ENABLED                MASTER_VOLUME                AUDIO_MIN_INTERVAL_SECONDS
AUDIO_ENTER_YES              AUDIO_ENTER_NO               AUDIO_WAIT
AUDIO_DATA_HOLD              AUDIO_ROLLOVER               AUDIO_RESOLUTION
AUDIO_ERROR

VOICE_ENABLED                VOICE_RATE                   VOICE_VOLUME
VOICE_ENTER                  VOICE_DATA_HOLD              VOICE_ROLLOVER
VOICE_RESOLUTION             VOICE_ERROR                  VOICE_MIN_INTERVAL_SECONDS

DIAGNOSTIC_LOG               DEVELOPER_MODE
```

Precedence, later wins: defaults → `config/mantis_v4.ui.json` → environment → command-line flags.

---

## 5. Reading the screen

Start at the big panel on the left. It tells you the one thing that matters right now:

| What you see | What it means |
|---|---|
| `▲ ENTER YES` | MANTIS favours YES, the signal passed every gate, and the contract is economically attractive |
| `▼ ENTER NO` | the same, for NO |
| `◆ WAIT` | not eligible **yet**, or eligible but economics are missing or too thin. The line underneath says which |
| `⊘ NO TRADE` | this contract is rejected. Not an error |
| `■ DATA HOLD` | MANTIS does not trust its inputs and is deliberately showing you nothing. Not an error either |

The line under the decision is always the reason. `CONFIDENCE BELOW THRESHOLD`, `CONTRACT ECONOMICS UNAVAILABLE`, `LCB EV <= 0`, `STALE UNDERLYING DATA`, and so on.

The big `T-04:37` is time left in the current 15-minute contract, counted against the contract's own resolution timestamp. It turns amber under two minutes and red under thirty seconds. Those are emphasis marks, not promises about what happens at those moments.

The five cards along the middle are the other assets. The one with a `◆` beside its name is the one shown in the big panel.

**Colour is never the only signal.** Every state also has its own word, its own glyph, and its own border style, so the screen still reads correctly in a screenshot, in monochrome, or if you are colour-blind.

---

## 6. `AUTH NOT CONFIGURED` — what it means

You will see this in the header and the provider panel:

```text
WEBULL              ○ AUTH NOT CONFIGURED
ECONOMICS           ○ DISABLED
REFERENCE             PROXY UNVERIFIED
```

**This is not a failure and nothing is broken.** It means MANTIS has no Webull API credentials, so it cannot see real contract prices.

What still works: everything about the model. Prices, the reference, probability, the conservative lower bound, fragility, disagreement, crossing risk, the Phase 6 classification, contract timing, the forward-validation record.

What does not: contract economics. Without a real, verified YES/NO ask price there is no honest break-even, no edge, and no expected value. MANTIS refuses to guess one. So an eligible signal shows as:

```text
CLASSIFICATION      ENTER YES  96.4%
ECONOMICS           UNAVAILABLE
FINAL               WAIT — CONTRACT ECONOMICS UNAVAILABLE
```

That is the system being honest, not the system being stuck.

To supply verified economics by hand, copy `config/contracts/example_economics.json` to `config/contracts/live_economics.json`, replace every field with the real active contract, and update the quote timestamp each time the quote changes. That file is gitignored. A quote older than 15 seconds, or one that does not match the current contract and reference, is rejected as stale rather than used.

---

## 7. Where your data is

| Path | Contents |
|---|---|
| `data/forward/observations.jsonl` | every scan, immutable, written before any outcome is known |
| `data/forward/entries.jsonl` | the first qualifying entry event per asset per contract |
| `data/forward/resolutions.jsonl` | how each contract actually resolved |
| `data/forward/provider_health.jsonl` | provider successes, failures and state |
| `data/forward/runs.jsonl` | one row per launch: version, git commit, config hash |
| `data/logs/mantis_ui_diagnostics.log` | full tracebacks for anything the screen summarised |

These are append-only. MANTIS never rewrites a line. They are excluded from Git, so they are yours alone and they survive upgrades. The event log on screen is a bounded window onto recent activity; the files are the complete record.

To get spreadsheet-friendly copies:

```powershell
python -c "from pathlib import Path; from mantis_v4.forward import ForwardStore, export_csv; print(export_csv(ForwardStore(Path('data/forward')), Path('data/forward_csv')))"
```

---

## 8. Forward sample versus historical holdout

The validation panel shows two numbers and never mixes them.

- **`FORWARD OBSERVATION SAMPLE`** — what MANTIS has actually done live, on this machine, since you started collecting. Small at first. The sample-status label tells you how much weight it deserves: under 30 is `VERY SMALL SAMPLE`, 30–99 `SMALL SAMPLE`, 100–299 `EARLY EVIDENCE`, 300–999 `MEANINGFUL FORWARD SAMPLE`, 1000+ `LARGER FORWARD SAMPLE`.
- **`HISTORICAL PROXY HOLDOUT`** — the Phase 6 result: 96.58% accuracy at 63.73% coverage, measured on historical data with a proxy settlement reference. It is classification research, not a profitability result.

The regime line compares the two without ever retuning anything: `FORWARD SAMPLE TOO SMALL`, `FORWARD REGIME NORMAL`, or `FORWARD REGIME SHIFT WARNING`. Averaging the two numbers together would be meaningless, so the interface will not do it.

---

## 9. When something goes wrong

You get a short readable block, never a wall of red text:

```text
SYSTEM ERROR
Provider:  yahoo
Component: scan BTC-USD
Time:      2026-08-14T19:10:23+00:00
Recovery:  retrying / degraded mode
Detail:    HTTPError: 503 Service Unavailable
```

The complete technical detail goes to `data/logs/mantis_ui_diagnostics.log`. Set `MANTIS_UI_DEVELOPER_MODE=1` if you want it on screen.

| Symptom | What it means | What to do |
|---|---|---|
| `TERMINAL TOO SMALL` | window under 96×30 | maximise the window, or `Ctrl` `-` to shrink the font, or run `--no-ui` |
| one asset stuck on `DATA HOLD` | no fresh bars for that asset | usually transient; it clears itself. The other four keep scanning |
| all assets on `DATA HOLD` | the market-data provider is down | check your internet; MANTIS keeps retrying |
| `PROVIDER DEGRADED` in the log | the provider is retrying or serving cache | informational; it recovers on its own |
| no sound | check `AUDIO_ENABLED`, `MASTER_VOLUME`, and that you did not pass `--no-audio` |
| no voice | Windows only. If speech fails three times MANTIS disables it and shows the provider panel voice state as off |
| `INTERFACE DISABLED` on exit | the screen renderer failed repeatedly | the scan kept running and your records are complete. Use `--no-ui` and report the diagnostics log |
| boxes look like `#` and `*` | legacy console or non-Unicode code page | expected; MANTIS switched the whole visual language to ASCII. Use Windows Terminal for the full look |

**Nothing in the interface can stop the scanner.** A failing screen, a dead sound card, a broken speech synthesiser and an exploding provider are all contained: they degrade their own subsystem, write a log line, and the five-second scan carries on.

---

## 10. Things MANTIS will never do

- place an order, of any kind, on any venue
- connect a broker for execution or hold credentials for trading
- buy or sell a contract automatically
- record that you took a trade unless you supply that yourself
- present a proxy reference as a verified contract strike
- show a stale number as if it were current
- invent an expected value when there is no real quote to compute one from
- blend the historical holdout result into the live forward sample
