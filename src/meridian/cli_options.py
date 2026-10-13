"""Shared Typer option and argument types so every command speaks one flag vocabulary."""

from __future__ import annotations

from typing import Annotated

import typer

TARGET_HELP = "Server: IP, saved name, user@host[:port], or 'local'"

Target = Annotated[str, typer.Argument(metavar="SERVER", help=TARGET_HELP, show_default=False)]
NewServer = Annotated[
    str, typer.Argument(metavar="SERVER", help="Server IP, user@IP[:port], or local (this machine)", show_default=False)
]
USER_HELP = "SSH user (default: saved, else root)"
SSH_PORT_HELP = "SSH port (default: saved, else 22)"
YES_HELP = "Skip confirmation"
User = Annotated[str, typer.Option("--user", "-u", help=USER_HELP, show_default=False)]
SshPort = Annotated[
    int,
    typer.Option("--ssh-port", min=0, max=65535, help=SSH_PORT_HELP, show_default=False),
]
# A string so bad values reach the command's validation and keep the JSON error envelope.
Timeout = Annotated[
    str, typer.Option("--timeout", metavar="SECONDS", help="Per-network-operation timeout, 1-30 seconds")
]
Yes = Annotated[bool, typer.Option("--yes", "-y", help=YES_HELP)]
