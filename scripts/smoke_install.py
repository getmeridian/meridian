"""Smoke-test an installed `meridian`: the CLI loads and the control service starts and answers.

The dependency canary (.github/workflows/dependency-canary.yml) runs it against a fresh
install with the newest allowed dependencies, which is how #140 (Typer 0.29) reached users.

    python scripts/smoke_install.py ~/.local/bin/meridian
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health(port: int) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def check_cli(meridian: str) -> None:
    for args in (["--version"], ["--help"], ["deploy", "--help"], ["setup", "--help"]):
        result = subprocess.run([meridian, *args], capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0:
            raise SystemExit(f"`meridian {' '.join(args)}` exited {result.returncode}:\n{result.stderr[-2000:]}")
        print(f"OK: meridian {' '.join(args)}")  # noqa: T201


def check_service(meridian: str, *, timeout: float = 60) -> None:
    """Start `meridian serve` on a loopback port; an unauthenticated health check must get 401."""
    with tempfile.TemporaryDirectory(prefix="meridian-smoke-") as tmp:
        port = _free_port()
        config = Path(tmp, "config.json")
        config.write_text(
            json.dumps(
                {
                    "fleet_id": _FLEET_ID,
                    "admin_token_sha256": "a" * 64,
                    "listen_port": port,
                    "state_dir": str(Path(tmp, "state")),
                }
            )
        )
        Path(tmp, "state").mkdir(mode=0o700)
        log = Path(tmp, "serve.log")
        with log.open("w") as out:
            service = subprocess.Popen(
                [meridian, "serve", "--config", str(config)], stdout=out, stderr=subprocess.STDOUT
            )
            try:
                deadline = time.monotonic() + timeout
                status = 0
                while time.monotonic() < deadline and service.poll() is None:
                    status = _health(port)
                    if status:
                        break
                    time.sleep(0.5)
            finally:
                service.terminate()
                try:
                    service.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    service.kill()
        if status != 401:
            raise SystemExit(f"The control service answered {status or 'nothing'}:\n{log.read_text()[-3000:]}")
        print("OK: meridian serve answers /api/health with 401")  # noqa: T201


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("meridian", help="path to the installed meridian executable")
    args = parser.parse_args()
    check_cli(args.meridian)
    check_service(args.meridian)


if __name__ == "__main__":
    main()
