/* ═══════════════════════════════════════════════════════════════
   J.A.R.V.I.S — Face-scan Login (optional, opt-in)
   ───────────────────────────────────────────────────────────────
   Flow:  loading-models → [enrollment if first time] → mode-picker
          → camera-scan → welcome|denied → boot-animation
   Stores: localStorage 'jarvisFaceLogin'    = 'on' | 'off'
           localStorage 'jarvisFaceMode'     = 'secure' | 'public' | 'ask'
           localStorage 'jarvisFaceDesc'     = JSON [128-float descriptor]
           localStorage 'jarvisFaceName'     = 'Prem' (display name)
   ───────────────────────────────────────────────────────────────
   Library: @vladmandic/face-api (UMD via jsdelivr, cached after 1st)
   Privacy: 100% in-browser. Camera stream never leaves the device.
            We store a 128-float face signature, NOT a photo.
   ═══════════════════════════════════════════════════════════════ */

// Skip everything if not enabled (default).
window.__faceLoginPending = (localStorage.getItem('jarvisFaceLogin') === 'on');

(function () {
  const PREF_KEY     = 'jarvisFaceLogin';
  const MODE_KEY     = 'jarvisFaceMode';
  const DESC_KEY     = 'jarvisFaceDesc';
  const NAME_KEY     = 'jarvisFaceName';
  const FACEAPI_SRC  = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/dist/face-api.js';
  const MODELS_URL   = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/model';
  const MATCH_THRESH = 0.5;          // lower = stricter; 0.5 is balanced
  const SCAN_INTERVAL_MS = 150;      // was 200ms — tighter polling
  const ENROLL_FRAMES = 3;           // was 5 — fewer frames = much faster enrollment
  const ENROLL_GAP_MS = 350;         // was 700 — half the wait between captures
  const DENY_TIMEOUT  = 60_000;
  const PUBLIC_TIMEOUT= 12_000;
  // Detector input size: 320 → 224 cuts detection compute ~50% with negligible
  // accuracy loss for close-range selfie use (face fills ~30%+ of frame).
  const DETECT_INPUT_SIZE = 224;

  if (!window.__faceLoginPending) return;

  // Block boot animation auto-start; we'll trigger it ourselves at the end.
  let bootOverlay = null;
  document.addEventListener('DOMContentLoaded', start, { once: true });
  if (document.readyState !== 'loading') start();

  let overlay, stages, currentStream, scanLoopId, deniedTimeoutId;

  // ── Lifecycle entrypoint ─────────────────────────────────────
  async function start() {
    bootOverlay = document.getElementById('bootOverlay');
    overlay     = document.getElementById('faceLoginOverlay');
    if (!overlay) {
      // No HTML for the overlay — bail to boot
      proceedToBoot();
      return;
    }
    stages = overlay.querySelectorAll('.face-stage');
    if (bootOverlay) bootOverlay.style.display = 'none';
    overlay.style.display = 'flex';

    try {
      showStage('loading');
      await loadFaceApiScript();
      await loadModels();

      const enrolled = readEnrollment();
      if (!enrolled) {
        const ok = await runEnrollmentIntro();
        if (!ok) return cancelAndBoot();
        const desc = await captureEnrollment();
        if (!desc) return cancelAndBoot();
        saveEnrollment(desc);
      }

      const savedMode = localStorage.getItem(MODE_KEY) || 'ask';
      const mode = savedMode === 'ask' ? await showModePicker() : savedMode;
      if (mode === 'skip') return cancelAndBoot();

      await runScanLoop(mode);
      // runScanLoop handles welcome/denied transitions itself.
    } catch (err) {
      console.warn('[face-login] error, falling back to boot:', err);
      cancelAndBoot();
    }
  }

  // ── Stage helpers ────────────────────────────────────────────
  function showStage(name) {
    stages.forEach(s => s.style.display = (s.dataset.stage === name) ? 'flex' : 'none');
  }

  // ── Boot fallback / completion ───────────────────────────────
  function cancelAndBoot() {
    cleanup();
    if (overlay) overlay.style.display = 'none';
    proceedToBoot();
  }
  function proceedToBoot() {
    if (bootOverlay) bootOverlay.style.display = 'flex';
    if (typeof window.startBootSequence === 'function') {
      window.startBootSequence();
    }
  }
  function cleanup() {
    if (scanLoopId)       { clearInterval(scanLoopId); scanLoopId = null; }
    if (deniedTimeoutId)  { clearTimeout(deniedTimeoutId); deniedTimeoutId = null; }
    if (currentStream)    { currentStream.getTracks().forEach(t => t.stop()); currentStream = null; }
  }

  // ── face-api.js dynamic load ─────────────────────────────────
  function loadFaceApiScript() {
    return new Promise((resolve, reject) => {
      if (window.faceapi) return resolve();
      const s = document.createElement('script');
      s.src = FACEAPI_SRC;
      s.onload  = () => resolve();
      s.onerror = () => reject(new Error('face-api.js failed to load'));
      document.head.appendChild(s);
    });
  }

  async function loadModels() {
    const fa = window.faceapi;
    setLoadingProgress(20, 'LOADING DETECTOR…');
    await fa.nets.tinyFaceDetector.loadFromUri(MODELS_URL);
    setLoadingProgress(50, 'LOADING LANDMARKS…');
    await fa.nets.faceLandmark68Net.loadFromUri(MODELS_URL);
    setLoadingProgress(80, 'LOADING RECOGNITION…');
    await fa.nets.faceRecognitionNet.loadFromUri(MODELS_URL);
    setLoadingProgress(100, 'READY');
  }
  function setLoadingProgress(pct, label) {
    const fill  = document.getElementById('faceLoadFill');
    const text  = document.getElementById('faceLoadText');
    if (fill)  fill.style.width = pct + '%';
    if (text)  text.textContent = label;
  }

  // ── Enrollment ───────────────────────────────────────────────
  function runEnrollmentIntro() {
    return new Promise(resolve => {
      showStage('enroll-intro');
      overlay.querySelector('#faceEnrollBegin').onclick = () => resolve(true);
      overlay.querySelector('#faceEnrollSkip').onclick  = () => {
        // Skip means: use Jarvis without face login this session, but don't disable globally
        resolve(false);
      };
    });
  }

  async function captureEnrollment() {
    showStage('scan');
    setScanText('ENROLLMENT — STAY IN FRAME', 'Capturing 5 reference frames…');
    const stream = await activateCamera();
    if (!stream) return null;

    const video = document.getElementById('faceVideo');
    const canvas = document.getElementById('faceCanvas');
    await waitForVideo(video);
    sizeCanvasToVideo(video, canvas);

    const fa = window.faceapi;
    const opts = new fa.TinyFaceDetectorOptions({ inputSize: DETECT_INPUT_SIZE, scoreThreshold: 0.5 });
    const descriptors = [];
    const start = Date.now();
    const ENROLL_TIMEOUT = 20_000;

    while (descriptors.length < ENROLL_FRAMES && (Date.now() - start) < ENROLL_TIMEOUT) {
      await sleep(ENROLL_GAP_MS);
      try {
        const r = await fa.detectSingleFace(video, opts).withFaceLandmarks().withFaceDescriptor();
        if (r) {
          descriptors.push(Array.from(r.descriptor));
          drawDetection(canvas, r, video.videoWidth, video.videoHeight, true);
          setScanText('ENROLLING…', `${descriptors.length} / ${ENROLL_FRAMES} frames captured`);
        } else {
          drawDetection(canvas, null);
          setScanText('ENROLLING…', 'No face detected — center yourself');
        }
      } catch (e) { /* continue */ }
    }
    cleanup();
    if (descriptors.length === 0) return null;

    // Average the descriptors → one stable signature
    const avg = new Array(128).fill(0);
    descriptors.forEach(d => { d.forEach((v, i) => avg[i] += v); });
    return avg.map(v => v / descriptors.length);
  }

  function readEnrollment() {
    try {
      const raw = localStorage.getItem(DESC_KEY);
      if (!raw) return null;
      const arr = JSON.parse(raw);
      return (Array.isArray(arr) && arr.length === 128) ? arr : null;
    } catch { return null; }
  }
  function saveEnrollment(descArr) {
    localStorage.setItem(DESC_KEY, JSON.stringify(descArr));
    if (!localStorage.getItem(NAME_KEY)) localStorage.setItem(NAME_KEY, 'Prem');
  }

  // ── Mode picker ──────────────────────────────────────────────
  function showModePicker() {
    return new Promise(resolve => {
      showStage('mode');
      overlay.querySelector('#faceModeSecure').onclick = () => resolve('secure');
      overlay.querySelector('#faceModePublic').onclick = () => resolve('public');
      overlay.querySelector('#faceModeSkip').onclick   = () => resolve('skip');
    });
  }

  // ── Camera ───────────────────────────────────────────────────
  async function activateCamera() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: 640, height: 480 },
        audio: false,
      });
      currentStream = stream;
      const video = document.getElementById('faceVideo');
      video.srcObject = stream;
      return stream;
    } catch (e) {
      setScanText('CAMERA UNAVAILABLE', e.name === 'NotAllowedError' ? 'Permission denied — skipping' : e.message);
      await sleep(2000);
      return null;
    }
  }

  // ── Main scan loop ───────────────────────────────────────────
  async function runScanLoop(mode) {
    showStage('scan');
    setScanText('AWAITING USER…', 'OPTICAL SCAN ACTIVE');
    const stream = await activateCamera();
    if (!stream) return cancelAndBoot();

    const video  = document.getElementById('faceVideo');
    const canvas = document.getElementById('faceCanvas');
    await waitForVideo(video);
    sizeCanvasToVideo(video, canvas);

    const fa   = window.faceapi;
    const opts = new fa.TinyFaceDetectorOptions({ inputSize: DETECT_INPUT_SIZE, scoreThreshold: 0.5 });
    const reference = readEnrollment();
    const refDescriptor = reference ? new Float32Array(reference) : null;

    let scanCount = 0;
    const startedAt = Date.now();

    return new Promise(resolve => {
      const tick = async () => {
        try {
          scanCount++;
          const r = await fa.detectSingleFace(video, opts).withFaceLandmarks().withFaceDescriptor();
          if (r) {
            drawDetection(canvas, r, video.videoWidth, video.videoHeight, true);
            const dist = refDescriptor
              ? fa.euclideanDistance(refDescriptor, r.descriptor)
              : Infinity;
            const isMe = dist < MATCH_THRESH;

            if (mode === 'secure') {
              if (isMe) {
                clearInterval(scanLoopId);
                clearTimeout(deniedTimeoutId);
                await runWelcome(true);
                resolve();
                return;
              } else {
                setScanText('UNAUTHORIZED USER', `MATCH FAIL · DIST ${dist.toFixed(2)}`);
              }
            } else { // public
              clearInterval(scanLoopId);
              await runWelcome(isMe);
              resolve();
              return;
            }
          } else {
            drawDetection(canvas, null);
            setScanText('AWAITING USER…', `SCAN PASS ${scanCount}`);
          }
        } catch (e) { /* keep scanning */ }
      };
      scanLoopId = setInterval(tick, SCAN_INTERVAL_MS);
      tick();

      // Timeouts
      if (mode === 'secure') {
        deniedTimeoutId = setTimeout(() => {
          clearInterval(scanLoopId);
          showDeniedScreen(resolve);
        }, DENY_TIMEOUT);
      } else {
        deniedTimeoutId = setTimeout(() => {
          clearInterval(scanLoopId);
          // No face seen in public mode — just proceed as guest
          runWelcome(false).then(resolve);
        }, PUBLIC_TIMEOUT);
      }
    });
  }

  // ── Welcome / denied screens ─────────────────────────────────
  async function runWelcome(isMe) {
    cleanup();
    showStage('welcome');
    const name = localStorage.getItem(NAME_KEY) || 'Prem';
    const greet = isMe ? `WELCOME, ${name.toUpperCase()}` : 'WELCOME, GUEST';
    document.getElementById('faceWelcomeText').textContent = greet;
    await sleep(1800);
    overlay.style.display = 'none';
    proceedToBoot();
  }
  function showDeniedScreen(resolveOuter) {
    cleanup();
    showStage('denied');
    overlay.querySelector('#faceDeniedSkip').onclick = () => {
      overlay.style.display = 'none';
      proceedToBoot();
      resolveOuter();
    };
  }

  // ── Drawing helpers ──────────────────────────────────────────
  function sizeCanvasToVideo(video, canvas) {
    canvas.width  = video.videoWidth  || 640;
    canvas.height = video.videoHeight || 480;
  }

  // 68-point face mesh connections (face-api.js standard layout)
  const FACE_MESH_GROUPS = [
    { name: 'jaw',         pts: [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16],  closed: false },
    { name: 'browR',       pts: [17,18,19,20,21],                            closed: false },
    { name: 'browL',       pts: [22,23,24,25,26],                            closed: false },
    { name: 'noseBridge',  pts: [27,28,29,30],                               closed: false },
    { name: 'noseLower',   pts: [31,32,33,34,35],                            closed: false },
    { name: 'eyeR',        pts: [36,37,38,39,40,41],                         closed: true  },
    { name: 'eyeL',        pts: [42,43,44,45,46,47],                         closed: true  },
    { name: 'mouthOuter',  pts: [48,49,50,51,52,53,54,55,56,57,58,59],       closed: true  },
    { name: 'mouthInner',  pts: [60,61,62,63,64,65,66,67],                   closed: true  },
  ];

  function drawDetection(canvas, result, vidW, vidH, withScan = false) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!result) return;

    // Backwards compat: caller might still pass a raw box
    const box = result.detection ? result.detection.box : result;
    if (!box || box.x == null) return;
    const { x, y, width, height } = box;

    // ── 1. Corner brackets (HUD frame around face) ─────────────
    ctx.strokeStyle = '#fbbf24';
    ctx.lineWidth   = 2;
    ctx.shadowColor = 'rgba(251, 191, 36, 0.7)';
    ctx.shadowBlur  = 10;
    const L = Math.min(width, height) * 0.22;
    drawBracket(ctx, x,         y,         L, +1, +1);
    drawBracket(ctx, x + width, y,         L, -1, +1);
    drawBracket(ctx, x,         y + height, L, +1, -1);
    drawBracket(ctx, x + width, y + height, L, -1, -1);

    // ── 2. (Face mesh skeleton removed — brackets + scan line only for cleaner look) ──

    // ── 3. Horizontal scan line sweeping over the face area ────
    if (withScan) {
      const t = (Date.now() % 1500) / 1500;
      const sy = y + t * height;
      const grad = ctx.createLinearGradient(x, sy, x + width, sy);
      grad.addColorStop(0,    'rgba(251,191,36,0)');
      grad.addColorStop(0.5,  'rgba(251,191,36,0.95)');
      grad.addColorStop(1,    'rgba(251,191,36,0)');
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x, sy); ctx.lineTo(x + width, sy); ctx.stroke();
    }
    ctx.shadowBlur = 0;
  }

  function drawFaceMesh(ctx, pts) {
    // Connection lines — thin, faint amber
    ctx.lineWidth   = 1.2;
    ctx.strokeStyle = 'rgba(251, 191, 36, 0.55)';
    ctx.shadowColor = 'rgba(251, 191, 36, 0.45)';
    ctx.shadowBlur  = 4;
    for (const grp of FACE_MESH_GROUPS) {
      ctx.beginPath();
      const seq = grp.pts;
      ctx.moveTo(pts[seq[0]].x, pts[seq[0]].y);
      for (let i = 1; i < seq.length; i++) {
        ctx.lineTo(pts[seq[i]].x, pts[seq[i]].y);
      }
      if (grp.closed) ctx.lineTo(pts[seq[0]].x, pts[seq[0]].y);
      ctx.stroke();
    }

    // Landmark dots — bright amber, slightly pulsing
    const phase = (Date.now() % 1200) / 1200;
    const pulseAlpha = 0.65 + 0.35 * Math.sin(phase * Math.PI * 2);
    ctx.shadowBlur = 6;
    ctx.shadowColor = 'rgba(251, 191, 36, 0.7)';
    for (let i = 0; i < pts.length; i++) {
      // Highlight key feature points (eyes, nose tip, mouth corners) with bigger dots
      const isKey = (i === 30) || (i === 36) || (i === 39) || (i === 42)
                  || (i === 45) || (i === 48) || (i === 54) || (i === 8);
      ctx.fillStyle = isKey
        ? `rgba(253, 230, 138, ${pulseAlpha})`
        : 'rgba(251, 191, 36, 0.85)';
      ctx.beginPath();
      ctx.arc(pts[i].x, pts[i].y, isKey ? 2.6 : 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.shadowBlur = 0;
  }

  function drawBracket(ctx, x, y, len, dx, dy) {
    ctx.beginPath();
    ctx.moveTo(x, y + dy * len);
    ctx.lineTo(x, y);
    ctx.lineTo(x + dx * len, y);
    ctx.stroke();
  }

  function setScanText(main, detail) {
    const m = document.getElementById('faceStatusText');
    const d = document.getElementById('faceStatusDetail');
    if (m) m.textContent = main || '';
    if (d) d.textContent = detail || '';
  }

  // ── Misc utils ───────────────────────────────────────────────
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  function waitForVideo(video) {
    return new Promise(resolve => {
      if (video.readyState >= 2) return resolve();
      video.onloadedmetadata = () => resolve();
    });
  }
})();

// Public helper: clear face data (used by Settings)
window.jarvisClearFaceData = function () {
  localStorage.removeItem('jarvisFaceDesc');
  localStorage.removeItem('jarvisFaceName');
};
