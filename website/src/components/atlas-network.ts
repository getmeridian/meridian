// City-centre coordinates in degrees, not the locations of Meridian users or servers.
// Each independent example runs from a device through zero or one relay to a server.
export const rad = Math.PI / 180;
export type Vec = [number, number, number];
export const world = (lon: number, lat: number): Vec => {
  const c = Math.cos(lat * rad), l = (lon + 25) * rad;
  return [c * Math.sin(l), Math.sin(lat * rad), c * Math.cos(l)];
};
function city(lon: number, lat: number, en: string, ru: string, fa: string, zh: string) {
  return { lon, lat, pos: world(lon, lat), names: { en, ru, fa, zh } };
}
export const itineraries = [
  [city(73.37, 54.99, 'Omsk', 'Омск', 'اومسک', '鄂木斯克'),
   city(37.62, 55.75, 'Moscow', 'Москва', 'مسکو', '莫斯科'),
   city(4.90, 52.37, 'Amsterdam', 'Амстердам', 'آمستردام', '阿姆斯特丹')],
  [city(-46.63, -23.55, 'São Paulo', 'Сан-Паулу', 'سائو پائولو', '圣保罗'),
   city(-38.52, -3.73, 'Fortaleza', 'Форталеза', 'فورتالزا', '福塔莱萨'),
   city(-9.14, 38.72, 'Lisbon', 'Лиссабон', 'لیسبون', '里斯本')],
  [city(36.82, -1.29, 'Nairobi', 'Найроби', 'نایروبی', '内罗毕'),
   city(18.42, -33.93, 'Cape Town', 'Кейптаун', 'کیپ‌تاون', '开普敦')],
  [city(121.56, 25.03, 'Taipei', 'Тайбэй', 'تایپه', '台北'),
   city(103.82, 1.35, 'Singapore', 'Сингапур', 'سنگاپور', '新加坡'),
   city(115.86, -31.95, 'Perth', 'Перт', 'پرت', '珀斯')],
  [city(-123.12, 49.28, 'Vancouver', 'Ванкувер', 'ونکوور', '温哥华'),
   city(-149.90, 61.22, 'Anchorage', 'Анкоридж', 'انکوریج', '安克雷奇'),
   city(139.69, 35.68, 'Tokyo', 'Токио', 'توکیو', '东京')],
  [city(-157.86, 21.31, 'Honolulu', 'Гонолулу', 'هونولولو', '火奴鲁鲁'),
   city(174.76, -36.85, 'Auckland', 'Окленд', 'اوکلند', '奥克兰')],
  [city(-70.67, -33.45, 'Santiago', 'Сантьяго', 'سانتیاگو', '圣地亚哥'),
   city(-58.38, -34.60, 'Buenos Aires', 'Буэнос-Айрес', 'بوئنوس آیرس', '布宜诺斯艾利斯')],
];
export type City = typeof itineraries[number][number];
export type Role = 'device' | 'relay' | 'server';
export const roleAt = (i: number, length: number): Role => i === 0 ? 'device' : i === length - 1 ? 'server' : 'relay';
export const clamp = (v: number) => Math.max(0, Math.min(1, v));
export const ease = (t: number) => t * t * (3 - 2 * t);
export const limbFade = (depth: number) => ease(clamp(depth / .23));
export const routeBands = [.04, .19, .44, .77, 1];

function leg(a: Vec, b: Vec) {
  const angle = Math.acos(Math.max(-1, Math.min(1, a[0]*b[0] + a[1]*b[1] + a[2]*b[2])));
  const s = Math.sin(angle), count = Math.max(8, Math.ceil(angle / (2.5 * rad)));
  // Spherical interpolation follows the shorter great-circle arc, including across the date line.
  const at = (t: number): Vec => {
    const u = Math.sin((1 - t) * angle) / s, v = Math.sin(t * angle) / s;
    return [u*a[0] + v*b[0], u*a[1] + v*b[1], u*a[2] + v*b[2]];
  };
  const pts = new Float32Array((count + 1) * 3);
  for (let i = 0; i <= count; i++) pts.set(at(i / count), i * 3);
  return { pts, at };
}
export const journeys = itineraries.map(stops => stops.slice(1).map((to, i) => leg(stops[i].pos, to.pos)));
export function traceStep(route: number, phase: number) {
  const legs = journeys[route].length;
  const p = clamp((phase - .06) / .88) * legs, leg = Math.min(legs - 1, Math.floor(p));
  // Hold briefly at the relay before beginning the next leg.
  return { leg, t: ease(clamp((p - leg - .04) / .87)) };
}

export function chooseRoute(yaw: number, pitch: number, after?: number) {
  const cp = Math.cos(pitch), sp = Math.sin(pitch), dx = Math.sin(yaw)*cp, dz = Math.cos(yaw)*cp;
  const ranked = itineraries.map((stops, id) => {
    const depths = stops.map(({ pos: [x,y,z] }) => x*dx + y*sp + z*dz);
    const minimum = Math.min(...depths);
    const centre = depths.reduce((sum, d) => sum + clamp(d), 0) / stops.length;
    return { id, score: centre + 1.2 * minimum, whole: minimum > .10 };
  });
  if (after === undefined && Math.cos(yaw) > .98 && ranked[0].whole) return 0;
  const whole = ranked.filter(r => r.whole);
  const options = whole.length ? whole : ranked;
  options.sort((a,b) => b.score - a.score);
  const prominent = options.filter(r => r.score >= options[0].score - .68);
  if (after !== undefined) {
    // Rotate through equally readable alternatives in an invariant order on successive taps.
    for (let n = 1; n <= itineraries.length; n++) {
      const id = (after + n) % itineraries.length;
      if (prominent.some(r => r.id === id)) return id;
    }
  }
  return options[0].id;
}

export const homePitch = 26 * rad, radius = 166, cx = 271, cy = 213;
export const roll = 13 * rad;
export const networkInk = { shadow: '#19293b', line: '#f1cd8b', marker: '#f4d49d', server: '#e6b567' };
export const roleSize = { device: 3.5, relay: 4, server: 5 };
export const homeView = ([x, y, z]: Vec): Vec => {
  const v = y * Math.cos(homePitch) - z * Math.sin(homePitch);
  return [cx + radius*(x*Math.cos(roll) + v*Math.sin(roll)), cy + radius*(x*Math.sin(roll) - v*Math.cos(roll)),
    y*Math.sin(homePitch) + z*Math.cos(homePitch)];
};

export type ScreenLeg = { band: number; xy: number[] };
export function visibleLeg(pts: Float32Array, project: (p: Vec) => Vec): ScreenLeg[] {
  const parts: ScreenLeg[] = [];
  let a = project([pts[0], pts[1], pts[2]]);
  for (let i = 3; i < pts.length; i += 3) {
    const b = project([pts[i], pts[i + 1], pts[i + 2]]);
    if (a[2] > 0 || b[2] > 0) {
      let ax = a[0], ay = a[1], bx = b[0], by = b[1];
      if (a[2] < 0 || b[2] < 0) {
        const t = a[2] / (a[2] - b[2]);
        const x = ax + (bx - ax) * t - cx, y = ay + (by - ay) * t - cy;
        const k = radius / Math.hypot(x, y);
        if (a[2] < 0) { ax = cx+x*k; ay = cy+y*k; } else { bx = cx+x*k; by = cy+y*k; }
      }
      const fade = limbFade((Math.max(0, a[2]) + Math.max(0, b[2])) * .5);
      const band = Math.min(routeBands.length - 1, Math.floor(fade * routeBands.length));
      const part = parts.at(-1);
      if (a[2] >= 0 && part?.band === band) part.xy.push(bx, by);
      else parts.push({ band, xy: [ax, ay, bx, by] });
    }
    a = b;
  }
  return parts;
}
