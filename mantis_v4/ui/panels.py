"""Rich renderables for the MANTIS command center.

Every function here takes already-computed backend values and draws them. There
is no arithmetic in this module beyond formatting, bar widths, and the
countdown difference against the backend's own resolution timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import theme
from .state import AssetView, LogEntry, SystemError, SystemStatus, UiSnapshot

UTC = timezone.utc

DASH = "—"

SEVERITY_STYLE = {
    "INFO": "mantis.info",
    "NOTICE": "mantis.notice",
    "ACTION": "mantis.action",
    "WARNING": "mantis.warn",
    "CRITICAL": "mantis.crit",
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def num(value: Optional[float], spec: str = ".3f", suffix: str = "") -> str:
    if value is None:
        return DASH
    try:
        return format(float(value), spec) + suffix
    except (TypeError, ValueError):
        return DASH


def pct(value: Optional[float], places: int = 1) -> str:
    return DASH if value is None else f"{float(value) * 100:.{places}f}%"


def price(value: Optional[float]) -> str:
    """Adaptive precision so BTC and ADA are both readable."""
    if value is None:
        return DASH
    magnitude = abs(float(value))
    if magnitude >= 1000:
        return f"{value:,.2f}"
    if magnitude >= 1:
        return f"{value:,.4f}"
    return f"{value:,.6f}"


def signed(value: Optional[float], spec: str = ",.2f") -> str:
    if value is None:
        return DASH
    return f"{value:+{spec.lstrip('+')}}"


def money(value: Optional[float]) -> str:
    return DASH if value is None else f"{value:+.4f}"


def meter(value: Optional[float], width: int, marks: dict,
          maximum: float = 1.0) -> Text:
    """Horizontal bar. ``value`` is already a backend quantity."""
    if value is None or maximum <= 0:
        return Text(marks["meter_empty"] * width, style="mantis.meter.empty")
    ratio = max(0.0, min(1.0, float(value) / maximum))
    filled = int(round(ratio * width))
    bar = Text()
    bar.append(marks["meter_full"] * filled, style="mantis.meter.fill")
    bar.append(marks["meter_empty"] * (width - filled), style="mantis.meter.empty")
    return bar


def sparkline(values: Sequence[float], marks: dict, width: int = 24) -> Text:
    """Spark of real observed history. Never synthesised for decoration."""
    ramp = marks["spark"]
    points = list(values)[-width:]
    if len(points) < 2:
        return Text(DASH, style="mantis.unit")
    low, high = min(points), max(points)
    span = high - low
    out = []
    for point in points:
        index = 0 if span <= 0 else int(round((point - low) / span * (len(ramp) - 1)))
        out.append(ramp[index])
    return Text("".join(out), style="mantis.meter.fill")


def _kv(label: str, value: Any, value_style: str = "mantis.value") -> tuple[Text, Text]:
    return (Text(label, style="mantis.label"),
            value if isinstance(value, Text) else Text(str(value), style=value_style))


def _grid(pad: int = 1) -> Table:
    # Both columns clip rather than wrap: a wrapped value silently doubles a
    # panel's height and pushes later rows out of the fixed layout band.
    table = Table.grid(padding=(0, pad), expand=True)
    table.add_column(justify="left", no_wrap=True, overflow="ellipsis")
    table.add_column(justify="right", no_wrap=True, overflow="ellipsis")
    return table


# ---------------------------------------------------------------------------
# Large countdown
# ---------------------------------------------------------------------------

BIG_GLYPHS = {
    "0": ("███", "█ █", "█ █", "█ █", "███"),
    "1": ("  █", "  █", "  █", "  █", "  █"),
    "2": ("███", "  █", "███", "█  ", "███"),
    "3": ("███", "  █", "███", "  █", "███"),
    "4": ("█ █", "█ █", "███", "  █", "  █"),
    "5": ("███", "█  ", "███", "  █", "███"),
    "6": ("███", "█  ", "███", "█ █", "███"),
    "7": ("███", "  █", "  █", "  █", "  █"),
    "8": ("███", "█ █", "███", "█ █", "███"),
    "9": ("███", "█ █", "███", "  █", "███"),
    ":": ("   ", " █ ", "   ", " █ ", "   "),
    "-": ("   ", "   ", "███", "   ", "   "),
    "T": ("███", " █ ", " █ ", " █ ", " █ "),
    " ": ("   ", "   ", "   ", "   ", "   "),
}


def big_text(value: str, ascii_only: bool = False) -> list[str]:
    rows = ["", "", "", "", ""]
    for character in value.upper():
        cell = BIG_GLYPHS.get(character, BIG_GLYPHS[" "])
        for index in range(5):
            rows[index] += cell[index] + " "
    if ascii_only:
        rows = [row.replace("█", "#") for row in rows]
    return [row.rstrip() for row in rows]


def countdown_block(view: Optional[AssetView], now: datetime, ascii_only: bool) -> Group:
    seconds = view.seconds_remaining(now) if view else None
    label = theme.format_countdown(seconds)
    style = theme.countdown_style(seconds if seconds is not None else 999)
    body = Text("\n".join(big_text(label, ascii_only)), style=style)

    marks = theme.glyphs(ascii_only)
    elapsed = view.elapsed_fraction(now) if view else None
    progress = meter(elapsed, 34, marks)

    footnote = Text()
    if seconds is not None:
        crossed = [m for m in theme.COUNTDOWN_MARKS if seconds <= m]
        nearest = min(crossed) if crossed else None
        if nearest is not None:
            footnote.append(f"PAST T-{nearest}  ", style="mantis.label")
    footnote.append("WINDOW ELAPSED ", style="mantis.label")
    footnote.append(pct(elapsed, 0), style="mantis.value.dim")
    return Group(Align.center(body), Align.center(progress), Align.center(footnote))


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def header_panel(snapshot: UiSnapshot, now: datetime, local_now: datetime,
                 ascii_only: bool = False) -> Panel:
    status = snapshot.status
    marks = theme.glyphs(ascii_only)

    identity = Table.grid(padding=(0, 2))
    identity.add_column(justify="left", no_wrap=True, overflow="ellipsis")
    brand = Text()
    brand.append(f"{theme.PRODUCT_SHORT}", style="mantis.brand")
    brand.append("   ", style="mantis.label")
    brand.append(theme.PRODUCT_EXPANSION, style="mantis.expansion")
    identity.add_row(brand)
    identity.add_row(Text(theme.ADVISORY_BANNER, style="mantis.warn"))
    identity.add_row(Text(theme.SOFTWARE_VERSION, style="mantis.unit"))

    facts = Table.grid(padding=(0, 3), expand=True)
    for _ in range(4):
        facts.add_column(justify="left", no_wrap=True)

    health_style = "mantis.ok" if status.underlying_state in ("LIVE", "DEGRADED") else "mantis.warn"
    facts.add_row(
        Text.assemble(("UTC  ", "mantis.label"), (now.strftime("%Y-%m-%d %H:%M:%S"), "mantis.value")),
        Text.assemble(("LOCAL  ", "mantis.label"), (local_now.strftime("%H:%M:%S %Z"), "mantis.value")),
        Text.assemble(("RUN  ", "mantis.label"), (status.run_id[:8], "mantis.value.dim")),
        Text.assemble(("BUILD  ", "mantis.label"), (status.software_version, "mantis.value.dim")),
    )
    facts.add_row(
        Text.assemble((f"{marks['ok']} SYSTEM  ", "mantis.label"),
                      ("OPERATIONAL" if status.error_count == 0 else "DEGRADED",
                       "mantis.ok" if status.error_count == 0 else "mantis.warn")),
        Text.assemble(("PROVIDER  ", "mantis.label"), (status.underlying_state, health_style)),
        Text.assemble(("WEBULL  ", "mantis.label"),
                      (status.webull_status.replace("_", " "), "mantis.warn")),
        Text.assemble(("LATENCY  ", "mantis.label"),
                      (num(status.latency_seconds, ".2f", " s"), "mantis.value.dim")),
    )
    facts.add_row(
        Text.assemble(("MODEL  ", "mantis.label"), (status.model_version, "mantis.value.dim")),
        Text.assemble(("MODE  ", "mantis.label"),
                      ("OBSERVATION ONLY" if status.observation_only else "UNKNOWN", "mantis.value.dim")),
        Text.assemble(("COMMIT  ", "mantis.label"), (status.git_commit[:10], "mantis.value.dim")),
        Text.assemble(("SCANS  ", "mantis.label"), (str(status.scan_count), "mantis.value.dim")),
    )

    body = Table.grid(expand=True)
    body.add_column(ratio=2)
    body.add_column(ratio=3)
    body.add_row(identity, facts)

    if snapshot.demo_mode:
        banner = Align.center(Text("  DEMO / SYNTHETIC DATA — NOT A LIVE SIGNAL  ", style="mantis.demo"))
        body = Group(body, banner)

    return Panel(body, box=box.SQUARE, border_style="mantis.border",
                 title=f"[mantis.title]{theme.PRODUCT_NAME}[/]",
                 title_align="left",
                 subtitle=f"[mantis.label]{status.policy_name}[/]", subtitle_align="right")


# ---------------------------------------------------------------------------
# Primary decision area
# ---------------------------------------------------------------------------

def decision_panel(view: Optional[AssetView], now: datetime,
                   ascii_only: bool = False, compact: bool = False) -> Panel:
    """The dominant element: side, confidence, eligibility, countdown, reason.

    ``compact`` drops the spacer rows and the block countdown so the panel
    still fits when the terminal is short or narrow.
    """
    if view is None:
        view = AssetView(asset="—")
    style = theme.decision_style(view.final_decision)
    marks = theme.glyphs(ascii_only)
    seconds = view.seconds_remaining(now)

    headline = Text(style.decorate(ascii_only), style=style.style)
    reason = Text(theme.reason_text(view.final_reason) or DASH, style="mantis.value.dim")
    bar_width = 14 if compact else 22

    left = Table.grid(padding=(0, 1), expand=True)
    left.add_column(justify="center", no_wrap=True, overflow="ellipsis")
    title_line = Text(view.asset, style="mantis.title")
    if compact:
        title_line.append("    " + theme.format_countdown(seconds),
                          style=theme.countdown_style(seconds if seconds is not None else 999))
    left.add_row(title_line)
    left.add_row(Text(view.contract_id or "NO ACTIVE CONTRACT", style="mantis.unit"))
    if not compact:
        left.add_row(Text(""))
    left.add_row(headline)
    left.add_row(reason)
    if not compact:
        left.add_row(Text(""))

    confidence = Table.grid(padding=(0, 1), expand=True)
    confidence.add_column(justify="left", no_wrap=True)
    confidence.add_column(justify="left", ratio=1)
    confidence.add_column(justify="right", no_wrap=True)
    confidence.add_row(Text("P(YES)", style="mantis.label"),
                       meter(view.p_yes, bar_width, marks), Text(pct(view.p_yes), style="mantis.value"))
    confidence.add_row(Text("P(NO)", style="mantis.label"),
                       meter(view.p_no, bar_width, marks), Text(pct(view.p_no), style="mantis.value"))
    confidence.add_row(Text("LCB", style="mantis.label"),
                       meter(view.conservative_bound, bar_width, marks),
                       Text(pct(view.conservative_bound), style="mantis.value"))
    left.add_row(confidence)

    buffer_pct = (view.buffer / view.reference) if (view.buffer is not None and view.reference) else None
    market = Text.assemble(
        ("CURRENT ", "mantis.label"),
        (price(view.current_price) if view.has_data else DASH, "mantis.value"),
        ("    REFERENCE ", "mantis.label"),
        (price(view.reference) if view.has_data else DASH, "mantis.value"),
        ("    BUFFER ", "mantis.label"),
        (pct(buffer_pct, 3) if (view.has_data and buffer_pct is not None) else DASH,
         "mantis.value.dim"),
    )
    if not compact:
        left.add_row(Text(""))
        left.add_row(market)

    eligibility = Table.grid(padding=(0, 2), expand=True)
    eligibility.add_column(justify="left", no_wrap=True, overflow="ellipsis")
    eligibility.add_column(justify="left", no_wrap=True, overflow="ellipsis")
    eligibility.add_column(justify="left", no_wrap=True, overflow="ellipsis")
    classification = theme.decision_style(view.classification_state)
    eligibility.add_row(
        Text.assemble(("SIGNAL  ", "mantis.label"),
                      (classification.label, classification.style)),
        Text.assemble(("ECONOMICS  ", "mantis.label"),
                      (view.ev_status.replace("_", " "),
                       "mantis.ok" if view.ev_status == "ROBUST_POSITIVE_EV" else "mantis.warn")),
        Text.assemble(("SIDE  ", "mantis.label"), (view.predicted_side or DASH, "mantis.value")),
    )
    if not compact:
        left.add_row(Text(""))
    left.add_row(eligibility)

    if compact:
        body: Any = left
    else:
        body = Table.grid(expand=True)
        body.add_column(ratio=3)
        body.add_column(ratio=2)
        body.add_row(left, countdown_block(view, now, ascii_only))

    return Panel(body, box=style.border,
                 border_style=style.style if style.emphasis else "mantis.border",
                 title="[mantis.title]PRIMARY DECISION[/]", title_align="left",
                 subtitle=f"[mantis.label]{view.reference_status or ''}[/]",
                 subtitle_align="right", padding=(0, 2))


# ---------------------------------------------------------------------------
# Asset cards
# ---------------------------------------------------------------------------

def asset_card(view: AssetView, now: datetime, focused: bool = False,
               ascii_only: bool = False) -> Panel:
    marks = theme.glyphs(ascii_only)
    style = theme.decision_style(view.final_decision)
    seconds = view.seconds_remaining(now)

    table = _grid()
    if not view.has_data:
        table.add_row(*_kv("STATUS", Text("NO CURRENT DATA", style="mantis.hold")))
        table.add_row(*_kv("PRICE", DASH))
        table.add_row(*_kv("REFERENCE", DASH))
        table.add_row(*_kv("P(YES)", DASH))
        table.add_row(*_kv("SIDE", DASH))
    else:
        table.add_row(*_kv("PRICE", price(view.current_price)))
        reference = Text(price(view.reference), style="mantis.value")
        table.add_row(*_kv("REFERENCE", reference))
        buffer_pct = None
        if view.buffer is not None and view.reference:
            buffer_pct = view.buffer / view.reference
        table.add_row(*_kv("BUFFER", Text(
            f"{signed(view.buffer, ',.4f')}  {pct(buffer_pct, 3) if buffer_pct is not None else ''}".strip(),
            style="mantis.value.dim")))
        probability = Text()
        probability.append_text(meter(view.p_yes, 10, marks))
        probability.append("  " + pct(view.p_yes), style="mantis.value")
        table.add_row(*_kv("P(YES)", probability))
        table.add_row(*_kv("P(NO)", Text(pct(view.p_no), style="mantis.value.dim")))
        side = Text(view.predicted_side or DASH,
                    style="mantis.yes" if view.predicted_side == "YES" else "mantis.no")
        side.append(f"   LCB {pct(view.conservative_bound)}", style="mantis.value.dim")
        table.add_row(*_kv("SIDE", side))
        fragility = Text()
        fragility.append_text(meter(view.fragility, 8, marks, maximum=100.0))
        fragility.append(f"  {num(view.fragility, '.1f')}", style="mantis.value")
        table.add_row(*_kv("FRAGILITY", fragility))
        table.add_row(*_kv("CROSS RISK", Text(
            f"{pct(view.crossing_probability)}  ({view.reference_crossings if view.reference_crossings is not None else DASH}x)",
            style="mantis.value.dim")))

    classification = theme.decision_style(view.classification_state)
    table.add_row(*_kv("CLASS", Text(classification.label, style=classification.style)))
    table.add_row(*_kv("ECON", Text(view.ev_status.replace("_", " "), style="mantis.value.dim")))
    final = Text(f"{style.ascii_glyph if ascii_only else style.glyph} {style.label}", style=style.style)
    table.add_row(*_kv("FINAL", final))
    if view.spark:
        table.add_row(*_kv("P(YES) TREND", sparkline(view.spark, marks, 16)))

    title = Text()
    title.append(view.asset, style="mantis.title" if focused else "mantis.value")
    if focused:
        title.append("  " + ("*" if ascii_only else "◆"), style="mantis.brand")
    if view.synthetic:
        title.append("  SYNTHETIC", style="mantis.demo")

    return Panel(table, box=box.SQUARE,
                 border_style="mantis.border.active" if focused else "mantis.border",
                 title=title, title_align="left",
                 subtitle=f"[{theme.countdown_style(seconds if seconds is not None else 999)}]"
                          f"{theme.format_countdown(seconds)}[/]",
                 subtitle_align="right", padding=(0, 1))


def asset_rows(snapshot: UiSnapshot, now: datetime, ascii_only: bool = False) -> Table:
    """Compact one-row-per-asset form, used when the terminal is short."""
    table = Table(box=None, expand=True, show_header=True, header_style="mantis.label",
                  pad_edge=False, padding=(0, 1))
    for name, width, justify in (
            ("ASSET", 8, "left"), ("T-", 7, "left"), ("PRICE", 12, "right"),
            ("REFERENCE", 12, "right"), ("BUF %", 8, "right"), ("P(YES)", 7, "right"),
            ("LCB", 7, "right"), ("FRAG", 6, "right"), ("CROSS", 6, "right"),
            ("CLASS", 10, "left"), ("ECON", 9, "left"), ("FINAL", 11, "left")):
        table.add_column(name, width=width, justify=justify, no_wrap=True, overflow="ellipsis")

    for view in snapshot.assets:
        style = theme.decision_style(view.final_decision)
        classification = theme.decision_style(view.classification_state)
        seconds = view.seconds_remaining(now)
        mark = style.ascii_glyph if ascii_only else style.glyph
        buffer_pct = (view.buffer / view.reference) if (view.buffer is not None and view.reference) else None
        table.add_row(
            Text(view.asset.replace("-USD", "") + (" *" if view.asset == snapshot.focus else ""),
                 style="mantis.title" if view.asset == snapshot.focus else "mantis.value"),
            Text(theme.format_countdown(seconds),
                 style=theme.countdown_style(seconds if seconds is not None else 999)),
            Text(price(view.current_price) if view.has_data else DASH, style="mantis.value"),
            Text(price(view.reference) if view.has_data else DASH, style="mantis.value.dim"),
            Text(pct(buffer_pct, 3) if view.has_data else DASH, style="mantis.value.dim"),
            Text(pct(view.p_yes) if view.has_data else DASH, style="mantis.value"),
            Text(pct(view.conservative_bound) if view.has_data else DASH, style="mantis.value.dim"),
            Text(num(view.fragility, ".0f") if view.has_data else DASH, style="mantis.value.dim"),
            Text(pct(view.crossing_probability, 0) if view.has_data else DASH, style="mantis.value.dim"),
            Text(classification.label, style=classification.style),
            Text("EV" if view.economics_available else "N/A", style="mantis.value.dim"),
            Text(f"{mark} {style.label}", style=style.style),
        )
    return table


def assets_panel(snapshot: UiSnapshot, now: datetime, ascii_only: bool = False,
                 compact: bool = False) -> Panel:
    if not snapshot.assets:
        body: Any = Text("NO ASSETS CONFIGURED", style="mantis.warn")
    elif compact:
        body = asset_rows(snapshot, now, ascii_only)
    else:
        body = Columns([asset_card(view, now, focused=(view.asset == snapshot.focus),
                                   ascii_only=ascii_only) for view in snapshot.assets],
                       expand=True, equal=True, padding=(0, 0))
    return Panel(body, box=box.SQUARE, border_style="mantis.border",
                 title="[mantis.title]ASSET SURVEILLANCE[/]", title_align="left",
                 subtitle="[mantis.label]FIVE-ASSET CONTINUOUS SCAN[/]", subtitle_align="right",
                 padding=(0, 0))


# ---------------------------------------------------------------------------
# Economics
# ---------------------------------------------------------------------------

def economics_panel(view: Optional[AssetView], ascii_only: bool = False) -> Panel:
    view = view or AssetView(asset="—")
    table = _grid()

    if not view.economics_available:
        # Classification still shows above; economics degrades on its own.
        table.add_row(*_kv("EV ENGINE", Text("DISABLED", style="mantis.warn")))
        table.add_row(*_kv("STATUS", Text("UNAVAILABLE", style="mantis.warn")))
        table.add_row(*_kv("QUOTE", Text("NO VERIFIED BOOK", style="mantis.value.dim")))
        for label in ("YES BID / ASK", "NO BID / ASK", "BREAK-EVEN", "MODEL EDGE",
                      "LCB EDGE", "POINT EV", "LCB EV", "RETURN / COST"):
            table.add_row(*_kv(label, DASH))
        table.add_row(*_kv("FEES", Text("UNKNOWN FEES", style="mantis.unit")))
        table.add_row(*_kv("SLIPPAGE", Text("NOT MODELED", style="mantis.unit")))
    else:
        ev_style = "mantis.ok" if view.ev_status == "ROBUST_POSITIVE_EV" else "mantis.warn"
        table.add_row(*_kv("EV STATUS", Text(view.ev_status.replace("_", " "), style=ev_style)))
        table.add_row(*_kv("QUOTE", Text(view.quote_status.replace("_", " "), style="mantis.value.dim")))
        table.add_row(*_kv("QUOTE AGE", num(view.quote_age, ".1f", " s")))
        table.add_row(*_kv("YES BID / ASK", f"{num(view.yes_bid, '.3f')} / {num(view.yes_ask, '.3f')}"))
        table.add_row(*_kv("NO BID / ASK", f"{num(view.no_bid, '.3f')} / {num(view.no_ask, '.3f')}"))
        table.add_row(*_kv("BREAK-EVEN", pct(view.break_even, 2)))
        table.add_row(*_kv("MODEL EDGE", Text(pct(view.model_edge, 2), style="mantis.value")))
        table.add_row(*_kv("LCB EDGE", Text(pct(view.lcb_edge, 2), style="mantis.value")))
        table.add_row(*_kv("POINT EV", Text(money(view.point_ev), style="mantis.value")))
        table.add_row(*_kv("LCB EV", Text(money(view.lcb_ev), style="mantis.value")))
        table.add_row(*_kv("RETURN / COST", pct(view.expected_return_on_cost, 1)))
        table.add_row(*_kv("FEES", Text(view.fees_status.replace("_", " "), style="mantis.unit")))
        table.add_row(*_kv("SLIPPAGE", Text(
            view.slippage_status.replace("SLIPPAGE_", "").replace("_", " "), style="mantis.unit")))

    return Panel(table, box=box.SQUARE, border_style="mantis.border",
                 title="[mantis.title]CONTRACT ECONOMICS[/]", title_align="left",
                 subtitle=f"[mantis.label]{view.asset}[/]", subtitle_align="right",
                 padding=(0, 1))


# ---------------------------------------------------------------------------
# Provider status
# ---------------------------------------------------------------------------

def provider_panel(status: SystemStatus, ascii_only: bool = False) -> Panel:
    marks = theme.glyphs(ascii_only)
    table = _grid()

    def indicator(state: str) -> Text:
        good = state in ("LIVE", "AVAILABLE", "OK", "READY")
        warn = state in ("DEGRADED", "CACHED", "SCHEMA UNVERIFIED", "AUTH_NOT_CONFIGURED",
                         "AUTH NOT CONFIGURED", "UNAVAILABLE", "ECONOMICS_UNAVAILABLE", "DISABLED")
        mark = marks["ok"] if good else marks["warn"] if warn else marks["crit"]
        style = "mantis.ok" if good else "mantis.warn" if warn else "mantis.crit"
        return Text(f"{mark} {state.replace('_', ' ')}", style=style)

    table.add_row(*_kv("UNDERLYING", indicator(status.underlying_state)))
    table.add_row(*_kv("  SOURCE", Text(status.underlying_provider, style="mantis.value.dim")))
    table.add_row(*_kv("  LAST OK", status.underlying_last_success.strftime("%H:%M:%S")
                       if status.underlying_last_success else DASH))
    table.add_row(*_kv("  OK / FAIL", f"{status.underlying_successes} / {status.underlying_failures}"))
    table.add_row(*_kv("  LATENCY", num(status.latency_seconds, ".2f", " s")))
    table.add_row(*_kv("WEBULL", indicator(status.webull_status)))
    table.add_row(*_kv("ECONOMICS", indicator(status.economics_status)))
    table.add_row(*_kv("  SOURCE", Text(status.economics_provider, style="mantis.value.dim")))
    table.add_row(*_kv("REFERENCE", Text(status.reference_status.replace("_", " "), style="mantis.warn")))
    table.add_row(*_kv("QUOTE", Text(status.quote_status.replace("_", " "), style="mantis.value.dim")))
    table.add_row(*_kv("SCANS", f"{status.scan_count}  ({status.error_count} errors)"))
    table.add_row(*_kv("AUDIO / VOICE", Text(
        ("ON" if status.audio_enabled else "OFF") + "  /  " + ("ON" if status.voice_enabled else "OFF"),
        style="mantis.ok" if (status.audio_enabled or status.voice_enabled) else "mantis.unit")))

    return Panel(table, box=box.SQUARE, border_style="mantis.border",
                 title="[mantis.title]PROVIDER STATUS[/]", title_align="left",
                 padding=(0, 1))


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def diagnostics_panel(view: Optional[AssetView], status: SystemStatus,
                      ascii_only: bool = False) -> Panel:
    view = view or AssetView(asset="—")
    table = _grid()
    table.add_row(*_kv("SIGMA (WINDOW)", num(view.volatility_estimate, ".6f")))
    table.add_row(*_kv("BUFFER Z", num(view.buffer_z, ".3f")))
    table.add_row(*_kv("VOL REGIME", view.volatility_regime or "UNKNOWN"))
    table.add_row(*_kv("CROSSING PROB", pct(view.crossing_probability, 2)))
    table.add_row(*_kv("CROSSING COUNT", view.reference_crossings
                       if view.reference_crossings is not None else DASH))
    table.add_row(*_kv("FRAGILITY", num(view.fragility, ".2f")))
    table.add_row(*_kv("DISAGREEMENT", num(view.disagreement, ".4f")))
    table.add_row(*_kv("DATA AGE", num(view.data_age_seconds, ".1f", " s")))
    table.add_row(*_kv("FETCH LATENCY", num(view.fetch_latency_seconds, ".3f", " s")))
    table.add_row(*_kv("PROVIDER CHAIN", view.provider_mode or DASH))
    table.add_row(*_kv("QUOTE VALIDATION", view.quote_status.replace("_", " ")))
    table.add_row(*_kv("QUALITY", (view.quality_reason or DASH)[:26]))
    table.add_row(Text(""), Text(""))
    table.add_row(*_kv("MODEL", view.model_version or status.model_version))
    table.add_row(*_kv("POLICY", status.policy_name))
    table.add_row(*_kv("CONFIG HASH", (view.config_hash or status.config_hash)[:16]))
    table.add_row(*_kv("GIT COMMIT", (view.git_commit or status.git_commit)[:12]))
    return Panel(table, box=box.SQUARE, border_style="mantis.border",
                 title="[mantis.title]ADVANCED DIAGNOSTICS[/]", title_align="left",
                 subtitle=f"[mantis.label]{view.asset}[/]", subtitle_align="right",
                 padding=(0, 1))


# ---------------------------------------------------------------------------
# Forward validation
# ---------------------------------------------------------------------------

def forward_panel(forward: Optional[dict], historical: Optional[dict],
                  ascii_only: bool = False) -> Panel:
    """Forward observations only. Never merged with the historical holdout."""
    table = _grid()
    table.add_row(Text("FORWARD OBSERVATION SAMPLE", style="mantis.title"),
                  Text("LIVE — NOT A BACKTEST", style="mantis.unit"))
    if not forward:
        table.add_row(*_kv("STATUS", Text("NO FORWARD RECORDS YET", style="mantis.value.dim")))
        table.add_row(*_kv("CLASSIFICATION ACC", DASH))
    else:
        table.add_row(*_kv("SAMPLE STATUS", Text(str(forward.get("sample_label", DASH)),
                                                 style="mantis.warn")))
        table.add_row(*_kv("CONTRACTS / ENTRIES",
                           f"{forward.get('total_contracts_observed', DASH)}"
                           f" / {forward.get('total_entry_events', DASH)}"))
        table.add_row(*_kv("RESOLVED / ABSTAIN",
                           f"{forward.get('resolved_entries', DASH)}"
                           f" / {pct(forward.get('abstention_rate'))}"))
        accuracy = forward.get("classification_accuracy")
        ci = ""
        if forward.get("ci_low") is not None:
            ci = f"  [{pct(forward.get('ci_low'), 1)}, {pct(forward.get('ci_high'), 1)}]"
        table.add_row(*_kv("CLASSIFICATION ACC",
                           Text(pct(accuracy, 2) + ci, style="mantis.value")))
        yes, no = forward.get("yes") or {}, forward.get("no") or {}
        table.add_row(*_kv("YES / NO ACCURACY",
                           f"{pct(yes.get('accuracy'), 1)} (n={yes.get('n', 0)})"
                           f"  {pct(no.get('accuracy'), 1)} (n={no.get('n', 0)})"))
        per_asset = " ".join(
            f"{asset.replace('-USD', '')} {pct((stats or {}).get('accuracy'), 0)}"
            for asset, stats in sorted((forward.get("per_asset") or {}).items()))
        table.add_row(*_kv("PER ASSET", Text(per_asset or DASH, style="mantis.value.dim")))

    table.add_row(Text(""), Text(""))
    table.add_row(Text("HISTORICAL PROXY HOLDOUT", style="mantis.title"),
                  Text("PHASE 6 — SEPARATE", style="mantis.unit"))
    if historical:
        table.add_row(*_kv("ACCURACY / COVERAGE",
                           f"{pct(historical.get('historical_accuracy'), 2)}"
                           f" / {pct(historical.get('historical_coverage'), 2)}"))
        regime = str(historical.get("status", DASH))
        warnings = ", ".join(str(w) for w in (historical.get("warnings") or []))
        table.add_row(*_kv("REGIME", Text(regime.replace("_", " "),
                                          style="mantis.warn" if "SHIFT" in regime else "mantis.value.dim")))
        if warnings:
            table.add_row(*_kv("  WARNING", Text(warnings, style="mantis.crit")))
    else:
        table.add_row(*_kv("ACCURACY / COVERAGE", DASH))
    table.add_row(Text("SAMPLES ARE NEVER COMBINED", style="mantis.unit"), Text(""))

    return Panel(table, box=box.SQUARE, border_style="mantis.border",
                 title="[mantis.title]VALIDATION[/]", title_align="left", padding=(0, 1))


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------

def event_log_panel(events: Iterable[LogEntry], rows: int,
                    local_tz=None, ascii_only: bool = False) -> Panel:
    table = Table(box=None, expand=True, show_header=True, header_style="mantis.label",
                  pad_edge=False, padding=(0, 1))
    table.add_column("TIME", no_wrap=True, width=8)
    table.add_column("LVL", no_wrap=True, width=8)
    table.add_column("SOURCE", no_wrap=True, width=9)
    table.add_column("EVENT", no_wrap=True, width=22)
    table.add_column("DETAIL", no_wrap=True, overflow="ellipsis")

    recent = list(events)[-rows:]
    for entry in recent:
        stamp = entry.timestamp.astimezone(local_tz) if local_tz else entry.timestamp
        style = SEVERITY_STYLE.get(entry.severity, "mantis.info")
        table.add_row(
            Text(stamp.strftime("%H:%M:%S"), style="mantis.unit"),
            Text(entry.severity, style=style),
            Text(entry.source, style="mantis.value.dim"),
            Text(entry.message, style=style),
            Text(entry.detail, style="mantis.value.dim"),
        )
    if not recent:
        table.add_row(Text("--:--:--", style="mantis.unit"), Text("INFO", style="mantis.info"),
                      Text("SYSTEM", style="mantis.value.dim"),
                      Text("AWAITING EVENTS", style="mantis.value.dim"), Text(""))

    return Panel(table, box=box.SQUARE, border_style="mantis.border",
                 title="[mantis.title]EVENT LOG[/]", title_align="left",
                 subtitle="[mantis.label]FORWARD LOGS ON DISK ARE COMPLETE[/]",
                 subtitle_align="right", padding=(0, 1))


def decisions_panel(snapshot: UiSnapshot, now: datetime,
                    ascii_only: bool = False) -> Panel:
    """Current advisory per asset: the at-a-glance answer for all five."""
    table = Table(box=None, expand=True, show_header=True, header_style="mantis.label",
                  pad_edge=False, padding=(0, 1))
    table.add_column("ASSET", no_wrap=True, width=9)
    table.add_column("T-", no_wrap=True, width=7)
    table.add_column("CLASS", no_wrap=True, width=11)
    table.add_column("FINAL", no_wrap=True, width=12)
    table.add_column("REASON", no_wrap=True, overflow="ellipsis")

    for view in snapshot.assets:
        style = theme.decision_style(view.final_decision)
        classification = theme.decision_style(view.classification_state)
        seconds = view.seconds_remaining(now)
        mark = style.ascii_glyph if ascii_only else style.glyph
        table.add_row(
            Text(view.asset.replace("-USD", ""),
                 style="mantis.title" if view.asset == snapshot.focus else "mantis.value"),
            Text(theme.format_countdown(seconds),
                 style=theme.countdown_style(seconds if seconds is not None else 999)),
            Text(classification.label, style=classification.style),
            Text(f"{mark} {style.label}", style=style.style),
            Text(theme.reason_text(view.final_reason), style="mantis.value.dim"),
        )
    return Panel(table, box=box.SQUARE, border_style="mantis.border",
                 title="[mantis.title]CURRENT ADVISORY[/]", title_align="left",
                 subtitle="[mantis.label]OBSERVATION ONLY[/]", subtitle_align="right",
                 padding=(0, 1))


# ---------------------------------------------------------------------------
# Errors and warnings
# ---------------------------------------------------------------------------

def error_panel(error: SystemError, developer_mode: bool = False) -> Panel:
    from .errors import render_lines
    body = Text("\n".join(render_lines(error, developer_mode)), style="mantis.value")
    return Panel(body, box=box.HEAVY, border_style="mantis.crit",
                 title="[mantis.crit]SYSTEM ERROR[/]", title_align="left", padding=(0, 1))


def too_small_panel(width: int, height: int, minimum_width: int, minimum_height: int) -> Panel:
    body = Text.assemble(
        (f"{theme.PRODUCT_NAME} COMMAND CENTER\n\n", "mantis.brand"),
        ("TERMINAL TOO SMALL\n\n", "mantis.warn"),
        (f"current   {width} x {height}\n", "mantis.value"),
        (f"required  {minimum_width} x {minimum_height}\n\n", "mantis.value"),
        ("Enlarge the window, reduce the font size, or run with --no-ui\n", "mantis.value.dim"),
        ("for the plain console renderer. The scanner keeps running.", "mantis.value.dim"),
    )
    return Panel(Align.center(body, vertical="middle"), box=box.HEAVY,
                 border_style="mantis.warn", title="[mantis.title]MANTIS[/]")
