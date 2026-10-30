"""This device's backup key and where a fleet's snapshots live on it.

Each fleet context keeps its own X25519 key beside `cluster.yml` (`device.key`,
0600) and its snapshots in `snapshots/`; the service learns only the public key.
"""

from __future__ import annotations

import os
import re
import socket
import tempfile
from pathlib import Path

from meridian import config
from meridian.backup import BackupError
from meridian.control.client import ControlClient
from meridian.control.snapshot import KeyPair, SnapshotError, generate_key_pair, public_key_of

DEVICE_KEY_NAME = "device.key"
SNAPSHOTS_DIR = "snapshots"
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f]")


def device_key_path() -> Path:
    return config.FLEET_HOME / DEVICE_KEY_NAME


def snapshots_dir() -> Path:
    return config.FLEET_HOME / SNAPSHOTS_DIR


def load_device_key() -> KeyPair | None:
    """This context's key, or None before the first pull; a malformed file is an error, never replaced."""
    path = device_key_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise BackupError(f"Cannot read {path}: {exc}", category="user") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    try:
        secret = lines[-1]
        return KeyPair(secret=secret, public=public_key_of(secret))
    except (IndexError, SnapshotError) as exc:
        raise BackupError(
            f"{path} does not hold a device key.",
            hint="Move it aside and run `meridian backup pull` to make a new one.",
            category="user",
        ) from exc


def create_device_key() -> KeyPair:
    """Make and save a new key for this context, replacing none: an existing file stays."""
    path = device_key_path()
    pair = generate_key_pair()
    text = f"# Meridian device backup key. Keep it private.\n# public: {pair.public}\n{pair.secret}\n"
    if write_private(path, text, exclusive=True):
        return pair
    # Another run made one first (two commands' first pulls at once): use that one, or the
    # snapshot it saved would be sealed to a key this file no longer holds.
    existing = load_device_key()
    if existing is None:
        raise BackupError(f"{path} vanished while it was being created; retry.", category="system")
    return existing


def device_name() -> str:
    name = _UNPRINTABLE.sub("", socket.gethostname()).strip()[:64]
    return name or "device"


def write_private(path: Path, text: str | bytes, *, exclusive: bool = False) -> bool:
    """Write atomically with mode 0600 in a 0700 directory; False when `exclusive` finds the file there."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = text.encode("utf-8") if isinstance(text, str) else text
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if not exclusive:
            os.replace(temporary, path)
            return True
        try:
            os.link(temporary, path)  # never replaces: fails if the file appeared meanwhile
        except FileExistsError:
            return False
        finally:
            Path(temporary).unlink(missing_ok=True)
        return True
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def rotate_recovery_key(client: ControlClient) -> KeyPair:
    """Make a new recovery key and give the service its public half; the secret exists only in the result."""
    pair = generate_key_pair()
    client.set_recovery_key(pair.public)
    return pair
