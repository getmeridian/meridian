"""Record a real Meridian terminal session as an asciicast for the video.

    source ~/.cache/meridian-realvm/lab-<id>/env.sh
    uv run --with pexpect --with pyte python video/recording/record.py setup "$VM_REC"

Each script in SCRIPTS waits for text on the emulated screen, pauses like a
person reading, then types. The raw cast keeps real addresses and secrets, so it
goes to ~/.cache/meridian-video/raw/ and never into git; `snapshots.py`
sanitizes it into what the video renders. Marker events ("m") name the scenes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pexpect
import pyte

ROOT = Path(__file__).resolve().parents[2]
MERIDIAN = str(ROOT / ".venv" / "bin" / "meridian")  # the working tree, without uv's banner
RAW = Path.home() / ".cache" / "meridian-video" / "raw"
COLUMNS, ROWS = 100, 30
TYPING_DELAY = 0.07

UP, DOWN, ENTER = "\x1b[A", "\x1b[B", "\r"


@dataclass(frozen=True)
class Step:
    wait: str  # regex searched on the visible screen
    keys: str = ""  # typed after the pause; plain text is typed one character at a time
    pause: float = 1.2  # seconds of "reading" before typing
    marker: str = ""  # scene name recorded when the wait matches
    timeout: float = 120


def setup_script(address: str, users: str, title: str) -> tuple[list[str], list[Step]]:
    steps = [
        Step(r"Language / Язык", ENTER, pause=2.0, marker="language"),
        Step(r"Server address", address + ENTER, pause=2.0, marker="server"),
        Step(r"Short name for this server", ENTER, pause=1.5, timeout=180),
        Step(r"Add another server\?", ENTER, pause=1.5),
        Step(r"Country code shown to users", ENTER, pause=1.5, marker="roles"),
        Step(r"Cloudflare WARP\?", ENTER, pause=1.5),
        Step(r"Refuse Russian sites", ENTER, pause=1.5),
        Step(r"Domain for the control panel", ENTER, pause=1.5),
        Step(r"Name of this VPN", title + ENTER, pause=1.5),
        Step(r"Looking for camouflage sites", marker="camouflage"),
        Step(r"Camouflage site \(SNI\)", ENTER, pause=3.0, timeout=300),
        Step(r"Domain pointed at", ENTER, pause=1.5),
        Step(r"Names of the people", users + ENTER, pause=1.5, marker="users"),
        Step(r"Set this up now\?", ENTER, pause=4.0, marker="review"),
        Step(r"usually takes 3 to 10 minutes", marker="apply"),
        Step(r"Checking the panel", marker="verify", timeout=1500),
        Step(r"Your VPN is ready", marker="handoff", timeout=600),
    ]
    return [MERIDIAN, "setup"], steps


def client_add_script(name: str) -> tuple[list[str], list[Step]]:
    return [MERIDIAN, "client", "add", name], [
        Step(r"\?|Continue", "", pause=0, marker="client-add", timeout=300),
    ]


class Recorder:
    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        self.screen = pyte.Screen(COLUMNS, ROWS)
        self.stream = pyte.Stream(self.screen)
        self.events: list[list[object]] = []
        self.start = time.monotonic()
        self.child = pexpect.spawn(
            command[0], command[1:], cwd=ROOT, env=env, encoding="utf-8", dimensions=(ROWS, COLUMNS), timeout=None
        )

    def now(self) -> float:
        return round(time.monotonic() - self.start, 3)

    def output(self, data: str) -> None:
        self.events.append([self.now(), "o", data])
        for chunk in re.split(r"(\x1b\[6n)", data):
            if chunk == "\x1b[6n":  # prompt_toolkit asks where the cursor is; answer like a terminal
                self.child.send(f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R")
            else:
                self.stream.feed(chunk)

    def pump(self, seconds: float) -> bool:
        """Read output for up to `seconds`; False once the program has exited."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.output(self.child.read_nonblocking(65536, timeout=0.05))
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                return False
        return True

    def text(self) -> str:
        return "\n".join(self.screen.display)

    def wait(self, step: Step) -> None:
        deadline = time.monotonic() + step.timeout
        while not re.search(step.wait, self.text()):
            if time.monotonic() > deadline or not self.pump(0.2):
                sys.stderr.write(f"\n--- waited for {step.wait!r}; screen:\n{self.text()}\n")
                raise SystemExit(1)

    def type(self, keys: str) -> None:
        self.events.append([self.now(), "i", keys])
        for key in re.findall(r"\x1b\[[A-D]|.", keys, flags=re.S):
            self.child.send(key)
            self.pump(TYPING_DELAY if key.isprintable() else 0.25)

    def prompt(self, command: str) -> None:
        """Show a shell prompt and type the command, as a person would."""
        self.events.append([0.0, "o", "\x1b[1;32m$\x1b[0m "])
        for index, char in enumerate(command):
            self.events.append([round(0.8 + index * TYPING_DELAY * 1.3, 3), "o", char])
        self.events.append([round(1.2 + len(command) * TYPING_DELAY * 1.3, 3), "o", "\r\n"])
        self.start -= 1.6 + len(command) * TYPING_DELAY * 1.3


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("script", choices=["setup", "client-add"])
    parser.add_argument("address", nargs="?", default="")
    parser.add_argument("--users", default="Mom, Dad, Alex")
    parser.add_argument("--title", default="Family VPN")
    parser.add_argument("--name", default="Grandma")
    parser.add_argument("--stop-at", default="", help="Quit at this marker (rehearse without applying)")
    args = parser.parse_args(argv)
    if "MERIDIAN_HOME" not in os.environ:
        raise SystemExit("Source a lab env.sh first: the recording must not touch your real Meridian home.")

    if args.script == "setup":
        command, steps = setup_script(args.address, args.users, args.title)
    else:
        command, steps = client_add_script(args.name)
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env = {**env, "COLUMNS": str(COLUMNS), "LINES": str(ROWS), "TERM": "xterm-256color", "LANG": "en_US.UTF-8"}
    recorder = Recorder(command, env)
    recorder.prompt("meridian " + " ".join(command[1:]))
    for step in steps:
        recorder.wait(step)
        if step.marker:
            recorder.events.append([recorder.now(), "m", step.marker])
        if step.marker and step.marker == args.stop_at:
            print(f"\n--- stopped at {step.marker}; screen:\n{recorder.text()}")
            recorder.child.sendcontrol("c")
            break
        if step.keys:
            recorder.pump(step.pause)
            recorder.type(step.keys)
    while recorder.pump(1.0) and not args.stop_at:
        pass
    recorder.pump(1.0)

    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{args.script}.cast"
    header = {"version": 2, "width": COLUMNS, "height": ROWS, "timestamp": int(time.time())}
    events = sorted(recorder.events, key=lambda event: float(event[0]))  # type: ignore[arg-type]
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in [header, *events]) + "\n")
    print(f"\nrecorded {path} ({events[-1][0]}s, exit {recorder.child.exitstatus})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
