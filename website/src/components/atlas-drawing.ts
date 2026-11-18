// The printed plate and this orthographic view share the same centre, radius and initial bearings.
// Geography is projected once into unit vectors; a moving frame only rotates those vectors.
import coasts from '../assets/globe-coasts.json';

const rad = Math.PI / 180, radius = 166, cx = 271, cy = 213;
const cosTilt = Math.cos(26 * rad), sinTilt = Math.sin(26 * rad);
const cosRoll = Math.cos(13 * rad), sinRoll = Math.sin(13 * rad);
type Vec = [number, number, number];
type Point = [number, number];
const world = (lon: number, lat: number): Vec => {
  const c = Math.cos(lat * rad), l = (lon + 25) * rad;
  return [c * Math.sin(l), Math.sin(lat * rad), c * Math.cos(l)];
};
function line(coords: number[], divisor = 1) {
  const out = new Float32Array(coords.length * 1.5);
  for (let i = 0; i < coords.length; i += 2) out.set(world(coords[i] / divisor, coords[i + 1] / divisor), i * 1.5);
  return out;
}
const land = coasts.map(ring => line(ring, 10));
const grid: Float32Array[] = [];
for (let lon = -180; lon < 180; lon += 15) {
  const a: number[] = [];
  for (let lat = -90; lat <= 90; lat += 3) a.push(lon, lat);
  grid.push(line(a));
}
for (let lat = -75; lat <= 75; lat += 15) {
  if (lat === 0) continue;
  const a: number[] = [];
  for (let lon = -180; lon <= 180; lon += 3) a.push(lon, lat);
  grid.push(line(a));
}
const equator = line(Array.from({ length: 121 }, (_, i) => [-180 + 3 * i, 0]).flat());
// Recover the geographic positions of the four marks from the static illustration.
function bearing(x: number, y: number): Vec {
  x = (x - cx) / radius; y = (cy - y) / radius;
  [x, y] = [x * cosRoll - y * sinRoll, x * sinRoll + y * cosRoll];
  const z = Math.sqrt(1 - x * x - y * y);
  return [x, y * cosTilt + z * sinTilt, z * cosTilt - y * sinTilt];
}
const hub = bearing(337.5, 161.3);
// Store each printed Bézier control relative to its chord, so the whole arc turns with its endpoints.
const links = [[179.5, 139.1, 260.5, 86.2], [193.4, 309.1, 232.4, 201.2], [428.3, 214.1, 398.9, 147.7]].map(
  ([x, y, bx, by]) => {
    const dx = x - 337.5, dy = y - 161.3, len2 = dx * dx + dy * dy;
    return { end: bearing(x, y), along: ((bx - 337.5) * dx + (by - 161.3) * dy) / len2,
      bend: ((by - 161.3) * dx - (bx - 337.5) * dy) / len2 };
  });
const ease = (t: number) => t * t * (3 - 2 * t);
const clamp = (x: number) => Math.max(0, Math.min(1, x));
const finalYaw = 16 * rad;

export function atlasPainter(canvas: HTMLCanvasElement) {
  const c = canvas.getContext('2d');
  if (!c) throw new Error('Canvas unavailable');
  const ctx = c;
  const hatchTile = document.createElement('canvas');
  hatchTile.width = hatchTile.height = 7;
  const ink = hatchTile.getContext('2d')!;
  ink.strokeStyle = 'rgba(39,56,74,.50)'; ink.lineWidth = .85;
  for (const offset of [-7, 0, 7]) {
    ink.beginPath(); ink.moveTo(offset - 3, 7); ink.lineTo(offset + 4, 0); ink.stroke();
  }
  const hatch = ctx.createPattern(hatchTile, 'repeat');
  hatch?.setTransform(new DOMMatrix().scale(.65));
  const waterTile = document.createElement('canvas');
  waterTile.width = waterTile.height = 4;
  const waterInk = waterTile.getContext('2d')!;
  waterInk.fillStyle = 'rgba(220,229,223,.32)'; waterInk.beginPath(); waterInk.arc(1, 1, .38, 0, 2 * Math.PI); waterInk.fill();
  const water = ctx.createPattern(waterTile, 'repeat');
  const sea = ctx.createRadialGradient(210, 134, 6, 261, 199, 199);
  sea.addColorStop(0, '#465567'); sea.addColorStop(.58, '#293a4b'); sea.addColorStop(1, '#182536');
  const earth = ctx.createLinearGradient(134, 78, 397, 365);
  earth.addColorStop(0, '#dad6bd'); earth.addColorStop(.5, '#c2bfa5'); earth.addColorStop(1, '#859394');
  const shade = ctx.createRadialGradient(251, 186, 98, 264, 205, 174);
  shade.addColorStop(0, 'rgba(19,32,50,0)'); shade.addColorStop(.78, 'rgba(19,32,50,.15)');
  shade.addColorStop(1, 'rgba(19,32,50,.48)');
  let width = 0;
  function size(cssWidth: number) {
    // Canvas dimensions follow the plate, not the screen. Cap raster work on high-density phones.
    const next = Math.max(1, Math.round(cssWidth * Math.min(devicePixelRatio || 1, 2)));
    if (next === width) return false;
    width = next; canvas.width = next; canvas.height = Math.round(next * 424 / 534); return true;
  }
  function draw(progress: number, back: boolean) {
    const phase = ease(clamp(progress)), yaw = finalYaw * (back ? 1 - phase : phase);
    const cos = Math.cos(yaw), sin = Math.sin(yaw);
    ctx.setTransform(canvas.width / 534, 0, 0, canvas.height / 424, 0, 0);
    ctx.clearRect(0, 0, 534, 424);
    ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, radius + .25, 0, 2 * Math.PI); ctx.clip();
    ctx.fillStyle = sea; ctx.fillRect(cx - 167, cy - 167, 334, 334);
    if (water) { ctx.fillStyle = water; ctx.fillRect(cx - 167, cy - 167, 334, 334); }
    function trace(path: Path2D, pts: Float32Array, coast: boolean) {
      let pen = false;
      for (let i = 0; i < pts.length; i += 3) {
        const x = pts[i] * cos - pts[i + 2] * sin, z = pts[i] * sin + pts[i + 2] * cos;
        const y = pts[i + 1] * cosTilt - z * sinTilt, depth = pts[i + 1] * sinTilt + z * cosTilt;
        // Pin the far coast just outside the circular clip: crossing rings follow the limb
        // without drawing a chord across the ocean. Meridian lines simply stop behind it.
        if (!coast && depth < 0) { pen = false; continue; }
        const mag = depth < 0 ? (radius + 3) / Math.max(.0001, Math.hypot(x, y)) : radius;
        const xx = cx + (x * cosRoll + y * sinRoll) * mag, yy = cy - (y * cosRoll - x * sinRoll) * mag;
        if (pen) path.lineTo(xx, yy); else path.moveTo(xx, yy);
        pen = true;
      }
      if (coast) path.closePath();
    }
    const coast = new Path2D();
    for (const ring of land) trace(coast, ring, true);
    ctx.fillStyle = earth; ctx.fill(coast, 'evenodd');
    ctx.lineWidth = .48; ctx.strokeStyle = '#c4c1aa'; ctx.stroke(coast);
    if (hatch) { ctx.fillStyle = hatch; ctx.fill(coast, 'evenodd'); }
    const lines = new Path2D();
    for (const l of grid) trace(lines, l, false);
    ctx.strokeStyle = 'rgba(191,202,202,.49)'; ctx.lineWidth = .65; ctx.stroke(lines);
    const equatorial = new Path2D(); trace(equatorial, equator, false);
    ctx.setLineDash([3, 2]); ctx.strokeStyle = 'rgba(226,214,183,.80)'; ctx.lineWidth = .8; ctx.stroke(equatorial); ctx.setLineDash([]);
    ctx.fillStyle = shade; ctx.fillRect(cx - 167, cy - 167, 334, 334);
    ctx.beginPath(); ctx.arc(cx, cy, radius - .35, 0, 2 * Math.PI); ctx.strokeStyle = '#64717a'; ctx.lineWidth = .9; ctx.stroke();

    const point = ([x, y, z]: Vec): Point => {
      const a = x * cos - z * sin, b = y * cosTilt - (x * sin + z * cos) * sinTilt;
      return [cx + radius * (a * cosRoll + b * sinRoll), cy - radius * (b * cosRoll - a * sinRoll)];
    };
    const [sx, sy] = point(hub);
    function circle(x: number, y: number, r: number, fill: string, stroke?: string) {
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fillStyle = fill; ctx.fill();
      if (stroke) { ctx.lineWidth = 1; ctx.strokeStyle = stroke; ctx.stroke(); }
    }
    ctx.lineCap = 'round';
    links.forEach(({ end, along, bend }, index) => {
      const [ex, ey] = point(end), dx = ex - sx, dy = ey - sy;
      const bx = sx + dx * along - dy * bend, by = sy + dy * along + dx * bend;
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.quadraticCurveTo(bx, by, ex, ey);
      ctx.strokeStyle = 'rgba(21,33,51,.74)'; ctx.lineWidth = 3.6; ctx.stroke();
      ctx.strokeStyle = '#f4c478'; ctx.lineWidth = 1.7; ctx.stroke();
      // A short, tapered signal travels from the server to each device in turn.
      const t = (progress - (.07 + index * .19)) / .46;
      if (t > 0 && t < 1) {
        const position = (v: number): Point => {
          const u = 1 - v; return [u * u * sx + 2 * u * v * bx + v * v * ex, u * u * sy + 2 * u * v * by + v * v * ey];
        };
        const head = ease(t);
        for (let i = 4; i > 0; --i) {
          const a = position(clamp(head - i * .024)), b = position(clamp(head - (i - 1) * .024));
          ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.lineWidth = 3 - i * .2;
          ctx.strokeStyle = 'rgba(255,242,190,' + (.94 - i * .13) + ')'; ctx.stroke();
        }
        const p = position(head); circle(p[0], p[1], 3, '#fff5d3', '#b98a43');
      }
      circle(ex, ey, 8, '#192738', '#f6cb79'); circle(ex, ey, 3, '#ffda8e');
      const echo = (progress - (.07 + index * .19 + .46)) / .12;
      if (echo > 0 && echo < 1) {
        ctx.beginPath(); ctx.arc(ex, ey, 8 + 7 * ease(echo), 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(255,219,149,' + (1 - echo) * .9 + ')'; ctx.lineWidth = 1.35; ctx.stroke();
      }
    });
    ctx.setLineDash([2, 2]); circle(sx, sy, 14, '#293446', '#f6cf86'); ctx.setLineDash([]);
    ctx.save(); ctx.translate(sx, sy); ctx.rotate(Math.PI / 4);
    ctx.fillStyle = '#e7b45f'; ctx.fillRect(-6.4, -6.4, 12.8, 12.8);
    ctx.strokeStyle = '#fff0c9'; ctx.strokeRect(-6.4, -6.4, 12.8, 12.8);
    ctx.fillStyle = '#263343'; ctx.fillRect(-2.4, -2.4, 4.8, 4.8); ctx.restore();
    ctx.restore();
  }
  return { size, draw };
}
