/* Command center controller.
 *
 * Receives snapshots over server-sent events and draws them. The connection is
 * strictly one-way: this shell has no route by which it could ask the backend
 * to do anything, which is the property that keeps a browser incapable of
 * influencing a scan.
 *
 * Two clocks run here, and only one of them is authoritative:
 *   - the backend's scan cadence delivers new snapshots;
 *   - a local animation frame interpolates the countdown against the
 *     window_end timestamp the backend published, so the seconds tick smoothly
 *     without the UI ever inventing a contract boundary.
 */

(function (global) {
    "use strict";

    var M = global.MantisModules;
    var A = global.MantisAudio;
    var F = global.F;

    var state = {
        snapshot: null,
        ready: false,
        connected: false,
        serverOffsetMs: 0,
        lastDecisions: Object.create(null),
        lastContract: Object.create(null)
    };

    /* --------------------------------------------------------------- views */

    function bindTabs() {
        document.querySelectorAll("#tabs li").forEach(function (tab) {
            tab.addEventListener("click", function () {
                document.querySelectorAll("#tabs li").forEach(function (t) {
                    t.classList.remove("active");
                });
                tab.classList.add("active");
                var target = tab.getAttribute("data-view");
                document.querySelectorAll(".view").forEach(function (v) {
                    v.classList.toggle("active", v.id === "view_" + target);
                });
                A.play("init_pulse");
                draw();
            });
        });

        // Number keys switch tabs; the interface is otherwise non-interactive.
        global.addEventListener("keydown", function (e) {
            var index = parseInt(e.key, 10);
            if (index >= 1 && index <= 4) {
                var tabs = document.querySelectorAll("#tabs li");
                if (tabs[index - 1]) tabs[index - 1].click();
            }
        });
    }

    function activeView() {
        var el = document.querySelector(".view.active");
        return el ? el.id.replace("view_", "") : "contract";
    }

    /* ----------------------------------------------------- alert routing */

    /* Visual state changes are echoed as sound, with the same anti-fatigue rule
     * the Rich front end uses: WAIT is silent, and every cue is throttled. */
    function routeAlerts(snapshot) {
        (snapshot.assets || []).forEach(function (v) {
            var previous = state.lastDecisions[v.asset];
            var key = v.final.key;
            if (previous !== undefined && previous !== key) {
                if (key === "enter_yes") A.play("enter_yes", 5);
                else if (key === "enter_no") A.play("enter_no", 5);
                else if (key === "data_hold") A.play("data_hold", 15);
                else if (key === "no_trade") A.play("warning", 15);
                // WAIT is deliberately silent: it is the most common state.
            }
            state.lastDecisions[v.asset] = key;

            var contract = state.lastContract[v.asset];
            if (contract !== undefined && contract !== v.contract_id && v.contract_id) {
                A.play("rollover", 10);
            }
            state.lastContract[v.asset] = v.contract_id;
        });

        if (snapshot.error) A.play("critical", 20);
    }

    function routeEvent(newest) {
        if (!newest) return;
        if (newest.severity === "CRITICAL") A.play("critical", 10);
        else if (newest.severity === "WARNING") A.play("warning", 10);
        else if (newest.severity === "NOTICE") A.play("resolution", 5);
    }

    /* ------------------------------------------------------------- drawing */

    // The countdown ticks locally between scans, always measured against the
    // backend's own window_end. If the backend published no window, it shows
    // nothing rather than counting from a boundary the UI made up.
    function interpolate(view) {
        if (!view || !view.window_end) return null;
        var end = Date.parse(view.window_end);
        if (Number.isNaN(end)) return null;
        return Math.max(0, (end - (Date.now() + state.serverOffsetMs)) / 1000);
    }

    function draw() {
        var snapshot = state.snapshot;
        if (!snapshot) return;
        var view = activeView();
        var focus = M.focused(snapshot);

        M.renderClock(snapshot);
        M.renderIdentity(snapshot);
        M.renderEngine(snapshot);
        M.renderTelemetry(snapshot);
        M.renderProcess(snapshot);
        M.renderEconomics(snapshot);
        M.renderProviders(snapshot);
        M.renderMap(snapshot);
        M.renderMatrix(snapshot);
        M.renderBandForward(snapshot);

        if (view === "contract") M.renderContract(snapshot, interpolate(focus));
        else if (view === "surveillance") M.renderSurveillance(snapshot);
        else if (view === "forward") M.renderForwardView(snapshot);
        else if (view === "diagnostics") M.renderDiagnostics(snapshot);
    }

    // Light frame: only the things that genuinely move between snapshots.
    function tick() {
        if (state.snapshot && state.ready) {
            M.renderClock(state.snapshot);
            M.renderMap(state.snapshot);
            if (activeView() === "contract") {
                M.renderContract(state.snapshot, interpolate(M.focused(state.snapshot)));
            }
        }
        global.requestAnimationFrame(tick);
    }

    /* ------------------------------------------------------------ transport */

    function apply(snapshot) {
        var first = state.snapshot === null;
        state.snapshot = snapshot;
        var serverNow = Date.parse(snapshot.server_time_utc);
        if (!Number.isNaN(serverNow)) state.serverOffsetMs = serverNow - Date.now();

        document.body.classList.toggle("demo", !!snapshot.demo_mode);
        if (snapshot.presentation && snapshot.presentation.scanlines === false) {
            document.body.classList.add("no-scanlines");
        }

        M.pushHistory(snapshot);
        if (!first) {
            routeAlerts(snapshot);
            A.play("scan", 4);
        } else {
            (snapshot.assets || []).forEach(function (v) {
                state.lastDecisions[v.asset] = v.final.key;
                state.lastContract[v.asset] = v.contract_id;
            });
        }

        if (state.ready) {
            draw();
            routeEvent(M.renderStream(snapshot, snapshot.presentation
                ? snapshot.presentation.event_log_rows + 6 : 14));
        }
    }

    function connect() {
        var source = new EventSource("/stream");
        source.onmessage = function (event) {
            state.connected = true;
            document.getElementById("offline").hidden = true;
            try {
                apply(JSON.parse(event.data));
            } catch (e) {
                // A malformed frame is dropped; the next one redraws everything.
            }
        };
        source.onerror = function () {
            state.connected = false;
            // The scanner is a separate process concern -- say so plainly.
            document.getElementById("offline").hidden = false;
        };
    }

    /* ----------------------------------------------------------------- boot */

    function start() {
        bindTabs();

        function requestImmersive() {
            if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
                document.documentElement.requestFullscreen().catch(function () {});
            }
        }

        // Prime with one snapshot so the boot sequence can hand over to a
        // populated screen rather than an empty one.
        fetch("/snapshot.json").then(function (r) { return r.json(); }).then(function (snapshot) {
            var p = snapshot.presentation || {};
            global.MANTIS_GRID = p.background_grid !== false;
            if (p.scanlines === false) document.body.classList.add("no-scanlines");
            A.init(p.audio_enabled !== false && p.boot_audio !== false, p.master_volume);

            state.snapshot = snapshot;
            var serverNow = Date.parse(snapshot.server_time_utc);
            if (!Number.isNaN(serverNow)) state.serverOffsetMs = serverNow - Date.now();
            M.pushHistory(snapshot);
            (snapshot.assets || []).forEach(function (v) {
                state.lastDecisions[v.asset] = v.final.key;
                state.lastContract[v.asset] = v.contract_id;
            });

            return global.MantisBoot.run({
                enabled: p.boot_enabled !== false,
                duration: p.boot_duration || 15.0,
                onReady: function () {
                    state.ready = true;
                    draw();
                    M.renderStream(snapshot, 14);
                    connect();
                }
            });
        }).catch(function () {
            // No backend: still show the shell so the failure is visible in the
            // interface's own language rather than as a blank page.
            global.MantisBoot.run({
                enabled: false,
                onReady: function () { state.ready = true; }
            });
            document.getElementById("offline").hidden = false;
            connect();
        });

        // Chromium suspends an AudioContext created before any gesture; resume
        // on the first interaction so a click or key press restores sound.
        ["click", "keydown"].forEach(function (name) {
            global.addEventListener(name, function () {
                A.resume();
                requestImmersive();
            }, { once: true });
        });

        global.requestAnimationFrame(tick);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})(window);
