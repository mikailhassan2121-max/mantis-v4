"""
MANTIS V4 — contract and prediction recording.

THIS MODULE EXISTS BECAUSE OF AUDIT FINDINGS A-2 AND C-1.

V3's rollover handler did exactly this when a position was open:

    if active_position is not None:
        active_position = None
        last_exit_time = time.time()

No log. No ledger row. No alert. The normal, expected termination of every
hold-to-resolution trade -- the contract expiring -- wrote nothing. The
consequence was not merely cosmetic: V3's ledger had 31 feature columns and no
outcome column, so it contained features with no labels. No Brier score, no log
loss, no calibration curve, no accuracy could ever be computed from it, and
Phase 3 could not have started from it.

The standing instruction for V4 is absolute:

    Do not allow any version of MANTIS to resolve a contract without logging
    its outcome.

That is enforced structurally here, not by discipline:

  * a contract row is written the FIRST time a window is observed, before any
    decision is made, with status OPEN;
  * every scan updates it and appends an immutable prediction row -- including
    NO TRADE decisions (master prompt section 17);
  * ``resolve_due_contracts`` runs every scan AND at startup, so a crash, a
    restart, or a missed boundary cannot orphan a contract;
  * a contract that genuinely cannot be settled becomes UNRESOLVED_NO_DATA with
    a stated reason. It is never deleted and never silently dropped.

Storage is SQLite (durable, queryable, WAL) mirrored to CSV (portable, easy to
hand to a notebook). Section 20 forbids deleting losing trades from logs; the
schema has no delete path at all.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .clock import UTC, Instant
from .contracts import ContractSpec, ContractStatus, ContractWindow

SCHEMA_VERSION = 1

CONTRACT_COLUMNS = [
    "key", "contract_id", "asset", "status",
    "window_start_utc", "window_end_utc", "window_start_local", "window_end_local",
    "timezone", "window_minutes",
    "reference_price", "reference_source", "reference_verified",
    "settlement_rule", "settlement_verified",
    "contract_provider", "venue_contract_id", "economics_available",
    "first_seen_utc", "last_seen_utc", "scan_count",
    "open_spot", "last_spot", "high_spot", "low_spot",
    "entered", "entry_time_utc", "entry_side", "entry_spot", "entry_reason",
    "decision", "decision_reason",
    "decision_p_yes", "decision_model_score", "decision_features_json",
    "terminal_price", "terminal_source", "terminal_bar_utc",
    "outcome_yes", "outcome_label", "prediction_correct",
    "resolved_at_utc", "resolution_attempts", "unresolved_reason",
    "model_version", "schema_version",
]

PREDICTION_COLUMNS = [
    "key", "contract_id", "asset", "scan_utc",
    "seconds_remaining", "elapsed_fraction",
    "spot", "reference_price", "reference_source",
    "buffer_abs", "buffer_pct",
    "p_yes", "p_no", "model_score", "lower_confidence_bound",
    "market_implied_yes", "yes_bid", "yes_ask", "no_bid", "no_ask",
    "quote_age_seconds", "spread",
    "ev_yes", "ev_no", "model_edge",
    "model_disagreement", "fragility", "regime",
    "decision", "decision_reason",
    "data_quality_passed", "data_quality_detail",
    "features_json", "model_version",
]


def _iso(moment: Optional[datetime]) -> Optional[str]:
    return None if moment is None else moment.astimezone(UTC).isoformat()


@dataclass
class PredictionRecord:
    """One scan's full decision snapshot for one asset.

    Every model field is Optional and defaults to None. Phase 2 has no model,
    so these are written as NULL -- honestly absent rather than zero-filled.
    Phases 4-7 populate them without a schema change.
    """

    key: str
    contract_id: str
    asset: str
    scan_utc: datetime
    seconds_remaining: Optional[float] = None
    elapsed_fraction: Optional[float] = None
    spot: Optional[float] = None
    reference_price: Optional[float] = None
    reference_source: Optional[str] = None
    buffer_abs: Optional[float] = None
    buffer_pct: Optional[float] = None
    p_yes: Optional[float] = None
    p_no: Optional[float] = None
    model_score: Optional[float] = None
    lower_confidence_bound: Optional[float] = None
    market_implied_yes: Optional[float] = None
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    quote_age_seconds: Optional[float] = None
    spread: Optional[float] = None
    ev_yes: Optional[float] = None
    ev_no: Optional[float] = None
    model_edge: Optional[float] = None
    model_disagreement: Optional[float] = None
    fragility: Optional[float] = None
    regime: Optional[str] = None
    decision: str = "NO_TRADE"
    decision_reason: str = ""
    data_quality_passed: bool = False
    data_quality_detail: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    model_version: str = "v4-phase2"

    def as_row(self) -> dict:
        return {
            "key": self.key,
            "contract_id": self.contract_id,
            "asset": self.asset,
            "scan_utc": _iso(self.scan_utc),
            "seconds_remaining": self.seconds_remaining,
            "elapsed_fraction": self.elapsed_fraction,
            "spot": self.spot,
            "reference_price": self.reference_price,
            "reference_source": self.reference_source,
            "buffer_abs": self.buffer_abs,
            "buffer_pct": self.buffer_pct,
            "p_yes": self.p_yes,
            "p_no": self.p_no,
            "model_score": self.model_score,
            "lower_confidence_bound": self.lower_confidence_bound,
            "market_implied_yes": self.market_implied_yes,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "no_bid": self.no_bid,
            "no_ask": self.no_ask,
            "quote_age_seconds": self.quote_age_seconds,
            "spread": self.spread,
            "ev_yes": self.ev_yes,
            "ev_no": self.ev_no,
            "model_edge": self.model_edge,
            "model_disagreement": self.model_disagreement,
            "fragility": self.fragility,
            "regime": self.regime,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "data_quality_passed": int(bool(self.data_quality_passed)),
            "data_quality_detail": self.data_quality_detail,
            "features_json": json.dumps(self.features, default=str, sort_keys=True),
            "model_version": self.model_version,
        }


class ContractRecorder:
    """Durable store for contracts, predictions and outcomes."""

    def __init__(
        self,
        db_path: Path,
        *,
        contracts_csv: Optional[Path] = None,
        predictions_csv: Optional[Path] = None,
        model_version: str = "v4-phase2",
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.contracts_csv = Path(contracts_csv) if contracts_csv else None
        self.predictions_csv = Path(predictions_csv) if predictions_csv else None
        self.model_version = model_version
        self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._conn.close()

    def __enter__(self) -> "ContractRecorder":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        cur = self._conn
        # WAL: a crash mid-write cannot corrupt earlier records, which matters
        # for a process that is expected to be killed with Ctrl-C routinely.
        try:
            cur.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        cur.execute("PRAGMA synchronous=NORMAL")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                key TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                status TEXT NOT NULL,
                window_start_utc TEXT NOT NULL,
                window_end_utc TEXT NOT NULL,
                window_start_local TEXT,
                window_end_local TEXT,
                timezone TEXT,
                window_minutes INTEGER,
                reference_price REAL,
                reference_source TEXT,
                reference_verified INTEGER,
                settlement_rule TEXT,
                settlement_verified INTEGER,
                contract_provider TEXT,
                venue_contract_id TEXT,
                economics_available INTEGER,
                first_seen_utc TEXT,
                last_seen_utc TEXT,
                scan_count INTEGER DEFAULT 0,
                open_spot REAL,
                last_spot REAL,
                high_spot REAL,
                low_spot REAL,
                entered INTEGER DEFAULT 0,
                entry_time_utc TEXT,
                entry_side TEXT,
                entry_spot REAL,
                entry_reason TEXT,
                decision TEXT,
                decision_reason TEXT,
                decision_p_yes REAL,
                decision_model_score REAL,
                decision_features_json TEXT,
                terminal_price REAL,
                terminal_source TEXT,
                terminal_bar_utc TEXT,
                outcome_yes INTEGER,
                outcome_label TEXT,
                prediction_correct INTEGER,
                resolved_at_utc TEXT,
                resolution_attempts INTEGER DEFAULT 0,
                unresolved_reason TEXT,
                model_version TEXT,
                schema_version INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                scan_utc TEXT NOT NULL,
                seconds_remaining REAL,
                elapsed_fraction REAL,
                spot REAL,
                reference_price REAL,
                reference_source TEXT,
                buffer_abs REAL,
                buffer_pct REAL,
                p_yes REAL,
                p_no REAL,
                model_score REAL,
                lower_confidence_bound REAL,
                market_implied_yes REAL,
                yes_bid REAL,
                yes_ask REAL,
                no_bid REAL,
                no_ask REAL,
                quote_age_seconds REAL,
                spread REAL,
                ev_yes REAL,
                ev_no REAL,
                model_edge REAL,
                model_disagreement REAL,
                fragility REAL,
                regime TEXT,
                decision TEXT,
                decision_reason TEXT,
                data_quality_passed INTEGER,
                data_quality_detail TEXT,
                features_json TEXT,
                model_version TEXT
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status)",
            "CREATE INDEX IF NOT EXISTS idx_contracts_end ON contracts(window_end_utc)",
            "CREATE INDEX IF NOT EXISTS idx_contracts_asset ON contracts(asset)",
            "CREATE INDEX IF NOT EXISTS idx_predictions_key ON predictions(key)",
            "CREATE INDEX IF NOT EXISTS idx_predictions_scan ON predictions(scan_utc)",
        ):
            cur.execute(statement)
        self._conn.commit()

    # ------------------------------------------------------------------
    # CSV mirroring
    # ------------------------------------------------------------------

    @staticmethod
    def _append_csv(path: Optional[Path], columns: list[str], row: dict) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            exists = path.exists()
            with path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                if not exists:
                    writer.writeheader()
                writer.writerow(row)
        except OSError:
            # Deliberately non-fatal: losing the CSV mirror must not stop
            # trading research, because SQLite remains the source of truth.
            # Unlike V3's bare `except: pass` on the PRIMARY store, this only
            # guards the secondary copy.
            pass

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def observe_contract(
        self,
        spec: ContractSpec,
        instant: Instant,
        *,
        spot: Optional[float] = None,
        economics_available: bool = False,
    ) -> str:
        """Create-or-update the contract row. Called on EVERY scan.

        The row exists from the first observation onward, so a contract can
        never expire without a record to resolve.
        """
        window = spec.window
        key = spec.key
        now_iso = _iso(instant.utc)

        row = self._conn.execute(
            "SELECT key, open_spot, high_spot, low_spot, scan_count FROM contracts WHERE key = ?",
            (key,),
        ).fetchone()

        if row is None:
            self._conn.execute(
                """
                INSERT INTO contracts (
                    key, contract_id, asset, status,
                    window_start_utc, window_end_utc,
                    window_start_local, window_end_local, timezone, window_minutes,
                    reference_price, reference_source, reference_verified,
                    settlement_rule, settlement_verified,
                    contract_provider, venue_contract_id, economics_available,
                    first_seen_utc, last_seen_utc, scan_count,
                    open_spot, last_spot, high_spot, low_spot,
                    entered, decision, model_version, schema_version
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    key, window.contract_id, spec.underlying, ContractStatus.OPEN.value,
                    _iso(window.start_utc), _iso(window.end_utc),
                    window.start_local.isoformat(), window.end_local.isoformat(),
                    window.tz_name, int(window.total_seconds // 60),
                    spec.reference_price, spec.reference_source.value,
                    int(spec.reference_source.is_verified),
                    spec.settlement_rule.value, int(spec.settlement_rule.is_verified),
                    spec.provider_name, spec.venue_contract_id,
                    int(bool(economics_available)),
                    now_iso, now_iso, 1,
                    spot, spot, spot, spot,
                    0, "NO_TRADE", self.model_version, SCHEMA_VERSION,
                ),
            )
        else:
            high = row["high_spot"]
            low = row["low_spot"]
            if spot is not None:
                high = spot if high is None else max(float(high), spot)
                low = spot if low is None else min(float(low), spot)
            self._conn.execute(
                """
                UPDATE contracts SET
                    last_seen_utc = ?, scan_count = scan_count + 1,
                    last_spot = COALESCE(?, last_spot),
                    high_spot = ?, low_spot = ?,
                    reference_price = COALESCE(?, reference_price),
                    reference_source = ?, reference_verified = ?,
                    settlement_rule = ?, settlement_verified = ?,
                    contract_provider = ?, venue_contract_id = COALESCE(?, venue_contract_id),
                    economics_available = ?
                WHERE key = ?
                """,
                (
                    now_iso, spot, high, low,
                    spec.reference_price, spec.reference_source.value,
                    int(spec.reference_source.is_verified),
                    spec.settlement_rule.value, int(spec.settlement_rule.is_verified),
                    spec.provider_name, spec.venue_contract_id,
                    int(bool(economics_available)),
                    key,
                ),
            )
        self._conn.commit()
        return key

    def record_prediction(self, record: PredictionRecord) -> None:
        """Append one immutable prediction row -- including NO TRADE.

        Master prompt section 17 requires storing every prediction, including
        abstentions. Without the abstentions, abstention rate and the
        selective-prediction coverage curves of section 26O cannot be computed.
        """
        row = record.as_row()
        placeholders = ",".join("?" for _ in PREDICTION_COLUMNS)
        self._conn.execute(
            f"INSERT INTO predictions ({','.join(PREDICTION_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [row[column] for column in PREDICTION_COLUMNS],
        )
        # Mirror the latest decision onto the contract so a resolved row is
        # self-contained for research without a join.
        self._conn.execute(
            """
            UPDATE contracts SET
                decision = ?, decision_reason = ?,
                decision_p_yes = COALESCE(?, decision_p_yes),
                decision_model_score = COALESCE(?, decision_model_score),
                decision_features_json = ?
            WHERE key = ?
            """,
            (
                record.decision, record.decision_reason,
                record.p_yes, record.model_score,
                row["features_json"], record.key,
            ),
        )
        self._conn.commit()
        self._append_csv(self.predictions_csv, PREDICTION_COLUMNS, row)

    def record_entry(
        self,
        key: str,
        instant: Instant,
        side: str,
        spot: Optional[float],
        reason: str = "",
    ) -> None:
        """Mark that MANTIS advised an entry, with its exact timestamp."""
        self._conn.execute(
            """
            UPDATE contracts SET
                entered = 1, entry_time_utc = ?, entry_side = ?,
                entry_spot = ?, entry_reason = ?
            WHERE key = ?
            """,
            (_iso(instant.utc), side, spot, reason, key),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Resolution -- the guarantee
    # ------------------------------------------------------------------

    def open_contracts(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM contracts WHERE status = ? ORDER BY window_end_utc",
                (ContractStatus.OPEN.value,),
            ).fetchall()
        )

    def due_contracts(self, instant: Instant, grace_seconds: float) -> list[sqlite3.Row]:
        """OPEN contracts whose window has ended and whose grace has elapsed.

        The grace period exists because the terminal 1-minute bar has not
        necessarily been published at the exact instant the window closes.
        """
        cutoff = instant.utc - timedelta(seconds=grace_seconds)
        return list(
            self._conn.execute(
                "SELECT * FROM contracts WHERE status = ? AND window_end_utc <= ? "
                "ORDER BY window_end_utc",
                (ContractStatus.OPEN.value, _iso(cutoff)),
            ).fetchall()
        )

    def mark_resolved(
        self,
        key: str,
        instant: Instant,
        *,
        terminal_price: float,
        terminal_source: str,
        terminal_bar_utc: Optional[datetime],
        outcome_yes: bool,
    ) -> dict:
        """Write the outcome. This is the row that turns features into data."""
        contract = self._conn.execute(
            "SELECT * FROM contracts WHERE key = ?", (key,)
        ).fetchone()
        if contract is None:
            raise KeyError(f"unknown contract {key}")

        outcome_label = "YES" if outcome_yes else "NO"

        # Correctness is only defined when MANTIS actually took a side.
        # A NO TRADE is neither correct nor incorrect; recording it as a loss
        # would corrupt every accuracy statistic downstream.
        predicted_side = contract["entry_side"]
        prediction_correct: Optional[int] = None
        if predicted_side in ("YES", "NO"):
            prediction_correct = int(predicted_side == outcome_label)

        self._conn.execute(
            """
            UPDATE contracts SET
                status = ?, terminal_price = ?, terminal_source = ?,
                terminal_bar_utc = ?, outcome_yes = ?, outcome_label = ?,
                prediction_correct = ?, resolved_at_utc = ?,
                unresolved_reason = NULL
            WHERE key = ?
            """,
            (
                ContractStatus.RESOLVED.value, terminal_price, terminal_source,
                _iso(terminal_bar_utc), int(outcome_yes), outcome_label,
                prediction_correct, _iso(instant.utc), key,
            ),
        )
        self._conn.commit()

        resolved = dict(
            self._conn.execute("SELECT * FROM contracts WHERE key = ?", (key,)).fetchone()
        )
        self._append_csv(self.contracts_csv, CONTRACT_COLUMNS, resolved)
        return resolved

    def bump_resolution_attempt(self, key: str, reason: str) -> int:
        self._conn.execute(
            "UPDATE contracts SET resolution_attempts = resolution_attempts + 1, "
            "unresolved_reason = ? WHERE key = ?",
            (reason, key),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT resolution_attempts FROM contracts WHERE key = ?", (key,)
        ).fetchone()
        return int(row["resolution_attempts"]) if row else 0

    def mark_unresolvable(self, key: str, instant: Instant, reason: str) -> dict:
        """Terminal state for a contract that genuinely cannot be settled.

        This is a first-class, permanently recorded outcome -- not a deletion.
        Phase 3 must be able to count how often MANTIS could not determine a
        result, because a silently missing contract would bias the dataset.
        """
        self._conn.execute(
            """
            UPDATE contracts SET
                status = ?, resolved_at_utc = ?, unresolved_reason = ?
            WHERE key = ?
            """,
            (ContractStatus.UNRESOLVED_NO_DATA.value, _iso(instant.utc), reason, key),
        )
        self._conn.commit()
        row = dict(
            self._conn.execute("SELECT * FROM contracts WHERE key = ?", (key,)).fetchone()
        )
        self._append_csv(self.contracts_csv, CONTRACT_COLUMNS, row)
        return row

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def counts_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM contracts GROUP BY status"
        ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def summary(self) -> dict:
        """Headline dataset counts. Deliberately NOT an accuracy claim.

        Accuracy/calibration reporting belongs to Phase 3 onward and requires
        confidence intervals; this only says how much data exists.
        """
        counts = self.counts_by_status()
        resolved = int(
            self._conn.execute(
                "SELECT COUNT(*) AS n FROM contracts WHERE status = ?",
                (ContractStatus.RESOLVED.value,),
            ).fetchone()["n"]
        )
        traded = int(
            self._conn.execute(
                "SELECT COUNT(*) AS n FROM contracts WHERE entered = 1"
            ).fetchone()["n"]
        )
        predictions = int(
            self._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
        )
        return {
            "contracts_total": sum(counts.values()),
            "contracts_open": counts.get(ContractStatus.OPEN.value, 0),
            "contracts_resolved": resolved,
            "contracts_unresolved": counts.get(ContractStatus.UNRESOLVED_NO_DATA.value, 0),
            "contracts_entered": traded,
            "prediction_rows": predictions,
        }

    def get_contract(self, key: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM contracts WHERE key = ?", (key,)
        ).fetchone()

    def predictions_for(self, key: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM predictions WHERE key = ? ORDER BY scan_utc", (key,)
            ).fetchall()
        )
