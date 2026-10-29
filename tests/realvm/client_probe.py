"""Connect through a delivered Xray config the way a phone does, with no Meridian involved.

`meridian test` asks the control service for the client's config; an outage drill
needs a config fetched beforehand, so a client keeps working while the service is down:

    python -m tests.realvm.client_probe fetch SUB_URL saved.json    # before the outage
    python -m tests.realvm.client_probe connect saved.json          # during and after

`fetch` saves the subscription's Xray JSON (asked for with a Happ User-Agent).
`connect` runs each config in it through a local Xray with a SOCKS inbound and fetches
Cloudflare's trace page (and `--download BYTES`, for traffic accounting), printing one
PASS/FAIL line per config; it exits 1 if any failed.
Xray comes from `MERIDIAN_TEST_XRAY` or the lab's cached `home/bin/xray-*`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
DOWNLOAD_URL = "https://speed.cloudflare.com/__down?bytes={size}"


def _xray() -> str:
    explicit = os.environ.get("MERIDIAN_TEST_XRAY", "")
    if explicit:
        return explicit
    home = Path(os.environ.get("MERIDIAN_HOME", ""))
    # Newest by version, not by name: "xray-26.9.1" sorts after "xray-26.10.1" as text.
    found = sorted(
        (home / "bin").glob("xray-*") if home.name else [],
        key=lambda path: [int(part) for part in re.findall(r"\d+", path.name)],
    )
    if not found:
        sys.exit("No Xray: set MERIDIAN_TEST_XRAY or source a lab env.sh whose home has bin/xray-*.")
    return str(found[-1])


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def fetch(url: str, out: Path) -> int:
    # Lab servers carry real certificates; a self-signed one is itself a failure worth seeing.
    response = httpx.get(url, headers={"User-Agent": "Happ/1.0"}, timeout=20)
    response.raise_for_status()
    configs = response.json()
    configs = configs if isinstance(configs, list) else [configs]
    if not configs:
        sys.exit("The subscription returned no configs.")
    out.write_text(json.dumps(configs, indent=2))
    print(f"saved {len(configs)} config(s)")
    return 0


def _listening(port: int, process: subprocess.Popen[bytes], seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and process.poll() is None:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _curl(port: int, *args: str, seconds: int) -> subprocess.CompletedProcess[str]:
    # curl, not httpx: SOCKS support would need another dependency. --fail: an error page is no egress.
    command = ["curl", "-sS", "--fail", "--max-time", str(seconds), "--socks5-hostname", f"127.0.0.1:{port}", *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _probe(xray: str, config: dict[str, object], download: int = 0) -> tuple[bool, str]:
    port = _free_port()
    inbound = {"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"udp": True}}
    config = {**config, "inbounds": [inbound]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(config, handle)  # holds the client's credentials: removed whatever happens below
    process: subprocess.Popen[bytes] | None = None
    try:
        # Xray's log goes to a file, not a pipe nobody reads, which would fill and stall it mid-download.
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([xray, "run", "-c", handle.name], stdout=subprocess.DEVNULL, stderr=log)
            if not _listening(port, process):
                log.seek(0)
                return False, "xray never listened: " + log.read().decode(errors="replace")[-200:]
            # A balancer routes to its fallback until the observatory's first round, so retry briefly.
            for attempt in range(3):
                trace = _curl(port, TRACE_URL, seconds=15)
                egress = next((item for item in trace.stdout.splitlines() if item.startswith("ip=")), "")
                if egress or attempt == 2:
                    break
                time.sleep(1)
            if not egress:
                return False, f"no egress ({trace.stderr.strip()[-120:]})"
            if not download:
                return True, f"egress {egress}"
            pulled = _curl(
                port, "-o", os.devnull, "-w", "%{size_download}", DOWNLOAD_URL.format(size=download), seconds=120
            )
            got = int(pulled.stdout) if pulled.stdout.isdigit() else 0
            return pulled.returncode == 0 and got >= download, f"egress {egress}, downloaded {got} of {download} bytes"
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        Path(handle.name).unlink(missing_ok=True)


def connect(saved: Path, download: int = 0) -> int:
    xray = _xray()
    configs = json.loads(saved.read_text())
    if not configs:
        print(f"FAIL {saved}: no configs saved")
        return 1
    failed = 0
    for config in configs:
        name = str(config.get("remarks", "config"))
        ok, detail = _probe(xray, config, download)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.realvm.client_probe", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch", help="Save a subscription's Xray JSON")
    fetch_cmd.add_argument("url")
    fetch_cmd.add_argument("out", type=Path)
    connect_cmd = sub.add_parser("connect", help="Connect through every saved config")
    connect_cmd.add_argument("saved", type=Path)
    connect_cmd.add_argument("--download", type=int, default=0, help="Also download this many bytes (traffic tests)")
    args = parser.parse_args(argv)
    return fetch(args.url, args.out) if args.command == "fetch" else connect(args.saved, args.download)


if __name__ == "__main__":
    sys.exit(main())
