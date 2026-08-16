from __future__ import annotations

import tempfile
import threading
import tracemalloc
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mantis_v4.kalshi_forward.core import ShadowStore
from mantis_v4.manual_signal.core import (ASSETS, MAX_TOTAL_ENTRY_COST,
    MIN_CONSERVATIVE_EDGE, POLICY_VERSION, apply_signal_lock, select_primary_signal)
from mantis_v4.ui import PresentationConfig
from mantis_v4.ui.voice import VoiceEngine

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/"mantis_v4"/"ui"/"web"
NOW=datetime(2026,8,16,13,0,tzinfo=UTC)


def row(asset, window, edge=.20, eligible=True, side="YES"):
    return {"asset":asset,"side":side,"confidence":.97,"conservative_probability":.94,
        "fragility":10.,"disagreement":.01,"crossing_probability":.1,"crossings":1,
        "seconds_remaining":850.,"contract_id":f"{asset}|{window.isoformat()}|15m",
        "window_start_utc":(window-timedelta(minutes=15)).isoformat(),"window_end_utc":window.isoformat(),
        "market_ticker":"KX"+asset[:3],"target":"100","proxy_current":"101",
        "ask":"0.70","fee":"0.02","total_cost":"0.72","net_ev":"0.25",
        "conservative_net_ev":"0.22","model_edge":str(edge+.03),"conservative_edge":str(edge),
        "status":"ELIGIBLE" if eligible else "CONF FAIL","reason":"OK" if eligible else "WEAK",
        "economically_valid":eligible,"reference_risk":"REFERENCE_ROBUST",
        "policy_version":POLICY_VERSION}


class ManualV2PolicyTests(unittest.TestCase):
    def test_policy_and_guards_are_explicit(self):
        self.assertEqual(POLICY_VERSION,"EXPERIMENTAL_MANUAL_SIGNAL_V2")
        self.assertEqual(str(MAX_TOTAL_ENTRY_COST),"0.95")
        self.assertEqual(str(MIN_CONSERVATIVE_EDGE),"0.02")

    def test_two_thousand_window_lock_soak_is_bounded(self):
        tracemalloc.start(); start=tracemalloc.get_traced_memory()[0]
        issued=set(); lock=None
        for index in range(2000):
            window=NOW+timedelta(minutes=15*(index+1)); lock=None
            selection=select_primary_signal([row(asset,window,edge=.20-index%3*.01) for asset in ASSETS])
            locked,lock=apply_signal_lock(selection,lock,window-timedelta(minutes=14))
            identity=(window.isoformat(),lock["asset"],lock["side"]); issued.add(identity)
            stronger=[row(asset,window,edge=.40,side="NO") for asset in reversed(ASSETS)]
            later,still=apply_signal_lock(select_primary_signal(stronger),lock,window-timedelta(minutes=5))
            self.assertEqual((still["asset"],still["side"]),(lock["asset"],lock["side"]))
            self.assertEqual(later["operator_state"]["headline"],"LOCKED PRIMARY SIGNAL")
        current,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
        self.assertEqual(len(issued),2000)
        self.assertLess(current-start,2_000_000)
        self.assertLess(peak-start,8_000_000)

    def test_shadow_dedupe_memory_can_be_recently_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ShadowStore(Path(tmp),recent_id_limit=64)
            for index in range(200):
                store.append("runs",{"run_record_id":f"run-{index}"})
            self.assertLessEqual(len(store._ids["runs"]),64)
            self.assertEqual(len(store.read("runs",limit=32)),32)

    def test_frontend_histories_and_listeners_are_bounded_and_singleton(self):
        modules=(WEB/"modules.js").read_text(encoding="utf-8")
        app=(WEB/"app.js").read_text(encoding="utf-8")
        self.assertIn("var MAX_POINTS = 120",modules)
        self.assertIn("history[k].splice",modules)
        self.assertEqual(app.count("new EventSource("),1)
        self.assertEqual(app.count('document.addEventListener("DOMContentLoaded", start)'),1)

    def test_voice_reuses_one_worker_and_bounds_history(self):
        cfg=PresentationConfig(voice_enabled=True)
        baseline=threading.active_count(); voice=VoiceEngine(cfg,speaker=lambda _text:None)
        try:
            for index in range(100):voice.say(f"signal {index}")
            voice.drain(); self.assertLessEqual(len(voice.spoken),32)
            self.assertLessEqual(threading.active_count(),baseline+1)
        finally:voice.stop()

