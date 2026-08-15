/* SHG / MANTIS boot sequence.
 *
 * Choreography follows the cadence measured from eDEX-UI and recorded in
 * research/PHASE9_EDEX_RESEARCH.md: a forged monochrome identity chain, an
 * uneven kernel-log burst, a large derezz handoff, then an interface that
 * assembles itself module by module with one sound per module.
 *
 * The identity order is deliberate and is the one branding change that matters:
 *
 *     SHG            strategic umbrella
 *     SAAF VENTURES  venture layer
 *     MANTIS         operational intelligence subsystem
 *     COMMAND CENTER operational
 *
 * Everything here is presentation. The scanner has already been running for the
 * whole of this sequence; nothing in the boot path gates a scan.
 */

(function (global) {
    "use strict";

    var A = global.MantisAudio;

    function delay(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

    var BRANDING = [
        { image: "/branding/saaf_holdings_group.png", name: "SAAF HOLDINGS GROUP",
          role: "STRATEGIC UMBRELLA // IDENTITY 01", cue: "shg_boot", wide: true },
        { image: "/branding/saaf_ventures.png", name: "SAAF VENTURES",
          role: "VENTURE SYSTEMS // IDENTITY 02", cue: "panel_online", wide: true },
        { image: "/branding/mantis_darpa.png", name: "MANTIS",
          role: "OPERATIONAL INTELLIGENCE // IDENTITY 03", cue: "init_pulse", wide: false }
    ];

    function setPhase(screen, phase) {
        screen.dataset.phase = phase;
        global.dispatchEvent(new CustomEvent("mantis:boot-phase", { detail: phase }));
    }

    async function brandPrelude(screen, scale) {
        setPhase(screen, "prelude");
        var card = document.getElementById("brand_card");
        var image = document.getElementById("brand_image");
        var echo = document.getElementById("brand_echo");
        for (var i = 0; i < BRANDING.length; i++) {
            var brand = BRANDING[i];
            image.src = echo.src = brand.image;
            image.alt = brand.name;
            image.className = echo.className = brand.wide ? "wide" : "";
            document.getElementById("brand_index").textContent = "IDENT // 0" + (i + 1);
            document.getElementById("brand_role").textContent = brand.role;
            document.getElementById("brand_name").textContent = brand.name;
            document.getElementById("brand_status").textContent = "RECONSTRUCTING IDENTITY";
            card.className = "brand-card";
            void card.offsetWidth;
            card.classList.add("acquire");
            A.play(brand.cue);
            await delay(340 * scale);
            document.getElementById("brand_status").textContent = "IDENTITY VERIFIED";
            await delay(900 * scale);
            card.classList.add("release");
            await delay(310 * scale);
        }
    }

    async function identityHandoff(screen, scale) {
        setPhase(screen, "handoff");
        var lines = document.querySelectorAll(".handoff-line");
        for (var i = 0; i < lines.length; i++) {
            lines[i].classList.add("on");
            A.play("tick");
            await delay(320 * scale);
        }
        document.querySelector(".handoff-lock").classList.add("on");
        A.play("shg_boot");
        await delay(720 * scale);
    }

    // A fake-but-plausible initialization log. It names real MANTIS subsystems
    // so the text is honest about what is starting, and it is decoration only:
    // no line here reports the result of a check.
    var LOG = [
        "SHG SECURE BOOT // INTELLIGENCE SYSTEMS DIVISION",
        "shg_loader: verifying platform integrity",
        "shg_loader: signature chain OK",
        "",
        "memory map established, 64-bit long mode",
        "scheduler: preemptive, quantum 10000 us",
        "hpet: high precision event timer enabled",
        "tsc: deadline timer supported and enabled",
        "entropy pool seeded from platform RNG",
        "SHGSEC: policy module 'observation-only' loaded",
        "SHGSEC: policy module 'no-execution-path' loaded",
        "SHGSEC: enforcing -- outbound order routing DENIED",
        "vfs: mounting append-only forward store",
        "vfs: data/forward observations.jsonl  [append-only]",
        "vfs: data/forward entries.jsonl       [append-only]",
        "vfs: data/forward resolutions.jsonl   [append-only]",
        "vfs: data/forward provider_health.jsonl",
        "vfs: data/forward runs.jsonl",
        "vfs: integrity check complete, 0 truncated records",
        "",
        "SHG subsystem registry:",
        "  [0] shg.core.clock              READY",
        "  [1] shg.core.telemetry          READY",
        "  [2] shg.net.providers           READY",
        "  [3] mantis.quant                LOADING",
        "",
        "mantis.quant: loading resolution model",
        "mantis.quant: model NORMAL_Z_PHASE6_LOCKED",
        "mantis.quant: classification policy locked",
        "mantis.quant: fragility estimator online",
        "mantis.quant: disagreement estimator online",
        "mantis.quant: crossing-risk estimator online",
        "mantis.econ: contract economics engine online",
        "mantis.econ: fee model UNKNOWN_FEES",
        "mantis.econ: slippage NOT_MODELED",
        "mantis.fwd: forward validation logger attached",
        "mantis.fwd: observation schema v8 verified",
        "",
        "providers: underlying market data      BINDING",
        "providers: reference source            PROXY_UNVERIFIED",
        "providers: webull contract quotes      AUTH_NOT_CONFIGURED",
        "",
        "ui: presentation layer detached from quant loop",
        "ui: renderer isolation verified",
        "ui: audio subsystem synthesized, 12 cues registered",
        "ui: scanline compositor enabled",
        "",
        "OBSERVATION ONLY -- NO AUTOMATED EXECUTION",
        "MANTIS subsystem handoff accepted",
        "Boot Complete"
    ];

    /* Upstream's timing shape: slow open, fast burst, pause, fast burst, pause.
     * scale lets BOOT_DURATION stretch or compress the whole thing. */
    function lineDelay(i, total, scale) {
        var ms;
        if (i < 3) ms = 90;
        else if (i < 19) ms = 12;
        else if (i === 19) ms = 140;
        else if (i < 26) ms = 14;
        else if (i === 26) ms = 120;
        else if (i < total - 4) ms = 10;
        else ms = 120;
        return ms * scale;
    }

    function runLog(el, scale) {
        return new Promise(function (resolve) {
            var i = 0;
            (function step() {
                if (i >= LOG.length) { setTimeout(resolve, 150 * scale); return; }
                var line = LOG[i];
                var cls = "";
                if (line === "Boot Complete") { cls = " class=\"ok\""; A.play("shg_boot"); }
                else if (/DENIED|AUTH_NOT_CONFIGURED|PROXY_UNVERIFIED|OBSERVATION ONLY/.test(line)) {
                    cls = " class=\"warn\"";
                    A.play("tick");
                } else if (line !== "") {
                    A.play("tick");
                }
                el.insertAdjacentHTML("beforeend", "<div" + cls + ">" + (line || "&nbsp;") + "</div>");
                // Keep the newest line on screen without a scrollbar.
                while (el.childElementCount > 46) el.removeChild(el.firstElementChild);
                i++;
                setTimeout(step, lineDelay(i, LOG.length, scale));
            })();
        });
    }

    /* A large identity slab that fills, outlines, tears, then settles.
     * Matches the upstream fill -> outline -> glitch -> settle progression. */
    async function identity(screen, text, subtitle, scale, cue) {
        screen.innerHTML = "";
        screen.className = "center";
        if (cue) A.play(cue);

        await delay(70 * scale);
        screen.innerHTML = '<h1 data-text="' + text + '">' + text +
            (subtitle ? '<span class="sub">' + subtitle + "</span>" : "") + "</h1>";
        var h1 = screen.querySelector("h1");

        await delay(160 * scale);
        document.body.className = "solid";
        h1.style.background = "rgb(var(--c))";
        h1.style.color = "var(--bg)";
        h1.style.borderBottom = "0.46vh solid rgb(var(--c))";

        await delay(180 * scale);
        h1.style.background = "transparent";
        h1.style.color = "";
        h1.style.border = "0.28vh solid rgb(var(--c))";

        await delay(90 * scale);
        h1.style.border = "";
        h1.classList.add("glitch");
        document.body.classList.add("flicker");

        await delay(220 * scale);
        h1.classList.remove("glitch");
        document.body.classList.remove("flicker");
        document.body.className = global.MANTIS_GRID === false ? "solid" : "grid";
        h1.style.border = "0.28vh solid rgb(var(--c))";

        await delay(180 * scale);
    }

    /* The interface builds itself: the centre pane unfolds first, then the rail
     * modules pop in one at a time with a sound each -- upstream's 500ms panel
     * cadence, scaled. */
    async function assemble(scale, onReady) {
        var shell = document.getElementById("shell");
        var workspace = document.getElementById("workspace");
        var screen = document.getElementById("boot_screen");

        screen.classList.add("transparent");
        workspace.classList.add("folded");
        shell.classList.add("live");

        await delay(40 * scale);
        A.play("init_pulse");
        workspace.classList.remove("folded");
        workspace.classList.add("on");

        await delay(300 * scale);
        screen.remove();

        // Modules populate in a deliberate order: identity and time first, then
        // the data path, then the instruments, then the lower band.
        var order = [
            "mod_clock", "mod_identity", "mod_economics",
            "mod_engine", "mod_providers", "mod_telemetry",
            "mod_assetmap", "mod_process", "mod_matrix",
            "mod_stream", "mod_forward"
        ];
        for (var i = 0; i < order.length; i++) {
            var node = document.getElementById(order[i]);
            if (node) {
                node.classList.add("on");
                A.play("panel_online");
            }
            await delay(60 * scale);
        }

        await delay(150 * scale);
        A.play("system_ready");
        if (onReady) onReady();
    }

    /* Unscaled: 4.65s brand forge + 1.68s identity handoff + 0.15s opening +
     * ~1.66s technical log + 1.8s wordmarks + 1.15s assembly. The default
     * stretches this deliberate choreography to fifteen seconds. */
    var NOMINAL_SECONDS = 11.09;

    async function run(options) {
        var opts = options || {};
        var scale = Math.max(0.15, (opts.duration || 15.0) / NOMINAL_SECONDS);
        var screen = document.getElementById("boot_screen");
        var log = document.getElementById("boot_log");

        if (!opts.enabled) {
            if (screen) screen.remove();
            document.body.className = global.MANTIS_GRID === false ? "solid" : "grid";
            document.getElementById("shell").classList.add("live");
            document.querySelectorAll(".module").forEach(function (m) { m.classList.add("on"); });
            if (opts.onReady) opts.onReady();
            return;
        }

        document.body.className = "solid";
        await brandPrelude(screen, scale);
        await identityHandoff(screen, scale);
        setPhase(screen, "boot");
        await delay(150 * scale);
        await runLog(log, scale);
        await identity(screen, "SHG", "SAAF HOLDINGS GROUP // INTELLIGENCE SYSTEMS", scale, "shg_boot");
        await identity(screen, "MANTIS",
            "MARKET ANALYSIS AND NEURO-TACTICAL INTRADAY SIGNAL SYSTEM", scale, "init_pulse");
        await assemble(scale, opts.onReady);
    }

    global.MantisBoot = { run: run, LOG: LOG, BRANDING: BRANDING };
})(window);
