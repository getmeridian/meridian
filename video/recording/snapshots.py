"""Turn a raw cast into sanitized screen snapshots that the video renders.

    uv run --with pyte python video/recording/snapshots.py setup --address 203.0.113.9

Replays ~/.cache/meridian-video/raw/<name>.cast through a terminal emulator,
caps idle gaps, and writes video/public/terminal/<name>.json. Every screen line
is sanitized before it leaves this script: the server's real address becomes
DEMO_ADDRESS, unknown hostnames become `.example` names, and long tokens
(URL secrets, keys) become same-length dummies. The script refuses to write if
any other IPv4 address survives.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import string
import sys
from collections.abc import Callable
from pathlib import Path

import pyte

ROOT = Path(__file__).resolve().parents[2]
RAW = Path.home() / ".cache" / "meridian-video" / "raw"
OUT = ROOT / "video" / "public" / "terminal"
FPS = 30
GAP_CAP = 1.2  # seconds; longer silences (SSH, apt, health polls) are shortened to this
READING_CAP = 5.0  # the recorder's pause before typing shows the question; keep it
DEMO_ADDRESS = "198.51.100.7"
# Hostnames that may appear as they are; anything else is a neighbour or a real deployment.
PUBLIC_HOSTS = {"www.google.com", "dl.google.com", "www.cloudflare.com", "getmeridian.org", "github.com"}
NOT_HOSTS = {"cluster.yml", "servers.json", "install.sh", "env.sh"}
HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.I)
TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{16,}(?![A-Za-z0-9])")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ALLOWED_NETS = [ipaddress.ip_network(net) for net in ("198.51.100.0/24", "127.0.0.0/8", "0.0.0.0/32")]
STYLE_FIELDS = ("fg", "bg", "bold", "italic", "underline", "reverse")


def _same_length(seed: str, length: int, alphabet: str) -> str:
    digest = hashlib.sha256(seed.encode()).digest()
    while len(digest) < length:
        digest += hashlib.sha256(digest).digest()
    return "".join(alphabet[byte % len(alphabet)] for byte in digest[:length])


FAKE_HOSTS = ("shop.nordhost.example", "cdn.fjordweb.example", "mail.birchcloud.example", "news.lakeview.example")


_fakes: dict[str, str] = {}


def _fake_host(host: str) -> str:
    """A readable fictional name under .example, assigned in order of first appearance."""
    key = host.lower()
    if key not in _fakes:
        index = len(_fakes)
        _fakes[key] = FAKE_HOSTS[index] if index < len(FAKE_HOSTS) else f"site{index}.hosting.example"
    return _fakes[key]


def _host(match: re.Match[str]) -> str:
    name = match.group(0)
    lowered = name.lower()
    if lowered in PUBLIC_HOSTS or lowered in NOT_HOSTS or lowered.endswith(".example"):
        return name
    if re.fullmatch(r"[\d.]+", name):
        return name
    return _fake_host(name)


Cells = list[tuple[str, int]]  # (character, column it takes its style from)


def _sub(cells: Cells, pattern: str | re.Pattern[str], replace: Callable[[re.Match[str]], str]) -> Cells:
    """re.sub over cells: a replacement borrows the styles of the text it replaces, whatever its length."""
    text = "".join(char for char, _ in cells)
    out: Cells = []
    last = 0
    for match in re.finditer(pattern, text):
        out += cells[last : match.start()]
        sources = [source for _, source in cells[match.start() : match.end()]] or [cells[match.start()][1]]
        out += [(char, sources[min(i, len(sources) - 1)]) for i, char in enumerate(replace(match))]
        last = match.end()
    return out + cells[last:]


def sanitize(cells: Cells, address: str) -> Cells:
    demo = DEMO_ADDRESS.ljust(len(address))[: max(len(address), len(DEMO_ADDRESS))]
    cells = _sub(cells, re.escape(address), lambda _: demo)
    # While the address is being typed, its prefixes sit before the cursor.
    for size in range(len(address) - 1, 1, -1):
        cells = _sub(cells, rf"(?<![\d.]){re.escape(address[:size])}(?=\s|$)", lambda _, size=size: demo[:size])
    cells = _sub(cells, re.escape(str(Path.home())), lambda _: "~")
    cells = _sub(cells, HOST, _host)
    alphabet = string.ascii_lowercase + string.digits
    return _sub(cells, TOKEN, lambda m: _same_length(m.group(0), len(m.group(0)), alphabet))


def _check(line: str) -> None:
    for match in IPV4.findall(line):
        try:
            ip = ipaddress.ip_address(match)
        except ValueError:
            continue
        if not any(ip in net for net in ALLOWED_NETS):
            raise SystemExit(f"Refusing to write: {match} survived sanitizing in {line.strip()!r}")


def snapshot(screen: pyte.Screen, address: str, styles: dict[tuple, int]) -> dict:
    lines = []
    for y in range(screen.lines):
        row = screen.buffer[y]
        cells = sanitize([(row[x].data, x) for x in range(screen.columns)], address)[: screen.columns]
        cells += [(" ", screen.columns - 1)] * (screen.columns - len(cells))
        _check("".join(char for char, _ in cells))
        runs: list[list] = []
        for char, x in cells:
            cell = row[x]
            style = (cell.fg, cell.bg, cell.bold, cell.italics, cell.underscore, cell.reverse)
            index = styles.setdefault(style, len(styles))
            if runs and runs[-1][1] == index:
                runs[-1][0] += char
            else:
                runs.append([char, index])
        # Trailing blanks on the default background carry nothing; drop them to keep the file small.
        while runs and runs[-1][0].strip() == "" and _plain(runs[-1][1], styles):
            runs.pop()
        if runs and _plain(runs[-1][1], styles):
            runs[-1][0] = runs[-1][0].rstrip()
        lines.append(runs)
    return {"lines": lines, "cursor": [screen.cursor.x, screen.cursor.y, not screen.cursor.hidden]}


def _plain(index: int, styles: dict[tuple, int]) -> bool:
    style = next(style for style, value in styles.items() if value == index)
    return style[1] == "default" and not style[5]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--address", required=True, help="The recorded server's real address, replaced everywhere")
    args = parser.parse_args(argv)

    header, *events = [json.loads(line) for line in (RAW / f"{args.name}.cast").read_text().splitlines()]
    screen = pyte.Screen(header["width"], header["height"])
    stream = pyte.Stream(screen)
    styles: dict[tuple, int] = {}
    frames: list[dict] = []
    markers: dict[str, float] = {}
    clock = previous = 0.0
    for time, kind, data in events:
        clock += min(max(time - previous, 0.0), READING_CAP if kind == "i" else GAP_CAP)
        previous = time
        if kind == "m":
            markers[data] = round(clock, 3)
            continue
        if kind != "o":
            continue
        stream.feed(data.replace("\x1b[6n", ""))
        frame = {"t": round(clock, 3), **snapshot(screen, args.address, styles)}
        if frames and frames[-1]["lines"] == frame["lines"] and frames[-1]["cursor"] == frame["cursor"]:
            continue
        if frames and clock - frames[-1]["t"] < 1 / FPS:
            frames[-1] = {**frame, "t": frames[-1]["t"]}  # several writes inside one video frame
            continue
        frames.append(frame)

    # Most lines repeat from frame to frame: store each once and refer to it by index.
    rows: dict[str, int] = {}
    for frame in frames:
        frame["lines"] = [rows.setdefault(json.dumps(line, ensure_ascii=False), len(rows)) for line in frame["lines"]]
    out = {
        "width": header["width"],
        "height": header["height"],
        "duration": round(clock + 2.0, 3),
        "markers": markers,
        "styles": [dict(zip(STYLE_FIELDS, style)) for style, _ in sorted(styles.items(), key=lambda item: item[1])],
        "rows": [json.loads(row) for row in rows],
        "frames": frames,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.name}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {path.relative_to(ROOT)}: {len(frames)} frames, {out['duration']}s, markers {markers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
