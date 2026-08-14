"""Functional Phase 8 console renderer; Phase 9 can replace it wholesale."""
from .models import LiveSnapshot
def render_snapshot(s:LiveSnapshot)->str:
 def v(x,fmt=".3f"): return "N/A" if x is None else format(x,fmt)
 return (f"{s.asset} | {s.window_start[11:16]}->{s.window_end[11:16]} UTC | T-{int(s.seconds_remaining):03d}s\n"
  f"REFERENCE {s.reference:.6g} [{s.reference_status}]  CURRENT {s.current_price:.6g}\n"
  f"P(YES) {s.p_yes:.1%}  P(NO) {s.p_no:.1%}  SIDE {s.predicted_side}  LCB {s.conservative_bound:.1%}\n"
  f"FRAG {s.fragility:.1f}  DISAGREE {s.disagreement:.3f}  CROSS {s.crossing_probability:.1%} ({s.reference_crossings})\n"
  f"CLASSIFICATION {s.phase6_state}: {s.phase6_reason}\n"
  f"YES ASK {v(s.yes_ask)}  NO ASK {v(s.no_ask)}  BREAK EVEN {v(s.break_even)}  EDGE {v(s.model_edge)}  EV {s.ev_status}\n"
  f"DECISION {s.final_decision}: {s.final_reason}")
