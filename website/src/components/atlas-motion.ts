// The engraving is the first paint. Load geography only when seen, or on deliberate input under reduced motion.
type Painter = ReturnType<typeof import('./atlas-drawing').atlasPainter>;
type State = 'still' | 'spinning' | 'tracing' | 'paused' | 'settled' | 'dragging' | 'coasting';
const rad = Math.PI / 180, home = 26 * rad, turn = 2.25 * rad / 1000, period = 1000 / 24;
const friction = 190, passage = 8500, tapPassage = 3200;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const wrap = (v: number) => Math.atan2(Math.sin(v), Math.cos(v));
const latitude = (v: number) => clamp(v, -65 * rad, 65 * rad);

export function mountAtlas(figure: HTMLElement, loadPainter = () => import('./atlas-drawing')) {
  const canvas = figure.querySelector('canvas')!;
  const grab = figure.querySelector<HTMLButtonElement>('[data-atlas-grab]')!;
  const motion = figure.querySelector<HTMLButtonElement>('[data-atlas-motion]')!;
  const stage = canvas.parentElement!, poster = stage.querySelector('img')!;
  // On a direct link to (for example) #setup, browsers can show the hero for a few
  // frames before scrolling to the bookmark. Keep the engraving through that jump.
  const target = window.location.hash && document.getElementById(window.location.hash.slice(1));
  const bookmarkUntil = target && target.getBoundingClientRect().top > stage.getBoundingClientRect().bottom
    ? performance.now() + 650 : 0;
  const preference = matchMedia('(prefers-reduced-motion: reduce)');
  // Reading this live query every frame can swallow Chromium’s change event.
  let reduced = preference.matches;
  if (!('PointerEvent' in window && 'IntersectionObserver' in window && 'ResizeObserver' in window)) return;

  let painter: Painter | undefined, loading = false, failed = false, visible = false, away = false, manual = false;
  let yaw = 0, pitch = home, phase = 0, route: number | undefined, signal: number | null = 0;
  let paused = false, tracing = false, coasting = false, ramp = 0, vx = 0, vy = 0, dirty = true;
  let frame = 0, timer = 0, bookmarkTimer = 0, last = 0, deadline = 0, suppressClick = false;
  let pointer: { id: number; x: number; y: number; lastX: number; lastY: number; at: number; radius: number; moved: boolean } | undefined;
  const awake = () => visible && !document.hidden && !away;
  const allowed = () => !reduced || manual;
  const moving = () => !pointer && !reduced && (!paused || tracing);
  function state(value: State) {
    if (figure.dataset.state !== value) figure.dataset.state = value;
    const held = paused && !tracing;
    if (motion.hasAttribute('data-paused') !== held || !motion.title) {
      motion.toggleAttribute('data-paused', held);
      const label = held ? motion.dataset.resume! : motion.dataset.pause!;
      motion.setAttribute('aria-label', label); motion.title = label;
    }
  }
  function describe() {
    state(pointer?.moved ? 'dragging' : coasting ? 'coasting' : reduced ? manual ? 'settled' : 'still'
      : paused && !tracing ? 'paused' : tracing ? 'tracing' : 'spinning');
  }
  function paint() {
    painter!.draw(yaw, signal, pitch, route);
    if (!figure.hasAttribute('data-painted')) figure.setAttribute('data-painted', '');
    dirty = false;
  }
  function stop() {
    if (timer) window.clearTimeout(timer);
    if (frame) cancelAnimationFrame(frame);
    frame = timer = last = deadline = 0;
  }
  function schedule(immediate = false) {
    if (!painter || !awake() || !allowed() || (!dirty && !moving() && !coasting)) return;
    if (immediate && timer) { window.clearTimeout(timer); timer = 0; }
    if (frame || timer) return;
    const now = performance.now(); if (!last) last = now;
    const delay = immediate || pointer || coasting ? 0 : Math.max(0, deadline - now - 8);
    if (delay < 2) frame = requestAnimationFrame(tick);
    else timer = window.setTimeout(() => { timer = 0; frame = requestAnimationFrame(tick); }, delay);
  }
  function tick(now: number) {
    frame = 0;
    const dt = clamp(now - last, 0, 80); last = now;
    if (coasting) {
      // Ease into the normal turn instead of snapping to a stop or fighting the next grab.
      const baseline = paused || reduced ? 0 : turn, damping = Math.exp(-dt / friction);
      const distance = friction * (1 - damping);
      yaw = wrap(yaw + baseline * dt + (vx - baseline) * distance);
      const next = pitch + vy * distance; pitch = latitude(next); if (pitch !== next) vy = 0;
      vx = baseline + (vx - baseline) * damping; vy *= damping;
      if (Math.hypot(vx - baseline, vy) < .00010) { coasting = false; ramp = 1; chooseRoute(false); describe(); }
      dirty = true;
    } else if (moving()) {
      ramp = Math.min(1, ramp + dt / 750);
      if (!paused) yaw = wrap(yaw + turn * ramp * dt);
      const interval = Math.floor(phase);
      phase += dt / (tracing ? tapPassage : passage);
      if (Math.floor(phase) !== interval) {
        tracing = false; route = undefined;
        if (paused) signal = null;
        describe();
      }
      if (!paused || tracing) signal = phase;
      dirty = true;
    }
    if (dirty) paint();
    if (moving() && !coasting) {
      deadline = Math.max(now - period, Math.min(deadline || now, now + period)) + period;
    } else { deadline = 0; if (!coasting) last = 0; }
    schedule();
  }
  function chooseRoute(next: boolean) {
    if (!painter) return;
    phase = Math.floor(phase) + 1;
    route = painter.nextRoute(yaw, pitch, next ? route : undefined);
    signal = reduced ? phase + .50 : phase;
    dirty = true;
  }
  function restAfterInput() {
    ramp = 0; coasting = tracing = false;
    if (!paused && !reduced) chooseRoute(false);
    describe(); schedule(true);
  }
  async function prepare() {
    loading = true;
    try {
      await poster.decode();
      if (!awake() || !allowed()) return;
      const { atlasPainter } = await loadPainter();
      if (!awake() || !allowed()) return;
      painter = atlasPainter(canvas);
      if (manual && signal !== null) chooseRoute(false);
    } catch {
      failed = true; grab.hidden = motion.hidden = true;
      figure.removeAttribute('data-painted'); state('still');
    } finally { loading = false; }
    if (painter) sync();
  }
  function sync() {
    if (failed) return;
    if (!awake() || !allowed()) { window.clearTimeout(bookmarkTimer); bookmarkTimer = 0; }
    if (!awake()) {
      stop(); releasePointer(); coasting = false; ramp = 0;
      if (painter && !reduced) state('paused');
      return;
    }
    grab.hidden = false; motion.hidden = !painter || reduced;
    if (!allowed()) return;
    if (!painter) {
      const delay = bookmarkUntil - performance.now();
      if (!manual && delay > 0) {
        if (!bookmarkTimer) bookmarkTimer = window.setTimeout(() => { bookmarkTimer = 0; sync(); }, delay + 1);
      } else if (!loading) void prepare();
      return;
    }
    if (painter.size(stage.clientWidth)) dirty = true;
    if (dirty) paint();
    describe(); schedule();
  }
  function deliberate() {
    manual = true; window.clearTimeout(bookmarkTimer); bookmarkTimer = 0;
    if (!painter && !loading && awake() && !failed) void prepare();
  }
  function releasePointer() {
    const p = pointer; pointer = undefined;
    if (!p) return;
    if (p.moved) suppressClick = true;
    if (grab.hasPointerCapture(p.id)) grab.releasePointerCapture(p.id);
  }
  grab.addEventListener('pointerdown', e => {
    if (!e.isPrimary) { releasePointer(); restAfterInput(); return; }
    if (e.button !== 0 || pointer) return;
    suppressClick = false; coasting = false; vx = vy = 0; stop();
    pointer = { id: e.pointerId, x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY,
      at: e.timeStamp, radius: Math.max(1, grab.getBoundingClientRect().width / 2), moved: false };
    grab.setPointerCapture(e.pointerId);
  });
  grab.addEventListener('pointermove', e => {
    const p = pointer;
    if (!p || p.id !== e.pointerId) return;
    if (!p.moved) {
      const x = e.clientX - p.x, y = e.clientY - p.y;
      if (Math.hypot(x, y) < 5) return;
      if (e.pointerType === 'touch' && Math.abs(y) > Math.abs(x) * 1.15) {
        suppressClick = true; releasePointer(); describe(); schedule(); return;
      }
      tracing = false; signal = null; p.moved = true; deliberate(); describe();
    }
    const dx = -(e.clientX - p.lastX) / p.radius, dy = (e.clientY - p.lastY) / p.radius;
    const dt = clamp(e.timeStamp - p.at, 4, 100), blend = 1 - Math.exp(-dt / 30);
    vx += (clamp(dx / dt, -.008, .008) - vx) * blend; vy += (clamp(dy / dt, -.005, .005) - vy) * blend;
    yaw = wrap(yaw + dx); pitch = latitude(pitch + dy); dirty = true;
    p.lastX = e.clientX; p.lastY = e.clientY; p.at = e.timeStamp; schedule(true);
  });
  grab.addEventListener('pointerup', e => {
    const p = pointer;
    if (!p || p.id !== e.pointerId) return;
    const flick = p.moved && !paused && !reduced && e.timeStamp - p.at < 90 && Math.hypot(vx, vy) > .00035;
    releasePointer();
    if (flick) { coasting = true; describe(); schedule(true); }
    else if (p.moved) restAfterInput();
    else { describe(); schedule(); }
  });
  for (const name of ['pointercancel', 'lostpointercapture'] as const) {
    grab.addEventListener(name, e => {
      if (pointer?.id !== e.pointerId) return;
      const moved = pointer.moved; releasePointer();
      if (moved) restAfterInput(); else { describe(); schedule(); }
    });
  }
  grab.addEventListener('click', e => {
    if (e.detail && suppressClick) { suppressClick = false; return; }
    deliberate(); coasting = false; tracing = !reduced; signal = 0; chooseRoute(true); dirty = true;
    describe(); sync();
  });
  grab.addEventListener('keydown', e => {
    if (e.altKey || e.metaKey || e.ctrlKey || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home'].includes(e.key)) return;
    e.preventDefault(); deliberate(); stop(); coasting = tracing = false; signal = null;
    if (e.key === 'Home') { yaw = 0; pitch = home; }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') yaw = wrap(yaw + (e.key === 'ArrowLeft' ? -15 : 15) * rad);
    else pitch = latitude(pitch + (e.key === 'ArrowUp' ? 10 : -10) * rad);
    dirty = true; restAfterInput();
  });
  motion.addEventListener('click', () => {
    const wasMoving = !paused || tracing;
    paused = wasMoving; tracing = coasting = false; ramp = 0; stop(); describe(); sync();
  });
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting && entry.intersectionRatio >= .12; sync();
  }, { threshold: [0, .12] }).observe(stage);
  new ResizeObserver(sync).observe(stage);
  document.addEventListener('visibilitychange', sync);
  preference.addEventListener('change', event => {
    reduced = event.matches; stop(); releasePointer();
    manual = tracing = coasting = false; yaw = phase = ramp = 0; pitch = home; route = undefined; signal = 0; dirty = true;
    figure.removeAttribute('data-painted'); state('still'); sync();
  });
  window.addEventListener('pagehide', () => { away = true; sync(); });
  window.addEventListener('pageshow', () => { away = false; sync(); });
}
