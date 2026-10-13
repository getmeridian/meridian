"""Drive `meridian setup` like a newcomer: answer a few prompts, press Enter on every other.

    python tests/realvm/drive_wizard.py root@198.51.100.10            # wizard on this machine
    python tests/realvm/drive_wizard.py --on-server 198.51.100.10     # wizard on the VM (address 'local')

Source a lab's env.sh first so ssh and MERIDIAN_HOME point at the lab. The
full transcript goes to `$REALVM_LAB/wizard-transcript.txt` (or the current
directory); extra arguments replace the default `--lang en setup` command.
Needs pexpect (`uv run --with pexpect`).
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from pathlib import Path

import pexpect

# Every wizard question ends its prompt line with "]", ":" or "?" after the arrow, maybe followed by colour codes.
PROMPT = r"(→|->)[^\r\n]*(\]|:|\?)(\x1b\[[0-9;]*m)*\s?(\x1b\[[0-9;]*m)*$"
TIMEOUT = 1200


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Drive the setup wizard with newcomer answers.")
    parser.add_argument("target", help="Server address for the wizard, or the VM to run it on with --on-server")
    parser.add_argument("--on-server", action="store_true", help="Run the wizard on the VM over ssh -tt")
    parser.add_argument("--users", default="alice, bob", help="Answer to the client-names question")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Meridian arguments (default: --lang en setup)")
    args = parser.parse_args(argv)
    meridian_args = args.command or ["--lang", "en", "setup"]

    transcript = Path(os.environ.get("REALVM_LAB", ".")) / "wizard-transcript.txt"
    if args.on_server:
        remote = "MERIDIAN_DISABLE_UPDATE_CHECK=1 COLUMNS=110 TERM=xterm meridian " + shlex.join(meridian_args)
        command, command_args = "ssh", ["-tt", f"root@{args.target}", remote]
        address = "local"
    else:
        command, command_args = "uv", ["run", "meridian", *meridian_args]
        address = args.target
    answers = [
        (r"Server address", address),
        (r"Trust this host key", "y"),
        (r"Names of the people", args.users),
    ]
    env = {**os.environ, "COLUMNS": "110", "TERM": "xterm", "MERIDIAN_DISABLE_UPDATE_CHECK": "1"}
    with transcript.open("w", buffering=1) as log:
        child = pexpect.spawn(command, command_args, encoding="utf-8", timeout=TIMEOUT, env=env, dimensions=(50, 110))
        child.logfile_read = log
        while True:
            index = child.expect([PROMPT, pexpect.EOF, pexpect.TIMEOUT], timeout=TIMEOUT)
            if index:
                print("ended:", "EOF" if index == 1 else "TIMEOUT")
                break
            child.sendline(next((answer for pattern, answer in answers if re.search(pattern, child.after)), ""))
        child.close()
    print(f"exit {child.exitstatus} signal {child.signalstatus}; transcript: {transcript}")
    return child.exitstatus or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
