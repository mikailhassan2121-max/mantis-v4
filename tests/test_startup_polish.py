"""Post-Phase-10 presentation-only startup lifecycle regressions."""
from __future__ import annotations

import tempfile
import unittest
import hashlib
from pathlib import Path

from PIL import Image

from mantis_v4.forward.events import AppEvent, EventType
from mantis_v4.ui import CommandCenterState, PresentationConfig
from mantis_v4.ui.alerts import AlertRouter
from mantis_v4.ui.webmodel import presentation_payload
from mantis_v4.ui.webserver import CommandCenterServer

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "mantis_v4" / "ui" / "web"


class FakeAudio:
    def __init__(self): self.played = []
    def play(self, cue): self.played.append(cue)


class FakeVoice:
    def __init__(self): self.spoken = []
    def say(self, text): self.spoken.append(text); return True


class StartupTimingTests(unittest.TestCase):
    def test_default_cinematic_timing_is_in_requested_window(self):
        cfg = PresentationConfig()
        total = (cfg.brand_prelude_duration_seconds +
                 cfg.technical_boot_duration_seconds + cfg.startup_settle_seconds)
        self.assertEqual(cfg.brand_prelude_duration_seconds, 13.0)
        self.assertEqual(cfg.technical_boot_duration_seconds, 8.5)
        self.assertGreaterEqual(total, 19.0)
        self.assertLessEqual(total, 23.0)

    def test_timing_is_configurable_and_serialized(self):
        cfg = PresentationConfig(brand_prelude_duration_seconds=11.0,
                                 technical_boot_duration_seconds=9.0,
                                 startup_settle_seconds=0.5,
                                 startup_snapshot_timeout_seconds=6.0)
        payload = presentation_payload(cfg)
        self.assertEqual(payload["brand_prelude_duration_seconds"], 11.0)
        self.assertEqual(payload["technical_boot_duration_seconds"], 9.0)
        self.assertEqual(payload["startup_settle_seconds"], 0.5)

    def test_no_startup_bypasses_both_sequences(self):
        class Args: no_startup = True
        cfg = PresentationConfig().apply_cli(Args())
        self.assertFalse(cfg.boot_sequence_enabled)
        self.assertFalse(cfg.brand_prelude_enabled)
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("p.boot_enabled === false ? 0", app)


class BootLockTests(unittest.TestCase):
    def test_logo_order_then_technical_boot_then_ready(self):
        boot = (WEB / "boot.js").read_text(encoding="utf-8")
        brand_order = ["saaf_holdings_group.png", "saaf_ventures.png", "mantis_darpa.png"]
        self.assertEqual([boot.index(token) for token in brand_order],
                         sorted(boot.index(token) for token in brand_order))
        run = boot[boot.index("async function run(options)"):]
        run_order = ["brandPrelude(screen", 'setPhase(screen, "boot")', "assemble(scale, opts)"]
        self.assertEqual([run.index(token) for token in run_order],
                         sorted(run.index(token) for token in run_order))
        assemble = boot[boot.index("async function assemble"):boot.index("async function run(options)")]
        lifecycle = ['setPhase(screen, "hydrating")', 'setPhase(screen, "settling")',
                     'setPhase(document.body, "ready")']
        self.assertEqual([assemble.index(token) for token in lifecycle],
                         sorted(assemble.index(token) for token in lifecycle))

    def test_sse_is_cached_while_shell_is_boot_locked(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn("if (!state.ready) {", app)
        self.assertLess(app.index("connect(); // cache every SSE frame"),
                        app.index("global.MantisBoot.run"))
        self.assertIn("body:not(.operational-ready) #shell", css)
        self.assertIn("sseSnapshotsDuringBoot++", app)
        self.assertIn("boot lock: cache and clock sync only", app)
        self.assertIn('draw("hydration")', app)
        self.assertEqual(app.count("global.requestAnimationFrame(tick)"), 2)
        self.assertIn("if (!state.frameLoopStarted)", app)

    def test_fullscreen_is_requested_at_most_once_and_focus_is_not_stolen(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertLessEqual(app.count("requestFullscreen()"), 1)
        self.assertNotIn(".focus()", app)
        self.assertIn("waitForViewportStable", app)
        self.assertIn('global.setTimeout(finish, 180)', app)

    def test_latest_complete_snapshot_timeout_and_backend_clock_are_explicit(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        for token in ("structurallyComplete", "waitForSnapshot", "truthfulHold",
                      "AWAITING LIVE MARKET STATE", "server_time_utc", "serverOffsetMs"):
            self.assertIn(token, app)

    def test_old_logo_forging_effects_are_absent_and_missing_asset_has_fallback(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        boot = (WEB / "boot.js").read_text(encoding="utf-8")
        for token in ("brand-reticle", "brand-scan", "brand-crosshair", "brand_echo"):
            self.assertNotIn(token, html + css + boot)
        self.assertIn('candidate.onerror = function', boot)
        self.assertNotIn('image.onerror = function', boot)
        self.assertIn("brand.available", boot)
        self.assertIn("fallback", css)

    def test_all_images_preload_and_decode_before_brand_one(self):
        boot = (WEB / "boot.js").read_text(encoding="utf-8")
        run = boot[boot.index("async function run(options)"):]
        self.assertLess(run.index("await preloadBranding()"), run.index("await brandPrelude("))
        self.assertIn("candidate.decode", boot)
        self.assertIn("Promise.all(jobs)", boot)

    def test_operational_dom_is_frozen_until_single_hydration_render(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("domRendersDuringBoot: 0", app)
        self.assertIn("hydrationRenders: 0", app)
        self.assertEqual(app.count('draw("hydration")'), 1)
        self.assertIn("prehydrateDom=", app)

    def test_manual_hydration_requires_authoritative_completed_scan(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('operator.mode === "KALSHI_MANUAL_SIGNAL"', app)
        self.assertIn('operator.policy === "EXPERIMENTAL_MANUAL_SIGNAL_V1"', app)
        self.assertIn("operator.first_scan_complete === true", app)
        self.assertIn("bypass && !manual", app)

    def test_snapshot_sequence_is_monotonic_and_sse_recovers(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("lastSequence: -1", app)
        self.assertIn("sequence <= state.lastSequence", app)
        self.assertIn('apply(parsed, "sse")', app)
        self.assertIn("MANTIS_SSE_EVENT_ERROR", app)
        self.assertIn('fetch("/snapshot.json", { cache: "no-store"', app)

    def test_manual_render_uses_backend_headline_and_rows(self):
        modules = (WEB / "modules.js").read_text(encoding="utf-8")
        self.assertIn('operator.headline || "STANDBY"', modules)
        self.assertIn("operator.candidate_rankings || []", modules)
        self.assertIn('data-rendered-mode', modules)
        self.assertNotIn("!operator.primary_selection ? \"STANDBY\"", modules)

    def test_manual_center_is_exclusive_and_does_not_require_primary(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        modules = (WEB / "modules.js").read_text(encoding="utf-8")
        self.assertIn('if (operator.mode === "KALSHI_MANUAL_SIGNAL")', modules)
        self.assertIn("renderManualSignalCenter(snap, ticked);", modules)
        self.assertIn("return;", modules[modules.index('if (operator.mode === "KALSHI_MANUAL_SIGNAL")'):])
        self.assertIn("selected || strongest || {}", modules)
        self.assertIn('centerWrite(snap, "decision_label", selected ? "PRIMARY SIGNAL — " + selected.side : headline)', modules)
        self.assertIn('centerWrite(snap, "contract_window", market)', modules)
        self.assertIn('centerWrite(snap, "contract_countdown", F.countdown(displayedSeconds))', modules)
        self.assertIn("operator.candidate_rankings || []", modules)
        self.assertIn("if (manualCenter) M.renderManualSignalCenter", app)
        self.assertIn('view === "contract" && !manualCenter', app)

    def test_manual_center_exposes_exact_dom_truth(self):
        modules = (WEB / "modules.js").read_text(encoding="utf-8")
        for token in ("document.documentElement.dataset.renderedMode",
                      "document.documentElement.dataset.renderedHeadline",
                      "document.documentElement.dataset.renderedReason",
                      "document.documentElement.dataset.renderedSequence",
                      "document.documentElement.dataset.centerRenderSource"):
            self.assertIn(token, modules)
        self.assertIn('source: "MANUAL_SIGNAL"', modules)
        self.assertIn('candidate_rows: (operator.candidate_rankings || []).length', modules)

    def test_frontend_build_mismatch_is_visible(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("FRONTEND VERSION MISMATCH", app)
        self.assertIn("__MANTIS_BUILD_ID__", html)


class ProcessedBrandAssetTests(unittest.TestCase):
    ORIGINAL_HASHES = {
        "mantis_darpa.png": "c2ffff693489fefff68a002d818925210fb3f173a444fa7bf2d59c833f389b45",
        "saaf_holdings_group.png": "8c07ff6720c42a85e54b169893dc447c83e517c887a72f4872b79b0dc21f2f50",
        "saaf_ventures.png": "d40d9fa041e05721038691b56388041964a48f93883deebe36b2df62a23b042b",
    }

    def test_original_brand_files_are_byte_identical(self):
        for name, expected in self.ORIGINAL_HASHES.items():
            source = ROOT / "assests" / "branding" / name
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), expected)

    def test_processed_assets_have_real_alpha_and_no_canvas_rectangle(self):
        directory = ROOT / "assets" / "branding" / "processed"
        for name in self.ORIGINAL_HASHES:
            image = Image.open(directory / name)
            self.assertEqual(image.mode, "RGBA")
            alpha = image.getchannel("A")
            self.assertEqual(alpha.getextrema(), (0, 255))
            corners = ((0, 0), (image.width - 1, 0),
                       (0, image.height - 1), (image.width - 1, image.height - 1))
            self.assertEqual([alpha.getpixel(point) for point in corners], [0, 0, 0, 0])
            self.assertLess(alpha.getbbox()[0], image.width - 1)

    def test_splash_is_one_edge_to_edge_black_field_without_inner_panel(self):
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn("width:100vw;height:100vh;background:#000", css)
        self.assertIn("background:transparent;border:0;outline:0;box-shadow:none", css)
        self.assertNotIn("mix-blend-mode:screen", css)


class ReadyAndAlertTests(unittest.TestCase):
    def test_ready_acknowledgement_and_callback_are_once_only(self):
        cfg = PresentationConfig()
        server = CommandCenterServer(CommandCenterState(["BTC-USD"], cfg), cfg)
        calls = []
        server.on_presentation_ready = lambda: calls.append("ready")
        server.mark_presentation_ready(); server.mark_presentation_ready()
        self.assertTrue(server.presentation_ready.is_set())
        self.assertEqual(calls, ["ready"])

    def test_missing_brand_asset_logs_once_and_does_not_raise(self):
        cfg = PresentationConfig()
        state = CommandCenterState(["BTC-USD"], cfg)
        server = CommandCenterServer(state, cfg)
        self.assertIsNone(server.read_branding("missing-user-logo.png"))
        self.assertIsNone(server.read_branding("missing-user-logo.png"))
        warnings = [event for event in state.snapshot().events
                    if event.message == "BRANDING ASSET MISSING"]
        self.assertEqual(len(warnings), 1)

    def test_operational_alerts_arm_at_ready_and_online_is_spoken_once(self):
        cfg = PresentationConfig(startup_alert_suppression_seconds=60.0)
        audio, voice = FakeAudio(), FakeVoice()
        router = AlertRouter(CommandCenterState(["BTC-USD"], cfg), audio, voice, cfg)
        event = AppEvent(EventType.ENTRY_YES, "now", {"asset": "BTC-USD", "side": "YES"})
        router.handle(event)
        self.assertEqual(audio.played, []); self.assertEqual(voice.spoken, [])
        router.arm(); router.arm()
        self.assertEqual(voice.spoken, ["MANTIS online."])
        router.handle(event)
        self.assertEqual(audio.played, ["enter_yes"])
        self.assertEqual(voice.spoken, ["MANTIS online."])

    def test_demo_feed_waits_for_ready_then_delays(self):
        runner = (ROOT / "mantis_v4_live.py").read_text(encoding="utf-8")
        ready = runner.index("server.presentation_ready.wait")
        delay = runner.index("ui_config.demo_ready_delay_seconds", ready)
        loop = runner.index("while True:", delay)
        self.assertLess(ready, delay); self.assertLess(delay, loop)


if __name__ == "__main__":
    unittest.main()
