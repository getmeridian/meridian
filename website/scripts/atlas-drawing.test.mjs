import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chooseRoute, homeView, itineraries, journeys, networkInk, rad, roleSize, traceStep, visibleLeg } from '../src/components/atlas-network.ts';
import { registerHooks } from 'node:module';
import test from 'node:test';
import sharp from 'sharp';

// Vite supplies JSON imports in production; let Node 24 load the same asset and TS module.
const hook = registerHooks({ resolve(specifier, context, next) {
  const result = next(specifier, context);
  return specifier.endsWith('/globe-coasts.json') ? { ...result, importAttributes: { type: 'json' } } : result;
} });
const { atlasPainter } = await import('../src/components/atlas-drawing.ts');
hook.deregister();

class Path {
  d = '';
  moveTo(x, y) { this.d += `M${x},${y}`; }
  lineTo(x, y) { this.d += `L${x},${y}`; }
  closePath() { this.d += 'Z'; }
  arc(x, y, r, start, end) {
    // The canvas angles increase clockwise. Preserve the long arc as well as the short one.
    const at = t => `${x + r * Math.cos(t)},${y + r * Math.sin(t)}`;
    const turn = 2 * Math.PI, sweep = (end - start + turn) % turn;
    this.d += (this.d ? 'L' : 'M') + at(start);
    if (end - start >= turn) this.d += `A${r},${r} 0 0 1 ${at(start + Math.PI)}`;
    this.d += `A${r},${r} 0 ${Number(sweep > Math.PI)} 1 ${at(end)}`;
  }
}

function frame(yaw, pitch, phase = null, route, lang = "en") {
  let land;
  const devices = [], active = [], names = [];
  const nothing = () => {};
  const gradient = { addColorStop: nothing };
  const ctx = new Proxy({
    createRadialGradient: () => gradient, createLinearGradient: () => gradient, createPattern: () => null,
    fill(path, rule) { if (rule === 'evenodd') land = path; },
    stroke(path) { if (path?.d && this.strokeStyle === networkInk.line && this.lineWidth > 1.5) active.push(path); },
    arc(x, y, r) { if (r === roleSize.device) devices.push([x, y]); },
    measureText(s) { return { width: [...s].length * 6 }; },
    fillText(name) { if (this.globalAlpha > .05) names.push(name); },
  }, { get: (obj, key) => obj[key] ?? nothing });
  atlasPainter({ getContext: () => ctx, width: 534, height: 424 }, lang).draw(yaw * rad, phase, pitch * rad, route);
  return {
    devices, active, names,
    async landAtCentre() {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="270.5 212.5 1 1"><path fill-rule="evenodd" d="${land.d}"/></svg>`;
      return (await sharp(Buffer.from(svg)).ensureAlpha().raw().toBuffer())[3] > 128;
    },
  };
}

test('rotating the atlas keeps the Atlantic water and hides the routes on its far side', async (t) => {
  // Tile canvases carry only the print texture. Render the real land path independently through SVG.
  globalThis.Path2D = Path;
  globalThis.document = { createElement: () => ({ getContext: () => ({
    beginPath() {}, moveTo() {}, lineTo() {}, arc() {}, stroke() {}, fill() {},
  }) }) };
  t.after(() => { delete globalThis.Path2D; delete globalThis.document; });

  // Home longitude is 25°W. At 40°W, 26°N the antipode lies inside Australia, which used to fill the sea.
  assert.equal(await frame(-15, 26).landAtCentre(), false);
  assert.equal(await frame(159, -25).landAtCentre(), true, 'central Australia is land');
  const [omsk] = itineraries[0], [x, y] = homeView(omsk.pos);
  const home = frame(0, 26, .3, 0), reverse = frame(180, -26, .3, 0);
  assert.ok(home.devices.some(([a,b]) => Math.hypot(a-x, b-y) < .01), 'device is anchored to Omsk');
  assert.ok(home.active.length > 0);
  assert.equal(reverse.active.length, 0, 'no part of the far-side relay route is highlighted through the globe');
  assert.ok(reverse.devices.length > 0, 'independent Pacific devices come into view');
  assert.deepEqual(reverse.names, []);
  assert.deepEqual(frame(35, 45, .25, 0, 'ru').names.sort(), ['Москва', 'Омск']);
  assert.deepEqual(frame(35, 45, .75, 0, 'ru').names.sort(), ['Амстердам', 'Москва']);
  assert.deepEqual(frame(35, 45).names, [], 'static poses carry no moving labels');
});

test('a relay receives the trace before forwarding it; all examples follow the near great-circle path', () => {
  assert.equal(chooseRoute(0, 26 * rad), 0);
  const first = traceStep(0, .49), second = traceStep(0, .5);
  assert.deepEqual(first, { leg: 0, t: 1 }); assert.deepEqual(second, { leg: 1, t: 0 });
  assert.deepEqual(itineraries[0].map(c => c.names.en), ['Omsk', 'Moscow', 'Amsterdam']);
  const antimeridian = journeys[5][0].at(.5);
  const longitude = Math.atan2(antimeridian[0], antimeridian[2]) / rad - 25;
  assert.ok(longitude > -180 && longitude < -165, 'Honolulu to Auckland crosses the Pacific, not Eurasia');
  assert.equal(visibleLeg(journeys[0][0].pts, p => [p[0], p[1], -1]).length, 0);
  // At polar and date-line views a tap must still have a whole visible itinerary.
  for (const lat of [-65,-30,0,30,65]) for (let yaw=-180; yaw<180; yaw+=15) {
    const stops = itineraries[chooseRoute(yaw * rad, lat * rad)];
    for (const city of stops) {
      const depth = Math.sin(city.lat*rad)*Math.sin(lat*rad) + Math.cos(city.lat*rad)*Math.cos(lat*rad)*Math.cos((city.lon+25-yaw)*rad);
      assert.ok(depth > 0, `${city.names.en} is behind the selected face at ${yaw},${lat}`);
    }
  }
  assert.notEqual(chooseRoute(0, 26*rad, 0), 0, 'a tap can choose another readable Atlantic route');
});

test('the static globe has the same home network as the interactive atlas', () => {
  execFileSync(process.execPath, [fileURLToPath(new URL('../figures/atlas-network.mjs', import.meta.url)), '--check']);
});
