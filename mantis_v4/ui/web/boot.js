/* SHG / MANTIS boot sequence.
 *
 * A quiet OEM-style identity prelude precedes the eDEX-inspired technical
 * boot: three monochrome marks on black, an uneven kernel-log burst, then an interface that
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
        { image: "/branding/processed/saaf_holdings_group.png", name: "SAAF HOLDINGS GROUP", key: "shg",
          role: "STRATEGIC UMBRELLA // IDENTITY 01", cue: "shg_boot", wide: true },
        { image: "/branding/processed/saaf_ventures.png", name: "SAAF VENTURES", key: "ventures",
          role: "VENTURE SYSTEMS // IDENTITY 02", cue: "panel_online", wide: true },
        { image: "/branding/processed/mantis_darpa.png", name: "MANTIS", key: "mantis",
          role: "OPERATIONAL INTELLIGENCE // IDENTITY 03", cue: "init_pulse", wide: false }
    ];

    function setPhase(screen, phase) {
        screen.dataset.phase = phase;
        document.body.classList.toggle("brand-prelude", phase === "prelude");
        global.dispatchEvent(new CustomEvent("mantis:boot-phase", { detail: phase }));
    }

    function mark(name, detail) {
        global.dispatchEvent(new CustomEvent("mantis:startup-mark", {
            detail: { name: name, detail: detail || "", at: performance.now() }
        }));
    }

    function withTimeout(promise, milliseconds) {
        return Promise.race([promise, delay(milliseconds).then(function () { return false; })]);
    }

    async function preloadBranding() {
        var jobs = BRANDING.map(function (brand) {
            var candidate = new Image();
            brand.available = false;
            return new Promise(function (resolve) {
                candidate.onload = function () {
                    var decoded = candidate.decode ? candidate.decode().catch(function () {}) : Promise.resolve();
                    decoded.then(function () { brand.available = true; resolve(true); });
                };
                candidate.onerror = function () { resolve(false); };
                candidate.src = brand.image;
            });
        });
        var fonts = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
        await withTimeout(Promise.all([Promise.all(jobs), fonts]), 2500);
        mark("ASSETS_PRELOADED", BRANDING.filter(function (b) { return b.available; }).length + "/3");
        return BRANDING;
    }

    async function brandPrelude(screen, durationSeconds) {
        setPhase(screen, "prelude");
        var card = document.getElementById("brand_card");
        var image = document.getElementById("brand_image");
        var scale = Math.max(0.05, durationSeconds / 13.0);
        var stages = [
            { fadeIn: 800, hold: 2500, fadeOut: 700 },
            { fadeIn: 800, hold: 2500, fadeOut: 700 },
            { fadeIn: 800, hold: 3000, fadeOut: 800 }
        ];
        for (var i = 0; i < BRANDING.length; i++) {
            var brand = BRANDING[i];
            var timing = stages[i];
            mark("BRAND_" + (i + 1) + "_START", brand.name);
            card.className = "brand-card";
            card.style.transition = "none";
            image.alt = brand.name;
            image.className = brand.wide ? "wide" : "";
            document.getElementById("brand_name").textContent = brand.name;
            if (brand.available) {
                image.hidden = false;
                image.src = brand.image;
            } else {
                image.hidden = true;
                card.classList.add("fallback");
                global.dispatchEvent(new CustomEvent("mantis:brand-missing", { detail: brand.name }));
            }
            await new Promise(function (resolve) { global.requestAnimationFrame(resolve); });
            card.style.transition = "opacity " + (timing.fadeIn * scale) +
                "ms ease, transform " + (timing.fadeIn * scale) + "ms ease";
            card.classList.add("visible");
            A.play(brand.cue);
            await delay((timing.fadeIn + timing.hold) * scale);
            card.style.transition = "opacity " + (timing.fadeOut * scale) +
                "ms ease, transform " + (timing.fadeOut * scale) + "ms ease";
            card.classList.remove("visible");
            await delay(timing.fadeOut * scale);
            mark("BRAND_" + (i + 1) + "_END", brand.name);
            if (i < BRANDING.length - 1) await delay(200 * scale);
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
                } else if (line !== "" && i % 4 === 0) {
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
        setPhase(screen, "identity");
        screen.classList.add("center");
        if (cue) A.play(cue);

        await delay(70 * scale);
        var h1 = document.getElementById("identity_title");
        document.getElementById("identity_word").textContent = text;
        document.getElementById("identity_subtitle").textContent = subtitle || "";
        h1.dataset.text = text;
        h1.className = "";
        h1.removeAttribute("style");

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
        await delay(220 * scale);
        document.body.className = global.MANTIS_GRID === false ? "solid" : "grid";
        h1.style.border = "0.28vh solid rgb(var(--c))";

        await delay(180 * scale);
    }

    /* The interface builds itself: the centre pane unfolds first, then the rail
     * modules pop in one at a time with a sound each -- upstream's 500ms panel
     * cadence, scaled. */
    async function assemble(scale, options) {
        var shell = document.getElementById("shell");
        var workspace = document.getElementById("workspace");
        var screen = document.getElementById("boot_screen");

        setPhase(screen, "assembly");
        workspace.classList.add("folded");
        shell.classList.add("live");

        await delay(40 * scale);
        A.play("init_pulse");
        workspace.classList.remove("folded");
        workspace.classList.add("on");

        await delay(300 * scale);

        // The populated shell is still boot-locked. Apply module visibility in
        // one style batch instead of running eleven invisible transitions.
        document.querySelectorAll(".module").forEach(function (node) { node.classList.add("on"); });
        A.play("panel_online");
        await delay(660 * scale); // preserve the established 8.5s technical cadence
        setPhase(screen, "hydrating");
        mark("TECH_BOOT_END");
        if (options.onHydrate) await options.onHydrate();
        setPhase(screen, "settling");
        await delay(Math.max(0, Number(options.settleSeconds || 0)) * 1000);
        document.body.classList.add("operational-ready");
        await new Promise(function (resolve) {
            global.requestAnimationFrame(function () { global.requestAnimationFrame(resolve); });
        });
        if (screen.parentNode) screen.remove();
        A.play("system_ready");
        setPhase(document.body, "ready");
        if (options.onReady) options.onReady();
        mark("READY");
    }

    /* Unscaled: 4.65s brand forge + 1.68s identity handoff + 0.15s opening +
     * ~1.66s technical log + 1.8s wordmarks + 1.15s assembly. The default
     * stretches this deliberate choreography to fifteen seconds. */
    var TECHNICAL_NOMINAL_SECONDS = 6.44;

    async function run(options) {
        var opts = options || {};
        var technicalSeconds = Number(opts.technicalDuration || 8.5);
        var scale = Math.max(0.05, technicalSeconds / TECHNICAL_NOMINAL_SECONDS);
        var screen = document.getElementById("boot_screen");
        var log = document.getElementById("boot_log");

        if (!opts.enabled) {
            document.body.className = global.MANTIS_GRID === false ? "solid" : "grid";
            document.getElementById("shell").classList.add("live");
            document.querySelectorAll(".module").forEach(function (m) { m.classList.add("on"); });
            if (opts.onHydrate) await opts.onHydrate(true);
            document.body.classList.add("operational-ready");
            if (screen) screen.remove();
            if (opts.onReady) opts.onReady();
            return;
        }

        document.body.className = "solid";
        await preloadBranding();
        var debugStage = new URLSearchParams(global.location.search).get("bootStage");
        if (debugStage) {
            var brand = BRANDING.find(function (item) { return item.key === debugStage; });
            if (brand) {
                setPhase(screen, "prelude");
                var card = document.getElementById("brand_card");
                var image = document.getElementById("brand_image");
                document.getElementById("brand_name").textContent = brand.name;
                card.className = "brand-card visible" + (brand.available ? "" : " fallback");
                image.hidden = !brand.available;
                if (brand.available) image.src = brand.image;
                return new Promise(function () {});
            }
        }
        if (opts.brandPreludeEnabled !== false) {
            await brandPrelude(screen, Number(opts.brandPreludeDuration || 13.0));
        }
        await identityHandoff(screen, scale);
        setPhase(screen, "boot");
        mark("TECH_BOOT_START");
        await delay(150 * scale);
        await runLog(log, scale);
        await identity(screen, "SHG", "SAAF HOLDINGS GROUP // INTELLIGENCE SYSTEMS", scale, "shg_boot");
        await identity(screen, "MANTIS",
            "MARKET ANALYSIS AND NEURO-TACTICAL INTRADAY SIGNAL SYSTEM", scale, "init_pulse");
        await assemble(scale, opts);
    }

    global.MantisBoot = {
        run: run, LOG: LOG, BRANDING: BRANDING,
        BRAND_PRELUDE_NOMINAL_SECONDS: 13.0,
        TECHNICAL_NOMINAL_SECONDS: TECHNICAL_NOMINAL_SECONDS
    };
})(window);
