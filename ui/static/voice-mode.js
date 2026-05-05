/* ═══════════════════════════════════════════════════════════════
   J.A.R.V.I.S — Voice Mode (Web Speech API + Camera + Claude vision)
   ───────────────────────────────────────────────────────────────
   Pipeline:
     mic → Web Speech recognition → text
     [optional] camera frame → base64 JPEG
     → existing WebSocket {type:"message", content, images?}
     → existing agent.run() (with Claude vision when images present)
     → text stream events → speechSynthesis.speak() → user hears reply

   States (data-voice-state on #voiceReactor):
     idle | listening | userSpeaking | thinking | jarvisSpeaking | error

   Storage keys:
     jarvisVoiceMode    : 'on' | 'off'  (master toggle, default off)
     jarvisVoiceCamera  : 'on' | 'off'  (camera-during-voice, default on)
     jarvisVoiceVoiceId : <name>        (preferred TTS voice)
     jarvisVoiceRate    : '1.0' etc.    (TTS rate)

   Privacy: Mic audio + camera frames stay in the browser.
            Transcribed text + (optional) the latest camera frame are sent
            to the existing /ws WebSocket — same path text mode uses.
   ═══════════════════════════════════════════════════════════════ */

(function () {
  if (window.__voiceModeLoaded) return;
  window.__voiceModeLoaded = true;

  const PREF_ENABLED = 'jarvisVoiceMode';
  const PREF_CAMERA  = 'jarvisVoiceCamera';
  const PREF_VOICE   = 'jarvisVoiceVoiceId';
  const PREF_RATE    = 'jarvisVoiceRate';

  // Browser SpeechRecognition: webkit-prefixed in Chrome, standard elsewhere
  const SpeechRecog = window.SpeechRecognition || window.webkitSpeechRecognition;

  // Public API
  window.voiceMode = {
    isActive:        () => state.active,
    isEnabled:       () => localStorage.getItem(PREF_ENABLED) === 'on',
    toggle:          toggleVoiceMode,
    enter:           enterVoiceMode,
    exit:            exitVoiceMode,
    speak:           speak,
    stopSpeaking:    stopSpeaking,
    handleAssistantText: handleAssistantText,
    // Used by gestures.js
    muteToggle:      () => {
      state.muted = !state.muted;
      setStatus(state.muted ? 'Mic muted' : 'Listening…',
                state.muted ? 'Show palm again to resume' : 'Click reactor to interrupt · Esc to exit');
    },
    injectUserMessage: (text) => sendUserMessage(text),
    snapshotAndAsk:    (q) => sendUserMessage(q),
  };

  // ── Internal state ───────────────────────────────────────────
  const state = {
    active:        false,
    listening:     false,
    muted:         false,    // spacebar held
    cameraStream:  null,
    micStream:     null,
    recognition:   null,
    audioCtx:      null,
    micAnalyser:   null,
    micRafId:      null,
    pendingReplyParts: [],   // accumulator for assistant text events
    speechQueue:   [],
    isSpeaking:    false,
  };

  // ── DOM refs (lazy: looked up on first use) ──────────────────
  const $ = (id) => document.getElementById(id);

  // ── Public: toggle ───────────────────────────────────────────
  async function toggleVoiceMode() {
    if (state.active) { exitVoiceMode(); return; }
    if (!SpeechRecog) {
      showToastSafe('Voice not supported in this browser. Try Chrome/Edge.', 'error');
      return;
    }
    // Check saved mode preference (set via "Always use this" checkbox)
    const savedMode = localStorage.getItem('jarvisVoicePickedMode');  // 'voice' | 'voice-cam' | null
    let chosen;
    if (savedMode === 'voice' || savedMode === 'voice-cam') {
      chosen = savedMode;
    } else {
      chosen = await showModePicker();
      if (!chosen) return; // cancelled
    }
    enterVoiceMode(chosen === 'voice-cam');
  }

  function showModePicker() {
    return new Promise(resolve => {
      const picker = $('voicePicker');
      if (!picker) return resolve('voice-cam');
      picker.style.display = 'flex';

      const onPick = (e) => {
        const btn = e.target.closest('[data-mode]');
        if (!btn) return;
        const mode = btn.dataset.mode;
        const remember = $('voicePickerRemember');
        if (remember && remember.checked) {
          localStorage.setItem('jarvisVoicePickedMode', mode);
        }
        cleanup();
        resolve(mode);
      };
      const onCancel = () => { cleanup(); resolve(null); };
      const cleanup = () => {
        picker.style.display = 'none';
        picker.removeEventListener('click', onPick);
        $('voicePickerCancel').removeEventListener('click', onCancel);
      };
      picker.addEventListener('click', onPick);
      $('voicePickerCancel').addEventListener('click', onCancel);
    });
  }

  // ── Enter voice mode ─────────────────────────────────────────
  async function enterVoiceMode(useCamera) {
    if (state.active) return;
    if (!SpeechRecog) {
      showToastSafe('Voice not supported in this browser. Try Chrome/Edge.', 'error');
      return;
    }
    if (localStorage.getItem(PREF_ENABLED) !== 'on') {
      localStorage.setItem(PREF_ENABLED, 'on');
    }
    // Persist user's session-level choice as the camera flag
    localStorage.setItem(PREF_CAMERA, useCamera ? 'on' : 'off');

    state.active = true;
    showOverlay(true, useCamera);
    setVoiceState('idle');
    setStatus('Initializing…', '');

    try {
      state.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      attachMicAnalyser(state.micStream);

      if (useCamera) {
        try {
          state.cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: 'user', width: 1280, height: 720 }
          });
          const v = $('voiceCameraVideo');
          if (v) {
            v.srcObject = state.cameraStream;
            await new Promise(r => { v.onloadedmetadata = r; });
          }
          // Start gesture detection if enabled (voice-mode dispatches voice actions)
          if (localStorage.getItem('jarvisGestures') === 'on' && window.jarvisGestures) {
            await window.jarvisGestures.start(v, $('voiceGestureCanvas'), 'voice');
            const hint = $('voiceGestureHint');
            if (hint) hint.style.display = 'flex';
          }
        } catch (e) {
          showToastSafe('Camera unavailable, continuing voice-only.', 'success');
          // fall back to voice-only layout
          const overlay = $('voiceOverlay');
          if (overlay) overlay.dataset.camera = 'off';
        }
      }

      const btn = $('micBtn');
      if (btn) btn.classList.add('mic-on');

      startRecognition();
      setVoiceState('listening');
      setStatus('Listening…', 'Click reactor to interrupt · Esc to exit');
    } catch (err) {
      console.error('[voice] enter failed:', err);
      showToastSafe(
        err && err.name === 'NotAllowedError'
          ? 'Microphone permission denied.'
          : 'Could not start voice mode: ' + (err && err.message || err),
        'error'
      );
      exitVoiceMode();
    }
  }

  // ── Exit voice mode (clean up everything) ────────────────────
  function exitVoiceMode() {
    state.active = false;
    state.listening = false;
    state.muted = false;

    if (window.jarvisGestures && window.jarvisGestures.isActive()) {
      window.jarvisGestures.stop();
    }
    const hint = $('voiceGestureHint');
    if (hint) hint.style.display = 'none';

    if (state.recognition) {
      try { state.recognition.onend = null; state.recognition.stop(); } catch {}
      state.recognition = null;
    }
    if (state.micAnalyser) {
      cancelAnimationFrame(state.micRafId);
      state.micRafId = null;
      state.micAnalyser = null;
    }
    if (state.audioCtx) {
      try { state.audioCtx.close(); } catch {}
      state.audioCtx = null;
    }
    if (state.micStream) {
      state.micStream.getTracks().forEach(t => t.stop());
      state.micStream = null;
    }
    if (state.cameraStream) {
      state.cameraStream.getTracks().forEach(t => t.stop());
      state.cameraStream = null;
    }
    stopSpeaking();
    showOverlay(false, false);
    const btn = $('micBtn');
    if (btn) btn.classList.remove('mic-on');
    setVoiceState('idle');
  }

  // ── Speech recognition setup ─────────────────────────────────
  function startRecognition() {
    const recog = new SpeechRecog();
    recog.continuous = true;
    recog.interimResults = true;
    recog.lang = 'en-US';

    let interim = '';

    recog.onresult = (ev) => {
      if (state.muted) return;
      let finalText = '';
      interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      if (interim) {
        setVoiceState('userSpeaking');
        setStatus('Listening…', '"' + interim.trim() + '"');
      }
      if (finalText.trim()) {
        sendUserMessage(finalText.trim());
        interim = '';
      }
    };

    recog.onerror = (ev) => {
      if (ev.error === 'no-speech') return;        // normal silence
      if (ev.error === 'aborted') return;          // we stopped it
      console.warn('[voice] recognition error:', ev.error);
      if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
        showToastSafe('Microphone permission required.', 'error');
        exitVoiceMode();
      }
    };

    recog.onend = () => {
      // Auto-restart while voice mode is active (continuous mode sometimes ends)
      if (state.active && !state.muted) {
        try { recog.start(); } catch {}
      }
    };

    state.recognition = recog;
    try { recog.start(); state.listening = true; }
    catch (e) { console.warn('[voice] recog.start failed:', e); }
  }

  // ── Send the transcribed message through the existing WS ─────
  async function sendUserMessage(text) {
    if (!text) return;
    setVoiceState('thinking');
    setStatus('Thinking…', '"' + text + '"');
    state.pendingReplyParts = [];

    // Show in chat as a normal user message (so it appears in history)
    if (typeof appendUserMessage === 'function') {
      try { appendUserMessage(text); hideWelcome(); } catch {}
    }

    // Capture a camera frame if camera is on
    let images = null;
    if (state.cameraStream && localStorage.getItem(PREF_CAMERA) !== 'off') {
      const frame = captureCameraFrame();
      if (frame) images = [frame];
    }

    // Send through the existing WebSocket. We rely on app.js's WS being open.
    if (window.ws && window.ws.readyState === WebSocket.OPEN) {
      const payload = { type: 'message', content: text };
      if (images) payload.images = images;
      window.ws.send(JSON.stringify(payload));
    } else {
      showToastSafe('Connection lost — reconnecting…', 'error');
      setVoiceState('error');
    }
  }

  function captureCameraFrame() {
    const video = $('voiceCameraVideo');
    if (!video || !video.videoWidth) return null;
    const canvas = document.createElement('canvas');
    canvas.width  = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0);
    // 0.7 quality keeps it under ~50KB; perfectly enough for "what is this?"
    return canvas.toDataURL('image/jpeg', 0.7);
  }

  // ── Receive assistant text events from app.js stream ─────────
  // app.js calls window.voiceMode.handleAssistantText(token, isDone) for each text event
  function handleAssistantText(token, isDone) {
    if (!state.active) return;
    if (token) state.pendingReplyParts.push(token);
    if (isDone) {
      const fullText = state.pendingReplyParts.join('').trim();
      state.pendingReplyParts = [];
      if (fullText) {
        speak(fullText);
      } else {
        // No text to speak (maybe pure tool actions) — just go back to listening
        setVoiceState('listening');
        setStatus('Listening…', 'Click reactor to interrupt · Esc to exit');
      }
    }
  }

  // ── TTS ──────────────────────────────────────────────────────
  function speak(text) {
    if (!text) return;
    if (!('speechSynthesis' in window)) {
      setVoiceState('listening');
      return;
    }
    stopSpeaking();
    const utter = new SpeechSynthesisUtterance(stripMarkdown(text));
    const voiceId = localStorage.getItem(PREF_VOICE);
    if (voiceId) {
      const v = speechSynthesis.getVoices().find(x => x.name === voiceId);
      if (v) utter.voice = v;
    }
    utter.rate   = parseFloat(localStorage.getItem(PREF_RATE) || '1.0');
    utter.pitch  = 1.0;
    utter.volume = 1.0;

    utter.onstart = () => {
      state.isSpeaking = true;
      setVoiceState('jarvisSpeaking');
      setStatus('Jarvis is speaking…', 'Click reactor to interrupt');
    };
    utter.onend   = () => {
      state.isSpeaking = false;
      setVoiceState('listening');
      setStatus('Listening…', 'Click reactor to interrupt · Esc to exit');
    };
    utter.onerror = () => {
      state.isSpeaking = false;
      setVoiceState('listening');
    };
    speechSynthesis.speak(utter);
  }
  function stopSpeaking() {
    if ('speechSynthesis' in window) {
      try { speechSynthesis.cancel(); } catch {}
    }
    state.isSpeaking = false;
  }

  // Strip light markdown for TTS — bullets, asterisks, code fences
  function stripMarkdown(t) {
    return t
      .replace(/```[\s\S]*?```/g, '. (code block omitted) .')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/\*([^*]+)\*/g, '$1')
      .replace(/^[\s]*[-*•]\s+/gm, '. ')
      .replace(/#+\s*/g, '')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/\s+/g, ' ')
      .trim();
  }

  // ── Mic analyser → reactor scale ─────────────────────────────
  function attachMicAnalyser(stream) {
    try {
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = state.audioCtx.createMediaStreamSource(stream);
      const analyser = state.audioCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      state.micAnalyser = analyser;

      const buf = new Uint8Array(analyser.frequencyBinCount);
      const reactor = $('voiceReactor');
      const tick = () => {
        if (!state.micAnalyser) return;
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length); // 0..~0.5
        const scaled = Math.min(1, rms * 4);     // boost
        if (reactor) reactor.style.setProperty('--mic-rms', scaled.toFixed(3));
        state.micRafId = requestAnimationFrame(tick);
      };
      state.micRafId = requestAnimationFrame(tick);
    } catch (e) {
      console.warn('[voice] analyser failed:', e);
    }
  }

  // ── UI helpers ───────────────────────────────────────────────
  function showOverlay(show, withCamera = false) {
    const o = $('voiceOverlay');
    if (!o) return;
    o.style.display = show ? 'flex' : 'none';
    o.dataset.camera = withCamera ? 'on' : 'off';
    if (show) o.classList.add('active');
    else o.classList.remove('active');
  }
  function setVoiceState(s) {
    const r = $('voiceReactor');
    if (r) r.dataset.voiceState = s;
  }
  function setStatus(main, detail) {
    const m = $('voiceStatusMain');
    const d = $('voiceStatusDetail');
    if (m) m.textContent = main || '';
    if (d) d.textContent = detail || '';
  }
  function showToastSafe(msg, kind) {
    if (typeof window.showToast === 'function') {
      try { window.showToast(msg, kind); return; } catch {}
    }
    console.log('[voice]', msg);
  }
  function hideWelcome() {
    if (typeof window.hideWelcome === 'function') {
      try { window.hideWelcome(); } catch {}
    } else {
      const w = document.getElementById('welcomeScreen');
      if (w) w.style.display = 'none';
    }
  }

  // ── Keyboard shortcuts ───────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    if (!state.active) return;
    // Ignore when user is typing in the chat input
    if (e.target && (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT')) return;
    if (e.code === 'Escape') {
      e.preventDefault();
      exitVoiceMode();
    } else if (e.code === 'Space' && !e.repeat) {
      e.preventDefault();
      state.muted = true;
      setStatus('Mic muted', 'Release Space to resume');
    }
  });
  document.addEventListener('keyup', (e) => {
    if (!state.active) return;
    if (e.code === 'Space') {
      e.preventDefault();
      state.muted = false;
      if (state.recognition) {
        try { state.recognition.stop(); } catch {} // onend will restart
      }
      setStatus('Listening…', 'Click reactor to interrupt · Esc to exit');
    }
  });

  // ── Click reactor to interrupt TTS ───────────────────────────
  document.addEventListener('click', (e) => {
    if (!state.active) return;
    const reactor = $('voiceReactor');
    if (reactor && (e.target === reactor || reactor.contains(e.target))) {
      if (state.isSpeaking) {
        stopSpeaking();
        setVoiceState('listening');
        setStatus('Listening…', 'Click reactor to interrupt · Esc to exit');
      }
    }
  });
})();
