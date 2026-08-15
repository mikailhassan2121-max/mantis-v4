/* Display formatting only.
 *
 * Nothing in this file computes a decision quantity. It turns numbers the
 * backend already published into strings, and it renders an em-dash wherever a
 * value is genuinely absent -- never a zero, and never the previous scan's
 * value. That rule is what keeps a missing quote visibly missing.
 */

(function (global) {
    "use strict";

    var DASH = "—";

    function has(v) { return v !== null && v !== undefined && !Number.isNaN(v); }

    function num(v, places, suffix) {
        if (!has(v)) return DASH;
        return Number(v).toFixed(places === undefined ? 3 : places) + (suffix || "");
    }

    function pct(v, places) {
        if (!has(v)) return DASH;
        return (Number(v) * 100).toFixed(places === undefined ? 1 : places) + "%";
    }

    // Adaptive precision so BTC and ADA are both readable at a glance.
    function price(v) {
        if (!has(v)) return DASH;
        var m = Math.abs(Number(v));
        var places = m >= 1000 ? 2 : (m >= 1 ? 4 : 6);
        return Number(v).toLocaleString("en-US", {
            minimumFractionDigits: places, maximumFractionDigits: places
        });
    }

    function signed(v, places) {
        if (!has(v)) return DASH;
        var s = Number(v).toLocaleString("en-US", {
            minimumFractionDigits: places === undefined ? 4 : places,
            maximumFractionDigits: places === undefined ? 4 : places
        });
        return Number(v) >= 0 ? "+" + s : s;
    }

    function money(v) { return has(v) ? (Number(v) >= 0 ? "+" : "") + Number(v).toFixed(4) : DASH; }

    // T-MM:SS from a duration the backend supplied. Never a UI clock.
    function countdown(seconds) {
        if (!has(seconds)) return "T--:--";
        var total = Math.max(0, Math.floor(seconds));
        var m = Math.floor(total / 60), s = total % 60;
        return "T-" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }

    var MARKS = [300, 180, 120, 60, 30];
    function mark(seconds) {
        if (!has(seconds)) return "AWAITING FIRST SCAN";
        var crossed = MARKS.filter(function (m) { return seconds <= m; });
        return crossed.length ? "PAST T-" + Math.min.apply(null, crossed) : "OPEN WINDOW";
    }

    function words(code) { return code ? String(code).replace(/_/g, " ") : DASH; }

    function clock(date) {
        return {
            h: String(date.getHours()).padStart(2, "0"),
            m: String(date.getMinutes()).padStart(2, "0"),
            s: String(date.getSeconds()).padStart(2, "0")
        };
    }

    function duration(seconds) {
        if (!has(seconds)) return DASH;
        var t = Math.max(0, Math.floor(seconds));
        var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
        return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }

    global.F = {
        DASH: DASH, has: has, num: num, pct: pct, price: price, signed: signed,
        money: money, countdown: countdown, mark: mark, words: words,
        clock: clock, duration: duration
    };
})(window);
