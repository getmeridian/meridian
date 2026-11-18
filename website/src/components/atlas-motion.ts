// The SVG is the complete first paint. Geography loads on sight, or on explicit input under reduced motion.
type Painter = ReturnType<typeof import('./atlas-drawing').atlasPainter>;
type State = 'still' | 'running' | 'paused' | 'settled' | 'dragging' | 'coasting';
const rad = Math.PI / 180, home = 26 * rad, duration = 4400, friction = 190;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const wrap = (v: number) => Math.atan2(Math.sin(v), Math.cos(v));
const latitude = (v: number) => clamp(v, -65 * rad, 65 * rad);

export function mountAtlas(figure: HTMLElement) {
  const canvas = figure.querySelector('canvas')!;
  const grab = figure.querySelector<HTMLButtonElement>('[data-atlas-grab]')!;
  const motion = figure.querySelector<HTMLButtonElement>('[data-atlas-motion]')!;
  const hint = figure.querySelector<HTMLElement>('.hero__gesture')!;
  const stage = canvas.parentElement!, poster = stage.querySelector('img')!;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  if (!('PointerEvent' in window && 'IntersectionObserver' in window && 'ResizeObserver' in window)) return;

  let painter: Painter | undefined, loading = false, failed = false, visible = false, away = false, manual = false;
  let yaw = 0, pitch = home, signal: number | null = null;
  let story: { from: number; delta: number; lat: number; far: boolean; time: number } | undefined;
  let paused = false, vx = 0, vy = 0, coasting = false, dirty = true;
  let frame = 0, last = 0, lastPaint = -Infinity, suppressClick = false;
  let pointer: { id: number; x: number; y: number; lastX: number; lastY: number; at: number; radius: number; moved: boolean } | undefined;
  const awake = () => visible && !document.hidden && !away;
  const allowed = () => !reduce.matches || manual;
  function state(value: State) {
    if (figure.dataset.state === value) return;
    figure.dataset.state = value;
    const label = value === 'running' ? motion.dataset.pause : value === 'paused' ? motion.dataset.resume : motion.dataset.replay;
    motion.setAttribute('aria-label', label!); motion.title = label!;
  }
  function paint(now: number) {
    painter!.draw(yaw, signal, pitch);
    if (!figure.hasAttribute('data-painted')) figure.setAttribute('data-painted', '');
    dirty = false; lastPaint = now;
  }
  function cancelFrame() {
    cancelAnimationFrame(frame); frame = 0; last = 0;
  }
  function schedule() {
    if (!frame && painter && awake() && allowed() && (dirty || coasting || (story && !paused))) {
      if (!last) last = performance.now();
      frame = requestAnimationFrame(tick);
    }
  }
  function tick(now: number) {
    frame = 0;
    // Use elapsed time, not frame counts. A suspended tab can't accumulate a giant fling.
    const dt = clamp(now - last, 0, 48); last = now;
    if (story && !paused) {
      story.time = Math.min(duration, story.time + dt);
      const t = story.time / duration, u = Math.min(1, story.far ? t / .31 : t), ease = u*u*(3-2*u);
      yaw = wrap(story.from + story.delta * ease); pitch = story.lat + (home - story.lat) * ease;
      signal = story.far && t < .28 ? null : story.far ? (t - .28) / .72 : t;
      // A short narrative at 30 fps; publish every available frame during manipulation.
      if (now - lastPaint >= 1000 / 30 - 1 || t === 1) paint(now);
      if (t === 1) { story = undefined; signal = null; state('settled'); }
    } else if (coasting) {
      const damping = Math.exp(-dt / friction), distance = friction * (1 - damping);
      yaw = wrap(yaw + vx * distance);
      const next = pitch + vy * distance; pitch = latitude(next); if (pitch !== next) vy = 0;
      vx *= damping; vy *= damping;
      if (Math.hypot(vx, vy) < .00010) { coasting = false; state('settled'); }
      paint(now);
    } else if (dirty) paint(now);
    if (!coasting && (!story || paused)) last = 0;
    schedule();
  }
  function resetPose() {
    story = undefined; coasting = paused = false; vx = vy = 0; signal = null; dirty = true;
  }
  function trace(destination: number) {
    resetPose();
    if (reduce.matches) { yaw = destination; pitch = home; state('settled'); }
    else {
      const delta = wrap(destination - yaw);
      story = { from: yaw, delta, lat: pitch, far: Math.abs(delta) > 45*rad || Math.abs(pitch-home) > 20*rad, time: 0 };
      state('running');
    }
  }
  async function prepare() {
    loading = true;
    try {
      await poster.decode();
      if (!awake() || !allowed()) return;
      const { atlasPainter } = await import('./atlas-drawing');
      if (!awake() || !allowed()) return;
      painter = atlasPainter(canvas);
    } catch {
      failed = true; grab.hidden = hint.hidden = motion.hidden = true;
      figure.removeAttribute('data-painted'); state('still');
    } finally { loading = false; }
    if (painter) sync();
  }
  function sync() {
    if (failed) return;
    if (!awake()) {
      cancelFrame(); dropPointer(); coasting = false;
      if (story) state('paused');
      else if (figure.dataset.state === 'coasting') state('settled');
      return;
    }
    grab.hidden = hint.hidden = false;
    motion.hidden = !painter || reduce.matches;
    if (!allowed()) return;
    if (!painter) { if (!loading) void prepare(); return; }
    if (painter.size(stage.clientWidth)) dirty = true;
    if (!manual && !story && figure.dataset.state === 'still') trace(16 * rad);
    if (story) state(paused ? 'paused' : 'running');
    if (dirty) paint(performance.now());
    schedule();
  }
  function deliberate() {
    manual = true;
    if (!painter && !loading && awake() && !failed) void prepare();
  }
  function dropPointer() {
    const p = pointer; pointer = undefined;
    if (!p) return;
    if (p.moved) { suppressClick = true; state('settled'); }
    if (grab.hasPointerCapture(p.id)) grab.releasePointerCapture(p.id);
  }
  grab.addEventListener('pointerdown', e => {
    if (!e.isPrimary) { dropPointer(); return; }
    if (e.button !== 0 || pointer) return;
    suppressClick = false;
    if (coasting) { coasting = false; state('settled'); }
    pointer = { id: e.pointerId, x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY,
      at: e.timeStamp, radius: Math.max(1, grab.getBoundingClientRect().width / 2), moved: false };
    grab.setPointerCapture(e.pointerId);
  });
  grab.addEventListener('pointermove', e => {
    const p = pointer;
    if (!p || p.id !== e.pointerId) return;
    if (!p.moved) {
      const x = e.clientX-p.x, y = e.clientY-p.y;
      if (Math.hypot(x,y) < 5) return;
      if (e.pointerType === 'touch' && Math.abs(y) > Math.abs(x)*1.15) {
        suppressClick = true; dropPointer(); return;
      }
      resetPose(); p.moved = true; deliberate(); state('dragging');
    }
    const dx = -(e.clientX-p.lastX) / p.radius, dy = (e.clientY-p.lastY) / p.radius;
    const dt = clamp(e.timeStamp-p.at, 4, 100), blend = 1 - Math.exp(-dt/30);
    vx += (clamp(dx/dt, -.008, .008) - vx) * blend;
    vy += (clamp(dy/dt, -.005, .005) - vy) * blend;
    yaw = wrap(yaw + dx); pitch = latitude(pitch + dy); dirty = true;
    p.lastX = e.clientX; p.lastY = e.clientY; p.at = e.timeStamp; schedule();
  });
  grab.addEventListener('pointerup', e => {
    const p = pointer;
    if (!p || p.id !== e.pointerId) return;
    const flick = p.moved && !reduce.matches && e.timeStamp - p.at < 90 && Math.hypot(vx,vy) > .00035;
    dropPointer();
    if (flick) { coasting = true; state('coasting'); schedule(); }
  });
  for (const name of ['pointercancel', 'lostpointercapture'] as const) {
    grab.addEventListener(name, e => { if (pointer?.id === e.pointerId) dropPointer(); });
  }
  grab.addEventListener('click', e => {
    if (e.detail && suppressClick) { suppressClick = false; return; }
    deliberate(); trace(reduce.matches ? 0 : 16 * rad); sync();
  });
  grab.addEventListener('keydown', e => {
    if (e.altKey || e.metaKey || e.ctrlKey || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home'].includes(e.key)) return;
    e.preventDefault(); deliberate(); resetPose();
    if (e.key === 'Home') { yaw = 0; pitch = home; }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') yaw = wrap(yaw + (e.key === 'ArrowLeft' ? -15 : 15)*rad);
    else pitch = latitude(pitch + (e.key === 'ArrowUp' ? 10 : -10)*rad);
    state('settled'); schedule();
  });
  motion.addEventListener('click', () => {
    deliberate();
    if (story) { paused = !paused; cancelFrame(); }
    else trace(Math.abs(wrap(yaw - 16*rad)) < 8*rad ? 0 : 16*rad);
    sync();
  });
  const observer = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting && entry.intersectionRatio >= .12; sync();
  }, { threshold: [0, .12] });
  observer.observe(stage);
  new ResizeObserver(sync).observe(stage);
  document.addEventListener('visibilitychange', sync);
  reduce.addEventListener('change', () => {
    cancelFrame(); dropPointer(); resetPose(); manual = false; yaw = 0; pitch = home;
    figure.removeAttribute('data-painted'); state('still'); sync();
  });
  window.addEventListener('pagehide', () => { away = true; sync(); });
  window.addEventListener('pageshow', () => { away = false; sync(); });
}
