import assert from 'node:assert/strict';
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

function frame(yaw, pitch) {
  let land;
  const marks = [], strokes = new Map();
  const nothing = () => {};
  const gradient = { addColorStop: nothing };
  const ctx = new Proxy({
    createRadialGradient: () => gradient, createLinearGradient: () => gradient, createPattern: () => null,
    fill(path, rule) { if (rule === 'evenodd') land = path; },
    stroke(path) {
      if (path?.d) strokes.set(this.strokeStyle, (strokes.get(this.strokeStyle) ?? 0) + 1);
    },
    arc(x, y, r) { if (r < 30) marks.push([x, y]); },
  }, { get: (obj, key) => obj[key] ?? nothing });
  atlasPainter({ getContext: () => ctx, width: 534, height: 424 }).draw(yaw * Math.PI / 180, null, pitch * Math.PI / 180);
  return {
    marks, strokes,
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
  const home = frame(0, 26), reverse = frame(180, -26);
  assert.equal(new Set(home.marks.map(String)).size, 4, 'the server and three devices are visible at home');
  assert.equal(reverse.marks.length, 0, 'all four locations are behind the globe');
  // At home all three routes have the same ink; their paths must be empty at the antipodal pose.
  const routeInks = [...home.strokes].filter(([, count]) => count === 3);
  assert.ok(routeInks.length > 0, 'three home routes were drawn');
  for (const [ink] of routeInks) assert.equal(reverse.strokes.get(ink) ?? 0, 0, 'no route may cross the reverse face');
});
