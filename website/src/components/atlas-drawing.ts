// The printed plate and this orthographic view share the same centre, radius and initial bearings.
// Geography is projected once into unit vectors; a moving frame only rotates those vectors.
import coasts from '../assets/globe-coasts.json';
import { chooseRoute, clamp, cx, cy, homePitch, itineraries, journeys, limbFade, networkInk, rad, radius,
  roleAt, roleSize, roll, routeBands, traceStep, visibleLeg, world, type City, type Role, type Vec } from './atlas-network.ts';

const maxPitch = 65 * rad;
const cosRoll = Math.cos(roll), sinRoll = Math.sin(roll);
type Point = [number, number];
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

export function atlasPainter(canvas: HTMLCanvasElement, locale = document.documentElement?.lang || 'en') {
  const code = locale.split('-')[0];
  const language = code === 'fa' || code === 'ru' || code === 'zh' ? code : 'en';
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
  let width = 0, labelSize = 11, lastCycle = -1, selected = 0;
  const labelWidths = new Map<string, number>();
  const nextRoute = (yaw: number, pitch: number, after?: number) => chooseRoute(yaw, pitch, after);
  function size(cssWidth: number) {
    // Canvas dimensions follow the plate, not the screen. Cap raster work on high-density phones.
    const next = Math.max(1, Math.round(cssWidth * Math.min(devicePixelRatio || 1, 2)));
    if (next === width) return false;
    labelSize = Math.max(11, Math.min(16, 10.6 * 534 / Math.max(1, cssWidth)));
    labelWidths.clear();
    width = next; canvas.width = next; canvas.height = Math.round(next * 424 / 534); return true;
  }
  function draw(yawRadians: number, progress: number | null, pitchRadians = homePitch, routeIndex?: number) {
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

    const phase = progress === null ? null : progress - Math.floor(progress);
    if (phase !== null) {
      const cycle = Math.floor(progress!);
      if (routeIndex !== undefined && itineraries[routeIndex]) selected = routeIndex;
      else if (lastCycle !== cycle) selected = nextRoute(yawRadians, pitch, lastCycle < 0 ? undefined : selected);
      lastCycle = cycle;
    }
    const step = phase === null ? null : traceStep(selected, phase);
    const basePaths = routeBands.map(() => new Path2D()), accentPaths = routeBands.map(() => new Path2D());
    const projected = itineraries.map(stops => stops.map(city => point(city.pos)));
    function addLeg(inks: Path2D[], pts: Float32Array) {
      for (const { band, xy } of visibleLeg(pts, point)) {
        const path = inks[band];
        path.moveTo(xy[0], xy[1]);
        for (let i = 2; i < xy.length; i += 2) path.lineTo(xy[i], xy[i + 1]);
      }
    }
    journeys.forEach(legs => legs.forEach(leg => addLeg(basePaths, leg.pts)));
    if (step && step.t > 0) {
      const leg = journeys[selected][step.leg], end = Math.floor(step.t * (leg.pts.length / 3 - 1)) * 3 + 3;
      if (end === leg.pts.length) addLeg(accentPaths, leg.pts);
      else {
        const reached = new Float32Array(end + 3);
        reached.set(leg.pts.subarray(0, end)); reached.set(leg.at(step.t), end);
        addLeg(accentPaths, reached);
      }
    }
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    function paths(inks: Path2D[], strength: number, thick: number) {
      inks.forEach((ink, band) => {
        ctx.globalAlpha = strength * routeBands[band];
        ctx.strokeStyle = networkInk.shadow; ctx.lineWidth = thick + 1.2; ctx.stroke(ink);
        ctx.strokeStyle = networkInk.line; ctx.lineWidth = thick; ctx.stroke(ink);
      });
    }
    const opacity = phase === null ? 0 : Math.min(clamp(phase / .05), clamp((1 - phase) / .07));
    paths(basePaths, .66, 1.1);
    if (step) paths(accentPaths, opacity, 1.9);
    function marker([x, y, depth]: Vec, role: Role) {
      if (depth <= 0) return;
      ctx.globalAlpha = limbFade(depth); ctx.fillStyle = role === 'server' ? networkInk.server : networkInk.shadow;
      ctx.strokeStyle = role === 'server' ? networkInk.shadow : networkInk.marker; ctx.lineWidth = 1.15;
      const r = roleSize[role];
      ctx.beginPath();
      if (role === 'device') ctx.arc(x, y, r, 0, 2*Math.PI);
      else if (role === 'relay') ctx.rect(x-r, y-r, 2*r, 2*r);
      else { ctx.moveTo(x, y-r); ctx.lineTo(x+r, y); ctx.lineTo(x, y+r); ctx.lineTo(x-r, y); ctx.closePath(); }
      ctx.fill(); ctx.stroke();
      if (role === 'device') { ctx.fillStyle = networkInk.marker; ctx.beginPath(); ctx.arc(x,y,1.15,0,2*Math.PI); ctx.fill(); }
    }
    const marks = projected.flat().filter(p => p[2] > .07);
    projected.forEach((stops, route) => stops.forEach((pos, i) => marker(pos, roleAt(i, itineraries[route].length))));
    if (step && phase !== null) {
      const from = step.leg, to = from + 1, cities = itineraries[selected], positions = projected[selected];
      const head = point(journeys[selected][from].at(step.t));
      if (head[2] > 0) {
        ctx.globalAlpha = limbFade(head[2]) * opacity; ctx.fillStyle = '#fff3cf'; ctx.strokeStyle = '#845f2b';
        ctx.lineWidth = .8; ctx.beginPath(); ctx.arc(head[0], head[1], 2.75, 0, 2*Math.PI); ctx.fill(); ctx.stroke();
      }
      // Set names in the current page language. Try both sides of each mark; omit a
      // label rather than covering another city, the globe's rim or the other name.
      ctx.font = '600 ' + labelSize + 'px system-ui, sans-serif'; ctx.direction = language === 'fa' ? 'rtl' : 'ltr';
      ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      const candidates = (city: City, [x, y, z]: Vec) => {
        const name = city.names[language];
        let length = labelWidths.get(name);
        if (length === undefined) { length = ctx.measureText(name).width; labelWidths.set(name, length); }
        const w = length + 7, h = labelSize + 4, pad = 6;
        // A label aligned with the mark's midline can tuck inside the horizon
        // without dropping onto the next city in a short relay chain.
        const inward = (y < cy ? 1 : -1) * pad / 2;
        const spots = z <= .12 ? [] : [[x+pad,y-h/2+inward], [x-w-pad,y-h/2+inward],
          [x+pad,y-h/2], [x-w-pad,y-h/2], [x+pad,y-h-pad/2], [x-w-pad,y-h-pad/2], [x+pad,y+pad/2],
          [x-w-pad,y+pad/2], [x-w/2,y+pad], [x-w/2,y-h-pad]].filter(([left, top]) =>
          ![left,left+w].some(a => [top,top+h].some(b => Math.hypot(a-cx,b-cy) > radius-3)) &&
          !marks.some(([a,b]) => a>left-4 && a<left+w+4 && b>top-4 && b<top+h+4));
        return { name, w, h, spots, fade: clamp((z - .12) / .16) * opacity };
      };
      const dest = candidates(cities[to], positions[to]), origin = candidates(cities[from], positions[from]);
      let d: number[] | undefined = dest.spots[0], o: number[] | undefined = origin.spots[0];
      if (d && o) {
        o = undefined;
        for (const s of dest.spots) {
          const fit = origin.spots.find(t => t[0]+origin.w+2 <= s[0] || s[0]+dest.w+2 <= t[0] ||
            t[1]+origin.h+2 <= s[1] || s[1]+dest.h+2 <= t[1]);
          if (fit) { d = s; o = fit; break; }
        }
      }
      function label(item: typeof dest, spot?: number[]) {
        if (!spot || !item.fade) return;
        const [x,y] = spot;
        ctx.globalAlpha = item.fade * .88; ctx.fillStyle = networkInk.shadow; ctx.fillRect(x,y,item.w,item.h);
        ctx.globalAlpha = item.fade; ctx.fillStyle = '#fff1ce'; ctx.fillText(item.name,x+3.5,y+item.h/2);
      }
      label(dest,d); label(origin,o);
    }
    ctx.restore();
  }
  return { size, draw, nextRoute };
}
