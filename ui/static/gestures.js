/* ═══════════════════════════════════════════════════════════════
   J.A.R.V.I.S — Hand gesture control (MediaPipe Tasks Vision)
   ───────────────────────────────────────────────────────────────
   Detects 6 gestures and dispatches actions to voiceMode:
     ✋ open palm   → mute mic toggle
     👊 closed fist → interrupt Jarvis (stop TTS)
     ✌️ peace sign  → exit voice mode
     👍 thumbs up   → confirm pending action ("yes")
     👎 thumbs down → cancel pending action ("no")
     ☝️ point       → take snapshot ("look at this")

   Each gesture must be HELD for ~700ms before firing — prevents
   false positives from incidental hand poses.

   Detection runs at 15 fps (configurable). Hand skeleton + active
   gesture badge drawn on the camera canvas.

   Public API (window.jarvisGestures):
     start(videoEl, canvasEl)  — begin detection loop
     stop()                    — release model + animation frame
     isActive()                — bool

   Storage:
     jarvisGestures        : 'on' | 'off' (default 'off')
     jarvisGesturesSkeleton: 'on' | 'off' (default 'on' when gestures on)
   ═══════════════════════════════════════════════════════════════ */

(function () {
  if (window.jarvisGestures) return;

  const TASKS_VISION_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs';
  const MODEL_URL        = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';
  const FPS              = 15;            // frames per second to run detection
  const HOLD_MS          = 700;           // gesture must be held this long to fire
  const COOLDOWN_MS      = 1500;          // after firing, ignore gestures for this long

  const state = {
    landmarker: null,
    video:      null,
    canvas:     null,
    ctx:        null,
    running:    false,
    rafId:      null,
    lastDetectAt: 0,
    currentGesture: null,
    gestureSince:   0,
    cooldownUntil:  0,
    mode: 'voice',           // 'voice' | 'navigation'
    // Navigation-mode state:
    wristHistory: [],        // [{x, y, t}] for swipe detection
    strokePath:   [],        // [{x, y, t}] index fingertip path for shape drawing
    strokeIdleSince: 0,      // when the fingertip last moved meaningfully
  };

  window.jarvisGestures = {
    start, stop,
    isActive: () => state.running,
    getMode:  () => state.mode,
  };

  // ── Lifecycle ────────────────────────────────────────────────
  async function start(videoEl, canvasEl, mode = 'voice') {
    if (state.running) return;
    state.video  = videoEl;
    state.canvas = canvasEl;
    state.ctx    = canvasEl ? canvasEl.getContext('2d') : null;
    state.mode   = mode;
    state.wristHistory = [];
    state.strokePath   = [];

    if (!state.landmarker) {
      try {
        const vision = await import(TASKS_VISION_URL);
        const fileset = await vision.FilesetResolver.forVisionTasks(
          'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm'
        );
        state.landmarker = await vision.HandLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: MODEL_URL, delegate: 'GPU' },
          runningMode: 'VIDEO',
          numHands: 1,
          minHandDetectionConfidence: 0.6,
          minHandPresenceConfidence: 0.6,
          minTrackingConfidence: 0.6,
        });
      } catch (e) {
        console.warn('[gestures] Failed to load MediaPipe — gestures disabled:', e);
        return;
      }
    }

    state.running = true;
    state.lastDetectAt = 0;
    state.currentGesture = null;
    sizeCanvas();
    state.rafId = requestAnimationFrame(loop);
  }

  function stop() {
    state.running = false;
    if (state.rafId) {
      cancelAnimationFrame(state.rafId);
      state.rafId = null;
    }
    if (state.ctx && state.canvas) {
      state.ctx.clearRect(0, 0, state.canvas.width, state.canvas.height);
    }
    state.currentGesture = null;
    state.gestureSince = 0;
    // We keep state.landmarker alive — re-creating is expensive.
  }

  // ── Main detection + draw loop ───────────────────────────────
  async function loop() {
    if (!state.running) return;
    const now = performance.now();
    const minInterval = 1000 / FPS;
    if (state.video && state.video.readyState >= 2 && (now - state.lastDetectAt) >= minInterval) {
      state.lastDetectAt = now;
      sizeCanvas();
      try {
        const result = state.landmarker.detectForVideo(state.video, now);
        const landmarks = (result.landmarks && result.landmarks[0]) || null;
        const handedness = (result.handedness && result.handedness[0] && result.handedness[0][0]) || null;

        clearCanvas();
        if (landmarks) {
          drawHand(landmarks);
          if (state.mode === 'navigation') {
            processNavigation(landmarks, now);
          } else {
            const gesture = classifyGesture(landmarks, handedness);
            updateGesture(gesture, now);
          }
        } else {
          if (state.mode === 'navigation') {
            // No hand → reset trackers
            state.wristHistory = [];
            state.strokePath = [];
          } else {
            updateGesture(null, now);
          }
        }
      } catch (e) {
        // Don't spam console — likely a transient MediaPipe error
      }
    }
    state.rafId = requestAnimationFrame(loop);
  }

  function sizeCanvas() {
    if (!state.video || !state.canvas) return;
    const w = state.video.videoWidth  || 320;
    const h = state.video.videoHeight || 240;
    if (state.canvas.width !== w)  state.canvas.width  = w;
    if (state.canvas.height !== h) state.canvas.height = h;
  }
  function clearCanvas() {
    if (state.ctx) state.ctx.clearRect(0, 0, state.canvas.width, state.canvas.height);
  }

  // ── Gesture classification (from 21 hand landmarks) ──────────
  // Landmark indices (MediaPipe standard):
  //   0=WRIST, thumb=1-4, index=5-8, middle=9-12, ring=13-16, pinky=17-20
  // Each landmark has {x,y,z} normalized [0..1] (origin top-left of image).
  function classifyGesture(lm, handedness) {
    // Finger extended check — for fingers other than thumb:
    //   tip y < pip y (i.e. tip above pip in image space)
    const isExtended = (tipIdx, pipIdx) => lm[tipIdx].y < lm[pipIdx].y - 0.02;

    const indexExt  = isExtended(8,  6);
    const middleExt = isExtended(12, 10);
    const ringExt   = isExtended(16, 14);
    const pinkyExt  = isExtended(20, 18);

    // Thumb: compare TIP (4) to IP (3) on x-axis (depending on hand)
    // Simpler: thumb tip relative to wrist y for up/down detection
    const thumbTip = lm[4];
    const thumbIp  = lm[3];
    const thumbMcp = lm[2];
    const wrist    = lm[0];

    // Thumb extended if tip is far from wrist
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const thumbExt = dist(thumbTip, wrist) > dist(thumbMcp, wrist) * 1.05;

    // Thumb pointing UP: tip well above wrist (smaller y)
    const thumbUp   = thumbExt && thumbTip.y < wrist.y - 0.10;
    // Thumb pointing DOWN: tip well below wrist
    const thumbDown = thumbExt && thumbTip.y > wrist.y + 0.10;

    const otherFingersCurled = !indexExt && !middleExt && !ringExt && !pinkyExt;

    // ─── Classify ───
    // 👍 thumbs up — only thumb extended, pointing up
    if (thumbUp && otherFingersCurled) return 'thumbsUp';
    // 👎 thumbs down — only thumb extended, pointing down
    if (thumbDown && otherFingersCurled) return 'thumbsDown';
    // ✌️ peace sign — index + middle extended, others curled
    if (indexExt && middleExt && !ringExt && !pinkyExt) return 'peace';
    // ☝️ point — only index extended
    if (indexExt && !middleExt && !ringExt && !pinkyExt && !thumbUp && !thumbDown) return 'point';
    // ✋ open palm — all 4 fingers extended (thumb optional, often slightly out)
    if (indexExt && middleExt && ringExt && pinkyExt) return 'palm';
    // 👊 fist — all 4 fingers curled, thumb not strongly extended
    if (!indexExt && !middleExt && !ringExt && !pinkyExt && !thumbUp && !thumbDown) return 'fist';

    return null;  // ambiguous / no clear gesture
  }

  // ── Hold-debounced gesture firing ────────────────────────────
  function updateGesture(gesture, now) {
    if (now < state.cooldownUntil) {
      // In cooldown — clear current gesture so it doesn't double-fire
      state.currentGesture = null;
      drawGestureBadge(null, 0);
      return;
    }
    if (gesture !== state.currentGesture) {
      state.currentGesture = gesture;
      state.gestureSince   = now;
      drawGestureBadge(gesture, 0);
      return;
    }
    if (gesture && (now - state.gestureSince) >= HOLD_MS) {
      // FIRE!
      fireGestureAction(gesture);
      state.currentGesture = null;
      state.cooldownUntil  = now + COOLDOWN_MS;
      drawGestureBadge(gesture, 1, /*fired*/ true);
    } else if (gesture) {
      const pct = Math.min(1, (now - state.gestureSince) / HOLD_MS);
      drawGestureBadge(gesture, pct);
    }
  }

  function fireGestureAction(g) {
    const vm = window.voiceMode;
    if (!vm || !vm.isActive || !vm.isActive()) return;

    switch (g) {
      case 'palm':
        if (vm.muteToggle) vm.muteToggle();
        toast('✋ Mic toggled');
        break;
      case 'fist':
        if (vm.stopSpeaking) vm.stopSpeaking();
        toast('👊 Stopped');
        break;
      case 'peace':
        if (vm.exit) vm.exit();
        toast('✌️ Voice mode off');
        break;
      case 'thumbsUp':
        if (vm.injectUserMessage) vm.injectUserMessage('yes');
        toast('👍 Confirmed');
        break;
      case 'thumbsDown':
        if (vm.injectUserMessage) vm.injectUserMessage('no, cancel that');
        toast('👎 Cancelled');
        break;
      case 'point':
        if (vm.snapshotAndAsk) vm.snapshotAndAsk('What is this?');
        toast('☝️ Snapshot sent');
        break;
    }
  }

  function toast(msg) {
    if (typeof window.showToast === 'function') {
      try { window.showToast(msg, 'success'); } catch {}
    }
  }

  // ── Drawing helpers ──────────────────────────────────────────
  // Hand skeleton — connections between landmarks
  const CONNECTIONS = [
    [0,1],[1,2],[2,3],[3,4],          // thumb
    [0,5],[5,6],[6,7],[7,8],          // index
    [5,9],[9,10],[10,11],[11,12],     // middle
    [9,13],[13,14],[14,15],[15,16],   // ring
    [13,17],[17,18],[18,19],[19,20],  // pinky
    [0,17],                           // palm
  ];

  function drawHand(landmarks) {
    if (!state.ctx || localStorage.getItem('jarvisGesturesSkeleton') === 'off') return;
    const w = state.canvas.width;
    const h = state.canvas.height;
    const ctx = state.ctx;

    // Mirror: video is mirrored via CSS transform, so we mirror landmarks too
    const px = (lm) => (1 - lm.x) * w;
    const py = (lm) => lm.y * h;

    // Connections
    ctx.lineWidth   = 2;
    ctx.strokeStyle = 'rgba(251, 191, 36, 0.85)';
    ctx.shadowColor = 'rgba(251, 191, 36, 0.6)';
    ctx.shadowBlur  = 6;
    for (const [a, b] of CONNECTIONS) {
      ctx.beginPath();
      ctx.moveTo(px(landmarks[a]), py(landmarks[a]));
      ctx.lineTo(px(landmarks[b]), py(landmarks[b]));
      ctx.stroke();
    }
    // Joints
    ctx.fillStyle   = '#fde68a';
    ctx.shadowBlur  = 4;
    for (let i = 0; i < landmarks.length; i++) {
      ctx.beginPath();
      ctx.arc(px(landmarks[i]), py(landmarks[i]), i === 0 ? 6 : 4, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.shadowBlur = 0;
  }

  function drawGestureBadge(gesture, holdPct, fired = false) {
    if (!state.ctx || !gesture) return;
    const ctx = state.ctx;
    const w = state.canvas.width;
    const x = 12, y = 12, padX = 10, padY = 6;
    const labels = {
      palm: '✋ PALM', fist: '👊 FIST', peace: '✌️ PEACE',
      thumbsUp: '👍 YES', thumbsDown: '👎 NO', point: '☝️ POINT',
    };
    const text = labels[gesture] || '';
    ctx.font = 'bold 14px sans-serif';
    const tw = ctx.measureText(text).width;

    // Background pill
    ctx.fillStyle = fired ? 'rgba(34, 197, 94, 0.85)' : 'rgba(20, 12, 6, 0.85)';
    ctx.strokeStyle = fired ? '#86efac' : 'rgba(251, 191, 36, 0.6)';
    ctx.lineWidth = 1.5;
    roundRect(ctx, x, y, tw + padX * 2, 22 + padY, 6);
    ctx.fill();
    ctx.stroke();

    // Text
    ctx.fillStyle = fired ? '#052e16' : '#fde68a';
    ctx.fillText(text, x + padX, y + padY + 14);

    // Hold progress bar (under the pill)
    if (holdPct > 0 && !fired) {
      ctx.fillStyle = 'rgba(251, 191, 36, 0.9)';
      ctx.fillRect(x, y + 22 + padY + 2, (tw + padX * 2) * holdPct, 2);
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  // ═════════════════════════════════════════════════════════════
  // NAVIGATION MODE — swipes (palm + motion) and stroke drawing
  // (point + path). Fires events on the host page; app.js binds
  // them to UI actions (open sidebar, settings, etc.).
  // ═════════════════════════════════════════════════════════════

  const SWIPE_WINDOW_MS = 600;
  const SWIPE_MIN_DX    = 0.25;       // 25% of frame width
  const SWIPE_NAV_COOLDOWN = 1200;
  const STROKE_IDLE_MS  = 600;        // path ends after this much fingertip stillness
  const STROKE_MAX_MS   = 4000;       // hard cap on a single stroke

  function processNavigation(lm, now) {
    const wrist = lm[0];
    const indexTip = lm[8];
    const pose = classifyGesture(lm, null);  // 'palm' | 'point' | 'fist' | etc.

    if (pose === 'palm') {
      state.strokePath = [];
      state.wristHistory.push({ x: wrist.x, y: wrist.y, t: now });
      pruneHistory(state.wristHistory, SWIPE_WINDOW_MS, now);
      if (now >= state.cooldownUntil) {
        const dir = detectSwipe(state.wristHistory);
        if (dir) {
          state.cooldownUntil = now + SWIPE_NAV_COOLDOWN;
          state.wristHistory = [];
          fireNavAction({ type: 'swipe', dir });
          drawNavBadge('SWIPE ' + dir.toUpperCase(), true);
        }
      }
    } else if (pose === 'point') {
      state.wristHistory = [];
      const point = { x: indexTip.x, y: indexTip.y, t: now };
      const last = state.strokePath[state.strokePath.length - 1];
      const moved = !last || Math.hypot(point.x - last.x, point.y - last.y) > 0.005;
      if (moved) {
        state.strokePath.push(point);
        state.strokeIdleSince = 0;
      } else if (!state.strokeIdleSince) {
        state.strokeIdleSince = now;
      }

      drawStrokePath(state.strokePath);

      const strokeStart = state.strokePath[0]?.t || now;
      const strokeAge = now - strokeStart;
      const idle = state.strokeIdleSince ? (now - state.strokeIdleSince) : 0;
      // End stroke if idle long enough OR hard timeout
      if (state.strokePath.length > 8 && (idle >= STROKE_IDLE_MS || strokeAge >= STROKE_MAX_MS)) {
        const shape = recognizeStroke(state.strokePath);
        if (shape && now >= state.cooldownUntil) {
          state.cooldownUntil = now + SWIPE_NAV_COOLDOWN;
          fireNavAction({ type: 'stroke', name: shape });
          drawNavBadge('SHAPE: ' + shape, true);
        }
        state.strokePath = [];
        state.strokeIdleSince = 0;
      }
    } else {
      state.wristHistory = [];
      // Don't immediately clear strokePath on transient pose loss — give it a bit
      // (but the next palm/other will clear it).
    }
  }

  function pruneHistory(arr, windowMs, now) {
    while (arr.length && (now - arr[0].t) > windowMs) arr.shift();
  }

  function detectSwipe(history) {
    if (history.length < 4) return null;
    const a = history[0];
    const b = history[history.length - 1];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const elapsed = b.t - a.t;
    if (elapsed > SWIPE_WINDOW_MS) return null;

    // Coordinate convention:
    //   Raw landmark x: 0=left of camera image, 1=right.
    //   Selfie cam: when user moves their hand to user-right, hand goes toward
    //   the camera-LEFT side (so raw x DECREASES).
    //   The video is mirrored on screen so the user sees natural motion.
    //   We map "user swiped right" = raw x decreased.
    if (Math.abs(dx) > Math.abs(dy) * 1.4 && Math.abs(dx) >= SWIPE_MIN_DX) {
      return dx < 0 ? 'right' : 'left';
    }
    if (Math.abs(dy) > Math.abs(dx) * 1.4 && Math.abs(dy) >= SWIPE_MIN_DX) {
      return dy < 0 ? 'up' : 'down';
    }
    return null;
  }

  // ── Stroke shape recognition ──────────────────────────────────
  // Heuristic for "S": three predominantly-horizontal runs alternating
  // direction, with overall downward Y progression. We slice the path
  // into top/middle/bottom thirds of its bounding box and inspect the
  // dominant horizontal direction in each third.
  function recognizeStroke(path) {
    if (path.length < 12) return null;
    let minX = 1, maxX = 0, minY = 1, maxY = 0;
    for (const p of path) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
    const w = maxX - minX;
    const h = maxY - minY;
    if (h < 0.10 || w < 0.07) return null;          // too small to be a deliberate stroke

    // Slice path by Y position into 3 thirds based on bounding box
    const t1 = minY + h / 3;
    const t2 = minY + (2 * h) / 3;
    const top = [], mid = [], bot = [];
    for (const p of path) {
      if (p.y <= t1) top.push(p);
      else if (p.y <= t2) mid.push(p);
      else bot.push(p);
    }
    if (top.length < 3 || mid.length < 3 || bot.length < 3) return null;

    const dirOf = (seg) => {
      // Compare a few points from start/end to determine dominant horizontal direction
      const a = seg[0], b = seg[seg.length - 1];
      return b.x - a.x;
    };
    const dTop = dirOf(top);
    const dMid = dirOf(mid);
    const dBot = dirOf(bot);

    // For "S": top goes one way, middle reverses, bottom matches top.
    // (mirror flip makes it "right→left→right" or "left→right→left" raw — both are S-like)
    const isS =
      Math.abs(dTop) > 0.04 &&
      Math.abs(dMid) > 0.04 &&
      Math.abs(dBot) > 0.04 &&
      Math.sign(dTop) !== Math.sign(dMid) &&
      Math.sign(dTop) === Math.sign(dBot);

    if (isS) return 'S';
    return null;
  }

  function drawStrokePath(path) {
    if (!state.ctx || path.length < 2) return;
    const ctx = state.ctx;
    const w = state.canvas.width, h = state.canvas.height;
    ctx.lineWidth = 4;
    ctx.lineCap   = 'round';
    ctx.lineJoin  = 'round';
    ctx.strokeStyle = 'rgba(251, 191, 36, 0.9)';
    ctx.shadowColor = 'rgba(251, 191, 36, 0.85)';
    ctx.shadowBlur  = 12;
    ctx.beginPath();
    // Mirror x because video is CSS-mirrored
    ctx.moveTo((1 - path[0].x) * w, path[0].y * h);
    for (let i = 1; i < path.length; i++) {
      ctx.lineTo((1 - path[i].x) * w, path[i].y * h);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  function drawNavBadge(text, fired) {
    if (!state.ctx) return;
    const ctx = state.ctx;
    const x = 12, y = 12, padX = 10, padY = 6;
    ctx.font = 'bold 13px sans-serif';
    const tw = ctx.measureText(text).width;
    ctx.fillStyle   = fired ? 'rgba(34, 197, 94, 0.85)' : 'rgba(20, 12, 6, 0.85)';
    ctx.strokeStyle = fired ? '#86efac' : 'rgba(251, 191, 36, 0.6)';
    ctx.lineWidth = 1.5;
    roundRect(ctx, x, y, tw + padX * 2, 22 + padY, 6);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = fired ? '#052e16' : '#fde68a';
    ctx.fillText(text, x + padX, y + padY + 14);
  }

  function fireNavAction(detail) {
    try {
      window.dispatchEvent(new CustomEvent('jarvisNavGesture', { detail }));
    } catch (e) { /* ignore */ }
  }
})();
