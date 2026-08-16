/* Module renderers.
 *
 * One function per screen region. Each takes the snapshot the backend
 * published and writes DOM. No function here derives a decision quantity; the
 * only arithmetic is bar widths, canvas coordinates and the countdown against
 * the backend's own window_end.
 */

(function (global) {
    "use strict";

    var F = global.F;
    var DASH = F.DASH;

    /* ------------------------------------------------------------ helpers */

    function esc(s) {
        return String(s === null || s === undefined ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }

    function rows(target, list) {
        var html = "";
        for (var i = 0; i < list.length; i++) {
            var r = list[i];
            if (r === null) { html += '<div class="rule"></div>'; continue; }
            var cls = r.cls ? " " + r.cls : "";
            var sub = r.sub ? " sub" : "";
            html += '<div class="row' + sub + '"><span class="k">' + esc(r.k) +
                    '</span><span class="v' + cls + '">' + (r.html || esc(r.v)) + "</span></div>";
        }
        target.innerHTML = html;
    }

    function dot(state) {
        var good = ["LIVE", "AVAILABLE", "OK", "READY", "VALID"].indexOf(state) >= 0;
        var warn = ["DEGRADED", "CACHED", "AUTH_NOT_CONFIGURED", "UNAVAILABLE",
                    "DISABLED", "PROXY_UNVERIFIED", "ECONOMICS_UNAVAILABLE",
                    "SCHEMA_UNVERIFIED", "NO_VERIFIED_BOOK"].indexOf(state) >= 0;
        var cls = good ? "ok" : (warn ? "warnc" : "critc");
        var hollow = good ? "" : " hollow";
        var live = good ? " live" : "";
        return '<span class="' + cls + '"><i class="dot' + hollow + live + '"></i>' +
               esc(F.words(state)) + "</span>";
    }

    function meter(fraction, cls) {
        var pct = Math.max(0, Math.min(1, F.has(fraction) ? fraction : 0)) * 100;
        return '<span class="meter ' + (cls || "") + '" style="display:inline-block;width:6vh;vertical-align:middle">' +
               '<i style="width:' + pct.toFixed(1) + '%"></i></span>';
    }

    function focused(snap) {
        var list = snap.assets || [];
        for (var i = 0; i < list.length; i++) if (list[i].asset === snap.focus) return list[i];
        return list[0] || null;
    }

    /* -------------------------------------------------------------- clock */

    function renderClock(snap) {
        var now = new Date();
        var c = F.clock(now);
        var zone = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
            .formatToParts(now).filter(function (p) { return p.type === "timeZoneName"; });
        document.getElementById("clock_time").innerHTML =
            "<span>" + c.h[0] + "</span><span>" + c.h[1] + "</span><em>:</em>" +
            "<span>" + c.m[0] + "</span><span>" + c.m[1] + "</span><em>:</em>" +
            "<span>" + c.s[0] + "</span><span>" + c.s[1] + "</span>" +
            '<span class="zone">' + esc(zone.length ? zone[0].value : "") + "</span>";
        document.getElementById("clock_day").textContent =
            now.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })
                .toUpperCase();
        document.getElementById("clock_run").textContent =
            "RUN " + String(snap.status.run_id || DASH).slice(0, 8).toUpperCase();
    }

    /* ----------------------------------------------------------- identity */

    function renderIdentity(snap) {
        var s = snap.status;
        rows(document.getElementById("identity_rows"), [
            { k: "MODE", v: s.observation_only ? "OBSERVATION ONLY" : "UNKNOWN", cls: "warnc" },
            { k: "MODEL", v: s.model_version },
            { k: "POLICY", v: s.policy_name },
            { k: "BUILD", v: s.software_version },
            { k: "COMMIT", v: String(s.git_commit || DASH).slice(0, 10) },
            { k: "UPTIME", v: uptime(s) }
        ]);
    }

    function uptime(status) {
        if (!status.started_at) return DASH;
        var started = Date.parse(status.started_at);
        if (Number.isNaN(started)) return DASH;
        return F.duration((Date.now() - started) / 1000);
    }

    /* ------------------------------------------------------- data engine */

    function renderEngine(snap) {
        var s = snap.status;
        var view = focused(snap) || {};
        rows(document.getElementById("engine_rows"), [
            { k: "UNDERLYING", html: dot(s.underlying_state) },
            { k: "SOURCE", v: s.underlying_provider, sub: true },
            { k: "OK / FAIL", v: s.underlying_successes + " / " + s.underlying_failures, sub: true },
            { k: "FETCH LATENCY", v: F.num(s.latency_seconds, 3, " s") },
            { k: "DATA AGE", v: F.num(view.data_age_seconds, 1, " s") },
            { k: "SCANS / ERRORS", v: s.scan_count + " / " + s.error_count,
              cls: s.error_count ? "critc" : "" }
        ]);
    }

    /* --------------------------------------------------------- telemetry */

    var history = { pyes: [], vol: [], lat: [] };
    var MAX_POINTS = 120;

    function pushHistory(snap) {
        var view = focused(snap);
        if (!view) return;
        // Only real published values are recorded. A scan with no data records
        // nothing rather than repeating the previous point.
        if (F.has(view.p_yes)) history.pyes.push(view.p_yes);
        if (F.has(view.volatility_estimate)) history.vol.push(view.volatility_estimate);
        if (F.has(snap.status.latency_seconds)) history.lat.push(snap.status.latency_seconds);
        Object.keys(history).forEach(function (k) {
            if (history[k].length > MAX_POINTS) history[k].splice(0, history[k].length - MAX_POINTS);
        });
    }

    function drawGraph(id, series, options) {
        var canvas = document.getElementById(id);
        if (!canvas) return;
        var ratio = global.devicePixelRatio || 1;
        var w = canvas.clientWidth, h = canvas.clientHeight;
        if (!w || !h) return;
        if (canvas.width !== w * ratio || canvas.height !== h * ratio) {
            canvas.width = w * ratio; canvas.height = h * ratio;
        }
        var g = canvas.getContext("2d");
        g.setTransform(ratio, 0, 0, ratio, 0, 0);
        g.clearRect(0, 0, w, h);

        var accent = options && options.accent ? options.accent : "rgba(170,207,209,1)";

        // Faint reference grid, echoing the background texture.
        g.strokeStyle = "rgba(170,207,209,0.10)";
        g.lineWidth = 1;
        for (var x = 0; x <= 4; x++) {
            g.beginPath(); g.moveTo((w / 4) * x, 0); g.lineTo((w / 4) * x, h); g.stroke();
        }
        for (var y = 0; y <= 2; y++) {
            g.beginPath(); g.moveTo(0, (h / 2) * y); g.lineTo(w, (h / 2) * y); g.stroke();
        }

        if (series.length < 2) return;
        var lo = Math.min.apply(null, series), hi = Math.max.apply(null, series);
        if (options && options.floor !== undefined) lo = Math.min(lo, options.floor);
        if (options && options.ceil !== undefined) hi = Math.max(hi, options.ceil);
        var span = (hi - lo) || 1;
        var step = w / (MAX_POINTS - 1);
        var offset = w - step * (series.length - 1);

        function pointY(v) { return h - ((v - lo) / span) * (h - 2) - 1; }

        // Filled area then stroke -- the upstream graphs read as solid traces.
        g.beginPath();
        g.moveTo(offset, h);
        for (var i = 0; i < series.length; i++) g.lineTo(offset + i * step, pointY(series[i]));
        g.lineTo(offset + (series.length - 1) * step, h);
        g.closePath();
        g.fillStyle = accent.replace(/,1\)$/, ",0.14)");
        g.fill();

        g.beginPath();
        for (var j = 0; j < series.length; j++) {
            var px = offset + j * step, py = pointY(series[j]);
            if (j === 0) g.moveTo(px, py); else g.lineTo(px, py);
        }
        g.strokeStyle = accent;
        g.lineWidth = 1.2;
        g.stroke();

        // Leading marker on the newest sample.
        var lx = offset + (series.length - 1) * step, ly = pointY(series[series.length - 1]);
        g.fillStyle = accent;
        g.fillRect(lx - 1.5, ly - 1.5, 3, 3);
    }

    function renderTelemetry(snap) {
        var view = focused(snap) || {};
        document.getElementById("tel_p_cap").textContent =
            "P(YES) — " + (view.short || DASH);
        document.getElementById("tel_p_val").textContent = F.pct(view.p_yes);
        document.getElementById("tel_v_val").textContent = F.num(view.volatility_estimate, 6);
        document.getElementById("tel_l_val").textContent = F.num(snap.status.latency_seconds, 3, " s");
        drawGraph("graph_pyes", history.pyes, { floor: 0, ceil: 1 });
        drawGraph("graph_vol", history.vol, {});
        drawGraph("graph_lat", history.lat, { floor: 0 });
    }

    /* ----------------------------------------------------------- process */

    function renderProcess(snap) {
        var host = snap.status.host || {};
        var list = [
            { k: "PID / THREADS", v: (F.has(host.pid) ? host.pid : DASH) + " / " +
                (F.has(host.threads) ? host.threads : DASH) },
            { k: "STREAM", v: (host.clients === undefined ? DASH : host.clients + " client(s)") }
        ];
        // CPU and RSS are only shown when the host could actually measure them.
        if (F.has(host.cpu_percent)) {
            list.push({ k: "CPU", html: meter(host.cpu_percent / 100) + " " + host.cpu_percent.toFixed(0) + "%" });
        }
        if (F.has(host.rss_mb)) {
            list.push({ k: "RSS", v: host.rss_mb.toFixed(0) + " MB" });
        }
        rows(document.getElementById("process_rows"), list);
    }

    /* ------------------------------------------------- active contract view */

    function centerWrite(snap, nodeId, value, html) {
        var node = document.getElementById(nodeId);
        if (!node) return;
        if (html) node.innerHTML = value;
        else node.textContent = value;
        if (snap.presentation && snap.presentation.operator_diagnostics) {
            console.info("CENTER_WRITE", "source=MANUAL_SIGNAL", "node=" + nodeId,
                "value=" + node.textContent, "sequence=" + snap.sequence);
        }
    }

    function renderManualSignalCenter(snap, ticked) {
        var operator = snap.operator_state || {};
        var selected = operator.primary_selection || null;
        var strongest = operator.strongest_candidate || null;
        var summary = selected || strongest || {};
        var contractSeconds = F.has(summary.seconds_remaining) ? Number(summary.seconds_remaining) : ticked;
        var eligibilitySeconds = F.has(operator.seconds_until_entry_eligible) ?
            Number(operator.seconds_until_entry_eligible) : null;
        var displayedSeconds = eligibilitySeconds > 0 ? eligibilitySeconds : contractSeconds;
        var headline = operator.headline || (operator.first_scan_complete ? "NO SIGNAL" : "SCANNING");
        var reason = operator.reason || (operator.first_scan_complete ? "NO CURRENT SIGNAL" : "AWAITING FIRST KALSHI SCAN");
        var status = summary.status || "SCANNING";
        var asset = summary.asset || "KALSHI 15M";
        var market = summary.market_ticker || operator.current_window || "AWAITING CURRENT MARKET";

        document.documentElement.dataset.renderedMode = "KALSHI_MANUAL_SIGNAL";
        document.documentElement.dataset.renderedHeadline = headline;
        document.documentElement.dataset.renderedReason = reason;
        document.documentElement.dataset.renderedSequence = String(snap.sequence);
        document.documentElement.dataset.centerRenderSource = "MANUAL_SIGNAL";

        centerWrite(snap, "contract_asset", String(asset).replace("-USD", ""));
        centerWrite(snap, "contract_window", market);
        centerWrite(snap, "contract_countdown", F.countdown(displayedSeconds));
        centerWrite(snap, "contract_mark", eligibilitySeconds > 0 ? "ENTRY ELIGIBLE IN" : "CONTRACT TIME REMAINING");
        var countdown = document.getElementById("contract_countdown");
        countdown.className = !F.has(displayedSeconds) ? "" :
            (displayedSeconds <= 30 ? "final" : (displayedSeconds <= 120 ? "near" : ""));

        var elapsed = F.has(contractSeconds) ? Math.max(0, Math.min(1, (900 - contractSeconds) / 900)) : 0;
        document.querySelector("#window_meter > i").style.width = (elapsed * 100).toFixed(1) + "%";

        var slab = document.getElementById("decision_slab");
        slab.className = "state-" + (selected ? (selected.side === "YES" ? "enter_yes" : "enter_no") :
            (headline === "SCANNING" ? "wait" : "no_trade"));
        centerWrite(snap, "decision_glyph", selected ? "◆" : (headline === "SCANNING" ? "◌" : "×"));
        centerWrite(snap, "decision_label", selected ? "PRIMARY SIGNAL — " + selected.side : headline);
        centerWrite(snap, "decision_reason", "STATUS<b>" + esc(reason) + "</b>", true);

        var summaryRows = [
            { k: selected ? "PRIMARY SIGNAL" : "FOCUS", v: summary.asset ? summary.asset.replace("-USD", "") + " / 15M" : DASH, cls: selected ? "ok" : "warnc" },
            { k: "MODEL SIDE / CONFIDENCE", v: (summary.side || DASH) + " / " + F.pct(summary.confidence) },
            { k: "CONSERVATIVE PROB", v: F.pct(summary.conservative_probability) },
            { k: "KALSHI TARGET", v: F.has(summary.target) ? F.price(summary.target) : DASH },
            { k: "PROXY CURRENT", v: F.has(summary.proxy_current) ? F.price(summary.proxy_current) : DASH },
            { k: "REFERENCE RISK", v: F.words(summary.reference_risk || "REFERENCE UNKNOWN") + " / DEV_P95" },
            { k: "STATUS", v: status, cls: status === "PRIMARY" ? "ok" : "warnc" },
            { k: "MARKET", v: market },
            { k: "EVENT MARKET SOURCE", v: operator.event_market_provider || "KALSHI_PUBLIC_REST" },
            { k: "MODEL STATUS", v: "EXPERIMENTAL — NOT YET FORWARD VALIDATED", cls: "warnc" },
            { k: "EXECUTION", v: "MANUAL ONLY", cls: "warnc" }
        ];
        if (eligibilitySeconds > 0) summaryRows.splice(1, 0,
            { k: "ENTRY ELIGIBLE IN", v: F.countdown(eligibilitySeconds), cls: "warnc" });
        rows(document.getElementById("primary_selection_summary"), summaryRows);

        var table = '<div class="rank-head"><span>ASSET</span><span>SIDE</span><span>MODEL %</span><span>REF RISK / ASK</span><span>FEE / CONS EDGE</span><span>STATUS</span></div>';
        (operator.candidate_rankings || []).forEach(function (c) {
            table += '<div class="rank-row"><span>' + esc(String(c.asset || "").replace("-USD", "")) +
                '</span><span>' + esc(c.side || DASH) + '</span><span>' + esc(F.pct(c.confidence)) +
                '</span><span>' + esc(F.words(c.reference_risk || "REFERENCE UNKNOWN") + " / " + (F.has(c.ask) ? F.money(c.ask) : DASH)) +
                '</span><span>' + esc((F.has(c.fee) ? F.money(c.fee) : DASH) + " / " + (F.has(c.conservative_edge) ? F.pct(c.conservative_edge, 1) : DASH)) +
                '</span><span class="' + (c.status === "PRIMARY" ? "ok" : "warnc") + '">' + esc(c.status || c.reason || "NO SIGNAL") + '</span></div>';
        });
        document.getElementById("candidate_ranking").innerHTML = table;

        rows(document.getElementById("cell_confidence"), [
            { k: "MODEL CONFIDENCE", html: meter(summary.confidence) + " " + F.pct(summary.confidence) },
            { k: "CONSERVATIVE PROB", html: meter(summary.conservative_probability) + " " + F.pct(summary.conservative_probability) },
            { k: "MODEL SIDE", v: summary.side || DASH }
        ]);
        rows(document.getElementById("cell_underlying"), [
            { k: "PROXY CURRENT", v: F.has(summary.proxy_current) ? F.price(summary.proxy_current) : DASH },
            { k: "KALSHI TARGET", v: F.has(summary.target) ? F.price(summary.target) : DASH },
            { k: "DISTANCE", v: F.has(summary.distance_bps) ? F.num(summary.distance_bps, 2, " bp") : DASH }
        ]);
        rows(document.getElementById("cell_risk"), [
            { k: "REFERENCE RISK", v: F.words(summary.reference_risk || "REFERENCE UNKNOWN") },
            { k: "FRAGILITY", v: F.num(summary.fragility, 1) },
            { k: "DISAGREEMENT", v: F.num(summary.disagreement, 4) },
            { k: "CROSSING RISK", v: F.pct(summary.crossing_probability) }
        ]);
        rows(document.getElementById("cell_class"), [
            { k: "POLICY", v: operator.policy || DASH },
            { k: "STATUS", v: status },
            { k: "REASON", v: reason }
        ]);
        rows(document.getElementById("cell_econ"), [
            { k: (summary.side || "EVENT") + " ASK", v: F.has(summary.ask) ? F.money(summary.ask) : "UNAVAILABLE" },
            { k: "ASK SIZE", v: F.has(summary.ask_size) ? F.num(summary.ask_size, 4) : DASH },
            { k: "FEE", v: F.has(summary.fee) ? F.money(summary.fee) : "NOT YET EVALUATED" },
            { k: "CONSERVATIVE EDGE", v: F.has(summary.conservative_edge) ? F.pct(summary.conservative_edge, 1) : DASH }
        ]);
        rows(document.getElementById("cell_contract"), [
            { k: "MARKET", v: market },
            { k: "CONTRACT REMAINING", v: F.countdown(contractSeconds) },
            { k: "SOURCE", v: operator.event_market_provider || "KALSHI_PUBLIC_REST" },
            { k: "EXECUTION", v: "MANUAL ONLY" }
        ]);
        global.dispatchEvent(new CustomEvent("mantis:center-rendered", { detail: {
            mode: "KALSHI_MANUAL_SIGNAL", headline: headline, reason: reason,
            sequence: snap.sequence, source: "MANUAL_SIGNAL", asset: asset,
            market: market, countdown: F.countdown(displayedSeconds),
            candidate_rows: (operator.candidate_rankings || []).length
        }}));
    }

    function renderContract(snap, ticked) {
        var operator = snap.operator_state || {};
        if (operator.mode === "KALSHI_MANUAL_SIGNAL") {
            renderManualSignalCenter(snap, ticked);
            return;
        }
        var view = focused(snap) || {asset:"SYSTEM", classification:{key:"unknown",label:"STANDBY"},
            final:{key:"unknown",label:"STANDBY"}, provider:{}, economics:{}};
        var selection = snap.primary_selection || {};
        var selected = operator.primary_selection || null;
        var strongest = operator.strongest_candidate || null;
        var manual = false;
        document.body.setAttribute("data-rendered-mode", operator.mode || "STANDBY");
        document.body.setAttribute("data-rendered-headline", operator.headline || "STANDBY");
        document.body.setAttribute("data-rendered-sequence", String(snap.sequence));
        if (snap.presentation && snap.presentation.operator_diagnostics) {
            console.info("MANTIS_OPERATOR", "FRONTEND_RENDERED_CENTER", operator.mode || "STANDBY");
        }

        document.getElementById("contract_asset").textContent = view.asset;
        document.getElementById("contract_window").textContent =
            view.contract_id ? view.contract_id : "NO ACTIVE CONTRACT";

        var seconds = ticked;
        var el = document.getElementById("contract_countdown");
        el.textContent = F.countdown(seconds);
        el.className = !F.has(seconds) ? "" : (seconds <= 30 ? "final" : (seconds <= 120 ? "near" : ""));
        document.getElementById("contract_mark").textContent = F.mark(seconds);

        var elapsed = view.elapsed_fraction;
        document.querySelector("#window_meter > i").style.width =
            ((F.has(elapsed) ? elapsed : 0) * 100).toFixed(1) + "%";

        var slab = document.getElementById("decision_slab");
        var mode = operator.mode || "STANDBY";
        var selectedKey = selected ? (selected.side === "YES" ? "enter_yes" : "enter_no") :
            (mode === "PROVIDER_DEGRADED" ? "data_hold" : (mode === "STANDBY" ? "unknown" : "no_trade"));
        slab.className = "state-" + selectedKey;
        document.getElementById("decision_glyph").textContent = selected ? "◆" : "×";
        document.getElementById("decision_label").textContent = selected ? "PRIMARY SELECTION — ENTER " + selected.side : "NO TRADE";
        document.getElementById("decision_reason").innerHTML =
            "STATUS<b>" + esc(selected ? "ECONOMICALLY VALIDATED" : ((strongest && strongest.reason) || "AWAITING CANDIDATES")) + "</b>";

        // The backend owns operator semantics; these assignments intentionally
        // override the legacy selector-derived labels above.
        document.getElementById("decision_label").textContent = selected ?
            (manual ? "PRIMARY SIGNAL — " : "PRIMARY SELECTION — ENTER ") + selected.side : (operator.headline || "STANDBY");
        document.getElementById("decision_reason").innerHTML =
            "STATUS<b>" + esc(operator.reason || "AWAITING FIRST SCANNER SNAPSHOT") + "</b>";
        var summary = selected || strongest || {};
        var summaryRows = manual ? [
            { k: "MANTIS", v: "KALSHI EXPERIMENTAL MANUAL SIGNAL", cls: "warnc" },
            { k: selected ? "PRIMARY SIGNAL" : "STRONGEST CURRENT CANDIDATE", v: summary.asset ? summary.asset.replace("-USD", "") + " / 15M / " + (summary.side || DASH) : DASH, cls: selected ? "ok" : "warnc" },
            { k: "MODEL / CONSERVATIVE PROB", v: F.pct(summary.confidence) + " / " + F.pct(summary.conservative_probability) },
            { k: "KALSHI TARGET / PROXY CURRENT", v: F.has(summary.target) ? F.price(summary.target) + " / " + F.price(summary.proxy_current) : DASH },
            { k: "DISTANCE / REFERENCE RISK", v: F.has(summary.distance_bps) ? F.num(summary.distance_bps, 2, " bp") + " / " + F.words(summary.reference_risk) + " DEV_P95" : DASH },
            { k: (summary.side || "EVENT") + " ASK / SIZE", v: F.has(summary.ask) ? F.money(summary.ask) + " / " + F.num(summary.ask_size, 4) : "UNAVAILABLE" },
            { k: "KALSHI FEE / TOTAL COST", v: F.has(summary.fee) ? F.money(summary.fee) + " / " + F.money(summary.total_cost) : "UNVERIFIED" },
            { k: "BREAK-EVEN", v: F.pct(summary.net_break_even) },
            { k: "MODEL / CONS EDGE", v: F.has(summary.model_edge) ? F.pct(summary.model_edge, 1) + " / " + F.pct(summary.conservative_edge, 1) : DASH },
            { k: "NET / CONS EV", v: F.has(summary.net_ev) ? F.money(summary.net_ev) + " / " + F.money(summary.conservative_net_ev) : DASH },
            { k: "EVENT MARKET SOURCE", v: "KALSHI_PUBLIC_REST" },
            { k: "KALSHI MARKET", v: summary.market_ticker || DASH },
            { k: "TIME REMAINING", v: F.has(summary.seconds_remaining) ? F.countdown(summary.seconds_remaining) : DASH },
            { k: "MODEL STATUS", v: "EXPERIMENTAL — NOT YET FORWARD VALIDATED", cls: "warnc" },
            { k: "EXECUTION", v: "MANUAL ONLY", cls: "warnc" }
        ] : [
            { k: selected ? "PRIMARY CONTRACT SELECTION" : "STRONGEST CURRENT CANDIDATE", v: summary.asset ? summary.asset.replace("-USD", "") + " / 15M" : DASH, cls: selected ? "ok" : "warnc" },
            { k: "SIDE / MODEL CONF", v: (summary.side || DASH) + " / " + F.pct(summary.confidence) },
            { k: "CONSERVATIVE PROB", v: F.pct(summary.conservative_probability) },
            { k: "EXECUTABLE ASK", v: F.has(summary.ask) ? F.money(summary.ask) : "UNAVAILABLE" },
            { k: "OPENING FEE", v: F.has(summary.fee) ? F.money(summary.fee) : "$0.0200" },
            { k: "NET BREAK-EVEN", v: F.pct(summary.net_break_even) },
            { k: "MODEL / CONS EDGE", v: F.has(summary.model_edge) ? F.pct(summary.model_edge, 1) + " / " + F.pct(summary.conservative_edge, 1) : DASH },
            { k: "NET / CONS EV", v: F.has(summary.net_ev) ? F.money(summary.net_ev) + " / " + F.money(summary.conservative_net_ev) : DASH },
            { k: "ACTION", v: selected ? "PRIMARY SELECTION" : "WAIT / NO TRADE", cls: selected ? "ok" : "warnc" }
        ];
        rows(document.getElementById("primary_selection_summary"), summaryRows);
        if (!selected && F.has(operator.seconds_until_entry_eligible) && operator.seconds_until_entry_eligible > 0) {
            document.getElementById("primary_selection_summary").innerHTML += '<div class="row"><span class="k">EARLIEST ENTRY IN</span><span class="v warnc">'+esc(F.countdown(operator.seconds_until_entry_eligible))+'</span></div>';
        }
        var table=manual ? '<div class="rank-head"><span>ASSET</span><span>SIDE</span><span>MODEL %</span><span>REF RISK / ASK</span><span>FEE / CONS EDGE</span><span>STATUS</span></div>' : '<div class="rank-head"><span>ASSET</span><span>SIDE</span><span>CONF</span><span>ASK</span><span>NET EDGE</span><span>STATUS</span></div>';
        (operator.candidate_rankings || []).forEach(function(c){
            var fourth=manual ? F.words(c.reference_risk)+" / "+(F.has(c.ask)?F.money(c.ask):DASH) : (F.has(c.ask)?F.money(c.ask):DASH);
            var fifth=manual ? (F.has(c.fee)?F.money(c.fee):DASH)+" / "+(F.has(c.conservative_edge)?F.pct(c.conservative_edge,1):DASH) : (F.has(c.model_edge)?F.pct(c.model_edge,1):DASH);
            table+='<div class="rank-row"><span>'+esc(String(c.asset||"").replace("-USD",""))+'</span><span>'+esc(c.side||DASH)+'</span><span>'+esc(F.pct(c.confidence))+'</span><span>'+esc(fourth)+'</span><span>'+esc(fifth)+'</span><span class="'+(c.status==="PRIMARY"?"ok":"warnc")+'">'+esc(selected&&selected.asset===c.asset?(manual?"PRIMARY":"SELECTED"):(c.status||c.reason))+'</span></div>';
        });
        document.getElementById("candidate_ranking").innerHTML=table;

        var bufferPct = (F.has(view.buffer) && view.reference) ? view.buffer / view.reference : null;

        rows(document.getElementById("cell_confidence"), [
            { k: "P(YES)", html: meter(view.p_yes) + " " + F.pct(view.p_yes) },
            { k: "P(NO)", html: meter(view.p_no) + " " + F.pct(view.p_no) },
            { k: "LCB", html: meter(view.conservative_bound) + " " + F.pct(view.conservative_bound) },
            { k: "SIDE", v: view.predicted_side || DASH }
        ]);
        rows(document.getElementById("cell_underlying"), [
            { k: "CURRENT", v: view.has_data ? F.price(view.current_price) : DASH },
            { k: "REFERENCE", v: view.has_data ? F.price(view.reference) : DASH },
            { k: "BUFFER", v: view.has_data ? F.signed(view.buffer) : DASH },
            { k: "BUFFER %", v: view.has_data ? F.pct(bufferPct, 3) : DASH }
        ]);
        rows(document.getElementById("cell_risk"), [
            { k: "FRAGILITY", html: meter(F.has(view.fragility) ? view.fragility / 100 : null) + " " + F.num(view.fragility, 1) },
            { k: "DISAGREEMENT", v: F.num(view.disagreement, 4) },
            { k: "CROSSING RISK", v: F.pct(view.crossing_probability) },
            { k: "CROSSINGS", v: F.has(view.reference_crossings) ? String(view.reference_crossings) + "x" : DASH }
        ]);
        rows(document.getElementById("cell_class"), [
            { k: "PHASE 6", v: view.classification.label,
              cls: view.classification.key === "enter_yes" || view.classification.key === "enter_no" ? "ok" : "" },
            { k: "REASON", v: view.classification_reason_text || DASH },
            { k: "MODEL", v: view.model_version || snap.status.model_version }
        ]);
        rows(document.getElementById("cell_econ"), [
            { k: "EV STATUS", v: F.words(view.ev_status),
              cls: view.ev_status === "ROBUST_POSITIVE_EV" ? "ok" : "warnc" },
            { k: "LCB EV", v: view.economics_available ? F.money(view.lcb_ev) : DASH },
            { k: "MODEL EDGE", v: view.economics_available ? F.pct(view.model_edge, 2) : DASH }
        ]);
        rows(document.getElementById("cell_contract"), [
            { k: "REFERENCE", v: F.words(view.reference_status) },
            { k: "QUOTE", v: F.words(view.quote_status) },
            { k: "DATA AGE", v: F.num(view.data_age_seconds, 1, " s") }
        ]);

        // The trace is drawn only from probabilities the backend actually
        // produced this session. No history is fabricated for decoration.
        var spark = view.spark || [];
        document.getElementById("trace_meta").textContent =
            spark.length ? spark.length + " OBSERVATIONS  ·  LATEST " + F.pct(view.p_yes)
                         : "AWAITING OBSERVATIONS";
        drawGraph("graph_contract", spark, { floor: 0, ceil: 1 });

        var block = document.getElementById("error_block");
        if (snap.error) {
            block.hidden = false;
            block.innerHTML =
                "SYSTEM ERROR<br/>Provider:  " + esc(snap.error.provider) +
                "<br/>Component: " + esc(snap.error.component) +
                "<br/>Recovery:  " + esc(snap.error.recovery) +
                "<br/>Detail:    " + esc(snap.error.message) +
                (snap.error.traceback ? "<br/><br/>" + esc(snap.error.traceback) : "");
        } else {
            block.hidden = true;
        }
    }

    /* ------------------------------------------------------ other views */

    function renderSurveillance(snap) {
        var list = [];
        (snap.assets || []).forEach(function (v) {
            var bufferPct = (F.has(v.buffer) && v.reference) ? v.buffer / v.reference : null;
            list.push({ k: v.asset + (v.asset === snap.focus ? "  ◆" : ""),
                        html: '<span class="' + stateClass(v.final.key) + '">' +
                              esc(v.final.glyph + " " + v.final.label) + "</span>" });
            list.push({ k: "PRICE / REF", sub: true,
                        v: v.has_data ? F.price(v.current_price) + "   " + F.price(v.reference) : DASH });
            list.push({ k: "BUFFER / P(YES) / LCB", sub: true,
                        v: v.has_data ? F.pct(bufferPct, 3) + "   " + F.pct(v.p_yes) + "   " + F.pct(v.conservative_bound) : DASH });
            list.push({ k: "FRAG / DISAG / CROSS", sub: true,
                        v: v.has_data ? F.num(v.fragility, 1) + "   " + F.num(v.disagreement, 4) + "   " + F.pct(v.crossing_probability) : DASH });
            list.push({ k: "REASON", sub: true, v: v.final_reason_text || DASH });
            list.push(null);
        });
        rows(document.getElementById("surv_rows"), list);
    }

    function renderForwardView(snap) {
        var f = snap.forward, h = snap.historical;
        var list = [{ k: "FORWARD OBSERVATION SAMPLE", v: "LIVE — NOT A BACKTEST" }, null];
        if (!f) {
            list.push({ k: "STATUS", v: "NO FORWARD RECORDS YET" });
        } else {
            var ci = F.has(f.ci_low) ? "  [" + F.pct(f.ci_low) + ", " + F.pct(f.ci_high) + "]" : "";
            list.push({ k: "SAMPLE STATUS", v: f.forward_status || f.sample_label || DASH, cls: "warnc" });
            list.push({ k: "FORWARD SAMPLE", v: String(f.resolved_entries || 0) });
            list.push({ k: "ELIGIBLE WINDOWS", v: String(f.eligible_windows || 0) });
            list.push({ k: "ENTRY EVENTS", v: String(f.total_entry_events) });
            list.push({ k: "COVERAGE", v: F.pct(f.coverage) });
            list.push({ k: "RESOLVED", v: String(f.resolved_entries) });
            list.push({ k: "ACCURACY", v: F.has(f.classification_accuracy) ? F.pct(f.classification_accuracy, 1) + ci : "INSUFFICIENT SAMPLE" });
            list.push({ k: "DRIFT STATUS", v: f.drift ? F.words(f.drift.status) : "INSUFFICIENT SAMPLE", cls: "warnc" });
            list.push({ k: "DATA HOLDS / PROVIDER", v: (f.data_hold_count || 0) + " / " + (f.provider_failure_count || 0) });
            var yes = f.yes || {}, no = f.no || {};
            list.push({ k: "YES ACCURACY", v: F.pct(yes.accuracy) + " (n=" + (yes.n || 0) + ")" });
            list.push({ k: "NO ACCURACY", v: F.pct(no.accuracy) + " (n=" + (no.n || 0) + ")" });
        }
        list.push(null);
        list.push({ k: "HISTORICAL PROXY HOLDOUT", v: "PHASE 6 — SEPARATE" });
        if (h) {
            list.push({ k: "ACCURACY", v: F.pct(h.historical_accuracy, 2) });
            list.push({ k: "COVERAGE", v: F.pct(h.historical_coverage, 2) });
            list.push({ k: "REGIME", v: F.words(h.status),
                        cls: /SHIFT/.test(String(h.status)) ? "warnc" : "" });
        } else {
            list.push({ k: "ACCURACY / COVERAGE", v: DASH });
        }
        list.push(null);
        list.push({ k: "SAMPLES ARE NEVER COMBINED", v: "" });
        rows(document.getElementById("fwd_rows"), list);
    }

    function renderDiagnostics(snap) {
        var v = focused(snap) || {};
        var s = snap.status;
        rows(document.getElementById("diag_rows"), [
            { k: "SIGMA (WINDOW)", v: F.num(v.volatility_estimate, 6) },
            { k: "BUFFER Z", v: F.num(v.buffer_z, 3) },
            { k: "VOL REGIME", v: v.volatility_regime || "UNKNOWN" },
            { k: "CROSSING PROB", v: F.pct(v.crossing_probability, 2) },
            { k: "CROSSING COUNT", v: F.has(v.reference_crossings) ? String(v.reference_crossings) : DASH },
            { k: "FRAGILITY", v: F.num(v.fragility, 2) },
            { k: "DISAGREEMENT", v: F.num(v.disagreement, 4) },
            { k: "DATA AGE", v: F.num(v.data_age_seconds, 1, " s") },
            { k: "FETCH LATENCY", v: F.num(v.fetch_latency_seconds, 3, " s") },
            { k: "PROVIDER CHAIN", v: v.provider_mode || DASH },
            { k: "QUOTE VALIDATION", v: F.words(v.quote_status) },
            { k: "QUALITY", v: v.quality_reason || DASH },
            null,
            { k: "RAW CLASS REASON", v: v.classification_reason || DASH },
            { k: "RAW FINAL REASON", v: v.final_reason || DASH },
            { k: "MODEL", v: v.model_version || s.model_version },
            { k: "POLICY", v: s.policy_name },
            { k: "CONFIG HASH", v: String(v.config_hash || s.config_hash || DASH).slice(0, 16) },
            { k: "GIT COMMIT", v: String(v.git_commit || s.git_commit || DASH).slice(0, 12) }
        ]);
    }

    /* ---------------------------------------------------------- economics */

    function renderEconomics(snap) {
        var v = focused(snap) || {};
        document.getElementById("econ_asset").textContent = v.asset || DASH;
        var list;
        if (!v.economics_available) {
            // The safe fallback: say so, and show em-dashes rather than zeros.
            list = [
                { k: "EV ENGINE", v: "DISABLED", cls: "warnc" },
                { k: "QUOTE", v: "NO VERIFIED BOOK", cls: "warnc" },
                null,
                { k: "YES BID / ASK", v: DASH }, { k: "NO BID / ASK", v: DASH },
                { k: "BREAK EVEN", v: DASH },
                null,
                { k: "EDGE / LCB", v: DASH }, { k: "POINT EV", v: DASH },
                { k: "LCB EV", v: DASH }, { k: "RETURN / COST", v: DASH },
                null,
                { k: "QUOTE AGE", v: DASH },
                { k: "SOURCE", v: F.words(snap.status.economics_provider) },
                { k: "FEES / SLIP", v: "UNKNOWN FEES / NOT MODELED" }
            ];
        } else {
            list = [
                { k: "EV STATUS", v: F.words(v.ev_status),
                  cls: v.ev_status === "ROBUST_POSITIVE_EV" ? "ok" : "warnc" },
                { k: "QUOTE", v: F.words(v.quote_status) },
                null,
                { k: "YES BID / ASK", v: F.num(v.yes_bid) + " / " + F.num(v.yes_ask) },
                { k: "NO BID / ASK", v: F.num(v.no_bid) + " / " + F.num(v.no_ask) },
                { k: "BREAK EVEN", v: F.pct(v.break_even, 2) },
                null,
                { k: "EDGE / LCB", v: F.pct(v.model_edge, 2) + " / " + F.pct(v.lcb_edge, 2) },
                { k: "POINT EV", v: F.money(v.point_ev) },
                { k: "LCB EV", v: F.money(v.lcb_ev) },
                { k: "RETURN / COST", v: F.pct(v.expected_return_on_cost) },
                null,
                { k: "QUOTE AGE", v: F.num(v.quote_age, 1, " s") },
                { k: "SOURCE", v: F.words(snap.status.economics_provider) },
                { k: "FEES / SLIP", v: F.words(v.fees_status) + " / " +
                    F.words(String(v.slippage_status).replace("SLIPPAGE_", "")) }
            ];
        }
        rows(document.getElementById("econ_rows"), list);
    }

    /* ---------------------------------------------------------- providers */

    function renderProviders(snap) {
        var s = snap.status;
        rows(document.getElementById("provider_rows"), [
            { k: "UNDERLYING", html: dot(s.underlying_state) },
            { k: "WEBULL", html: dot(s.webull_status) },
            { k: "ECONOMICS", html: dot(s.economics_status) },
            { k: "REFERENCE", html: dot(s.reference_status) },
            { k: "QUOTE", html: dot(s.quote_status) },
            { k: "FORWARD LOG", html: dot(s.forward_logger_status) },
            { k: "HTTP / SSE", v: (s.http_server_status || "—") + " / " +
                F.val((s.host || {}).clients, "0") },
            { k: "BROWSER", html: dot(s.browser_shell_status) },
            null,
            { k: "AUDIO / VOICE", v: (s.audio_enabled ? "ON" : "OFF") + "  /  " + (s.voice_enabled ? "ON" : "OFF") }
        ]);
    }

    /* -------------------------------------------------------- connectivity */

    /* A provider connectivity topology, not a market topology. MANTIS publishes
     * no cross-asset correlation, so none is drawn: every node and every link
     * here represents a provider or asset state the backend actually reported. */
    function renderMap(snap) {
        var canvas = document.getElementById("assetmap");
        if (!canvas) return;
        var ratio = global.devicePixelRatio || 1;
        var w = canvas.clientWidth, h = canvas.clientHeight;
        if (!w || !h) return;
        if (canvas.width !== w * ratio || canvas.height !== h * ratio) {
            canvas.width = w * ratio; canvas.height = h * ratio;
        }
        var g = canvas.getContext("2d");
        g.setTransform(ratio, 0, 0, ratio, 0, 0);
        g.clearRect(0, 0, w, h);

        var cx = w / 2, cy = h * 0.46, coreR = Math.min(w, h) * 0.13;
        var phase = Date.now() / 1000;

        // Sweep ring -- moves only while the provider is actually live.
        var live = snap.status.underlying_state === "LIVE";
        g.strokeStyle = "rgba(170,207,209,0.12)";
        g.lineWidth = 1;
        [1.7, 2.5, 3.3].forEach(function (k) {
            g.beginPath(); g.arc(cx, cy, coreR * k, 0, Math.PI * 2); g.stroke();
        });
        if (live) {
            var sweep = (phase * 0.6) % (Math.PI * 2);
            g.beginPath();
            g.arc(cx, cy, coreR * 3.3, sweep, sweep + 0.5);
            g.strokeStyle = "rgba(170,207,209,0.5)";
            g.lineWidth = 1.5;
            g.stroke();
        }

        var assets = snap.assets || [];
        var colours = {
            enter_yes: "rgba(170,207,209,1)", enter_no: "rgba(110,168,255,1)",
            wait: "rgba(217,164,65,1)", no_trade: "rgba(170,207,209,0.35)",
            data_hold: "rgba(224,86,79,1)", unknown: "rgba(170,207,209,0.3)"
        };

        assets.forEach(function (v, i) {
            var angle = (Math.PI * 2 * i) / Math.max(1, assets.length) - Math.PI / 2;
            var radius = coreR * 2.6;
            var x = cx + Math.cos(angle) * radius, y = cy + Math.sin(angle) * radius;
            var colour = colours[v.final.key] || colours.unknown;

            g.beginPath();
            g.moveTo(cx + Math.cos(angle) * coreR, cy + Math.sin(angle) * coreR);
            g.lineTo(x, y);
            g.strokeStyle = v.has_data ? "rgba(170,207,209,0.35)" : "rgba(224,86,79,0.35)";
            g.setLineDash(v.has_data ? [] : [2, 3]);
            g.lineWidth = 1;
            g.stroke();
            g.setLineDash([]);

            // A packet travels the link only when that asset returned data.
            if (v.has_data) {
                var t = ((phase * 0.5) + i * 0.2) % 1;
                var px = cx + Math.cos(angle) * (coreR + (radius - coreR) * t);
                var py = cy + Math.sin(angle) * (coreR + (radius - coreR) * t);
                g.fillStyle = "rgba(170,207,209,0.8)";
                g.fillRect(px - 1, py - 1, 2, 2);
            }

            g.beginPath();
            g.arc(x, y, 2.6, 0, Math.PI * 2);
            g.fillStyle = colour;
            g.fill();
            if (v.asset === snap.focus) {
                g.beginPath(); g.arc(x, y, 5.5, 0, Math.PI * 2);
                g.strokeStyle = colour; g.lineWidth = 1; g.stroke();
            }

            g.font = "600 9px Bahnschrift, sans-serif";
            g.fillStyle = "rgba(170,207,209,0.75)";
            g.textAlign = "center";
            g.fillText(v.short || v.asset, x, y - 8);
        });

        // Core node.
        g.beginPath(); g.arc(cx, cy, coreR, 0, Math.PI * 2);
        g.strokeStyle = "rgba(170,207,209,0.7)"; g.lineWidth = 1.4; g.stroke();
        g.font = "700 10px Bahnschrift, sans-serif";
        g.fillStyle = "rgba(170,207,209,0.95)";
        g.textAlign = "center";
        g.fillText("MANTIS", cx, cy + 3);

        // Provider legend along the bottom.
        var legend = [
            ["UNDERLYING", snap.status.underlying_state],
            ["WEBULL", snap.status.webull_status],
            ["ECONOMICS", snap.status.economics_status]
        ];
        g.font = "8px Bahnschrift, sans-serif";
        g.textAlign = "left";
        legend.forEach(function (pair, i) {
            var y = h - 2 - (legend.length - 1 - i) * 10;
            var good = pair[1] === "LIVE";
            g.fillStyle = good ? "rgba(170,207,209,0.9)" : "rgba(217,164,65,0.9)";
            g.fillRect(2, y - 5, 4, 4);
            g.fillStyle = "rgba(170,207,209,0.6)";
            g.fillText(pair[0] + "  " + F.words(pair[1]), 10, y - 1);
        });
    }

    /* ------------------------------------------------------- asset matrix */

    function stateClass(key) {
        return { enter_yes: "ok", enter_no: "ok", wait: "warnc",
                 data_hold: "critc", no_trade: "" }[key] || "";
    }

    function renderMatrix(snap) {
        var html = "";
        (snap.assets || []).forEach(function (v) {
            var focus = v.asset === snap.focus ? " focus" : "";
            html +=
                '<div class="tile state-' + v.final.key + focus + '">' +
                '<span class="sym">' + esc(v.short) + "</span>" +
                '<span class="mid">' +
                  '<span class="px">' + (v.has_data ? esc(F.price(v.current_price)) : DASH) + "</span>" +
                  '<span class="px">' + esc(v.predicted_side || DASH) + "  " + esc(F.pct(v.p_yes)) + "</span>" +
                "</span>" +
                '<span class="st ' + stateClass(v.final.key) + '">' +
                  esc(v.final.glyph + " " + v.final.label) + "</span>" +
                "</div>";
        });
        document.getElementById("matrix_list").innerHTML = html;
    }

    /* -------------------------------------------------------- event stream */

    var lastEventKey = null;

    function renderStream(snap, rowLimit) {
        var events = (snap.events || []).slice(-(rowLimit || 14));
        var html = "";
        for (var i = events.length - 1; i >= 0; i--) {
            var e = events[i];
            var stamp = e.timestamp ? new Date(e.timestamp).toLocaleTimeString("en-GB") : "--:--:--";
            html += '<div class="ev sev-' + esc(e.severity) + '">' +
                    '<span class="t">' + esc(stamp) + "</span>" +
                    '<span class="s">' + esc(e.severity) + "</span>" +
                    '<span class="o">' + esc(e.source) + "</span>" +
                    '<span class="m">' + esc(e.message) + (e.detail ? "  ·  " + esc(e.detail) : "") + "</span>" +
                    "</div>";
        }
        html += '<div class="ev"><span class="t"></span><span class="s"></span>' +
                '<span class="o"></span><span class="m"><span id="cursor"></span></span></div>';
        document.getElementById("stream_list").innerHTML = html;

        var newest = events.length ? events[events.length - 1] : null;
        var key = newest ? newest.timestamp + newest.message + newest.source : null;
        var changed = key !== null && key !== lastEventKey;
        lastEventKey = key;
        return changed ? newest : null;
    }

    /* ------------------------------------------------------ band validation */

    function renderBandForward(snap) {
        var f = snap.forward, h = snap.historical;
        rows(document.getElementById("band_forward_rows"), !f ? [
            { k: "STATUS", v: "NO FORWARD RECORDS YET" }
        ] : [
            { k: "SAMPLE / STATUS", v: f.resolved_entries + " / " + (f.forward_status || f.sample_label || DASH), cls: "warnc" },
            { k: "ENTRIES / COVERAGE", v: f.total_entry_events + " / " + F.pct(f.coverage) },
            { k: "RESOLVED / HOLDS", v: f.resolved_entries + " / " + (f.data_hold_count || 0) },
            { k: "ACCURACY / DRIFT", v: (F.has(f.classification_accuracy) ? F.pct(f.classification_accuracy, 1) : "N/A") +
                (F.has(f.ci_low) ? "  [" + F.pct(f.ci_low) + ", " + F.pct(f.ci_high) + "]" : "") }
        ]);
        rows(document.getElementById("band_hist_rows"), h ? [
            { k: "ACCURACY / COVERAGE",
              v: F.pct(h.historical_accuracy, 2) + " / " + F.pct(h.historical_coverage, 2) },
            { k: "REGIME", v: F.words(h.status), cls: /SHIFT/.test(String(h.status)) ? "warnc" : "" }
        ] : [{ k: "ACCURACY / COVERAGE", v: DASH }]);
    }

    global.MantisModules = {
        renderClock: renderClock, renderIdentity: renderIdentity,
        renderEngine: renderEngine, renderTelemetry: renderTelemetry,
        renderProcess: renderProcess, renderContract: renderContract,
        renderManualSignalCenter: renderManualSignalCenter,
        renderSurveillance: renderSurveillance, renderForwardView: renderForwardView,
        renderDiagnostics: renderDiagnostics, renderEconomics: renderEconomics,
        renderProviders: renderProviders, renderMap: renderMap,
        renderMatrix: renderMatrix, renderStream: renderStream,
        renderBandForward: renderBandForward,
        pushHistory: pushHistory, focused: focused, history: history
    };
})(window);
