"""Regenerate the landing globe's coastlines without a network request or dependencies.

Source: Natural Earth 1:110m land (public domain), packaged as world-atlas 2.0.2:
https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json
Run: python3 website/figures/globe_coasts.py /path/to/land-110m.json > website/src/assets/globe-coasts.json

Each ring is [longitude × 10, latitude × 10, …], without the repeated closing vertex.
Keep source winding, including the Caspian hole. Rings that cross ±180° are continuous on a sphere.
"""

import hashlib
import json
import math
import sys
from itertools import groupby
from pathlib import Path

SOURCE_SHA256 = "ead5f68119c49a9250902e7da303bcb209341bbb8fefe7369a439b48b704658a"
Point = tuple[int, int]
Vector = tuple[float, float, float]


def vector(point: Point) -> Vector:
    lon, lat = (math.radians(n / 10) for n in point)
    return math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)


def simplify(ring: list[Point]) -> list[Point]:
    if len(ring) <= 12:  # Preserve the outlines of the small islands.
        return ring
    sphere = [vector(p) for p in [*ring, ring[0]]]
    kept = {0}
    remaining = [(0, len(ring))]
    while remaining:
        first, last = remaining.pop()
        a, b = sphere[first], sphere[last]
        edge = [y - x for x, y in zip(a, b)]
        length2 = sum(v * v for v in edge)
        # Douglas–Peucker on the unit sphere: half a CSS pixel at radius 166
        # in any orthographic view, including at the poles or antimeridian.
        max_error = (0.5 / 166) ** 2
        split = None
        for i in range(first + 1, last):
            offset = [y - x for x, y in zip(a, sphere[i])]
            t = min(1, max(0, sum(v * d for v, d in zip(offset, edge)) / length2)) if length2 else 0
            error = sum((v - t * d) ** 2 for v, d in zip(offset, edge))
            if error > max_error:
                max_error, split = error, i
        if split is not None:
            kept.add(split)
            remaining.extend(((first, split), (split, last)))
    return [ring[i] for i in sorted(kept)] if len(kept) >= 3 else ring


def main(path: Path) -> None:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise SystemExit("Expected the pinned world-atlas 2.0.2 land-110m.json; source checksum differs.")
    data = json.loads(raw)
    sx, sy = data["transform"]["scale"]
    tx, ty = data["transform"]["translate"]
    arcs: list[list[Point]] = []
    for arc in data["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((round((x * sx + tx) * 10), round((y * sy + ty) * 10)))
        arcs.append(points)

    rings = []
    for geometry in data["objects"]["land"]["geometries"]:
        for polygon in geometry["arcs"]:
            for indices in polygon:
                points = []
                for index in indices:
                    arc = arcs[index] if index >= 0 else arcs[~index][::-1]
                    points.extend(arc[1:] if points else arc)
                ring = [point for point, _ in groupby(points)]
                if ring.pop() != ring[0]:
                    raise ValueError("Unclosed Natural Earth coastline")
                rings.append([v for point in simplify(ring) for v in point])
    print("[" + ",\n".join(json.dumps(r, separators=(",", ":")) for r in rings) + "]")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 globe_coasts.py /path/to/land-110m.json")
    main(Path(sys.argv[1]))
