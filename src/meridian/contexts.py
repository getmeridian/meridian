"""Fleet contexts: several fleets in one Meridian home, chosen kube-context style.

The install home holds what belongs to the person, not to a fleet: Meridian's SSH key
pair, ControlMaster sockets, the language preference, the update-check cache, and the
local Xray runtime. The ``default`` context is the install home itself, so a home from
before contexts keeps every file where it was; any other context is a directory under
``contexts/`` holding the same per-fleet files (``cluster.yml``, its backup,
``servers.json``, ``setup.json``). ``MERIDIAN_HOME`` pins one directory that is both.

This module is pure filesystem logic with explicit arguments; ``meridian.config``
binds its result to the path globals every command reads.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from meridian.core.errors import LocalStateError

DEFAULT_CONTEXT = "default"
CONTEXT_ENV = "MERIDIAN_CONTEXT"
CONTEXTS_DIR = "contexts"
CURRENT_CONTEXT_FILE = "current-context"
# Beside a fleet's files once a restore in another context took that fleet's servers over.
SUPERSEDED_FILE = "superseded.json"
# What `default` holds of its fleet (any other context is its whole directory). Clearing it keeps
# the person's files (SSH key, sockets, preferences, cache) and migration `backups/`.
DEFAULT_FLEET_FILES = ("cluster.yml", "cluster.yml.bak", "servers.json", "setup.json", "device.key", SUPERSEDED_FILE)
DEFAULT_FLEET_DIRS = ("snapshots", "restore")
# Lowercase only: macOS and Windows file systems would treat `Work` and `work` as one directory.
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")

ContextSource = Literal["pinned", "flag", "env", "saved", "default"]


class ContextError(LocalStateError):
    """A context name that is malformed, missing, or not allowed for this operation."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message, hint=hint, category="user")


@dataclass(frozen=True)
class ContextSelection:
    """The fleet a run works on and why it was chosen."""

    name: str
    home: Path
    source: ContextSource

    @property
    def pinned(self) -> bool:
        return self.source == "pinned"


def validate_name(name: str) -> str:
    """Return ``name`` if it can name a context directory, else raise."""
    if not _NAME.fullmatch(name):
        raise ContextError(
            f"Invalid context name: {name!r}",
            hint="Use up to 32 lowercase letters, digits, '-' or '_', starting with a letter or digit.",
        )
    return name


def context_home(install_home: Path, name: str) -> Path:
    """Where a context keeps its fleet files: the install home for ``default``."""
    if name == DEFAULT_CONTEXT:
        return install_home
    return install_home / CONTEXTS_DIR / validate_name(name)


def context_exists(install_home: Path, name: str) -> bool:
    """``default`` always exists; another context exists once its directory does."""
    return name == DEFAULT_CONTEXT or context_home(install_home, name).is_dir()


def context_names(install_home: Path) -> list[str]:
    """``default`` first, then every named context in name order."""
    root = install_home / CONTEXTS_DIR
    named = sorted(p.name for p in root.iterdir() if p.is_dir() and _NAME.fullmatch(p.name)) if root.is_dir() else []
    return [DEFAULT_CONTEXT, *(name for name in named if name != DEFAULT_CONTEXT)]


def saved_context(install_home: Path) -> str:
    """The persisted current context, or ``default`` when none is saved."""
    try:
        value = (install_home / CURRENT_CONTEXT_FILE).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return DEFAULT_CONTEXT
    except (OSError, UnicodeDecodeError) as exc:
        raise ContextError(
            f"Cannot read {install_home / CURRENT_CONTEXT_FILE}: {exc}",
            hint="Fix or delete the file, then run `meridian context use NAME`.",
        ) from exc
    return value or DEFAULT_CONTEXT


def save_context(install_home: Path, name: str) -> None:
    """Persist ``name`` as the current context; ``default`` removes the file."""
    path = install_home / CURRENT_CONTEXT_FILE
    if name == DEFAULT_CONTEXT:
        path.unlink(missing_ok=True)
        return
    install_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=str(install_home), prefix=".current-context.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(validate_name(name) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class Superseded:
    """A context whose fleet a restore in context ``by`` took over at ``at``; it no longer changes the fleet."""

    by: str
    at: str


def superseded(home: Path) -> Superseded | None:
    """The marker in ``home``, if any; an unreadable one still counts (``by`` empty), so nothing applies from it."""
    try:
        data = json.loads((home / SUPERSEDED_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return Superseded(by="", at="")
    if not isinstance(data, dict):
        return Superseded(by="", at="")
    return Superseded(by=str(data.get("by", "")), at=str(data.get("at", "")))


def mark_superseded(home: Path, marker: Superseded) -> None:
    """Record in ``home`` that context ``marker.by`` now holds this fleet."""
    fd, tmp = tempfile.mkstemp(dir=str(home), prefix=".superseded.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"by": marker.by, "at": marker.at}) + "\n")
        os.replace(tmp, home / SUPERSEDED_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clear_default_fleet(install_home: Path) -> None:
    """Delete the ``default`` context's copy of its fleet, keeping the person's files in the install home."""
    for name in DEFAULT_FLEET_FILES:
        (install_home / name).unlink(missing_ok=True)
    for name in DEFAULT_FLEET_DIRS:
        if (install_home / name).is_dir():
            shutil.rmtree(install_home / name)


def resolve_context(
    install_home: Path,
    *,
    pinned: bool,
    flag: str = "",
    env: str = "",
) -> ContextSelection:
    """Pick the fleet: ``MERIDIAN_HOME`` > ``--context`` > ``MERIDIAN_CONTEXT`` > saved > ``default``.

    ``MERIDIAN_HOME`` pins one fleet, so an explicit ``--context`` beside it is a
    contradiction and is refused rather than silently ignored; the environment
    variable yields to it, as weaker settings always yield to stronger ones.
    """
    if pinned:
        if flag:
            raise ContextError(
                f"--context {flag} cannot be used while MERIDIAN_HOME is set",
                hint="MERIDIAN_HOME pins one fleet. Unset it to choose between contexts.",
            )
        return ContextSelection(DEFAULT_CONTEXT, install_home, "pinned")
    source: ContextSource
    if flag:
        name, source = flag, "flag"
    elif env:
        name, source = env, "env"
    else:
        name = saved_context(install_home)
        source = "saved" if name != DEFAULT_CONTEXT else "default"
    return ContextSelection(validate_name(name), context_home(install_home, name), source)


def missing_context_error(install_home: Path, selection: ContextSelection) -> ContextError:
    """The error for a selected context that does not exist, listing the ones that do."""
    names = ", ".join(context_names(install_home))
    origin = {
        "flag": "--context",
        "env": CONTEXT_ENV,
        "saved": "the saved current context",
    }.get(selection.source, "the context")
    hint = f"Contexts: {names}. Create one with `meridian deploy IP --context {selection.name}`"
    if selection.source == "saved":
        hint += ", or switch back with `meridian context use default`"
    return ContextError(f"No context named {selection.name!r} ({origin})", hint=hint + ".")
