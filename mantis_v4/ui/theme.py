"""MANTIS visual identity: palette, typography, decision-state descriptors.

Restrained mission-control language. Dark graphite and deep navy grounds, steel
structure, muted cyan for signal, cool white for data, amber and a desaturated
red reserved for states that genuinely require attention.

No value in this module is computed from market data. It only describes how a
backend state is drawn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rich import box
from rich.style import Style
from rich.theme import Theme

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

PRODUCT_NAME = "MANTIS"
PRODUCT_EXPANSION = "Market Analysis and Neuro-Tactical Intraday Signal System"
PRODUCT_SHORT = "M A N T I S"
SOFTWARE_VERSION = "MANTIS 4.10.0 / PHASE 10 HARDENED"
ADVISORY_BANNER = "OBSERVATION ONLY — NO AUTOMATED EXECUTION"

# The operating environment owns the boot identity; MANTIS is the subsystem that
# boots inside it. SHG is therefore large at startup and quiet afterwards.
SYSTEM_OWNER = "SAAF HOLDINGS GROUP"
SYSTEM_OWNER_SHORT = "SHG"
SYSTEM_OWNER_LINE = "SAAF HOLDINGS GROUP // INTELLIGENCE SYSTEMS"

WORDMARK = r"""
 ▄▄▄▄▄▄   ▄▄▄▄    ▄▄     ▄▄  ▄▄▄▄▄▄  ▄▄  ▄▄▄▄▄▄▄   ▄▄▄▄
 ██   ██  ██  ██  ███   ███    ██    ██  ██       ██
 ██   ██  ██████  ██ █▄█ ██    ██    ██  ▀▀▀▀██▄   ▀▀▀█▄
 ██   ██  ██  ██  ██  ▀  ██    ██    ██       ██       ██
 ██   ██  ██  ██  ██     ██    ██    ██  ▀▀▀▀▀▀▀   ▀▀▀▀
"""

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

GRAPHITE = "#12161c"        # primary ground
GRAPHITE_RAISED = "#181e26"  # panel ground
NAVY = "#141c2b"            # deep navy accent ground
STEEL = "#6f7f92"           # structure, borders, secondary labels
STEEL_DIM = "#4a5666"       # grid lines and rules
COOL_WHITE = "#dfe6ee"      # primary data
COOL_WHITE_DIM = "#a8b4c2"  # secondary data
CYAN = "#4fb8c9"            # signal / system identity
CYAN_BRIGHT = "#7fd8e6"     # emphasis only, used sparingly
TEAL = "#2fb6ad"            # ENTER YES
AZURE = "#5f8fd6"           # ENTER NO
AMBER = "#d9a441"           # WAIT / caution
RED = "#c85a54"             # DATA HOLD / critical
SLATE = "#8593a6"           # NO TRADE

MANTIS_THEME = Theme({
    "mantis.ground": Style(bgcolor=GRAPHITE),
    "mantis.panel": Style(bgcolor=GRAPHITE_RAISED),
    "mantis.border": Style(color=STEEL_DIM),
    "mantis.border.active": Style(color=CYAN),
    "mantis.title": Style(color=CYAN, bold=True),
    "mantis.brand": Style(color=CYAN_BRIGHT, bold=True),
    "mantis.expansion": Style(color=STEEL, italic=True),
    "mantis.label": Style(color=STEEL),
    "mantis.value": Style(color=COOL_WHITE),
    "mantis.value.dim": Style(color=COOL_WHITE_DIM),
    "mantis.unit": Style(color=STEEL_DIM),
    "mantis.rule": Style(color=STEEL_DIM),
    "mantis.yes": Style(color=TEAL, bold=True),
    "mantis.no": Style(color=AZURE, bold=True),
    "mantis.wait": Style(color=AMBER, bold=True),
    "mantis.notrade": Style(color=SLATE, bold=True),
    "mantis.hold": Style(color=RED, bold=True),
    "mantis.ok": Style(color=TEAL),
    "mantis.warn": Style(color=AMBER),
    "mantis.crit": Style(color=RED, bold=True),
    "mantis.info": Style(color=STEEL),
    "mantis.notice": Style(color=CYAN),
    "mantis.action": Style(color=CYAN_BRIGHT, bold=True),
    "mantis.demo": Style(color=GRAPHITE, bgcolor=AMBER, bold=True),
    "mantis.meter.fill": Style(color=CYAN),
    "mantis.meter.empty": Style(color=STEEL_DIM),
    "mantis.countdown": Style(color=COOL_WHITE, bold=True),
    "mantis.countdown.near": Style(color=AMBER, bold=True),
    "mantis.countdown.final": Style(color=RED, bold=True),
})

# ---------------------------------------------------------------------------
# Glyphs (Unicode with an ASCII fallback for legacy consoles)
# ---------------------------------------------------------------------------

GLYPHS_UNICODE = {
    "yes": "▲", "no": "▼", "wait": "◆", "notrade": "⊘", "hold": "■",
    "ok": "●", "warn": "○", "crit": "✕", "info": "·", "bullet": "›",
    "meter_full": "█", "meter_half": "▌", "meter_empty": "░",
    "spark": "▁▂▃▄▅▆▇█", "arrow_up": "↑", "arrow_down": "↓", "flat": "→",
}
GLYPHS_ASCII = {
    "yes": "^", "no": "v", "wait": "*", "notrade": "x", "hold": "#",
    "ok": "o", "warn": "!", "crit": "X", "info": ".", "bullet": ">",
    "meter_full": "#", "meter_half": "=", "meter_empty": "-",
    "spark": ".:-=+*#@", "arrow_up": "^", "arrow_down": "v", "flat": "-",
}


def glyphs(ascii_only: bool = False) -> dict:
    return dict(GLYPHS_ASCII if ascii_only else GLYPHS_UNICODE)


# ---------------------------------------------------------------------------
# Decision state descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionStyle:
    """How one backend decision string is drawn.

    Colour is never the only carrier: ``label``, ``glyph``, ``shape`` and
    ``border`` all differ between states so the screen stays readable in
    monochrome, for colour-blind operators, and in a screenshot.
    """

    key: str
    label: str
    glyph: str
    ascii_glyph: str
    style: str
    border: box.Box
    shape: str            # bracket treatment, differs per state
    emphasis: bool        # dominant states get the loud treatment

    def decorate(self, ascii_only: bool = False) -> str:
        mark = self.ascii_glyph if ascii_only else self.glyph
        left, right = self.shape[0], self.shape[1]
        return f"{left} {mark}  {self.label}  {mark} {right}"


ENTER_YES = DecisionStyle("enter_yes", "ENTER YES", "▲", "^", "mantis.yes",
                          box.DOUBLE, "[]", True)
ENTER_NO = DecisionStyle("enter_no", "ENTER NO", "▼", "v", "mantis.no",
                         box.DOUBLE, "<>", True)
WAIT = DecisionStyle("wait", "WAIT", "◆", "*", "mantis.wait",
                     box.SQUARE, "::", False)
NO_TRADE = DecisionStyle("no_trade", "NO TRADE", "⊘", "x", "mantis.notrade",
                         box.SQUARE, "--", False)
DATA_HOLD = DecisionStyle("data_hold", "DATA HOLD", "■", "#", "mantis.hold",
                          box.HEAVY, "!!", True)
UNKNOWN = DecisionStyle("unknown", "STANDBY", "·", ".", "mantis.info",
                        box.SQUARE, "..", False)

# Keys are the exact backend ``Decision`` values. The UI classifies nothing; it
# looks the decision string up.
DECISION_STYLES = {
    "ENTER YES": ENTER_YES,
    "ENTER NO": ENTER_NO,
    "WAIT": WAIT,
    "NO TRADE THIS CONTRACT": NO_TRADE,
    "NO TRADE": NO_TRADE,
    "DATA HOLD": DATA_HOLD,
}


def decision_style(decision: Optional[str]) -> DecisionStyle:
    if not decision:
        return UNKNOWN
    return DECISION_STYLES.get(str(decision).upper().strip(), UNKNOWN)


# ---------------------------------------------------------------------------
# Reason phrasing
# ---------------------------------------------------------------------------

# Backend reason codes are stable identifiers. These are operator-facing
# renderings of the same code; the raw code is always still available in the
# diagnostics view, so nothing is hidden by this mapping.
REASON_TEXT = {
    "ELIGIBLE": "ALL CLASSIFICATION GATES PASSED",
    "ROBUST_POSITIVE_EV": "ROBUST POSITIVE EV",
    "PROBABILITY_TOO_LOW": "CONFIDENCE BELOW THRESHOLD",
    "CONSERVATIVE_BOUND_TOO_LOW": "LOWER BOUND BELOW THRESHOLD",
    "BUFFER_TOO_SMALL": "REFERENCE BUFFER TOO SMALL",
    "FRAGILITY_TOO_HIGH": "FRAGILITY ABOVE THRESHOLD",
    "DISAGREEMENT_TOO_HIGH": "MODEL DISAGREEMENT ABOVE THRESHOLD",
    "CROSSING_RISK_TOO_HIGH": "REFERENCE CROSSING RISK TOO HIGH",
    "TREND_BUFFER_DEVELOPING": "TREND / BUFFER STILL DEVELOPING",
    "EARLY_INSUFFICIENT_INFORMATION": "TOO EARLY FOR THIS POLICY",
    "ENTRY_WINDOW_EXPIRED": "ENTRY WINDOW EXPIRED",
    "TOO_LATE": "ENTRY WINDOW CLOSED",
    "CHOP_UNSTABLE": "REFERENCE CROSSING BEHAVIOUR UNSTABLE",
    "CONTRACT_QUOTE_UNAVAILABLE": "CONTRACT ECONOMICS UNAVAILABLE",
    "INVALID_OR_STALE_QUOTE": "STALE OR INVALID CONTRACT QUOTE",
    "ECONOMICS_UNVERIFIED": "CONTRACT ECONOMICS UNVERIFIED",
    "NEGATIVE_EV": "POINT EV <= 0",
    "NON_ROBUST_EV": "LCB EV <= 0",
    "EDGE_TOO_SMALL": "ECONOMIC MARGIN BELOW THRESHOLD",
    "EXPECTED_RETURN_TOO_LOW": "EXPECTED RETURN ON COST TOO LOW",
    "STALE_DATA": "STALE UNDERLYING DATA",
    "MISSING_REFERENCE": "REFERENCE UNAVAILABLE",
    "INVALID_CONTRACT_ID": "INVALID CONTRACT IDENTITY",
    "CONTRACT_ROLLOVER": "CONTRACT ROLLOVER IN PROGRESS",
    "INSUFFICIENT_HISTORY": "INSUFFICIENT PRICE HISTORY",
    "NONFINITE_INPUT": "NON-FINITE MODEL INPUT",
    "INVALID_TIME_REMAINING": "INVALID TIME REMAINING",
    "INCONSISTENT_PROBABILITY": "INCONSISTENT PROBABILITY STATE",
    "INCONSISTENT_DIAGNOSTICS": "INCONSISTENT DIAGNOSTICS",
    "UNDERLYING_DATA_UNAVAILABLE": "UNDERLYING DATA UNAVAILABLE",
    "AWAITING_FIRST_SCAN": "AWAITING FIRST SCAN",
}


def reason_text(code: Optional[str]) -> str:
    if not code:
        return ""
    return REASON_TEXT.get(str(code), str(code).replace("_", " "))


# ---------------------------------------------------------------------------
# Countdown emphasis thresholds
# ---------------------------------------------------------------------------

# Emphasis marks only. They are display cues drawn from the policy's own timing
# vocabulary and carry no guarantee about outcomes at those instants.
COUNTDOWN_MARKS = (300, 180, 120, 60, 30)


def countdown_style(seconds_remaining: float) -> str:
    if seconds_remaining <= 30:
        return "mantis.countdown.final"
    if seconds_remaining <= 120:
        return "mantis.countdown.near"
    return "mantis.countdown"


def format_countdown(seconds_remaining: Optional[float]) -> str:
    """``T-MM:SS`` from a duration the backend supplied. Never a UI clock."""
    if seconds_remaining is None:
        return "T--:--"
    total = max(0, int(seconds_remaining))
    return f"T-{total // 60:02d}:{total % 60:02d}"
