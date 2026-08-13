# ================================================================
# MANTIS // 15M RESOLUTION V3 — Market Analysis and Neuro-Tactical Intraday Signal System
# Signals only. Does NOT connect to Webull or place orders.
# Underlying crypto movement != exact Webull event-contract P&L.
# ================================================================

import os
import csv
import sqlite3
import uuid
import time
import platform
import subprocess
import threading
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich import box
except ImportError as exc:
    missing = getattr(exc, "name", "a required package")
    print(f"\nMissing Python package: {missing}")
    print("Run this command, then start CICADA again:")
    print("python -m pip install yfinance pandas numpy rich tzdata")
    raise SystemExit(1)


# ================================================================
# SETTINGS
# ================================================================

TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]

POLL_SECONDS = 10

# Market data: bootstrap with 7 days so we always have enough candles.
BOOTSTRAP_PERIOD = "7d"
REFRESH_PERIOD = "1d"
DATA_INTERVAL = "1m"
MIN_CANDLES = 60
MAX_CACHE_ROWS = 5000
MAX_DATA_AGE_MINUTES = 5

# 15-minute event-contract timing.
NO_NEW_ENTRY_MINUTES = 0.75
FORCE_EXIT_MINUTES = 0.12

# Entry filters.
MIN_ENTRY_SCORE = 0.0
MIN_ENTRY_STRENGTH = 80.0

# Resolution model.
MIN_RESOLUTION_CONFIDENCE = 0.80
MIN_ENTRY_ELAPSED_MINUTES = 2.5
MIN_BUFFER_Z = 0.55
EMERGENCY_OPPOSITE_CONFIDENCE = 0.82

COOLDOWN_SECONDS = 45

# Risk.
ATR_STOP_MULTIPLIER = 0.95
MIN_STOP_PCT = 0.0008
MAX_STOP_PCT = 0.0032
REWARD_RISK_RATIO = 1.15

# Profit protection.
BREAKEVEN_TRIGGER = 0.00035
LOCK_LEVEL_1 = 0.00065
LOCK_LEVEL_2 = 0.00115
LOCK_LEVEL_3 = 0.00190
WEAKNESS_EXIT_SCORE = 1.75
REVERSAL_EXIT_SCORE = 1.25

# Manual execution model.
# The system cannot see your actual Webull fill, so it models the time
# between alert -> tap/order -> fill and exit alert -> actual close.
ASSUMED_ENTRY_DELAY_SECONDS = 7
ASSUMED_EXIT_DELAY_SECONDS = 8

# Voice command system.
VOICE_ENABLED = True
VOICE_REPEAT_ENTRY = 2
VOICE_REPEAT_EXIT = 3
VOICE_RATE = -1
VOICE_VOLUME = 100

# CICADA V3 scalp-protection layer.
# These thresholds refer to the underlying crypto move, not the event-contract P&L.
SCALP_ARM_TRIGGER = 0.00035
SCALP_FAST_LOCK_TRIGGER = 0.00070
SCALP_STRONG_LOCK_TRIGGER = 0.00120

SCALP_MAX_GIVEBACK_EARLY = 0.45
SCALP_MAX_GIVEBACK_MID = 0.35
SCALP_MAX_GIVEBACK_STRONG = 0.25

SCALP_DECAY_MIN_PEAK = 0.00040
SCALP_DECAY_GIVEBACK = 0.00015

PROFITABLE_SCORE_DROP_EXIT = 0.85
PROFITABLE_STRENGTH_DROP_EXIT = 4.0

# Pending-entry protection.
PENDING_SCORE_DROP = 0.75
PENDING_STRENGTH_DROP = 4.0
PENDING_MAX_ADVERSE_MOVE = 0.0008

# Faster scalp protection.
EARLY_FAILURE_SECONDS = 75
EARLY_FAILURE_MOVE = -0.00055
CONVICTION_DROP_EXIT_POINTS = 1.15
STRENGTH_DROP_EXIT_POINTS = 6.5

# Exit-delay anticipation. When short-term momentum is moving against the
# position, CICADA exits before the raw profit floor is actually touched.
BASE_EXIT_BUFFER = 0.00010
MAX_EXIT_BUFFER = 0.00045

# System.
try:
    EASTERN = ZoneInfo("America/New_York")
except Exception:
    EASTERN = datetime.now().astimezone().tzinfo

LOG_FILE = "mantis_resolution_v3_log.csv"

LEDGER_DB = "mantis_resolution_v3_ledger.db"
LEDGER_CSV = "mantis_resolution_v3_ledger.csv"

# Composite active-trade health score. This is NOT a probability.
SCALP_HEALTH_WARNING = 58.0
SCALP_HEALTH_EXIT = 42.0
console = Console()

active_position = None
pending_entry = None
last_exit_time = None
market_cache = {ticker: None for ticker in TICKERS}
data_status = {ticker: "BOOTING" for ticker in TICKERS}


# ================================================================
# ASCII ART
# ================================================================

CICADA_LOGO = r"""
        __  \
   .-._/  \_.-.
  /___  __  ___\        ███╗   ███╗ █████╗ ███╗   ██╗████████╗██╗███████╗
      \/  \/             ████╗ ████║██╔══██╗████╗  ██║╚══██╔══╝██║██╔════╝
   /\  .--.  /\          ██╔████╔██║███████║██╔██╗ ██║   ██║   ██║███████╗
  /  \/    \/  \         ██║╚██╔╝██║██╔══██║██║╚██╗██║   ██║   ██║╚════██║
 |   /  /\  \   |        ██║ ╚═╝ ██║██║  ██║██║ ╚████║   ██║   ██║███████║
 |  /__/  \__\  |        ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚══════╝
  \   \__/   /
   '._    _.'
      |__|

                                  // 15M RESOLUTION V3
            MARKET ANALYSIS AND NEURO-TACTICAL INTRADAY SIGNAL SYSTEM
"""

YES_ART = r"""
YES
"""

NO_ART = r"""
NO
"""

EXIT_ART = r"""
EXIT
"""

PROFIT_ART = r"""
TAKE PROFIT
"""


# ================================================================
# BASIC HELPERS
# ================================================================

def now_et():
    return datetime.now(EASTERN)


def current_contract_window(now=None):
    """
    Crypto event windows are hard 15-minute clock buckets:
      :00 -> :15
      :15 -> :30
      :30 -> :45
      :45 -> next :00

    Entry time NEVER changes the expiration of the current contract.
    """
    now = now or now_et()
    now = now.replace(microsecond=0)

    start_minute = (now.minute // 15) * 15
    start = now.replace(
        minute=start_minute,
        second=0,
    )
    end = start + timedelta(minutes=15)

    contract_id = (
        f"{start.strftime('%Y%m%d-%H%M')}"
        f"_{end.strftime('%H%M')}"
    )

    return {
        "id": contract_id,
        "start": start,
        "end": end,
        "seconds_left": max(
            0.0,
            (end - now).total_seconds(),
        ),
        "minutes_left": max(
            0.0,
            (end - now).total_seconds() / 60.0,
        ),
        "elapsed_minutes": max(
            0.0,
            (now - start).total_seconds() / 60.0,
        ),
    }


def minutes_until_next_quarter():
    return current_contract_window()["minutes_left"]


def fmt_price(price):
    price = float(price)
    if price >= 1000:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:,.4f}"
    return f"${price:,.6f}"


def pct(value):
    return f"{float(value) * 100:+.3f}%"


def strength_bar(value, width=22):
    value = max(0.0, min(100.0, float(value)))
    filled = int(value / 100.0 * width)
    return ("█" * filled) + ("░" * (width - filled))


def move_bar(value, max_move=0.01, width=22):
    amount = min(abs(float(value)), max_move)
    filled = int(amount / max_move * width)
    return ("█" * filled) + ("░" * (width - filled))


# ================================================================
# SOUND + ANIMATION
# ================================================================

def terminal_bell():
    print("\a", end="", flush=True)


def windows_beeps(sequence):
    try:
        import winsound
        for frequency, duration in sequence:
            winsound.Beep(frequency, duration)
    except Exception:
        terminal_bell()



# ================================================================
# VOICE COMMAND SYSTEM
# ================================================================

VOICE_NAMES = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
    "XRP-USD": "X R P",
}


def spoken_coin(ticker):
    return VOICE_NAMES.get(ticker, ticker.replace("-USD", ""))


def _speak_windows(text):
    if platform.system() != "Windows" or not VOICE_ENABLED:
        return

    safe_text = text.replace("'", "''")

    ps_command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {VOICE_RATE}; "
        f"$s.Volume = {VOICE_VOLUME}; "
        f"$s.Speak('{safe_text}')"
    )

    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ps_command,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            timeout=20,
            check=False,
        )
    except Exception:
        return


def speak_async(text, repeat=1, pause=0.15):
    if not VOICE_ENABLED:
        return

    def worker():
        for i in range(max(1, repeat)):
            _speak_windows(text)
            if i < repeat - 1:
                time.sleep(pause)

    threading.Thread(
        target=worker,
        daemon=True,
        name="CICADA-Voice",
    ).start()


def voice_entry(ticker, direction):
    speak_async(
        f"MANTIS signal. Buy {direction}. {spoken_coin(ticker)}.",
        repeat=VOICE_REPEAT_ENTRY,
        pause=0.10,
    )


def voice_exit(ticker, direction):
    speak_async(
        f"MANTIS exit. Close {spoken_coin(ticker)} {direction}. Now.",
        repeat=VOICE_REPEAT_EXIT,
        pause=0.08,
    )


def voice_profit(ticker, direction):
    speak_async(
        f"MANTIS profit alert. Close {spoken_coin(ticker)} {direction}. Take profit now.",
        repeat=VOICE_REPEAT_EXIT,
        pause=0.08,
    )


def voice_cancel(ticker, direction):
    speak_async(
        f"MANTIS cancel. Do not enter {spoken_coin(ticker)} {direction}.",
        repeat=2,
        pause=0.10,
    )


def sound_entry():
    if platform.system() == "Windows":
        windows_beeps([
            (240, 260), (240, 260),
            (520, 220), (240, 260),
            (760, 520),
        ])
    else:
        for _ in range(5):
            terminal_bell()
            time.sleep(0.10)


def sound_exit():
    if platform.system() == "Windows":
        windows_beeps([
            (210, 250), (820, 250),
            (210, 250), (820, 250),
            (210, 250), (980, 700),
        ])
    else:
        for _ in range(7):
            terminal_bell()
            time.sleep(0.08)


def sound_profit():
    if platform.system() == "Windows":
        windows_beeps([
            (300, 220), (700, 220),
            (300, 220), (900, 220),
            (1120, 650),
        ])
    else:
        for _ in range(6):
            terminal_bell()
            time.sleep(0.09)


def sound_warning():
    if platform.system() == "Windows":
        windows_beeps([
            (250, 300), (250, 300), (250, 500),
        ])
    else:
        for _ in range(3):
            terminal_bell()
            time.sleep(0.12)


def flash_panel(content, title, style, flashes=8, delay=0.22):
    for i in range(flashes):
        console.clear()
        console.print(
            Panel(
                Align.center(content),
                title=title,
                border_style=style if i % 2 == 0 else "bright_white",
                box=box.SQUARE if i % 2 == 0 else box.SQUARE,
                padding=(1, 4),
            )
        )
        time.sleep(delay)


# ================================================================
# LOGGING
# ================================================================

def log_event(event, ticker, direction, price, score, strength, reason):
    file_exists = os.path.exists(LOG_FILE)
    fields = [
        "timestamp", "event", "ticker", "direction",
        "price", "score", "strength", "reason",
    ]

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "timestamp": now_et().isoformat(),
            "event": event,
            "ticker": ticker,
            "direction": direction,
            "price": float(price),
            "score": round(float(score), 3),
            "strength": round(float(strength), 1),
            "reason": reason,
        })


# ================================================================
# MARKET DATA
# ================================================================

def normalize_yfinance_data(data):
    if data is None or data.empty:
        return None

    data = data.copy()

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in data.columns for column in required):
        return None

    data = data[required].copy()

    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    data.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    data = data[~data.index.duplicated(keep="last")]
    data.sort_index(inplace=True)

    return data


def yfinance_download_attempt(ticker, period):
    raw = yf.download(
        ticker,
        period=period,
        interval=DATA_INTERVAL,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    return normalize_yfinance_data(raw)


def yfinance_history_attempt(ticker, period):
    raw = yf.Ticker(ticker).history(
        period=period,
        interval=DATA_INTERVAL,
        auto_adjust=False,
    )
    return normalize_yfinance_data(raw)


def merge_with_cache(ticker, fresh):
    old = market_cache.get(ticker)

    if fresh is None or fresh.empty:
        return old

    if old is None or old.empty:
        merged = fresh.copy()
    else:
        merged = pd.concat([old, fresh])
        merged = merged[~merged.index.duplicated(keep="last")]
        merged.sort_index(inplace=True)

    if len(merged) > MAX_CACHE_ROWS:
        merged = merged.iloc[-MAX_CACHE_ROWS:].copy()

    market_cache[ticker] = merged
    return merged


def data_age_minutes(data):
    if data is None or data.empty:
        return float("inf")

    last_stamp = pd.Timestamp(data.index[-1])

    try:
        if last_stamp.tzinfo is not None:
            now_stamp = pd.Timestamp.now(tz=last_stamp.tz)
        else:
            now_stamp = pd.Timestamp.now().tz_localize(None)

        return max(
            0.0,
            (now_stamp - last_stamp).total_seconds() / 60.0,
        )
    except Exception:
        return 0.0


def fetch_market_data(ticker):
    cached = market_cache.get(ticker)
    period = (
        BOOTSTRAP_PERIOD
        if cached is None or len(cached) < MIN_CANDLES
        else REFRESH_PERIOD
    )

    fresh = None
    first_error = None

    try:
        fresh = yfinance_download_attempt(ticker, period)
    except Exception as exc:
        first_error = exc

    if fresh is None or len(fresh) < MIN_CANDLES:
        try:
            fallback = yfinance_history_attempt(ticker, BOOTSTRAP_PERIOD)
            if fallback is not None and not fallback.empty:
                fresh = fallback
        except Exception as exc:
            if first_error is None:
                first_error = exc

    combined = merge_with_cache(ticker, fresh)

    if combined is not None and len(combined) >= MIN_CANDLES:
        age = data_age_minutes(combined)

        if age > MAX_DATA_AGE_MINUTES:
            data_status[ticker] = f"STALE • {age:.1f}m old"
            return None

        if fresh is None or fresh.empty:
            data_status[ticker] = f"CACHED • {len(combined)} candles"
        else:
            data_status[ticker] = f"LIVE • {len(combined)} candles"

        return combined

    if first_error is not None:
        data_status[ticker] = f"ERROR • {type(first_error).__name__}"
    elif combined is not None:
        data_status[ticker] = f"WARMING UP • {len(combined)}/{MIN_CANDLES}"
    else:
        data_status[ticker] = "NO DATA"

    return None


# ================================================================
# INDICATORS
# ================================================================

def add_indicators(df):
    d = df.copy()

    d["ema5"] = d["Close"].ewm(span=5, adjust=False).mean()
    d["ema9"] = d["Close"].ewm(span=9, adjust=False).mean()
    d["ema21"] = d["Close"].ewm(span=21, adjust=False).mean()

    ema12 = d["Close"].ewm(span=12, adjust=False).mean()
    ema26 = d["Close"].ewm(span=26, adjust=False).mean()

    d["macd"] = ema12 - ema26
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_signal"]
    d["macd_hist_change"] = d["macd_hist"].diff()

    change = d["Close"].diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi"] = (100 - (100 / (1 + rs))).fillna(50)

    previous_close = d["Close"].shift(1)
    true_range = pd.concat(
        [
            d["High"] - d["Low"],
            (d["High"] - previous_close).abs(),
            (d["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    d["atr"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()

    d["mom1"] = d["Close"].pct_change(1)
    d["mom3"] = d["Close"].pct_change(3)
    d["mom5"] = d["Close"].pct_change(5)
    d["mom10"] = d["Close"].pct_change(10)

    candle_range = (d["High"] - d["Low"]).replace(0, np.nan)
    d["close_location"] = (
        (d["Close"] - d["Low"]) / candle_range
    ).fillna(0.5)

    d["body_pct"] = (
        (d["Close"] - d["Open"])
        / d["Open"].replace(0, np.nan)
    ).fillna(0)

    volume_average = d["Volume"].rolling(20).mean()
    d["volume_ratio"] = (
        d["Volume"] / volume_average.replace(0, np.nan)
    )
    d["volume_ratio"] = (
        d["volume_ratio"]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(1.0)
    )

    return d


# ================================================================
# SIGNAL MODEL
# ================================================================

def analyze_market(df):
    """
    Predict which side is most likely to still be winning at the 15-minute
    resolution, rather than trying to scalp every favorable wiggle.

    IMPORTANT:
    The model uses the first price of the current 15-minute bucket as its
    internal directional anchor. If a specific event contract uses a different
    strike/reference, that exact contract price/threshold is required for exact
    expected-value math.
    """
    d = add_indicators(df)
    latest = d.iloc[-1]
    previous = d.iloc[-2]

    price = float(latest["Close"])

    contract = current_contract_window()
    minutes_left = contract["minutes_left"]
    elapsed_minutes = contract["elapsed_minutes"]

    # Anchor MUST belong to the current hard clock contract.
    # Convert the ET contract start into the dataframe's timezone instead
    # of inferring a window from the latest candle timestamp.
    index = pd.DatetimeIndex(d.index)

    contract_start = pd.Timestamp(contract["start"])

    if index.tz is not None:
        contract_start_for_data = contract_start.tz_convert(index.tz)
    else:
        contract_start_for_data = contract_start.tz_localize(None)

    current_bucket = d.loc[d.index >= contract_start_for_data]

    if current_bucket.empty:
        # Data may lag by one candle at the exact boundary. In that case,
        # do NOT pretend the old contract belongs to the new window.
        anchor_price = price
        anchor_ready = False
    else:
        anchor_price = float(current_bucket.iloc[0]["Open"])
        anchor_ready = True

    buffer_pct = (price - anchor_price) / anchor_price

    closes = d["Close"].astype(float)
    log_returns = np.log(closes / closes.shift(1)).dropna()

    recent_20 = log_returns.tail(20)
    recent_60 = log_returns.tail(60)

    vol_20 = float(recent_20.std(ddof=1)) if len(recent_20) >= 10 else 0.0
    vol_60 = float(recent_60.std(ddof=1)) if len(recent_60) >= 10 else vol_20

    realized_vol_1m = max(
        0.00001,
        (0.70 * vol_20) + (0.30 * vol_60),
    )

    remaining_sigma_pct = (
        realized_vol_1m
        * math.sqrt(max(minutes_left, 0.20))
    )

    # Underlying distance from the anchor measured against the amount of
    # movement statistically plausible in the time remaining.
    raw_z = buffer_pct / max(remaining_sigma_pct, 0.00001)

    # Trend evidence only nudges the probability; it does not override
    # a large actual buffer already established in the contract window.
    trend_points = 0.0
    reasons = []

    if latest["ema5"] > latest["ema9"] > latest["ema21"]:
        trend_points += 2.0
        reasons.append("EMA trend bullish")
    elif latest["ema5"] < latest["ema9"] < latest["ema21"]:
        trend_points -= 2.0
        reasons.append("EMA trend bearish")

    if latest["macd_hist"] > 0:
        trend_points += 0.8
    else:
        trend_points -= 0.8

    trend_points += 0.35 if latest["macd_hist_change"] > 0 else -0.35

    for momentum, weight in [
        (latest["mom1"], 0.20),
        (latest["mom3"], 0.45),
        (latest["mom5"], 0.60),
        (latest["mom10"], 0.70),
    ]:
        if momentum > 0:
            trend_points += weight
        elif momentum < 0:
            trend_points -= weight

    rsi = float(latest["rsi"])

    if 54 <= rsi <= 72:
        trend_points += 0.35
    elif 28 <= rsi <= 46:
        trend_points -= 0.35

    recent_rate = (
        0.55 * float(latest["mom1"])
        + 0.45 * float(latest["mom3"]) / 3.0
    )
    broader_rate = (
        0.60 * float(latest["mom5"]) / 5.0
        + 0.40 * float(latest["mom10"]) / 10.0
    )
    momentum_acceleration = recent_rate - broader_rate

    if momentum_acceleration > 0:
        trend_points += 0.30
    elif momentum_acceleration < 0:
        trend_points -= 0.30

    # Modest trend adjustment, capped so probability remains mostly driven
    # by the actual lead versus remaining volatility/time.
    trend_adjustment = max(
        -0.55,
        min(0.55, trend_points / 10.0),
    )

    adjusted_z = raw_z + trend_adjustment

    def normal_cdf(z):
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    yes_probability = normal_cdf(adjusted_z)
    no_probability = 1.0 - yes_probability

    if yes_probability >= no_probability:
        direction = "YES"
        confidence = yes_probability
        directional_z = adjusted_z
    else:
        direction = "NO"
        confidence = no_probability
        directional_z = -adjusted_z

    # Confidence is an internal model estimate, not a calibrated guarantee.
    strength = max(
        50.0,
        min(97.0, confidence * 100.0),
    )

    # "Worth it" here means worth alerting from SIGNAL QUALITY alone:
    # enough elapsed information, a meaningful buffer relative to remaining
    # volatility, and high modeled finish confidence.
    #
    # Exact economic value still requires the live contract purchase price.
    worth_it = (
        anchor_ready
        and elapsed_minutes >= MIN_ENTRY_ELAPSED_MINUTES
        and minutes_left > NO_NEW_ENTRY_MINUTES
        and confidence >= MIN_RESOLUTION_CONFIDENCE
        and directional_z >= MIN_BUFFER_Z
    )

    return {
        "price": price,
        "contract_id": contract["id"],
        "contract_start": contract["start"],
        "contract_end": contract["end"],
        "anchor_ready": anchor_ready,
        "anchor_price": anchor_price,
        "buffer_pct": float(buffer_pct),
        "remaining_sigma_pct": float(remaining_sigma_pct),
        "z_score": float(directional_z),
        "confidence": float(confidence),
        "worth_it": bool(worth_it),
        "atr": float(latest["atr"]),
        "score": float(trend_points),
        "strength": round(strength, 1),
        "direction": direction,
        "rsi": rsi,
        "mom1": float(latest["mom1"]),
        "mom3": float(latest["mom3"]),
        "mom5": float(latest["mom5"]),
        "mom10": float(latest["mom10"]),
        "macd_hist": float(latest["macd_hist"]),
        "macd_hist_previous": float(previous["macd_hist"]),
        "realized_vol_1m": float(realized_vol_1m),
        "momentum_acceleration": float(momentum_acceleration),
        "reasons": reasons,
    }


# ================================================================
# CICADA EXPERIMENTAL LEDGER + RESOLUTION HEALTH
# ================================================================

LEDGER_FIELDS = [
    "timestamp",
    "trade_id",
    "event",
    "ticker",
    "direction",
    "underlying_price",
    "signal_price",
    "effective_entry_price",
    "current_move_pct",
    "peak_move_pct",
    "giveback_pct",
    "score",
    "entry_score",
    "score_change",
    "strength",
    "entry_strength",
    "strength_change",
    "rsi",
    "mom1_pct",
    "mom3_pct",
    "mom5_pct",
    "mom10_pct",
    "momentum_acceleration_pct",
    "macd_hist",
    "atr",
    "realized_vol_1m_pct",
    "minutes_to_resolution",
    "scalp_health",
    "profit_floor_pct",
    "exit_delay_buffer_pct",
    "reason",
]


def ensure_ledger():
    conn = sqlite3.connect(LEDGER_DB)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger (
            timestamp TEXT,
            trade_id TEXT,
            event TEXT,
            ticker TEXT,
            direction TEXT,
            underlying_price REAL,
            signal_price REAL,
            effective_entry_price REAL,
            current_move_pct REAL,
            peak_move_pct REAL,
            giveback_pct REAL,
            score REAL,
            entry_score REAL,
            score_change REAL,
            strength REAL,
            entry_strength REAL,
            strength_change REAL,
            rsi REAL,
            mom1_pct REAL,
            mom3_pct REAL,
            mom5_pct REAL,
            mom10_pct REAL,
            momentum_acceleration_pct REAL,
            macd_hist REAL,
            atr REAL,
            realized_vol_1m_pct REAL,
            minutes_to_resolution REAL,
            scalp_health REAL,
            profit_floor_pct REAL,
            exit_delay_buffer_pct REAL,
            reason TEXT
        )
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_trade_id ON ledger(trade_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON ledger(timestamp)"
    )
    conn.commit()
    conn.close()


def trade_move_for_state(state, signal):
    entry_price = state.get("entry_price")
    if entry_price is None:
        entry_price = state.get("signal_price")

    if entry_price in (None, 0):
        return None

    entry_price = float(entry_price)
    current_price = float(signal["price"])

    if state.get("direction") == "YES":
        return (current_price - entry_price) / entry_price

    if state.get("direction") == "NO":
        return (entry_price - current_price) / entry_price

    return None


def update_trade_peak(position, signal):
    current_move = trade_move_for_state(position, signal)
    if current_move is None:
        return

    existing_peak = float(position.get("peak_move", 0.0))
    position["peak_move"] = max(existing_peak, current_move)


def calculate_scalp_health(state, signal, minutes_left):
    """
    0-100 composite health score for the current scalp.
    Not a probability and not an event-contract return estimate.
    """
    direction = state.get("direction")
    current_move = trade_move_for_state(state, signal)

    entry_score_raw = state.get("entry_score", state.get("signal_score"))
    entry_strength_raw = state.get(
        "entry_strength",
        state.get("signal_strength"),
    )

    entry_score = abs(float(entry_score_raw or 0.0))
    entry_strength = float(entry_strength_raw or signal["strength"])
    current_score = abs(float(signal["score"]))
    current_strength = float(signal["strength"])

    peak_move = float(
        state.get(
            "peak_move",
            max(0.0, current_move or 0.0),
        )
    )
    giveback = max(0.0, peak_move - (current_move or 0.0))

    if direction == "YES":
        aligned_1m = signal["mom1"] > 0
        aligned_3m = signal["mom3"] > 0
        aligned_accel = signal["momentum_acceleration"] >= 0
        direction_intact = signal["score"] > 0
    else:
        aligned_1m = signal["mom1"] < 0
        aligned_3m = signal["mom3"] < 0
        aligned_accel = signal["momentum_acceleration"] <= 0
        direction_intact = signal["score"] < 0

    score_component = min(100.0, 50.0 + current_score * 6.0)

    score_retention = (
        1.0
        if entry_score <= 0
        else min(1.0, current_score / entry_score)
    )
    strength_retention = min(
        1.0,
        current_strength / max(entry_strength, 1e-9),
    )
    retention_component = 100.0 * (
        0.55 * score_retention
        + 0.45 * strength_retention
    )

    momentum_component = (
        (35.0 if aligned_1m else 0.0)
        + (30.0 if aligned_3m else 0.0)
        + (20.0 if aligned_accel else 0.0)
        + (15.0 if direction_intact else 0.0)
    )

    if current_move is None:
        pnl_component = 50.0
    elif current_move >= 0.0015:
        pnl_component = 100.0
    elif current_move >= 0.0005:
        pnl_component = 82.0
    elif current_move >= 0:
        pnl_component = 67.0
    elif current_move >= -0.0005:
        pnl_component = 42.0
    else:
        pnl_component = 20.0

    if peak_move <= 0:
        giveback_component = 75.0
    else:
        giveback_ratio = min(
            1.0,
            giveback / max(peak_move, 1e-9),
        )
        giveback_component = 100.0 * (1.0 - giveback_ratio)

    if minutes_left > 10:
        time_component = 100.0
    elif minutes_left > 5:
        time_component = 85.0
    elif minutes_left > FORCE_EXIT_MINUTES:
        time_component = 62.0
    else:
        time_component = 0.0

    health = (
        0.24 * score_component
        + 0.18 * retention_component
        + 0.20 * momentum_component
        + 0.16 * pnl_component
        + 0.14 * giveback_component
        + 0.08 * time_component
    )

    return max(0.0, min(100.0, health))


def ledger_event(event, state, signal, minutes_left, reason=""):
    if state is None or signal is None:
        return

    try:
        ensure_ledger()
    except Exception:
        return

    current_move = trade_move_for_state(state, signal)
    peak_move = state.get("peak_move")

    giveback = None
    if current_move is not None and peak_move is not None:
        giveback = max(
            0.0,
            float(peak_move) - current_move,
        )

    entry_score = state.get("entry_score", state.get("signal_score"))
    entry_strength = state.get(
        "entry_strength",
        state.get("signal_strength"),
    )

    floor = None
    if peak_move is not None:
        floor = calculate_profit_floor(float(peak_move))

    delay_buffer = None
    if state.get("entry_price") is not None:
        try:
            delay_buffer = adverse_exit_buffer(state, signal)
        except Exception:
            delay_buffer = None

    row = {
        "timestamp": now_et().isoformat(),
        "trade_id": state.get("trade_id", ""),
        "event": event,
        "ticker": state.get("ticker", ""),
        "direction": state.get("direction", ""),
        "underlying_price": float(signal["price"]),
        "signal_price": state.get("signal_price"),
        "effective_entry_price": state.get("entry_price"),
        "current_move_pct": (
            None if current_move is None else current_move * 100.0
        ),
        "peak_move_pct": (
            None if peak_move is None else float(peak_move) * 100.0
        ),
        "giveback_pct": (
            None if giveback is None else giveback * 100.0
        ),
        "score": float(signal["score"]),
        "entry_score": entry_score,
        "score_change": (
            None
            if entry_score is None
            else float(signal["score"]) - float(entry_score)
        ),
        "strength": float(signal["strength"]),
        "entry_strength": entry_strength,
        "strength_change": (
            None
            if entry_strength is None
            else float(signal["strength"]) - float(entry_strength)
        ),
        "rsi": float(signal["rsi"]),
        "mom1_pct": float(signal["mom1"]) * 100.0,
        "mom3_pct": float(signal["mom3"]) * 100.0,
        "mom5_pct": float(signal["mom5"]) * 100.0,
        "mom10_pct": float(signal["mom10"]) * 100.0,
        "momentum_acceleration_pct": (
            float(signal["momentum_acceleration"]) * 100.0
        ),
        "macd_hist": float(signal["macd_hist"]),
        "atr": float(signal["atr"]),
        "realized_vol_1m_pct": (
            float(signal["realized_vol_1m"]) * 100.0
        ),
        "minutes_to_resolution": float(minutes_left),
        "scalp_health": calculate_scalp_health(
            state,
            signal,
            minutes_left,
        ),
        "profit_floor_pct": (
            None if floor is None else floor * 100.0
        ),
        "exit_delay_buffer_pct": (
            None if delay_buffer is None else delay_buffer * 100.0
        ),
        "reason": reason,
    }

    try:
        conn = sqlite3.connect(LEDGER_DB, timeout=5)
        placeholders = ",".join("?" for _ in LEDGER_FIELDS)

        conn.execute(
            f"INSERT INTO ledger ({','.join(LEDGER_FIELDS)}) "
            f"VALUES ({placeholders})",
            [row[field] for field in LEDGER_FIELDS],
        )

        conn.commit()
        conn.close()
    except Exception:
        pass

    try:
        csv_exists = os.path.exists(LEDGER_CSV)

        with open(
            LEDGER_CSV,
            "a",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=LEDGER_FIELDS,
            )

            if not csv_exists:
                writer.writeheader()

            writer.writerow(row)
    except Exception:
        pass


# ================================================================
# ENTRY + POSITION MANAGEMENT
# ================================================================

def find_best_setup(signals):
    """
    Pick the coin with the highest modeled probability of still being on the
    winning side when the 15-minute window resolves.
    """
    candidates = []

    for ticker, signal in signals.items():
        if not signal.get("worth_it", False):
            continue

        if signal.get("confidence", 0.0) < MIN_RESOLUTION_CONFIDENCE:
            continue

        candidates.append((ticker, signal))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[1].get("confidence", 0.0),
            item[1].get("z_score", 0.0),
        ),
        reverse=True,
    )

    return candidates[0]



def create_position(ticker, signal):
    price = signal["price"]

    atr_distance = signal["atr"] * ATR_STOP_MULTIPLIER
    minimum_distance = price * MIN_STOP_PCT
    maximum_distance = price * MAX_STOP_PCT

    stop_distance = min(
        max(atr_distance, minimum_distance),
        maximum_distance,
    )

    target_distance = stop_distance * REWARD_RISK_RATIO

    if signal["direction"] == "YES":
        stop = price - stop_distance
        target = price + target_distance
    else:
        stop = price + stop_distance
        target = price - target_distance

    return {
        "contract_id": signal.get("contract_id", current_contract_window()["id"]),
        "contract_start": signal.get("contract_start", current_contract_window()["start"]),
        "contract_end": signal.get("contract_end", current_contract_window()["end"]),
        "ticker": ticker,
        "direction": signal["direction"],
        "entry_price": price,
        "entry_time": now_et(),
        "initial_stop": stop,
        "target": target,
        "peak_price": price,
        "peak_move": 0.0,
        "entry_score": signal["score"],
        "entry_strength": signal["strength"],
    }


def move_from_entry(position, current_price):
    entry = position["entry_price"]

    if position["direction"] == "YES":
        return (current_price - entry) / entry

    return (entry - current_price) / entry


def update_peak(position, current_price):
    if position["direction"] == "YES":
        position["peak_price"] = max(
            position["peak_price"],
            current_price,
        )
    else:
        position["peak_price"] = min(
            position["peak_price"],
            current_price,
        )

    peak_move = move_from_entry(
        position,
        position["peak_price"],
    )

    position["peak_move"] = max(
        position["peak_move"],
        peak_move,
    )


def calculate_profit_floor(peak_move):
    """
    Scalp-first profit retention based on the underlying move.

    Important: this is NOT the event-contract percentage return.
    """
    if peak_move < SCALP_ARM_TRIGGER:
        return None

    if peak_move < SCALP_FAST_LOCK_TRIGGER:
        return max(
            -0.00003,
            peak_move * (1.0 - SCALP_MAX_GIVEBACK_EARLY),
        )

    if peak_move < SCALP_STRONG_LOCK_TRIGGER:
        return max(
            0.00015,
            peak_move * (1.0 - SCALP_MAX_GIVEBACK_MID),
        )

    return max(
        0.00035,
        peak_move * (1.0 - SCALP_MAX_GIVEBACK_STRONG),
    )


def adverse_exit_buffer(position, signal):
    """Estimate how far the underlying could move against us while the user
    reacts to an EXIT alert. This is intentionally conservative."""
    adverse_mom = 0.0

    if position["direction"] == "YES":
        adverse_mom = max(0.0, -float(signal["mom1"]))
    else:
        adverse_mom = max(0.0, float(signal["mom1"]))

    delay_fraction_of_minute = ASSUMED_EXIT_DELAY_SECONDS / 60.0
    projected = adverse_mom * delay_fraction_of_minute

    return min(
        MAX_EXIT_BUFFER,
        BASE_EXIT_BUFFER + projected * 1.35,
    )


def momentum_is_weakening(position, signal):
    weakness = 0

    if position["direction"] == "YES":
        if signal["mom1"] < 0:
            weakness += 1
        if signal["mom3"] < 0:
            weakness += 1
        if signal["macd_hist"] < signal["macd_hist_previous"]:
            weakness += 1
        if signal["score"] < 1:
            weakness += 1
    else:
        if signal["mom1"] > 0:
            weakness += 1
        if signal["mom3"] > 0:
            weakness += 1
        if signal["macd_hist"] > signal["macd_hist_previous"]:
            weakness += 1
        if signal["score"] > -1:
            weakness += 1

    return weakness >= 3


def check_exit(position, signal, minutes_left):
    """
    Hold-to-resolution logic.

    The absolute rule is the contract ID:
      11:00-11:15 is OVER at 11:15.
      11:15-11:30 is a completely new contract.

    A missed polling cycle around the boundary can NEVER carry an old
    position into the new 15-minute contract.
    """
    direction = position["direction"]
    current_direction = signal["direction"]
    confidence = signal.get("confidence", 0.0)

    current_contract = current_contract_window()

    # Hard contract rollover. This is stronger than all trend logic.
    if position.get("contract_id") != current_contract["id"]:
        return (
            True,
            "CONTRACT RESOLVED — 15-minute window ended; reset for new contract",
        )

    # Final seconds of the same contract.
    if current_contract["minutes_left"] <= FORCE_EXIT_MINUTES:
        return True, "RESOLUTION IMMINENT — FINAL SECONDS"

    # Otherwise MANTIS rides the resolution unless the thesis becomes
    # overwhelmingly wrong before expiry.
    if (
        current_direction != direction
        and confidence >= EMERGENCY_OPPOSITE_CONFIDENCE
    ):
        return (
            True,
            "RESOLUTION THESIS FAILED — opposite side now overwhelmingly favored",
        )

    return False, ""



def giant_entry_alert(ticker, signal, position):
    voice_entry(ticker, signal["direction"])
    sound_entry()

    if signal["direction"] == "YES":
        art = YES_ART
        style = "bold green"
        command = "ENTER YES NOW"
    else:
        art = NO_ART
        style = "bold red"
        command = "ENTER NO NOW"

    body = Text()
    body.append("\nMANTIS RESOLUTION ENTRY\n", style="bold white")
    body.append(art, style=style)
    body.append("\n")
    body.append(command, style=f"{style} reverse")
    body.append("\n\n")
    body.append(f"{ticker}\n", style="bold white")
    body.append(f"{fmt_price(signal['price'])}\n\n", style="bold white")
    body.append(f"SIGNAL SCORE     {signal['score']:+.2f}\n")
    body.append(f"STRENGTH         {signal['strength']:.1f}%\n")
    body.append(f"INITIAL STOP     {fmt_price(position['initial_stop'])}\n")
    body.append(f"INITIAL TARGET   {fmt_price(position['target'])}\n")
    body.append(
        "\nMANTIS IS LOCKED TO THIS CONTRACT.\n"
        "THE NORMAL MARKET MONITOR WILL NOT RETURN UNTIL THIS TRADE IS CLOSED.",
        style="bold white",
    )

    flash_panel(
        body,
        " MANTIS // 15M RESOLUTION V3 — ENTRY COMMAND ",
        style,
        flashes=8,
        delay=0.22,
    )

    console.clear()
    console.print(Align.center(Text(CICADA_LOGO, style="bold white")))
    console.print(
        Panel(
            Align.center(body),
            title=" MANTIS // SIGNAL LOCKED ",
            border_style=style,
            box=box.SQUARE,
            padding=(1, 4),
        )
    )


def giant_exit_alert(position, signal, reason):
    voice_exit(position["ticker"], position["direction"])
    sound_exit()

    current_move = move_from_entry(
        position,
        signal["price"],
    )

    body = Text()
    body.append(EXIT_ART, style="bold red")
    body.append("\n\n")
    body.append(
        "EXIT POSITION NOW",
        style="bold white on red ",
    )
    body.append("\n\n")
    body.append(
        f"{position['ticker']} {position['direction']}\n",
        style="bold white",
    )
    body.append(
        f"\nREASON:\n{reason}\n",
        style="bold red",
    )
    body.append(f"\nCURRENT MOVE: {pct(current_move)}")
    body.append(f"\nBEST MOVE: {pct(position['peak_move'])}")

    flash_panel(
        body,
        " EXIT SIGNAL ",
        "bold red",
        flashes=8,
        delay=0.14,
    )

    console.print(
        Panel(
            Align.center(body),
            title=" CICADA EXIT SIGNAL ",
            border_style="bold red",
            box=box.SQUARE,
        )
    )


def giant_profit_alert(position, signal):
    voice_profit(position["ticker"], position["direction"])
    sound_profit()

    current_move = move_from_entry(
        position,
        signal["price"],
    )

    body = Text()
    body.append(PROFIT_ART, style="bold green")
    body.append("\n\n")
    body.append(
        "TAKE PROFIT — EXIT NOW",
        style="bold black on green ",
    )
    body.append("\n\n")
    body.append(
        f"{position['ticker']} {position['direction']}\n",
        style="bold white",
    )
    body.append(f"\nUNDERLYING MOVE: {pct(current_move)}")
    body.append(
        "\n\nEXIT AND LOCK PROFIT",
        style="bold green",
    )

    flash_panel(
        body,
        " CICADA PROFIT SIGNAL ",
        "bold green",
        flashes=7,
    )

    console.print(
        Panel(
            Align.center(body),
            title=" PROFIT ALERT ",
            border_style="green",
            box=box.SQUARE,
        )
    )


# ================================================================
# LOCKED-SIGNAL DISPLAY
# ================================================================

def display_locked_signal(pending, signal, minutes_left):
    console.clear()
    console.print(Align.center(Text(CICADA_LOGO, style="bold white")))

    direction = pending["direction"]
    style = "green" if direction == "YES" else "red"

    elapsed = max(
        0.0,
        (now_et() - pending["signal_time"]).total_seconds(),
    )
    remaining = max(0.0, ASSUMED_ENTRY_DELAY_SECONDS - elapsed)

    if direction == "YES":
        signal_move = (
            (signal["price"] - pending["signal_price"])
            / pending["signal_price"]
        )
    else:
        signal_move = (
            (pending["signal_price"] - signal["price"])
            / pending["signal_price"]
        )

    table = Table(
        box=box.SQUARE,
        expand=True,
        show_header=True,
        header_style="bold white",
    )
    table.add_column("METRIC")
    table.add_column("VALUE", justify="right")

    table.add_row("LOCKED SIGNAL", f"[bold {style}]{pending['ticker']} {direction}[/bold {style}]")
    table.add_row("COMMAND", f"[bold {style}]ENTER {direction} NOW[/bold {style}]")
    table.add_row("SIGNAL PRICE", fmt_price(pending["signal_price"]))
    table.add_row("CURRENT PRICE", fmt_price(signal["price"]))
    table.add_row("MOVE SINCE SIGNAL", pct(signal_move))
    table.add_row("ORIGINAL SCORE", f"{pending['signal_score']:+.2f}")
    table.add_row("CURRENT SCORE", f"{signal['score']:+.2f}")
    table.add_row("ORIGINAL STRENGTH", f"{pending['signal_strength']:.1f}%")
    table.add_row("CURRENT STRENGTH", f"{signal['strength']:.1f}%")
    table.add_row("ASSUMED ENTRY DELAY", f"{remaining:.0f} sec remaining")
    table.add_row("TIME TO RESOLUTION", f"{minutes_left:.1f} min")

    console.print(
        Panel(
            table,
            title=" MANTIS // 15M RESOLUTION V3 — SIGNAL LOCKED ",
            subtitle=" CICADA IS TRACKING THIS SIGNAL ONLY ",
            border_style=style,
            box=box.SQUARE,
        )
    )

    console.print(
        Panel(
            Align.center(
                Text(
                    f"ENTER {direction} NOW\n\n"
                    "CICADA WILL EITHER ACTIVATE THIS TRADE OR CANCEL THE ENTRY.\n"
                    "IT WILL NOT RESUME GENERAL MARKET SCANNING WHILE THIS SIGNAL IS LOCKED.",
                    style=f"bold {style}",
                )
            ),
            border_style=style,
            box=box.SQUARE,
        )
    )


def display_entry_cancelled(pending, reason):
    voice_cancel(pending["ticker"], pending["direction"])
    sound_warning()
    console.clear()
    console.print(Align.center(Text(CICADA_LOGO, style="bold white")))

    body = Text()
    body.append("\nCANCEL ENTRY\n", style="bold white on red")
    body.append(f"\n{pending['ticker']} {pending['direction']}\n", style="bold white")
    body.append(f"\n{reason}\n", style="bold red")
    body.append("\nDO NOT ENTER / DO NOT ADD TO THIS POSITION.", style="bold white")

    flash_panel(
        body,
        " CICADA // ENTRY CANCELLED ",
        "bold red",
        flashes=6,
        delay=0.22,
    )


# ================================================================
# DASHBOARD
# ================================================================

def display_dashboard(signals, minutes_left, position):
    console.clear()

    console.print(
        Align.center(
            Text(CICADA_LOGO, style="bold blue")
        )
    )

    console.print(
        Align.center(
            Text(
                "15-MINUTE HOLD-TO-RESOLUTION CONFIDENCE SYSTEM",
                style="bold white",
            )
        )
    )
    console.print()

    if minutes_left <= FORCE_EXIT_MINUTES:
        time_style = "bold white on red"
        contract_status = " FINAL RESOLUTION WINDOW"
    elif minutes_left <= NO_NEW_ENTRY_MINUTES:
        time_style = "bold yellow"
        contract_status = " ENTRY DISABLED"
    else:
        time_style = "bold green"
        contract_status = "ENTRY ELIGIBLE"

    console.print(
        Panel(
            Align.center(
                Text(
                    (
                        f"{now_et().strftime('%I:%M:%S %p ET')}\n"
                        f"CONTRACT: "
                        f"{current_contract_window()['start'].strftime('%I:%M')} "
                        f"→ {current_contract_window()['end'].strftime('%I:%M %p ET')}\n"
                        f"{minutes_left:.1f} MINUTES TO RESOLUTION\n"
                        f"{contract_status}"
                    ),
                    style=time_style,
                )
            ),
            border_style="blue",
            box=box.SQUARE,
        )
    )

    table = Table(
        title=" MARKET MONITOR ",
        box=box.SQUARE,
        expand=True,
    )

    table.add_column("RANK", justify="center")
    table.add_column("COIN", justify="center")
    table.add_column("PRICE", justify="right")
    table.add_column("BIAS", justify="center")
    table.add_column("CONF", justify="right")
    table.add_column("BUFFER", justify="right")
    table.add_column("VALUE?", justify="center")
    table.add_column("STRENGTH", justify="left")

    ranked = sorted(
        signals.items(),
        key=lambda item: abs(item[1]["score"]),
        reverse=True,
    )

    for rank, (ticker, signal) in enumerate(ranked, start=1):
        if signal["direction"] == "YES":
            bias = "[bold green] YES[/bold green]"
        elif signal["direction"] == "NO":
            bias = "[bold red] NO[/bold red]"
        else:
            bias = "[yellow]WAIT[/yellow]"

        table.add_row(
            f"#{rank}",
            ticker,
            fmt_price(signal["price"]),
            bias,
            f"{signal.get('confidence', 0.0) * 100:.1f}%",
            pct(signal.get("buffer_pct", 0.0)),
            "[green]YES[/green]" if signal.get("worth_it", False) else "[yellow]NO[/yellow]",
            (
                f"{strength_bar(signal['strength'])} "
                f"{signal['strength']:.1f}%"
            ),
        )

    console.print(table)

    feed_table = Table(
        title="DATA FEED",
        box=box.SIMPLE,
        expand=True,
    )
    feed_table.add_column("COIN")
    feed_table.add_column("STATUS")

    for ticker in TICKERS:
        status_text = data_status.get(ticker, "UNKNOWN")

        if status_text.startswith("LIVE"):
            style = "green"
        elif status_text.startswith(("CACHED", "WARMING")):
            style = "yellow"
        else:
            style = "red"

        feed_table.add_row(
            ticker,
            f"[{style}]{status_text}[/{style}]",
        )

    console.print(feed_table)

    if position is None:
        console.print(
            Panel(
                Align.center(
                    Text(
                        "MONITORING MARKETS\n\nNo qualified setup at this time.",
                        style="bold yellow",
                    )
                ),
                title=" MANTIS STATUS",
                border_style="yellow",
                box=box.SQUARE,
            )
        )
        return

    ticker = position["ticker"]
    signal = signals.get(ticker)

    if signal is None:
        console.print(
            Panel(
                (
                    f"{ticker} currently has no fresh signal data.\n"
                    "CICADA will not invent an exit from missing data."
                ),
                title=" DATA WARNING",
                border_style="yellow",
                box=box.SQUARE,
            )
        )
        return

    move = move_from_entry(position, signal["price"])
    current_move = move

    update_trade_peak(position, signal)
    peak = position["peak_move"]

    scalp_health = calculate_scalp_health(
        position,
        signal,
        minutes_left,
    )
    floor = calculate_profit_floor(peak)

    if move > 0:
        move_style = "bold green"
        state = " PROFIT"
    elif move < 0:
        move_style = "bold red"
        state = " LOSS"
    else:
        move_style = "bold yellow"
        state = " FLAT"

    position_table = Table(
        box=box.SQUARE,
        expand=True,
    )
    position_table.add_column("METRIC")
    position_table.add_column("VALUE", justify="right")

    position_table.add_row(
        "ACTIVE POSITION",
        f"[bold]{ticker} {position['direction']}[/bold]",
    )
    position_table.add_row(
        "STATE",
        f"[{move_style}]{state}[/{move_style}]",
    )
    position_table.add_row(
        "ENTRY",
        fmt_price(position["entry_price"]),
    )
    position_table.add_row(
        "CURRENT",
        fmt_price(signal["price"]),
    )
    position_table.add_row(
        "CURRENT MOVE",
        f"[{move_style}]{pct(move)}[/{move_style}]",
    )
    position_table.add_row(
        "MOVE METER",
        f"[{move_style}]{move_bar(move)}[/{move_style}]",
    )
    position_table.add_row(
        "BEST MOVE",
        f"[green]{pct(peak)}[/green]",
    )

    floor_text = (
        "[yellow]NOT ARMED[/yellow]"
        if floor is None
        else f"[green]{pct(floor)}[/green]"
    )

    position_table.add_row("RESOLUTION MODE", floor_text)
    position_table.add_row(
        "STOP",
        f"[red]{fmt_price(position['initial_stop'])}[/red]",
    )
    position_table.add_row(
        "TARGET",
        f"[green]{fmt_price(position['target'])}[/green]",
    )
    position_table.add_row(
        "SIGNAL SCORE",
        f"{signal['score']:+.2f}",
    )
    position_table.add_row(
        "STRENGTH",
        (
            f"{strength_bar(signal['strength'])} "
            f"{signal['strength']:.1f}%"
        ),
    )
    position_table.add_row("ENTRY SCORE", f"{position['entry_score']:+.2f}")
    position_table.add_row("SCORE CHANGE", f"{signal['score'] - position['entry_score']:+.2f}")
    position_table.add_row("RSI", f"{signal['rsi']:.1f}")
    position_table.add_row("1M MOMENTUM", pct(signal["mom1"]))
    position_table.add_row("3M MOMENTUM", pct(signal["mom3"]))
    position_table.add_row("5M MOMENTUM", pct(signal["mom5"]))
    position_table.add_row("10M MOMENTUM", pct(signal["mom10"]))
    position_table.add_row("MACD HIST", f"{signal['macd_hist']:+.6f}")
    position_table.add_row("ATR", fmt_price(signal["atr"]))
    position_table.add_row("TIME TO RESOLUTION", f"{minutes_left:.1f} min")
    position_table.add_row("EXIT DELAY BUFFER", pct(adverse_exit_buffer(position, signal)))
    position_table.add_row("RESOLUTION HEALTH", f"{scalp_health:.1f} / 100")
    position_table.add_row(
        "MOMENTUM ACCEL",
        pct(signal["momentum_acceleration"]),
    )
    position_table.add_row(
        "REALIZED VOL (1M)",
        pct(signal["realized_vol_1m"]),
    )
    position_table.add_row(
        "PEAK GIVEBACK",
        pct(max(0.0, position["peak_move"] - current_move)),
    )
    active_floor = calculate_profit_floor(position["peak_move"])
    position_table.add_row(
        "SCALP FLOOR",
        "NOT ARMED" if active_floor is None else pct(active_floor),
    )

    console.print(
        Panel(
            position_table,
            title=" MANTIS // 15M RESOLUTION V3 — ACTIVE TRADE ",
            border_style="white",
            box=box.SQUARE,
        )
    )


# ================================================================
# STARTUP
# ================================================================

def startup():
    ensure_ledger()
    console.clear()

    console.print(
        Align.center(
            Text(CICADA_LOGO, style="bold white")
        )
    )

    console.print(
        Align.center(
            Text(
                "INITIALIZING MANTIS // 15M RESOLUTION V3",
                style="bold white",
            )
        )
    )
    console.print()

    for system in [
        "MARKET DATA LINK",
        "MOMENTUM ENGINE",
        "VOLATILITY ENGINE",
        "RISK MATRIX",
        "PROFIT PROTECTION",
        "15M TIMER",
        "ALERT SYSTEM",
    ]:
        console.print(
            f"[blue]INITIALIZING[/blue] {system} ",
            end="",
        )
        time.sleep(0.12)
        console.print("[bold green] ONLINE[/bold green]")

    sound_entry()
    console.print()

    console.print(
        Panel(
            Align.center(
                Text(
                    "MANTIS // 15M RESOLUTION V3 ONLINE\n\nLOADING MARKET HISTORY...",
                    style="bold green",
                )
            ),
            border_style="green",
            box=box.SQUARE,
        )
    )
    time.sleep(0.8)



# ================================================================
# MANUAL EXECUTION DELAY MODEL
# ================================================================

def begin_pending_entry(ticker, signal):
    return {
        "trade_id": uuid.uuid4().hex[:12].upper(),
        "contract_id": signal.get("contract_id", current_contract_window()["id"]),
        "contract_start": signal.get("contract_start", current_contract_window()["start"]),
        "contract_end": signal.get("contract_end", current_contract_window()["end"]),
        "ticker": ticker,
        "direction": signal["direction"],
        "signal_time": now_et(),
        "signal_price": signal["price"],
        "signal_score": signal["score"],
        "signal_strength": signal["strength"],
    }


def pending_entry_status(pending, signal):
    """Wait for the assumed manual fill unless the resolution thesis truly dies."""
    elapsed = max(
        0.0,
        (now_et() - pending["signal_time"]).total_seconds(),
    )

    direction = pending["direction"]
    current_direction = signal["direction"]
    confidence = signal.get("confidence", 0.0)

    current_contract_id = current_contract_window()["id"]

    if pending.get("contract_id") != current_contract_id:
        return (
            False,
            True,
            "ENTRY CANCELLED — original 15-minute contract has already expired",
        )

    if (
        current_direction != direction
        and confidence >= 0.70
    ):
        return False, True, "ENTRY CANCELLED — resolution probability flipped"

    if confidence < 0.68:
        return False, True, "ENTRY CANCELLED — finish confidence collapsed"

    if elapsed >= ASSUMED_ENTRY_DELAY_SECONDS:
        return True, False, "ASSUMED MANUAL FILL COMPLETE — HOLD FOR RESOLUTION"

    return False, False, f"RESOLUTION SIGNAL LOCKED — {elapsed:.0f}/{ASSUMED_ENTRY_DELAY_SECONDS}s"



def activate_filled_position(pending, signal):
    position = create_position(pending["ticker"], signal)

    # Preserve the original signal for measuring slippage/conviction decay,
    # but use the later underlying price as the effective fill reference.
    position["trade_id"] = pending["trade_id"]
    position["signal_time"] = pending["signal_time"]
    position["signal_price"] = pending["signal_price"]
    position["entry_time"] = now_et()
    position["entry_price"] = signal["price"]
    position["entry_score"] = pending["signal_score"]
    position["entry_strength"] = pending["signal_strength"]

    # Rebuild stop and target around the effective delayed entry reference.
    price = signal["price"]
    atr_distance = signal["atr"] * ATR_STOP_MULTIPLIER
    stop_pct = atr_distance / price
    stop_pct = max(MIN_STOP_PCT, min(MAX_STOP_PCT, stop_pct))
    risk_distance = price * stop_pct

    if position["direction"] == "YES":
        position["initial_stop"] = price - risk_distance
        position["target"] = price + (risk_distance * REWARD_RISK_RATIO)
    else:
        position["initial_stop"] = price + risk_distance
        position["target"] = price - (risk_distance * REWARD_RISK_RATIO)

    position["peak_price"] = price
    position["peak_move"] = 0.0

    return position


# ================================================================
# MAIN PROGRAM
# ================================================================

def main():
    global active_position
    global pending_entry
    global last_exit_time

    startup()

    last_contract_id = current_contract_window()["id"]

    while True:
        contract = current_contract_window()
        current_minutes = contract["minutes_left"]

        # Absolute 15-minute rollover protection.
        # If the clock crossed :00/:15/:30/:45, the prior contract is finished.
        if contract["id"] != last_contract_id:
            if pending_entry is not None:
                pending_entry = None

            if active_position is not None:
                # The contract has resolved. Do not carry an old position or
                # old trend into the new interval.
                active_position = None
                last_exit_time = time.time()

            last_contract_id = contract["id"]

            console.clear()
            console.print(Align.center(Text(CICADA_LOGO, style="bold white")))
            console.print(
                Panel(
                    Align.center(
                        Text(
                            (
                                "NEW 15-MINUTE CONTRACT\n\n"
                                f"{contract['start'].strftime('%I:%M %p')} "
                                f"→ {contract['end'].strftime('%I:%M %p')} ET\n\n"
                                "OLD CONTRACT STATE CLEARED\n"
                                "BUILDING FRESH RESOLUTION MODEL"
                            ),
                            style="bold cyan",
                        )
                    ),
                    title=" MANTIS // CONTRACT RESET ",
                    border_style="cyan",
                    box=box.SQUARE,
                )
            )

            time.sleep(1.5)

        signals = {}

        for ticker in TICKERS:
            df = fetch_market_data(ticker)
            if df is None:
                continue
            try:
                signals[ticker] = analyze_market(df)
            except Exception as exc:
                data_status[ticker] = f"ANALYSIS ERROR - {type(exc).__name__}"

        if not signals:
            console.clear()
            console.print(Align.center(Text(CICADA_LOGO, style="bold white")))
            status_table = Table(title="MARKET DATA STATUS", box=box.SQUARE, expand=True)
            status_table.add_column("COIN")
            status_table.add_column("STATUS")
            for ticker in TICKERS:
                status_table.add_row(ticker, data_status.get(ticker, "UNKNOWN"))
            console.print(status_table)
            console.print(
                Panel(
                    "MANTIS does not currently have enough fresh market data.\n"
                    "It will retry automatically.\n\n"
                    "No signal will be generated from incomplete or stale data.",
                    title=" MANTIS // DATA HOLD ",
                    border_style="yellow",
                    box=box.SQUARE,
                )
            )
            time.sleep(POLL_SECONDS)
            continue

        # STATE 1: SIGNAL LOCKED / MANUAL ENTRY EXECUTION
        if pending_entry is not None:
            ticker = pending_entry["ticker"]
            signal = signals.get(ticker)

            if signal is None:
                console.clear()
                console.print(Align.center(Text(CICADA_LOGO, style="bold white")))
                console.print(
                    Panel(
                        f"MANTIS remains locked to {ticker} {pending_entry['direction']}.\n\n"
                        "Fresh data for the locked market is temporarily unavailable.\n"
                        "MANTIS will not silently abandon the signal.",
                        title=" MANTIS // SIGNAL LOCKED — DATA HOLD ",
                        border_style="yellow",
                        box=box.SQUARE,
                    )
                )
                time.sleep(POLL_SECONDS)
                continue

            display_locked_signal(pending_entry, signal, current_minutes)
            ready, cancel, reason = pending_entry_status(pending_entry, signal)

            if cancel:
                cancelled = pending_entry
                display_entry_cancelled(cancelled, reason)
                log_event(
                    "ENTRY_CANCELLED",
                    cancelled["ticker"],
                    cancelled["direction"],
                    signal["price"],
                    signal["score"],
                    signal["strength"],
                    reason,
                )
                ledger_event(
                    "ENTRY_CANCELLED",
                    cancelled,
                    signal,
                    current_minutes,
                    reason,
                )
                pending_entry = None
                last_exit_time = time.time()
                time.sleep(4)
                continue

            if ready:
                active_position = activate_filled_position(pending_entry, signal)
                log_event(
                    "ASSUMED_FILL",
                    active_position["ticker"],
                    active_position["direction"],
                    active_position["entry_price"],
                    signal["score"],
                    signal["strength"],
                    reason,
                )
                ledger_event(
                    "ASSUMED_FILL",
                    active_position,
                    signal,
                    current_minutes,
                    reason,
                )
                pending_entry = None
                display_dashboard(signals, current_minutes, active_position)
                time.sleep(1)
                continue

            time.sleep(POLL_SECONDS)
            continue

        # STATE 2: ACTIVE TRADE — never return to the idle scanner here.
        if active_position is not None:
            ticker = active_position["ticker"]
            signal = signals.get(ticker)

            display_dashboard(signals, current_minutes, active_position)

            if signal is None:
                time.sleep(POLL_SECONDS)
                continue

            update_trade_peak(active_position, signal)
            exit_now, reason = check_exit(
                active_position,
                signal,
                current_minutes,
            )

            if exit_now:
                if "TAKE PROFIT" in reason:
                    giant_profit_alert(active_position, signal)
                else:
                    giant_exit_alert(active_position, signal, reason)

                log_event(
                    "EXIT",
                    ticker,
                    active_position["direction"],
                    signal["price"],
                    signal["score"],
                    signal["strength"],
                    reason,
                )
                ledger_event(
                    "EXIT",
                    active_position,
                    signal,
                    current_minutes,
                    reason,
                )

                # Keep the action command visible for manual execution.
                time.sleep(10)
                active_position = None
                last_exit_time = time.time()
                continue

            time.sleep(POLL_SECONDS)
            continue

        # STATE 3: IDLE / GENERAL MARKET MONITOR
        if current_minutes <= NO_NEW_ENTRY_MINUTES:
            display_dashboard(signals, current_minutes, None)
            time.sleep(POLL_SECONDS)
            continue

        if last_exit_time is not None and time.time() - last_exit_time < COOLDOWN_SECONDS:
            display_dashboard(signals, current_minutes, None)
            time.sleep(POLL_SECONDS)
            continue

        # Qualify BEFORE drawing the idle screen.
        best = find_best_setup(signals)

        if best is None:
            display_dashboard(signals, current_minutes, None)
            time.sleep(POLL_SECONDS)
            continue

        # IDLE -> LOCKED SIGNAL
        ticker, signal = best
        preview_position = create_position(ticker, signal)
        pending_entry = begin_pending_entry(ticker, signal)

        giant_entry_alert(ticker, signal, preview_position)

        reason_text = "\n".join(
            f"- {reason}" for reason in signal["reasons"][:7]
        ) or "No additional reason text available."

        console.print(
            Panel(
                reason_text,
                title=" CICADA // SIGNAL BASIS ",
                border_style="white",
                box=box.SQUARE,
            )
        )

        log_event(
            "SIGNAL",
            ticker,
            signal["direction"],
            signal["price"],
            signal["score"],
            signal["strength"],
            "; ".join(signal["reasons"]),
        )
        ledger_event(
            "SIGNAL",
            pending_entry,
            signal,
            current_minutes,
            "; ".join(signal["reasons"]),
        )

        # Next cycle remains locked to this signal; it never returns to idle.
        time.sleep(5)


# ================================================================
# LAUNCH
# ================================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        console.clear()
        console.print(
            Panel(
                Align.center(
                    Text(
                        "CICADA OFFLINE\n\nTRACKER TERMINATED",
                        style="bold yellow",
                    )
                ),
                border_style="yellow",
                box=box.SQUARE,
            )
        )

    except Exception as exc:
        console.print()
        console.print(
            Panel(
                (
                    f"{type(exc).__name__}: {exc}\n\n"
                    "MANTIS stopped safely. No order was placed."
                ),
                title="MANTIS SYSTEM ERROR",
                border_style="red",
                box=box.SQUARE,
            )
        )
