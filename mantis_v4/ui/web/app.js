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
        lifecycle: "PRELOAD",
        connected: false,
        readyPosted: false,
        readyToken: "",
        frameLoopStarted: false,
        sseSnapshotsDuringBoot: 0,
        domRendersDuringBoot: 0,
        hydrationRenders: 0,
        lastSequence: -1,
        frontendBuildId: "",
        serverOffsetMs: 0,
        tabsBound: false,
        lastDecisions: Object.create(null),
        lastContract: Object.create(null)
    };

    /* --------------------------------------------------------------- views */

    function bindTabs() {
        if (state.tabsBound) return;
        state.tabsBound = true;
        document.querySelectorAll("#tabs li").forEach(function (tab) {
            if (tab.dataset.mantisBound === "true") return;
            tab.dataset.mantisBound = "true";
            tab.addEventListener("click", function () {
                document.querySelectorAll("#tabs li").forEach(function (t) {
                    t.classList.remove("active");
                });
                tab.classList.add("active");
                var target = tab.getAttribute("data-view");
                document.querySelectorAll(".view").forEach(function (v) {
                    v.classList.toggle("active", v.id === "view_" + target);
                });
                document.documentElement.dataset.activeView = target;
                A.play("init_pulse");
                draw("tab");
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
                // Per-asset qualification is not an operator action.
                if (key === "data_hold") A.play("data_hold", 15);
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

        var current=(snapshot.operator_state || {}).primary_selection;
        var identity=current ? [current.contract_id,current.asset,current.side].join("|") : "";
        if (identity && identity !== state.lastPrimarySelection) {
            A.play(current.side === "NO" ? "enter_no" : "enter_yes",5);
        }
        state.lastPrimarySelection=identity;

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

    function draw(reason) {
        var snapshot = state.snapshot;
        if (!snapshot) return;
        if (!state.ready && reason !== "hydration") {
            state.domRendersDuringBoot++;
            return;
        }
        if (reason === "hydration") state.hydrationRenders++;
        var view = activeView();
        var focus = M.focused(snapshot);
        var manualCenter = view === "contract" && snapshot.operator_state &&
            snapshot.operator_state.mode === "KALSHI_MANUAL_SIGNAL";

        // Manual operator state does not live in the legacy asset shells. Draw
        // its center first and exclusively so a failure in a peripheral module
        // cannot leave the initial STANDBY markup on screen.
        if (manualCenter) M.renderManualSignalCenter(snapshot, interpolate(focus));

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

        if (view === "contract" && !manualCenter) M.renderContract(snapshot, interpolate(focus));
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
                if ((state.snapshot.operator_state || {}).mode === "KALSHI_MANUAL_SIGNAL")
                    M.renderManualSignalCenter(state.snapshot, interpolate(M.focused(state.snapshot)));
                else M.renderContract(state.snapshot, interpolate(M.focused(state.snapshot)));
            }
        }
        global.requestAnimationFrame(tick);
    }

    /* ------------------------------------------------------------ transport */

    function showFrontendError(message) {
        var target = document.getElementById("frontend_error");
        if (target) { target.hidden = false; target.textContent = message; }
        document.body.setAttribute("data-frontend-error", message);
        console.error("MANTIS_FRONTEND", message);
    }

    function buildMatches(snapshot) {
        var backend = snapshot && (snapshot.frontend_build_id ||
            (snapshot.presentation || {}).frontend_build_id);
        if (!backend || !state.frontendBuildId || backend !== state.frontendBuildId) {
            showFrontendError("FRONTEND VERSION MISMATCH");
            return false;
        }
        return true;
    }

    function apply(snapshot, source) {
        if (!snapshot || typeof snapshot !== "object") return false;
        var sequence = Number(snapshot.sequence);
        if (!Number.isFinite(sequence)) return false;
        if (sequence <= state.lastSequence) {
            diagnostic("STATE_REJECTED", "source=" + (source || "unknown") + " sequence=" + sequence);
            return false;
        }
        if (!buildMatches(snapshot)) return false;
        state.lastSequence = sequence;
        var first = state.snapshot === null;
        state.snapshot = snapshot;
        diagnostic("STATE_ACCEPTED", "source=" + (source || "unknown") + " sequence=" + sequence);
        if (snapshot.presentation && snapshot.presentation.operator_diagnostics) {
            console.info("MANTIS_OPERATOR", "FRONTEND_RECEIVED", snapshot.sequence,
                snapshot.operator_state || null);
        }
        var serverNow = Date.parse(snapshot.server_time_utc);
        if (!Number.isNaN(serverNow)) state.serverOffsetMs = serverNow - Date.now();

        if (!state.ready) {
            state.sseSnapshotsDuringBoot++;
            return true; // boot lock: cache and clock sync only; never touch the DOM
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
        return true;
    }

    function connect() {
        var source = new EventSource("/stream");
        source.onmessage = function (event) {
            state.connected = true;
            document.getElementById("offline").hidden = true;
            try {
                var parsed = JSON.parse(event.data);
                diagnostic("SSE_EVENT", "sequence=" + parsed.sequence);
                apply(parsed, "sse");
            } catch (e) {
                // A malformed frame is dropped; the next one redraws everything.
                diagnostic("SSE_EVENT_ERROR", String(e && e.message ? e.message : e));
                console.error("MANTIS_SSE_EVENT_ERROR", e);
            }
        };
        source.onerror = function () {
            state.connected = false;
            // The scanner is a separate process concern -- say so plainly.
            document.getElementById("offline").hidden = false;
        };
    }

    /* ----------------------------------------------------------------- boot */

    function structurallyComplete(snapshot) {
        var base = !!(snapshot && snapshot.type === "snapshot" && snapshot.server_time_utc &&
            snapshot.status && snapshot.branding && snapshot.presentation &&
            Array.isArray(snapshot.assets) && snapshot.assets.length > 0 &&
            snapshot.assets.every(function (asset) {
                return asset && typeof asset.asset === "string" && asset.final && asset.classification;
            }));
        if (!base) return false;
        var operator = snapshot.operator_state || {};
        if (operator.mode === "KALSHI_MANUAL_SIGNAL") {
            return (operator.policy === "EXPERIMENTAL_MANUAL_SIGNAL_V1" ||
                (operator.policy === "EXPERIMENTAL_MANUAL_SIGNAL_V2" ||
                 operator.policy === "EXPERIMENTAL_MANUAL_SIGNAL_V2_1")) &&
                operator.first_scan_complete === true;
        }
        return true;
    }

    function truthfulHold(snapshot) {
        var held = JSON.parse(JSON.stringify(snapshot || {}));
        held.type = "snapshot";
        held.assets = (held.assets || []).map(function (asset) {
            asset.has_data = false;
            asset.final = { key: "data_hold", label: "DATA HOLD", glyph: "!", raw: "DATA_HOLD" };
            asset.final_reason = "AWAITING_LIVE_MARKET_STATE";
            asset.final_reason_text = "AWAITING LIVE MARKET STATE";
            return asset;
        });
        return held;
    }

    function waitForSnapshot(timeoutSeconds, bypass) {
        var manual = state.snapshot && (state.snapshot.operator_state || {}).mode === "KALSHI_MANUAL_SIGNAL";
        if ((bypass && !manual) || structurallyComplete(state.snapshot)) return Promise.resolve(true);
        state.lifecycle = "HYDRATING";
        return new Promise(function (resolve) {
            var deadline = Date.now() + Math.max(1000, timeoutSeconds * 1000);
            var timer = global.setInterval(function () {
                if (structurallyComplete(state.snapshot)) {
                    global.clearInterval(timer); resolve(true);
                } else if (Date.now() >= deadline) {
                    global.clearInterval(timer); resolve(false);
                }
            }, 50);
        });
    }

    function hydrate(timeoutSeconds, bypass) {
        return waitForSnapshot(timeoutSeconds, bypass).then(function (complete) {
            diagnostic("SNAPSHOT_READY", complete ? "complete" : "timeout-data-hold");
            if (!complete) state.snapshot = truthfulHold(state.snapshot);
            var snapshot = state.snapshot;
            if (!snapshot) return;
            var serverNow = Date.parse(snapshot.server_time_utc);
            if (!Number.isNaN(serverNow)) state.serverOffsetMs = serverNow - Date.now();
            state.lastDecisions = Object.create(null);
            state.lastContract = Object.create(null);
            (snapshot.assets || []).forEach(function (v) {
                state.lastDecisions[v.asset] = v.final.key;
                state.lastContract[v.asset] = v.contract_id;
            });
            return waitForViewportStable().then(function () {
                document.body.classList.toggle("demo", !!snapshot.demo_mode);
                if (snapshot.presentation && snapshot.presentation.scanlines === false) {
                    document.body.classList.add("no-scanlines");
                }
                M.pushHistory(snapshot);
                draw("hydration");
                M.renderStream(snapshot, snapshot.presentation
                    ? snapshot.presentation.event_log_rows + 6 : 14);
                diagnostic("HYDRATION_RENDER", "renders=" + state.hydrationRenders);
            });
        });
    }

    function waitForViewportStable() {
        return new Promise(function (resolve) {
            var finished = false;
            var stableTimer;
            var maximum = global.setTimeout(finish, 1000);
            function finish() {
                if (finished) return;
                finished = true;
                global.clearTimeout(maximum);
                global.clearTimeout(stableTimer);
                global.removeEventListener("resize", changed);
                diagnostic("VIEWPORT_STABLE", global.innerWidth + "x" + global.innerHeight);
                resolve();
            }
            function changed() {
                global.clearTimeout(stableTimer);
                stableTimer = global.setTimeout(finish, 180);
            }
            global.addEventListener("resize", changed, { passive: true });
            changed();
        });
    }

    var diagnosticsEnabled = new URLSearchParams(global.location.search).get("startupDiagnostics") === "1";
    function diagnostic(name, detail) {
        if (!diagnosticsEnabled) return;
        console.info("MANTIS_STARTUP", Math.round(performance.now()), name, detail || "");
    }
    global.addEventListener("mantis:startup-mark", function (event) {
        diagnostic(event.detail.name, event.detail.detail);
    });

    var lastRenderedTelemetry = "";
    global.addEventListener("mantis:center-rendered", function (event) {
        var detail = event.detail || {};
        var signature = [detail.sequence, detail.mode, detail.headline, detail.reason,
            detail.asset, detail.market, detail.countdown, detail.candidate_rows].join("|");
        if (!state.readyToken || signature === lastRenderedTelemetry) return;
        lastRenderedTelemetry = signature;
        var query = new URLSearchParams(detail);
        query.set("token", state.readyToken);
        fetch("/frontend-rendered?" + query.toString(), { cache: "no-store" }).catch(function () {});
    });
    var lastTabTelemetry="";
    global.addEventListener("mantis:tab-rendered",function(event){
        var detail=event.detail||{};
        var signature=[detail.view,detail.sequence,detail.row_count,detail.text].join("|");
        if(!state.readyToken || signature===lastTabTelemetry)return;
        lastTabTelemetry=signature;
        var query=new URLSearchParams(detail); query.set("token",state.readyToken);
        fetch("/frontend-rendered?"+query.toString(),{cache:"no-store"}).catch(function(){});
    });

    function postReady() {
        if (state.readyPosted) return;
        state.readyPosted = true;
        fetch("/presentation-ready?token=" + encodeURIComponent(state.readyToken),
              { cache: "no-store" }).catch(function () {});
    }

    function start() {
        bindTabs();
        var requestedView = new URLSearchParams(global.location.search).get("view");
        if (/^(contract|surveillance|forward|diagnostics)$/.test(requestedView || "")) {
            var requestedTab=document.querySelector('#tabs li[data-view="'+requestedView+'"]');
            if (requestedTab) requestedTab.click();
        }
        var buildNode = document.getElementById("frontend_build");
        state.frontendBuildId = buildNode ? buildNode.getAttribute("data-build-id") : "";
        document.body.setAttribute("data-frontend-build-id", state.frontendBuildId);
        console.info("MANTIS_FRONTEND_BUILD", state.frontendBuildId);

        diagnostic("BROWSER_PAGE_LOADED");

        // Prime with one snapshot so the boot sequence can hand over to a
        // populated screen rather than an empty one.
        fetch("/snapshot.json", { cache: "no-store", headers: { "Cache-Control": "no-cache" } }).then(function (r) { return r.json(); }).then(function (snapshot) {
            diagnostic("SERVER_READY");
            var p = snapshot.presentation || {};
            state.readyToken = p.ready_token || "";
            global.MANTIS_GRID = p.background_grid !== false;
            if (p.scanlines === false) document.body.classList.add("no-scanlines");
            A.init(p.audio_enabled !== false && p.boot_audio !== false, p.master_volume);

            apply(snapshot, "initial");
            connect(); // cache every SSE frame while the opaque boot lock is held

            return global.MantisBoot.run({
                enabled: p.boot_enabled !== false,
                brandPreludeEnabled: p.brand_prelude_enabled !== false,
                brandPreludeDuration: p.brand_prelude_duration_seconds || 13.0,
                technicalDuration: p.technical_boot_duration_seconds || 8.5,
                settleSeconds: p.boot_enabled === false ? 0 : (p.startup_settle_seconds || 0.75),
                onHydrate: function (bypass) {
                    return hydrate(p.startup_snapshot_timeout_seconds || 7.0, !!bypass);
                },
                onReady: function () {
                    state.lifecycle = "READY";
                    state.ready = true;
                    if (!state.frameLoopStarted) {
                        state.frameLoopStarted = true;
                        global.requestAnimationFrame(tick);
                    }
                    diagnostic("READY", "sse=" + state.sseSnapshotsDuringBoot +
                        " prehydrateDom=" + state.domRendersDuringBoot +
                        " hydration=" + state.hydrationRenders);
                    postReady();
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

        // Chromium suspends an AudioContext created before a gesture. Resume
        // audio only; fullscreen is owned by the single hardened browser launch.
        ["pointerdown", "keydown"].forEach(function (name) {
            global.addEventListener(name, function () {
                A.resume();
            }, { once: true });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }

    global.MantisAppLifecycle = {
        state: state,
        apply: apply,
        buildMatches: buildMatches,
        structurallyComplete: structurallyComplete,
        truthfulHold: truthfulHold
    };
})(window);
