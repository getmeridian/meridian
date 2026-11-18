// The printed plate and this orthographic view share the same centre, radius and initial bearings.
// Geography is projected once into unit vectors; a moving frame only rotates those vectors.
import coasts from '../assets/globe-coasts.json';

const rad = Math.PI / 180, radius = 166, cx = 271, cy = 213;
const homePitch = 26 * rad, maxPitch = 65 * rad;
const cosHome = Math.cos(homePitch), sinHome = Math.sin(homePitch);
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
  return [x, y * cosHome + z * sinHome, z * cosHome - y * sinHome];
}
const hub = bearing(337.5, 161.3);
// Lift the printed Béziers onto the home hemisphere. Reprojecting their points keeps the
// exact illustrated route at home and makes the far part disappear when a visitor spins it.
const links = [[179.5, 139.1, 260.5, 86.2], [193.4, 309.1, 232.4, 201.2], [428.3, 214.1, 398.9, 147.7]].map(
  ([ex, ey, bx, by]) => {
    const at = (t: number) => {
      const u = 1 - t;
      return bearing(u * u * 337.5 + 2 * u * t * bx + t * t * ex, u * u * 161.3 + 2 * u * t * by + t * t * ey);
    };
    const points = new Float32Array(49 * 3);
    for (let i = 0; i <= 48; i++) points.set(at(i / 48), i * 3);
    return { points, at, end: at(1) };
  });
const ease = (t: number) => t * t * (3 - 2 * t);
const clamp = (x: number) => Math.max(0, Math.min(1, x));

type Shape = { world: Float32Array; view: Float64Array };
const shape = (world: Float32Array): Shape => ({ world, view: new Float64Array(world.length) });

// Endpoints are screen positions plus signed camera depth. The plane through the
// camera horizon intersects the great-circle edge where its 3-D chord has depth zero.
function horizon(a: Float64Array, i: number, b: Float64Array, j: number): Point {
  const t = a[i + 2] / (a[i + 2] - b[j + 2]);
  const x = a[i] + (b[j] - a[i]) * t - cx, y = a[i + 1] + (b[j + 1] - a[i + 1]) * t - cy;
  const scale = radius / Math.hypot(x, y);
  return [cx + x * scale, cy + y * scale];
}
const angle = ([x, y]: Point) => Math.atan2(y - cy, x - cx);
type Run = { ring: Float64Array; start: number; end: number; enter: Point; leave: Point; from: number; to: number };

// Keep actual front-facing coast segments. The oriented Natural Earth rings have
// land on the right in screen space, including around inland water. At an exit,
// travel clockwise along the rim to the next entry. This also works with concave
// continents and with the antipode inside a continent; backside vertices never fill the disk.
function landPath(rings: Shape[]) {
  const path = new Path2D(), runs: Run[] = [];
  for (const { view: ring } of rings) {
    const n = ring.length;
    let back = 0;
    while (back < n && ring[back + 2] >= 0) back += 3;
    if (back === n) {
      path.moveTo(ring[0], ring[1]);
      for (let i = 3; i < n; i += 3) path.lineTo(ring[i], ring[i + 1]);
      path.closePath();
      continue;
    }
    let start = 0, enter: Point = [0, 0], prev = back;
    for (let k = 0; k < n; k += 3) {
      const i = prev + 3 === n ? 0 : prev + 3;
      const front = ring[i + 2] >= 0;
      if (front !== (ring[prev + 2] >= 0)) {
        const hit = horizon(ring, prev, ring, i);
        if (front) { start = i; enter = hit; }
        else runs.push({ ring, start, end: prev, enter, leave: hit, from: angle(enter), to: angle(hit) });
      }
      prev = i;
    }
  }
  // Entries and exits alternate on the horizon. Sort each group separately and
  // match them in cyclic order (each run has exactly one predecessor and successor).
  if (!runs.length) return path;
  const starts = runs.map((_, i) => i).sort((a, b) => runs[a].from - runs[b].from);
  const ends = runs.map((_, i) => i).sort((a, b) => runs[a].to - runs[b].to);
  let offset = starts.findIndex(i => runs[i].from >= runs[ends[0]].to);
  if (offset < 0) offset = 0;
  const next = new Int32Array(runs.length), seen = new Uint8Array(runs.length);
  ends.forEach((id, pos) => { next[id] = starts[(pos + offset) % starts.length]; });
  for (let seed = 0; seed < runs.length; seed++) {
    if (seen[seed]) continue;
    let id = seed;
    path.moveTo(...runs[id].enter);
    do {
      seen[id] = 1;
      const r = runs[id];
      for (let i = r.start;; i = (i + 3) % r.ring.length) {
        path.lineTo(r.ring[i], r.ring[i + 1]);
        if (i === r.end) break;
      }
      path.lineTo(...r.leave);
      id = next[id];
      path.arc(cx, cy, radius, r.to, runs[id].from);
    } while (id !== seed);
    path.closePath();
  }
  return path;
}

function frontLine(path: Path2D, view: Float64Array) {
  let pen = view[2] >= 0;
  if (pen) path.moveTo(view[0], view[1]);
  for (let i = 3; i < view.length; i += 3) {
    const front = view[i + 2] >= 0;
    if (pen !== front) {
      const hit = horizon(view, i - 3, view, i);
      if (front) path.moveTo(...hit); else path.lineTo(...hit);
    }
    if (front) path.lineTo(view[i], view[i + 1]);
    pen = front;
  }
}

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
  const continents = land.map(shape), graticules = grid.map(shape), equatorialLine = shape(equator);
  const routes = links.map(link => shape(link.points));
  const tail = new Float64Array(6);
  let width = 0;
  function size(cssWidth: number) {
    // Canvas dimensions follow the plate, not the screen. Cap raster work on high-density phones.
    const next = Math.max(1, Math.round(cssWidth * Math.min(devicePixelRatio || 1, 2)));
    if (next === width) return false;
    width = next; canvas.width = next; canvas.height = Math.round(next * 424 / 534); return true;
  }
  function draw(yawRadians: number, progress: number | null, pitchRadians = homePitch) {
    const pitch = Math.max(-maxPitch, Math.min(maxPitch, pitchRadians));
    const cos = Math.cos(yawRadians), sin = Math.sin(yawRadians), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const xx = radius * (cos * cosRoll - sin * sp * sinRoll), xy = radius * cp * sinRoll;
    const xz = radius * (-sin * cosRoll - cos * sp * sinRoll);
    const yx = radius * (cos * sinRoll + sin * sp * cosRoll), yy = -radius * cp * cosRoll;
    const yz = radius * (-sin * sinRoll + cos * sp * cosRoll), zx = sin * cp, zz = cos * cp;
    function project(line: Shape) {
      const src = line.world, dst = line.view;
      for (let i = 0; i < src.length; i += 3) {
        const x = src[i], y = src[i + 1], z = src[i + 2];
        dst[i] = cx + xx * x + xy * y + xz * z;
        dst[i + 1] = cy + yx * x + yy * y + yz * z;
        dst[i + 2] = zx * x + sp * y + zz * z;
      }
      return dst;
    }
    function point([x, y, z]: Vec): Vec {
      return [cx + xx * x + xy * y + xz * z, cy + yx * x + yy * y + yz * z, zx * x + sp * y + zz * z];
    }
    ctx.setTransform(canvas.width / 534, 0, 0, canvas.height / 424, 0, 0);
    ctx.clearRect(0, 0, 534, 424);
    ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, radius + .25, 0, 2 * Math.PI); ctx.clip();
    ctx.fillStyle = sea; ctx.fillRect(cx - 167, cy - 167, 334, 334);
    if (water) { ctx.fillStyle = water; ctx.fillRect(cx - 167, cy - 167, 334, 334); }
    continents.forEach(project);
    const coast = landPath(continents);
    ctx.fillStyle = earth; ctx.fill(coast, 'evenodd');
    ctx.lineWidth = .48; ctx.strokeStyle = '#c4c1aa'; ctx.stroke(coast);
    if (hatch) { ctx.fillStyle = hatch; ctx.fill(coast, 'evenodd'); }
    const lines = new Path2D();
    for (const line of graticules) frontLine(lines, project(line));
    ctx.strokeStyle = 'rgba(191,202,202,.49)'; ctx.lineWidth = .65; ctx.stroke(lines);
    const equatorial = new Path2D(); frontLine(equatorial, project(equatorialLine));
    ctx.setLineDash([3, 2]); ctx.strokeStyle = 'rgba(226,214,183,.80)'; ctx.lineWidth = .8; ctx.stroke(equatorial); ctx.setLineDash([]);
    ctx.fillStyle = shade; ctx.fillRect(cx - 167, cy - 167, 334, 334);
    ctx.beginPath(); ctx.arc(cx, cy, radius - .35, 0, 2 * Math.PI); ctx.strokeStyle = '#64717a'; ctx.lineWidth = .9; ctx.stroke();

    const [sx, sy, hubDepth] = point(hub);
    function circle(x: number, y: number, r: number, fill: string, stroke?: string) {
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fillStyle = fill; ctx.fill();
      if (stroke) { ctx.lineWidth = 1; ctx.strokeStyle = stroke; ctx.stroke(); }
    }
    ctx.lineCap = 'round';
    links.forEach(({ at, end }, index) => {
      const route = new Path2D(); frontLine(route, project(routes[index]));
      ctx.strokeStyle = 'rgba(21,33,51,.74)'; ctx.lineWidth = 3.6; ctx.stroke(route);
      ctx.strokeStyle = '#f4c478'; ctx.lineWidth = 1.7; ctx.stroke(route);
      const [ex, ey, depth] = point(end);
      if (progress !== null) {
        const t = (progress - (.07 + index * .19)) / .46;
        if (t > 0 && t < 1) {
          const head = ease(t);
          let a = point(at(clamp(head - 4 * .024)));
          for (let i = 4; i > 0; --i) {
            const b = point(at(clamp(head - (i - 1) * .024)));
            tail.set(a); tail.set(b, 3); a = b;
            const segment = new Path2D(); frontLine(segment, tail);
            ctx.lineWidth = 3 - i * .2;
            ctx.strokeStyle = 'rgba(255,242,190,' + (.94 - i * .13) + ')'; ctx.stroke(segment);
          }
          if (a[2] >= 0) circle(a[0], a[1], 3, '#fff5d3', '#b98a43');
        }
      }
      if (depth < 0) return;
      circle(ex, ey, 8, '#192738', '#f6cb79'); circle(ex, ey, 3, '#ffda8e');
      const echo = progress === null ? -1 : (progress - (.07 + index * .19 + .46)) / .12;
      if (echo > 0 && echo < 1) {
        ctx.beginPath(); ctx.arc(ex, ey, 8 + 7 * ease(echo), 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(255,219,149,' + (1 - echo) * .9 + ')'; ctx.lineWidth = 1.35; ctx.stroke();
      }
    });
    if (hubDepth >= 0) {
      ctx.setLineDash([2, 2]); circle(sx, sy, 14, '#293446', '#f6cf86'); ctx.setLineDash([]);
      ctx.save(); ctx.translate(sx, sy); ctx.rotate(Math.PI / 4);
      ctx.fillStyle = '#e7b45f'; ctx.fillRect(-6.4, -6.4, 12.8, 12.8);
      ctx.strokeStyle = '#fff0c9'; ctx.strokeRect(-6.4, -6.4, 12.8, 12.8);
      ctx.fillStyle = '#263343'; ctx.fillRect(-2.4, -2.4, 4.8, 4.8); ctx.restore();
    }
    ctx.restore();
  }
  return { size, draw };
}
