# Phase 9 — eDEX-UI Research and Technology Decision

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**  
> **OBSERVATION ONLY — NO AUTOMATED EXECUTION**

This document records what was actually measured in the eDEX-UI source before any
MANTIS code was written, and the rendering-technology decision that follows from it.

## Source of the findings

Nothing here is from memory. The archived upstream repository was enumerated through
the GitHub API and the relevant files were downloaded and read directly:

| File | What it fixed |
|---|---|
| `src/_renderer.js` | boot cadence, every delay value, audio trigger points, module init order |
| `src/ui.html` | document structure, dependency list (`augmented-ui`) |
| `src/assets/css/main.css` | background grid, section-title linework |
| `src/assets/css/boot_screen.css` | wordmark treatment and the derezz glitch |
| `src/assets/css/main_shell.css` | centre pane geometry, slanted tab geometry |
| `src/assets/css/mod_column.css` | rail geometry and the progressive fade-in |
| `src/assets/css/mod_clock.css` | large-clock treatment |
| `src/assets/css/filesystem.css`, `keyboard.css` | lower-band geometry |
| `src/assets/themes/tron.json` | the default palette, exactly |
| `src/classes/audiofx.class.js` | the cue set and mixing levels |
| `src/assets/misc/boot_log.txt` | boot-log character and length |

Upstream is archived (October 2021), Electron-based, MIT-licensed for its own code.
No upstream asset — font, sound, image or CSS file — was copied into MANTIS.

---

## 1. The startup / intro sequence

Three phases. The whole thing is driven by `await _delay(ms)` in `_renderer.js`.

### Phase 1 — kernel boot log (`displayLine`)

A ~90-line fake XNU/macOS kernel log is printed one line at a time, bottom-left
aligned, `font-family: monospace`, `font-size: 1.4vh`. The screen starts black
(`body.solidBackground`).

Timing is deliberately non-uniform — this is what makes it read as a real machine
rather than a progress bar:

| Line index `i` | Delay to next line |
|---|---|
| `i === 4` (and `i === 2`, which falls through) | 500 ms |
| `4 < i < 25` | 30 ms |
| `i === 25` | 400 ms |
| `i === 42` | 300 ms |
| `42 < i < 82` | 25 ms |
| `i === 83` | 25 ms |
| last two lines | 300 ms |
| default | `Math.pow(1 - (i/1000), 3) * 25` ms — a very slight accelerating drift |

So: a slow deliberate opening, a fast burst, a pause, another fast burst, a pause
at the end. Bursts and beats, not a metronome.

Audio: `stdout.wav` on **every** line; `granted.wav` on the single line `Boot Complete`.
After the last line, `setTimeout(displayTitleScreen, 300)`.

### Phase 2 — title screen (`displayTitleScreen`)

Clears the screen, plays `theme.wav`, then:

| Elapsed | Action |
|---|---|
| 0 | `theme.wav` plays; screen cleared |
| +400 ms | dot-grid background revealed; boot screen switches to centred; `<h1>eDEX-UI</h1>` inserted (fades in over 300 ms) |
| +200 ms | background returns to solid black |
| +100 ms | title becomes a **solid filled block** — background set to the theme colour, 5 px bottom border |
| +300 ms | title becomes an **outline** — 5 px border all round, transparent fill |
| +100 ms | title switches to `.glitch` (the derezz effect) |
| +500 ms | grid background returns, glitch off, 5 px outline restored |
| +1000 ms | boot screen removed, `initUI()` |

Total ≈ 2.6 s. The wordmark is `font-size: 10vh` — enormous, roughly a tenth of the
screen height.

The **derezz glitch** is two `::before`/`::after` clones of the same text, one
clipped to the top 40% and one to the bottom 60%, translated ±1–5% horizontally,
animating at `50 ms` `linear` `infinite` `alternate-reverse`. It is a horizontal
tear/RGB-split, not a fade.

### Phase 3 — interface assembly (`initUI`)

The interface visibly *builds itself*:

| Elapsed | Action |
|---|---|
| 0 | left rail, centre shell and right rail injected — shell at `height:0;width:0;opacity:0` |
| +10 ms | `expand.wav`; shell height/width released (0.5 s cubic-bezier transition) |
| +500 ms | shell reaches full size; its title bar fades in |
| +700 ms | shell fades out; lower band (filesystem + keyboard) injected |
| +10 ms | shell fades back in |
| +270 ms | greeting appears; lower band appears; `keyboard.wav`; keyboard animation state 1 |
| +100 ms | keyboard animation state 2 (rows sweep out to `width: 100vw`) |
| +1000 ms | greeting fades |
| +100 ms | keyboard settles |
| +400 ms | greeting removed; rail modules constructed |
| then | rails get `.activated` (0.5 s opacity transition) |
| then | **`setInterval` every 500 ms**: play `panels.wav` and un-pause the fade-in of the next left *and* right module |

That last loop is the signature move: modules do not appear together, they populate
**one pair every 500 ms**, each with its own 0.5 s fade, each with a sound. The
terminal and its tabs are injected 100 ms after the loop starts.

`nointro` skips phases 1–2 entirely and jumps to `initUI`.

---

## 2. Screen composition

Everything is sized in **`vh`**, not pixels. That is why eDEX looks identical on any
16:9 display — the entire interface scales with viewport height. Borders are
`0.092vh` (≈1 px at 1080p), titles `1.02vh`, the clock `4vh`, the boot wordmark `10vh`.

```text
        17%                    65%                      17%
┌────────────────┬──────────────────────────────┬────────────────┐
│ mod_column_left│        main_shell            │mod_column_right│  60.3% high
│                │  (bl-clip tr-clip, skew tabs)│                │
│ clock          │                              │ netstat        │
│ sysinfo        │                              │ globe          │
│ hardwareInsp.  │                              │ conninfo       │
│ cpuinfo        │                              │                │
│ ramwatcher     ├──────────────────────────────┤                │
│ toplist        │  filesystem 43vw × 30vh      │                │
│                │  keyboard   55.5vw           │                │
└────────────────┴──────────────────────────────┴────────────────┘
```

Measured values:

- `section.mod_column` — `width: 17%`, `position: absolute`, `top: 2.5vh`,
  `max-height: 96%`, `padding: 1.39vh`, left rail `left: -0.555vh`, right rail
  `right: -0.555vh`. Rails hug the screen edges and are slightly bled off-screen.
- `section#main_shell` — `width: 65%`, `height: 60.3%`, `padding: 0.74vh`.
- `section#filesystem` — `width: 43vw`, `height: 30vh`.
- `section#keyboard` — `width: 55.5vw`, rows `5.28vh` each.
- `div#mod_clock` — `height: 7.41vh`, `h1` at `font-size: 4vh`, digits in fixed
  `2.3vh` cells with `2.5vh` separators so the clock never reflows as digits change.

17 + 65 + 17 = 99%. The composition is a **narrow rail / dominant centre / narrow
rail** triptych over a full-width lower band.

---

## 3. Linework

This is what makes it not look like ordinary boxes.

- **Angular clipped corners** come from the `augmented-ui` CSS library.
  The centre pane is `augmented-ui="bl-clip tr-clip exe"` — bottom-left and
  top-right corners cut off diagonally — with `--aug-border: 0.18vh` and
  `--aug-border-opacity: 0.5`. Corners are cut on *opposite* diagonals, never all four.
- **Slanted tabs** are `transform: skewX(35deg)` on each `<li>`, with
  `transform: skewX(-35deg)` on the inner `<p>` so the label stays upright. The
  active tab is **inverted** — filled with the theme colour, text in the background
  colour, bold, and `scale(1.2)`.
- **Section titles** (`h3.title`) are a tiny `1.02vh` bar with two labels — a generic
  one on the left (`PANEL`) and a specific one on the right (`SYSTEM`) — over a
  `0.092vh` bottom rule at `0.3` alpha, with `::before`/`::after` pseudo-elements
  drawing short vertical **end ticks** that turn the rule into a bracket.
- Modules repeat that motif: `mod_clock` has a `border-top` at `0.3` alpha plus its
  own pair of end ticks.
- Almost all structure is drawn at **0.3–0.5 alpha**. Full-strength colour is reserved
  for *data*, not for chrome.

## 4. Palette — default `tron` theme, verbatim

```json
{ "r": 170, "g": 207, "b": 209,
  "black": "#000000", "light_black": "#05080d", "grey": "#262828" }
```

- primary **`rgb(170,207,209)` = `#aacfd1`** — a pale, desaturated cyan, *not* neon
- background `#05080d` — near-black with a blue cast
- `#262828` — used only for the background dot grid

The background texture is two `linear-gradient`s at `background-size: 2.04vh 2.04vh`
over the grey, producing a faint dot at every grid intersection. `body.solidBackground`
switches it off — which is how the boot sequence flashes between grid and solid.

Terminal foreground `#aacfd1`, background `#05080d`, block cursor, selection
`rgba(170,207,209,0.3)`.

## 5. Typography

- UI: **United Sans Medium** / **United Sans Light** (a condensed technical sans)
- Mono: **Fira Mono** / **Fira Code**

United Sans is proprietary and bundled by upstream; it was **not** copied. MANTIS uses
a CSS stack led by **Bahnschrift** — the DIN-derived condensed technical sans that
ships with Windows 10/11 — which carries the same engineered character legitimately,
falling back through Oswald/Roboto Condensed to the generic condensed sans. Mono uses
Cascadia Mono → Consolas.

## 6. Audio

Upstream loads WAVs through `howler.js`: `stdout`, `stdin`, `folder`, `granted`,
`keyboard`, `theme`, `expand`, `panels`, `scan`, `denied`, `info`, `alarm`, `error`.
`stdout`/`stdin` are mixed at `volume: 0.4`; the rest at full. Sound design for 2.1.x
was by IceWolf and is **not** MIT-licensed with the code.

Trigger points established above: `stdout` per boot-log line, `granted` on boot
complete, `theme` at the title screen, `expand` when the centre pane grows,
`keyboard` when the lower band arrives, `panels` on every module that pops in.

**No upstream audio file was downloaded, converted or shipped.** MANTIS synthesizes
every cue at runtime with the Web Audio API — oscillators, filtered noise and gain
envelopes — so the cue set is original by construction while keeping the same
functional character (short digital pulses, a low bass impact for system-ready,
a soft tick for routine activity).

## 7. Motion budget

Continuously moving: the clock, the network graphs, the RAM matrix, the process
list, the globe, the terminal cursor. Static until state changes: labels, section
titles, tab bar. Transitions are `0.5 s cubic-bezier(0.4, 0, 1, 1)` for opacity and
`0.5 s cubic-bezier(0.85, 0.5, 0.85, 0.5)` for size. Nothing bounces, nothing eases
out slowly — the curves are fast-in.

---

## 8. Technology decision

**Decision: replace the Rich terminal renderer with a Chromium-rendered local web
frontend. Keep the Rich dashboard as a supported fallback.**

### Why Rich cannot reach this target

The gap is not styling, it is the rendering model. A terminal is a fixed grid of
character cells. Specifically, the following are *not expressible* in any terminal:

| Required | Why Rich cannot |
|---|---|
| `skewX(35deg)` tabs | glyphs cannot be sheared |
| clipped/angular corners | corners are whole cells |
| `0.092vh` hairline rules at 0.3 alpha | minimum stroke is one full cell of a solid glyph |
| `10vh` wordmark | font size is fixed by the terminal; only block-glyph ASCII art |
| letter-spacing control | no sub-cell horizontal metrics |
| dot-grid background texture | background is per-cell, not sub-cell |
| smooth width/height transitions | geometry is quantised to whole cells |
| genuine scanlines | at best, alternating dim rows |
| 60 fps continuous graphs | full-screen redraw over a Windows Terminal pty tears |

The current Rich implementation is a *good terminal dashboard*. It is not a near-eDEX
result and no amount of restyling makes it one. Retaining it here would be sunk-cost
bias, which the brief explicitly rules out.

### Why this specific web stack

eDEX itself is Electron, i.e. Chromium. To match Chromium output, render in Chromium.

| Option | Verdict |
|---|---|
| **stdlib HTTP + SSE, rendered by Edge/Chrome in `--app` mode** | **Chosen.** Zero new Python dependencies. Chromium rendering, so `clip-path`, `skewX`, `vh` units, CSS transitions, canvas and the Web Audio API are all available exactly as upstream used them. Edge is guaranteed present on Windows 11, so the operator guide's "`pip install -r requirements.txt` then run" promise survives intact. Launched with `--app=` plus `--start-fullscreen` there is no browser chrome, no tab strip and no address bar. |
| Electron | Rejected. Requires Node, npm and a build/packaging step for a Python project, and ships a second runtime. |
| PySide6 / Qt WebEngine | Rejected. ~150 MB dependency to obtain the same Chromium already on the machine. |
| Textual | Rejected. Still a terminal; every limitation in the table above still applies. |
| pywebview | Viable and close second — a real borderless window via WebView2. Held in reserve as an optional upgrade; rejected as the default only because it adds a dependency for a window frame we can get from `--app` mode. |

### Isolation contract, unchanged

The web layer is a **read-only consumer**, exactly as the Rich layer was:

```text
QUANT / PROVIDERS / ECONOMICS / FORWARD LOGGING   (untouched Python)
        │  writes under lock
        ▼
CommandCenterState ──snapshot()──> UiSnapshot (immutable)
        │  field copying only, no arithmetic
        ▼
webmodel.snapshot_payload() ──JSON──> SSE ──> browser
```

The browser can only receive. There is no route that mutates state, no route that
touches `ForwardStore`, and the server runs on a daemon thread bound to `127.0.0.1`.
A dead browser, a failed server bind or a wedged SSE client cannot stall a scan, for
the same structural reason the render thread could not: the scan thread never waits
on it.

### Honest limitations

- The window is a Chromium **app window**, not a native executable. Alt-Tab shows it
  as Edge/Chrome. `pywebview` would fix this if it later matters.
- If Edge and Chrome are both absent, the launcher reports it and falls back to the
  Rich renderer rather than failing.
- `--start-fullscreen` is honoured by Chrome; Edge may open maximised instead, with
  F11 completing it.
