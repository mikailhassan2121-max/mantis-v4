/* MANTIS cue synthesis.
 *
 * Every cue is generated at runtime from oscillators, filtered noise and gain
 * envelopes. No sound file is loaded, shipped or derived from any other
 * project, so the cue set is original by construction and carries no licensing
 * obligation -- while keeping the functional character the brief asked for:
 * short digital pulses, a soft terminal tick for routine activity, clean
 * synthetic chirps for state changes, a restrained low impact for system ready.
 *
 * Audio is advisory. It is asynchronous, it is optional, and nothing here can
 * reach or delay the quantitative loop -- this file runs in a browser that only
 * ever receives snapshots.
 */

(function (global) {
    "use strict";

    function Cues() {
        this.ctx = null;
        this.master = null;
        this.enabled = true;
        this.volume = 0.7;
        this.lastPlayed = Object.create(null);
    }

    Cues.prototype.init = function (enabled, volume) {
        this.enabled = enabled !== false;
        if (typeof volume === "number") this.volume = volume;
        if (!this.enabled || this.ctx) return;
        var Ctor = global.AudioContext || global.webkitAudioContext;
        if (!Ctor) { this.enabled = false; return; }
        try {
            this.ctx = new Ctor();
            this.master = this.ctx.createGain();
            this.master.gain.value = this.volume;
            this.master.connect(this.ctx.destination);
        } catch (e) {
            this.enabled = false;
        }
    };

    Cues.prototype.resume = function () {
        if (this.ctx && this.ctx.state === "suspended") this.ctx.resume();
    };

    /* ---------------------------------------------------------- primitives */

    // One enveloped oscillator voice.
    Cues.prototype._tone = function (o) {
        if (!this.ctx) return;
        var t0 = this.ctx.currentTime + (o.at || 0);
        var osc = this.ctx.createOscillator();
        var gain = this.ctx.createGain();
        osc.type = o.type || "sine";
        osc.frequency.setValueAtTime(o.from, t0);
        if (o.to && o.to !== o.from) {
            osc.frequency.exponentialRampToValueAtTime(Math.max(1, o.to), t0 + o.dur);
        }
        var peak = (o.gain === undefined ? 0.28 : o.gain);
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(peak, t0 + Math.min(0.012, o.dur * 0.3));
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + o.dur);

        var node = osc;
        if (o.filter) {
            var biquad = this.ctx.createBiquadFilter();
            biquad.type = o.filter;
            biquad.frequency.value = o.filterFreq || 1800;
            osc.connect(biquad);
            node = biquad;
        }
        node.connect(gain);
        gain.connect(this.master);
        osc.start(t0);
        osc.stop(t0 + o.dur + 0.02);
    };

    // Filtered noise burst -- used for ticks, sweeps and the boot texture.
    Cues.prototype._noise = function (o) {
        if (!this.ctx) return;
        var t0 = this.ctx.currentTime + (o.at || 0);
        var dur = o.dur;
        var frames = Math.max(1, Math.floor(this.ctx.sampleRate * dur));
        var buffer = this.ctx.createBuffer(1, frames, this.ctx.sampleRate);
        var data = buffer.getChannelData(0);
        for (var i = 0; i < frames; i++) data[i] = Math.random() * 2 - 1;

        var src = this.ctx.createBufferSource();
        src.buffer = buffer;
        var biquad = this.ctx.createBiquadFilter();
        biquad.type = o.filter || "bandpass";
        biquad.frequency.setValueAtTime(o.from || 2000, t0);
        if (o.to) biquad.frequency.exponentialRampToValueAtTime(Math.max(1, o.to), t0 + dur);
        biquad.Q.value = o.q || 1.2;

        var gain = this.ctx.createGain();
        var peak = (o.gain === undefined ? 0.16 : o.gain);
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(peak, t0 + Math.min(0.01, dur * 0.25));
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

        src.connect(biquad);
        biquad.connect(gain);
        gain.connect(this.master);
        src.start(t0);
        src.stop(t0 + dur + 0.02);
    };

    /* --------------------------------------------------------------- cues */

    var RECIPES = {
        // Boot texture: one soft tick per log line. Deliberately tiny.
        tick: function (a) {
            a._noise({ dur: 0.018, from: 2600, to: 1500, q: 2.0, gain: 0.05 });
        },
        // SHG identity: low swell plus a rising synthetic sweep.
        shg_boot: function (a) {
            a._tone({ type: "sine", from: 55, to: 110, dur: 1.5, gain: 0.3 });
            a._tone({ type: "triangle", from: 220, to: 660, dur: 1.1, gain: 0.1 });
            a._noise({ dur: 1.2, from: 300, to: 5200, q: 0.7, gain: 0.07 });
        },
        // Each initialization beat during assembly.
        init_pulse: function (a) {
            a._tone({ type: "square", from: 880, to: 1320, dur: 0.07, gain: 0.09, filter: "lowpass", filterFreq: 2600 });
        },
        // A module coming online.
        panel_online: function (a) {
            a._tone({ type: "triangle", from: 660, dur: 0.05, gain: 0.12 });
            a._tone({ type: "triangle", from: 990, dur: 0.07, gain: 0.09, at: 0.045 });
        },
        // Routine scan activity.
        scan: function (a) {
            a._noise({ dur: 0.05, from: 1200, to: 3400, q: 3.0, gain: 0.045 });
        },
        // Actionable advisory, positive side: ascending pair.
        enter_yes: function (a) {
            a._tone({ type: "triangle", from: 784, dur: 0.09, gain: 0.3 });
            a._tone({ type: "triangle", from: 1175, dur: 0.16, gain: 0.26, at: 0.09 });
            a._tone({ type: "sine", from: 196, dur: 0.22, gain: 0.16 });
        },
        // Actionable advisory, negative side: descending pair, clearly distinct.
        enter_no: function (a) {
            a._tone({ type: "triangle", from: 784, dur: 0.09, gain: 0.3 });
            a._tone({ type: "triangle", from: 523, dur: 0.18, gain: 0.26, at: 0.09 });
            a._tone({ type: "sine", from: 165, dur: 0.24, gain: 0.16 });
        },
        // Technical hold: flat low double, no alarm colour.
        data_hold: function (a) {
            a._tone({ type: "square", from: 330, dur: 0.1, gain: 0.14, filter: "lowpass", filterFreq: 1200 });
            a._tone({ type: "square", from: 330, dur: 0.1, gain: 0.14, filter: "lowpass", filterFreq: 1200, at: 0.14 });
        },
        // Degraded capability.
        warning: function (a) {
            a._tone({ type: "triangle", from: 440, dur: 0.11, gain: 0.2 });
            a._tone({ type: "triangle", from: 349, dur: 0.17, gain: 0.18, at: 0.12 });
        },
        // New contract window: one soft mid tone.
        rollover: function (a) {
            a._tone({ type: "sine", from: 587, dur: 0.09, gain: 0.16 });
            a._noise({ dur: 0.09, from: 1800, to: 900, q: 2.2, gain: 0.04 });
        },
        // Something concluded.
        resolution: function (a) {
            a._tone({ type: "sine", from: 659, dur: 0.07, gain: 0.18 });
            a._tone({ type: "sine", from: 988, dur: 0.13, gain: 0.16, at: 0.07 });
        },
        // Provider or system failure.
        critical: function (a) {
            for (var i = 0; i < 3; i++) {
                a._tone({ type: "square", from: 262, dur: 0.13, gain: 0.2,
                          filter: "lowpass", filterFreq: 900, at: i * 0.17 });
            }
        },
        // Restrained bass impact once the command center is live.
        system_ready: function (a) {
            a._tone({ type: "sine", from: 110, to: 55, dur: 1.0, gain: 0.34 });
            a._tone({ type: "triangle", from: 440, dur: 0.1, gain: 0.16 });
            a._tone({ type: "triangle", from: 659, dur: 0.14, gain: 0.14, at: 0.1 });
            a._tone({ type: "triangle", from: 880, dur: 0.5, gain: 0.12, at: 0.22 });
        }
    };

    Cues.prototype.play = function (name, minInterval) {
        if (!this.enabled || !this.ctx) return false;
        var recipe = RECIPES[name];
        if (!recipe) return false;
        var now = Date.now();
        if (minInterval) {
            var last = this.lastPlayed[name] || 0;
            if (now - last < minInterval * 1000) return false;
        }
        this.lastPlayed[name] = now;
        try { recipe(this); } catch (e) { return false; }
        return true;
    };

    Cues.prototype.names = function () { return Object.keys(RECIPES); };

    global.MantisAudio = new Cues();
})(window);
