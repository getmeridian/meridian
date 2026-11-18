// The SVG is the complete first paint. Load geography only when an eligible reader sees the plate.
type Painter = ReturnType<typeof import('./atlas-drawing').atlasPainter>;
const duration = 4400;
export function mountAtlas(figure: HTMLElement) {
  const canvas = figure.querySelector('canvas')!;
  const button = figure.querySelector('button')!;
  const stage = canvas.parentElement!;
  const poster = stage.querySelector('img')!;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  if (!('IntersectionObserver' in window && 'ResizeObserver' in window) || !canvas.getContext) return;

  let painter: Painter | undefined, loading = false, failed = false, visible = false, away = false;
  let held = false, complete = false, back = false, frame = 0, elapsed = 0, start = 0, previous = -Infinity;
  function state(value: 'still' | 'running' | 'paused' | 'settled') {
    figure.dataset.state = value;
    const label = value === 'running' ? button.dataset.pause : value === 'paused' ? button.dataset.resume : button.dataset.replay;
    button.setAttribute('aria-label', label!); button.title = label!;
  }
  function stop() {
    if (frame) {
      cancelAnimationFrame(frame); frame = 0; elapsed = Math.min(duration, performance.now() - start);
    }
  }
  function tick(now: number) {
    const time = Math.min(duration, now - start);
    // No layout reads in the loop. About 30 geographical redraws/s, then zero at rest.
    if (now - previous >= 1000 / 30 - 1 || time === duration) {
      painter!.draw(time / duration, back); previous = now;
    }
    if (time < duration) frame = requestAnimationFrame(tick);
    else { frame = 0; elapsed = duration; complete = true; state('settled'); }
  }
  async function prepare() {
    loading = true;
    try {
      await poster.decode();
      if (reduce.matches || !visible || document.hidden || away) return;
      const { atlasPainter } = await import('./atlas-drawing');
      painter = atlasPainter(canvas);
    } catch {
      failed = true; button.hidden = true; figure.removeAttribute('data-painted'); state('still');
    } finally { loading = false; }
    if (painter) sync();
  }
  function sync() {
    if (reduce.matches) {
      stop(); elapsed = 0; back = held = complete = false; button.hidden = true;
      figure.removeAttribute('data-painted'); state('still'); return;
    }
    if (!visible || document.hidden || away || held || failed) {
      stop(); if (painter && !complete) state('paused'); return;
    }
    if (!painter) {
      if (!loading) void prepare();
      return;
    }
    const resized = painter.size(stage.clientWidth); button.hidden = false;
    if (complete && resized) painter.draw(1, back);
    if (complete || frame) return;
    if (!figure.hasAttribute('data-painted')) {
      painter.draw(0, back); figure.setAttribute('data-painted', '');
    }
    start = performance.now() - elapsed; previous = -Infinity;
    state('running'); frame = requestAnimationFrame(tick);
  }
  button.addEventListener('click', () => {
    if (complete) { back = !back; complete = false; elapsed = 0; held = false; }
    else held = !held;
    sync();
  });
  const observer = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting && entry.intersectionRatio >= .12; sync();
  }, { threshold: [0, .12] });
  observer.observe(stage);
  new ResizeObserver(() => {
    if (painter && visible && !document.hidden && !away && !reduce.matches) {
      if (painter.size(stage.clientWidth)) painter.draw(complete ? 1 : (frame ? Math.min(duration, performance.now() - start) : elapsed) / duration, back);
    }
  }).observe(stage);
  document.addEventListener('visibilitychange', sync);
  reduce.addEventListener('change', sync);
  window.addEventListener('pagehide', () => { away = true; sync(); });
  window.addEventListener('pageshow', () => { away = false; sync(); });
}
