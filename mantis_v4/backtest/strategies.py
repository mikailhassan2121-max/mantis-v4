
"""
MANTIS V4 — baseline historical strategies (Phase 3 requirement 11).

Phase 3 measures BASELINES ONLY. No model is trained and no threshold is tuned;
every constant below is either V3's shipped value or a value the master prompt
names explicitly. Tuning belongs to Phase 5+ and must happen on walk-forward
folds, never on the numbers this phase produces.

The four required baselines:

  A  SpotVsReferenceStrategy   always take the side implied by spot vs the
                               proxy reference. Coverage 100%, no abstention.
  B  V3ResolutionStrategy      V3's shipped resolution logic, reproduced gate
                               for gate against V3-faithful features.
  C  NormalZBaselineStrategy   section 26D: p = Phi(buffer / sigma_remaining),
                               no trend term, no drift assumption.
  D  GeminiEdgeStrategy        section 26H: edge > 20pp inside the final 5
                               minutes. REQUIRES a contract ask price.

D IS NOT EVALUABLE AND SAYS SO.

Its trigger is defined on ``model_edge = p_model - p_implied``, and
``p_implied`` comes from an executable contract ask. No historical Webull
event-contract quotes exist in this dataset (Phase 2 audit UNKNOWN-4/5 are
still open). Phase 3 requirement 11 is explicit: report it as NOT EVALUABLE
rather than approximating it.

The class therefore refuses to run rather than substituting a synthetic ask.
Substituting one would produce an edge number that looks like a measurement and
is actually an artefact of the number we invented.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Action(Enum):
    WAIT = "WAIT"
    ENTER_YES = "ENTER_YES"
    ENTER_NO = "ENTER_NO"
    NO_TRADE = "NO_TRADE"

    @property
    def is_entry(self) -> bool:
        return self in (Action.ENTER_YES, Action.ENTER_NO)

    @property
    def side(self) -> Optional[str]:
        if self is Action.ENTER_YES:
            return "YES"
        if self is Action.ENTER_NO:
            return "NO"
        return None


@dataclass(frozen=True)
class Decision:
    """One strategy's verdict at one scan instant.

    ``p_yes`` is the strategy's probability that the contract settles YES, when
    the strategy produces one at all. A strategy with no probabilistic output
    leaves it None, and the metrics layer then reports accuracy but NOT Brier
    or log loss (Phase 3 requirement 12).
    """

    action: Action
    p_yes: Optional[float] = None
    reason: str = ""

    @property
    def p_for_side(self) -> Optional[float]:
        """Probability assigned to the side actually taken."""
        if self.p_yes is None or not self.action.is_entry:
            return None
        return self.p_yes if self.action is Action.ENTER_YES else 1.0 - self.p_yes


class StrategyNotEvaluable(RuntimeError):
    """Raised when a strategy's required inputs do not exist in this dataset."""


class BacktestStrategy(ABC):
    """A decision rule evaluated at each historical scan."""

    name: str = "strategy"
    description: str = ""
    produces_probability: bool = False

    #: Set False when the strategy's required inputs are absent from the
    #: dataset. The runner then reports NOT EVALUABLE instead of results.
    evaluable: bool = True
    not_evaluable_reason: str = ""

    @abstractmethod
    def decide(self, features: dict[str, Any]) -> Decision:
        """Return a decision from a leakage-safe feature snapshot."""

    def reset(self) -> None:
        """Called at the start of every contract. No state may cross windows."""


# ---------------------------------------------------------------------------
# A. spot vs reference
# ---------------------------------------------------------------------------

class SpotVsReferenceStrategy(BacktestStrategy):
    """Take whichever side the current buffer implies. Never abstains.

    The floor baseline: it measures how much of any apparent skill is simply
    "price is above the open, so bet it stays above the open". A more elaborate
    model that cannot beat this has demonstrated nothing.
    """

    name = "A_spot_vs_reference"
    description = "Always take the side implied by spot vs the proxy reference"
    produces_probability = False

    def __init__(self, min_elapsed_seconds: float = 150.0) -> None:
        self.min_elapsed_seconds = min_elapsed_seconds

    def decide(self, features: dict[str, Any]) -> Decision:
        if not features.get("reference_available"):
            return Decision(Action.WAIT, reason="reference not yet available")
        if features["elapsed_seconds"] < self.min_elapsed_seconds:
            return Decision(Action.WAIT, reason="waiting for min elapsed")

        buffer_pct = features.get("buffer_pct")
        if buffer_pct is None:
            return Decision(Action.WAIT, reason="no buffer")
        if buffer_pct == 0:
            return Decision(Action.NO_TRADE, reason="spot exactly at reference")

        action = Action.ENTER_YES if buffer_pct > 0 else Action.ENTER_NO
        return Decision(action, reason=f"buffer {buffer_pct * 100:+.3f}%")


# ---------------------------------------------------------------------------
# B. V3
# ---------------------------------------------------------------------------

class V3ResolutionStrategy(BacktestStrategy):
    """V3's shipped entry logic, gate for gate.

    From ``analyze_market``/``find_best_setup``, V3 required ALL of:
        anchor_ready
        elapsed_minutes    >= MIN_ENTRY_ELAPSED_MINUTES     (2.5)
        minutes_left       >  NO_NEW_ENTRY_MINUTES          (0.75)
        confidence         >= MIN_RESOLUTION_CONFIDENCE     (0.80)
        directional_z      >= MIN_BUFFER_Z                  (0.55)

    Audit finding B-3 proved the last gate is DEAD: ``directional_z`` is always
    ``|adjusted_z|`` and ``confidence = Phi(|adjusted_z|)``, so confidence >=
    0.80 already implies |z| >= 0.8416 > 0.55. It is retained here anyway,
    exactly as V3 has it, because this class is a reproduction and not a repair.
    ``min_buffer_z_ever_binding`` records whether it ever binds, which
    re-verifies the audit finding on real data.

    The probability it emits is V3's ``confidence``. Master prompt section 6
    requires this be called MODEL SCORE, not confidence, until calibration is
    demonstrated. Brier and log loss are still computed for it — they are proper
    scoring rules for any [0,1] output — but a good score here would be evidence
    FOR calibration, not an assumption of it.
    """

    name = "B_v3_resolution"
    description = "V3 shipped resolution logic (faithful reproduction)"
    produces_probability = True

    def __init__(
        self,
        min_confidence: float = 0.80,
        min_elapsed_seconds: float = 150.0,
        no_new_entry_seconds: float = 45.0,
        min_buffer_z: float = 0.55,
    ) -> None:
        self.min_confidence = min_confidence
        self.min_elapsed_seconds = min_elapsed_seconds
        self.no_new_entry_seconds = no_new_entry_seconds
        self.min_buffer_z = min_buffer_z
        self.min_buffer_z_ever_binding = 0

    def decide(self, features: dict[str, Any]) -> Decision:
        if not features.get("reference_available"):
            return Decision(Action.WAIT, reason="anchor not ready")

        adjusted_z = features.get("v3_adjusted_z")
        p_yes = features.get("v3_p_yes")
        if adjusted_z is None or p_yes is None:
            return Decision(Action.WAIT, reason="v3 features unavailable")

        p_no = 1.0 - p_yes
        if p_yes >= p_no:
            direction, confidence, directional_z = Action.ENTER_YES, p_yes, adjusted_z
        else:
            direction, confidence, directional_z = Action.ENTER_NO, p_no, -adjusted_z

        if features["elapsed_seconds"] < self.min_elapsed_seconds:
            return Decision(Action.WAIT, p_yes, "elapsed < 2.5 min")
        if features["seconds_remaining"] <= self.no_new_entry_seconds:
            return Decision(Action.NO_TRADE, p_yes, "inside no-new-entry window")
        if confidence < self.min_confidence:
            return Decision(Action.WAIT, p_yes, f"confidence {confidence:.3f} < {self.min_confidence}")

        if directional_z < self.min_buffer_z:
            # Audit B-3 says this is unreachable. Count it if it ever fires.
            self.min_buffer_z_ever_binding += 1
            return Decision(Action.WAIT, p_yes, "buffer z below floor")

        return Decision(direction, p_yes, f"v3 confidence {confidence:.3f}")


# ---------------------------------------------------------------------------
# C. normal-Z baseline
# ---------------------------------------------------------------------------

class NormalZBaselineStrategy(BacktestStrategy):
    """Master prompt section 26D, honestly named.

        Z = buffer / sigma_remaining
        p_yes_normal_baseline = Phi(Z)

    This is the transparent terminal-probability benchmark: no trend term, no
    drift assumption, no tuned constants. Section 26D is explicit that it is a
    BASELINE and must never be labelled "true mathematical probability".

    It assumes Gaussian terminal returns, which audit finding B-4 flags as
    unvalidated in a fat-tailed market — the error direction being
    overconfidence at exactly the high-|z| values that trigger entries. Phase 3
    measures that; it does not correct it.
    """

    name = "C_normal_z_baseline"
    description = "Phi(buffer / sigma_remaining), no trend adjustment"
    produces_probability = True

    def __init__(
        self,
        min_confidence: float = 0.80,
        min_elapsed_seconds: float = 150.0,
        no_new_entry_seconds: float = 45.0,
    ) -> None:
        self.min_confidence = min_confidence
        self.min_elapsed_seconds = min_elapsed_seconds
        self.no_new_entry_seconds = no_new_entry_seconds

    def decide(self, features: dict[str, Any]) -> Decision:
        if not features.get("reference_available"):
            return Decision(Action.WAIT, reason="reference not yet available")

        p_yes = features.get("p_yes_normal_baseline")
        if p_yes is None or p_yes != p_yes:   # None or NaN
            return Decision(Action.WAIT, reason="sigma not estimable")

        if features["elapsed_seconds"] < self.min_elapsed_seconds:
            return Decision(Action.WAIT, p_yes, "waiting for min elapsed")
        if features["seconds_remaining"] <= self.no_new_entry_seconds:
            return Decision(Action.NO_TRADE, p_yes, "inside no-new-entry window")

        confidence = max(p_yes, 1.0 - p_yes)
        if confidence < self.min_confidence:
            return Decision(Action.WAIT, p_yes, f"confidence {confidence:.3f} below threshold")

        action = Action.ENTER_YES if p_yes >= 0.5 else Action.ENTER_NO
        return Decision(action, p_yes, f"normal baseline p={p_yes:.3f}")


# ---------------------------------------------------------------------------
# D. Gemini 20pp / under-5-minute rule
# ---------------------------------------------------------------------------

class GeminiEdgeStrategy(BacktestStrategy):
    """Master prompt section 26H. NOT EVALUABLE on this dataset.

    The rule is:
        seconds_remaining < 300
        model_edge = p_model - p_implied > 0.20
        quote fresh, reference valid, data-quality gate passes

    ``p_implied`` requires an executable historical contract ask. This dataset
    has none, and fabricating one is forbidden by Phase 3 requirements 2 and 3.

    Note that the temporal half of the rule (< 300 seconds) IS measurable, and
    the entry-time bucket analysis in the metrics layer reports accuracy inside
    the final five minutes. What cannot be measured is the ECONOMIC half — the
    20-percentage-point edge — which is the half that determines whether the
    rule is profitable rather than merely accurate.
    """

    name = "D_gemini_edge_20pp_under_5m"
    description = "Section 26H: edge > 20pp inside the final 5 minutes"
    produces_probability = True
    evaluable = False
    not_evaluable_reason = (
        "requires an executable historical event-contract ask price to compute "
        "p_implied and model_edge. No historical Webull quotes exist in this "
        "dataset (audit UNKNOWN-4/5 open). Fabricating an ask is forbidden by "
        "Phase 3 requirements 2 and 3."
    )

    name_short = "D"

    def decide(self, features: dict[str, Any]) -> Decision:
        raise StrategyNotEvaluable(self.not_evaluable_reason)


def default_strategies() -> list[BacktestStrategy]:
    """The Phase 3 baseline set, in the order the master prompt lists them."""
    return [
        SpotVsReferenceStrategy(),
        V3ResolutionStrategy(),
        NormalZBaselineStrategy(),
        GeminiEdgeStrategy(),
    ]
