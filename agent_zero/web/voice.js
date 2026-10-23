// Capture/playback and UI lifecycle only. Wake matching, STT and agent logic are Python.
export class VoiceSession {
  constructor({ request, getState, onStatus, onError, onSetup, onTrap, onWake = () => {}, isActive = () => true }) {
    Object.assign(this, { request, getState, onStatus, onError, onSetup, onTrap, onWake, isActive });
    this.phase = "idle";
    this.muted = false;
    this.level = 0;
    this.epoch = 0;
    this.capture = null;
    this.controller = null;
    this.wakeEnabled = false;
    this.interactive = false;
    this.paused = false;
    this.wakeTimer = null;
  }

  setPhase(phase) {
    this.phase = phase;
    this.onStatus(phase);
  }

  baseline() {
    const state = this.getState();
    return state?.mode !== "userspace" ? "suspended" : state?.voice?.input_ready ? "idle" : "unavailable";
  }

  rest() {
    clearTimeout(this.wakeTimer);
    if ((this.interactive || this.wakeEnabled) && !this.paused && this.isActive() && this.baseline() === "idle") {
      this.setPhase(this.interactive ? "awake" : "dormant");
      this.wakeTimer = setTimeout(() => {
        if ((this.interactive || this.wakeEnabled) && !this.paused && this.isActive()) {
          void this.start({ kind: this.interactive ? "turn" : "wake", automatic: true, followup: this.interactive });
        }
      }, 300);
    } else {
      this.setPhase((this.interactive || this.wakeEnabled) && this.baseline() === "idle" ? "paused" : this.baseline());
    }
  }

  sync(state) {
    if (state.mode !== "userspace") {
      if (this.phase !== "suspended") void this.pause();
    } else if (["idle", "unavailable", "suspended", "paused"].includes(this.phase)) {
      if ((this.interactive || this.wakeEnabled) && this.isActive()) { this.paused = false; this.rest(); }
      else this.setPhase(this.baseline());
    }
  }

  async toggleWake() {
    if (this.wakeEnabled) { await this.cancel(); return; }
    if (!this.getState()?.voice?.input_ready) { this.onSetup(); return; }
    this.wakeEnabled = true;
    this.interactive = false;
    this.paused = false;
    this.onWake(true);
    await this.start({ kind: "wake" });
  }

  async toggle() {
    if (this.capture?.kind === "turn") await this.stop();
    else await this.start();
  }

  async start({ kind = "turn", automatic = false, followup = false } = {}) {
    if (this.phase === "starting" || (automatic && this.capture)) return;
    const state = this.getState();
    if (!state || state.mode !== "userspace") { this.onError("Voice conversation is paused in Cardinal."); return; }
    if (!state.voice?.input_ready) { this.onSetup(); return; }
    if (automatic && (!this.isActive() || this.paused)) { this.rest(); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !window.AudioContext) {
      this.onError("This browser does not support local microphone capture.");
      await this.cancel();
      return;
    }
    await this.cancel({ disableWake: false });
    if (automatic && ((!this.interactive && !this.wakeEnabled) || this.paused || !this.isActive())) return;
    this.paused = false;
    if (kind === "turn") this.interactive = true;
    const epoch = ++this.epoch;
    this.setPhase("starting");
    let stream, context;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      if (epoch !== this.epoch) { stream.getTracks().forEach(track => track.stop()); return; }
      context = new AudioContext();
      await context.resume();
      if (epoch !== this.epoch) { stream.getTracks().forEach(track => track.stop()); await context.close(); return; }
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"].find(type => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error("No supported microphone recording format is available.");
      const recorder = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 32000 });
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      const source = context.createMediaStreamSource(stream);
      source.connect(analyser);
      const capture = { stream, context, recorder, source, analyser, epoch, kind, followup, chunks: [], bytes: 0, discard: false, started: performance.now(), lastSound: performance.now(), heardSound: false };
      this.capture = capture;
      recorder.ondataavailable = event => {
        if (event.data.size) { capture.chunks.push(event.data); capture.bytes += event.data.size; }
        if (capture.bytes > 768000) { capture.discard = true; void this.cancel(); this.onError("Voice clip exceeded the size limit."); }
      };
      recorder.onstop = () => void this.finish(capture);
      recorder.onerror = () => { capture.discard = true; void this.cancel(); this.onError("Microphone recording failed."); };
      recorder.start(250);
      this.setPhase(kind === "wake" ? "dormant" : "listening");
      const samples = new Float32Array(analyser.fftSize);
      capture.timer = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        this.level = kind === "wake" ? 0 : Math.min(1, rms * 9);
        const now = performance.now();
        if (rms > 0.025) { capture.heardSound = true; capture.lastSound = now; }
        if (!capture.heardSound && kind === "wake" && now - capture.started > 4000) {
          capture.discard = true; void this.stop();
        } else if (!capture.heardSound && followup && now - capture.started > 6000) {
          this.interactive = false; capture.discard = true; void this.stop();
        } else if (now - capture.started > 15000 || (capture.heardSound && now - capture.lastSound > (kind === "wake" ? 700 : 1300) && now - capture.started > 1000)) {
          void this.stop();
        }
      }, 80);
    } catch (error) {
      stream?.getTracks().forEach(track => track.stop());
      if (context && context.state !== "closed") await context.close();
      if (this.capture?.epoch === epoch) { clearInterval(this.capture.timer); this.capture = null; }
      if (epoch !== this.epoch) return;
      this.wakeEnabled = false;
      this.interactive = false;
      this.onWake(false);
      this.setPhase(this.baseline());
      this.onError(error.name === "NotAllowedError" ? "Microphone access was denied. Enable it in the browser’s site settings to speak." : "Could not open the microphone. Check the input device and browser permissions.");
    }
  }

  async stop() {
    const capture = this.capture;
    if (!capture || capture.stopping) return;
    capture.stopping = true;
    if (capture.recorder.state !== "inactive") capture.recorder.stop();
    else void this.finish(capture);
    await this.release(capture);
  }

  async release(capture) {
    clearInterval(capture.timer);
    this.level = 0;
    if (!capture.cleanup) {
      capture.stream.getTracks().forEach(track => track.stop());
      capture.source.disconnect();
      capture.cleanup = capture.context.state !== "closed" ? capture.context.close() : Promise.resolve();
    }
    await capture.cleanup;
  }

  async finish(capture) {
    if (capture.finished) return;
    capture.finished = true;
    await this.release(capture);
    if (this.capture === capture) this.capture = null;
    if (capture.discard || capture.epoch !== this.epoch || !capture.bytes) {
      capture.chunks.length = 0;
      if (capture.epoch === this.epoch) this.rest();
      return;
    }
    this.setPhase(capture.kind === "wake" ? "checking" : "thinking");
    this.controller = new AbortController();
    try {
      const blob = new Blob(capture.chunks, { type: capture.recorder.mimeType });
      const audio = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      if (capture.epoch !== this.epoch) return;
      let result = await this.request(capture.kind === "auth" ? "voice_auth" : capture.kind === "wake" ? "wake_probe" : "voice_turn", { audio, content_type: blob.type }, this.controller.signal);
      if (capture.epoch !== this.epoch) return;
      if (capture.kind === "auth") { this.rest(); this.onAuthenticated?.(result); return; }
      if (capture.kind === "wake") {
        if (result.awake !== true) { this.rest(); return; }
        this.interactive = true;
        this.setPhase("awake");
        if (!result.command) { this.rest(); return; }
        this.setPhase("thinking");
        result = await this.request("chat", { text: result.command }, this.controller.signal);
        if (capture.epoch !== this.epoch) return;
      }
      if (result.status === "interrupted") { this.setPhase("suspended"); this.onTrap(); return; }
      if (result.sleep) {
        this.interactive = false;
        if (result.disable_wake) await this.cancel();
        else this.rest();
        return;
      }
      // A microphone click starts the same conversational session as a wake
      // phrase, without silently enabling dormant wake-word monitoring.
      this.interactive = true;
      await this.speak(result.message, capture.epoch);
    } catch (error) {
      if (capture.epoch === this.epoch && error.name !== "AbortError") {
        // Stop on setup/decoder errors rather than repeatedly loading a bad model.
        this.interactive = false;
        if (capture.kind === "wake") { this.wakeEnabled = false; this.onWake(false); }
        this.rest();
        this.onError(error.message || "The voice turn failed. Check voice settings.");
      }
    } finally {
      capture.chunks.length = 0;
      if (capture.epoch === this.epoch) this.controller = null;
    }
  }

  async speak(text, epoch) {
    if (this.muted || !text) { this.rest(); return; }
    if (!("speechSynthesis" in window)) { this.rest(); this.onError("Local speech output is unavailable in this browser."); return; }
    let local = speechSynthesis.getVoices().find(voice => voice.localService && /^en/.test(voice.lang));
    if (!local) { await new Promise(resolve => setTimeout(resolve, 500)); local = speechSynthesis.getVoices().find(voice => voice.localService && /^en/.test(voice.lang)); }
    if (epoch !== this.epoch) return;
    if (!local) { this.rest(); this.onError("Install an English system voice to hear JARVIS. No cloud voice was used."); return; }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = local;
    utterance.rate = 1.04;
    utterance.onend = () => { if (epoch === this.epoch) this.rest(); };
    utterance.onerror = event => { if (epoch === this.epoch) { this.rest(); if (event.error !== "canceled" && event.error !== "interrupted") this.onError("Local speech playback failed."); } };
    speechSynthesis.cancel();
    this.setPhase("speaking");
    speechSynthesis.speak(utterance);
  }

  async cancel({ disableWake = true } = {}) {
    clearTimeout(this.wakeTimer);
    if (disableWake) { this.wakeEnabled = false; this.interactive = false; this.onWake(false); }
    const pending = !!this.controller || ["thinking", "checking", "starting"].includes(this.phase);
    this.epoch += 1;
    if (this.capture) { this.capture.discard = true; await this.stop(); }
    this.controller?.abort();
    this.controller = null;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    this.level = 0;
    this.setPhase(this.baseline());
    if (pending) { try { await this.request("voice_cancel"); } catch {} }
  }

  async pause() {
    this.paused = true;
    await this.cancel({ disableWake: false });
    this.setPhase(this.baseline() === "suspended" ? "suspended" : (this.interactive || this.wakeEnabled) ? "paused" : this.baseline());
  }

  resume() {
    this.paused = false;
    if (this.capture || this.controller || ["starting", "speaking", "thinking", "checking"].includes(this.phase)) return;
    if ((this.interactive || this.wakeEnabled) && this.isActive()) this.rest();
  }

  toggleMute() {
    this.muted = !this.muted;
    if (this.muted && "speechSynthesis" in window) {
      speechSynthesis.cancel();
      if (this.phase === "speaking") this.rest();
    }
    return this.muted;
  }
}
