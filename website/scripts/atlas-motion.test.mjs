import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { mountAtlas } from '../src/components/atlas-motion.ts';

// A synthetic clock checks the whole scheduling contract, including waking a sleeping globe.
// Native pointer and touch gestures are also exercised against the built site in real browsers.
test('the atlas remembers Pause and sleeps offscreen or under reduced motion', async () => {
  const { window } = new JSDOM('<figure data-state="still"><div><img><canvas></canvas><button data-atlas-grab></button><button data-atlas-motion data-pause="Pause" data-resume="Resume"></button></div></figure>', { pretendToBeVisual: true });
  const { document } = window, fig = document.querySelector('figure'), control = fig.querySelector('[data-atlas-motion]');
  const grab = fig.querySelector('[data-atlas-grab]'), media = new window.EventTarget(), queue = new Map(), poses = [];
  let clock = 1, id = 0, observer, loads = 0;
  media.matches = true;
  function later(fn, delay) { queue.set(++id, { fn, at: clock + delay }); return id; }
  function advance(dt) {
    const end = clock + dt;
    for (;;) {
      const task = [...queue].sort((a, b) => a[1].at - b[1].at)[0];
      if (!task || task[1].at > end) break;
      queue.delete(task[0]); clock = task[1].at; task[1].fn(clock);
    }
    clock = end;
  }
  function motionPreference(reduce) {
    media.matches = reduce;
    media.dispatchEvent(Object.assign(new window.Event('change'), { matches: reduce }));
  }
  function visible(v) { observer([{ isIntersecting: v, intersectionRatio: v ? 1 : 0 }]); }
  window.IntersectionObserver = class { constructor(cb) { observer = cb; } observe() {} };
  window.ResizeObserver = class { observe() {} };
  window.setTimeout = later; window.clearTimeout = n => queue.delete(n);
  Object.assign(globalThis, { window, document, IntersectionObserver: window.IntersectionObserver, ResizeObserver: window.ResizeObserver,
    matchMedia: () => media, requestAnimationFrame: cb => later(cb, 16), cancelAnimationFrame: n => queue.delete(n) });
  const original = Object.getOwnPropertyDescriptor(globalThis, 'performance');
  Object.defineProperty(globalThis, 'performance', { configurable: true, value: { now: () => clock } });
  fig.querySelector('img').decode = async () => {};
  try {
    mountAtlas(fig, async () => {
      loads++;
      return { atlasPainter: () => ({ size: () => false, draw: (yaw, phase) => poses.push([yaw, phase]), nextRoute: () => 0 }) };
    });
    visible(true); advance(5000);
    assert.equal(loads, 0); assert.equal(poses.length, 0, 'a reduced-motion visit uses only the poster');
    motionPreference(false); await new Promise(resolve => setImmediate(resolve)); advance(12000);
    assert.equal(loads, 1); assert.equal(fig.dataset.state, 'spinning');
    assert.ok(poses.length > 200 && poses.length < 320, 'sustain a modest paint rate over an entire route');
    assert.ok(poses.at(-1)[0] > .4 && poses.at(-1)[0] < .5, 'slow turn continues past the first passage');
    assert.ok(poses.at(-1)[1] > 1, 'the next itinerary has begun');
    control.click(); assert.equal(fig.dataset.state, 'paused'); assert.equal(queue.size, 0);
    const paused = poses.length;
    visible(false); visible(true); advance(6000);
    assert.equal(poses.length, paused, 'scrolling away and back must not undo an explicit pause');
    control.click(); advance(1000);
    assert.equal(fig.dataset.state, 'spinning'); assert.ok(poses.length > paused);
    visible(false); const hidden = poses.length; advance(10000);
    assert.equal(poses.length, hidden); assert.equal(queue.size, 0, 'no frame or timer wakes an offscreen globe');
    visible(true); advance(500); assert.ok(poses.length > hidden);
    grab.click(); assert.equal(fig.dataset.state, 'tracing');
    motionPreference(true); assert.equal(fig.dataset.state, 'still'); assert.equal(queue.size, 0);
    grab.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight' })); advance(40);
    assert.equal(fig.dataset.state, 'settled'); assert.equal(queue.size, 0);
    const deliberate = poses.length; advance(10000); assert.equal(poses.length, deliberate);
  } finally {
    Object.defineProperty(globalThis, 'performance', original); window.close();
  }
});
