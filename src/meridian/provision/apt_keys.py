"""Verify third-party apt signing keys against fingerprints pinned in code.

Downloading a key over TLS proves only which server sent it; checking the
fingerprint proves it is the publisher's key.
"""

from __future__ import annotations

import shlex

from meridian.config import APT_KEY_FINGERPRINTS
from meridian.ssh import ServerConnection


def primary_fingerprints(colons_output: str) -> set[str]:
    """Primary-key fingerprints from `gpg --show-keys --with-colons` (subkeys ignored)."""
    found: set[str] = set()
    after_pub = False
    for line in colons_output.splitlines():
        fields = line.split(":")
        if fields[0] == "pub":
            after_pub = True
        elif fields[0] == "fpr" and after_pub and len(fields) > 9:
            found.add(fields[9].upper())
            after_pub = False
    return found


def verify_apt_key(conn: ServerConnection, path: str, publisher: str) -> str:
    """Return an error message (and delete the key) unless every key in ``path`` is pinned."""
    allowed = APT_KEY_FINGERPRINTS[publisher]
    result = conn.run(f"gpg --show-keys --with-colons {shlex.quote(path)}", timeout=15)
    found = primary_fingerprints(result.stdout) if result.returncode == 0 else set()
    if found and found <= allowed:
        return ""
    conn.run(f"rm -f {shlex.quote(path)}", timeout=15)
    if not found:
        return f"could not read the downloaded {publisher} signing key"
    return f"unexpected {publisher} signing key {', '.join(sorted(found - allowed))}; refusing to trust it"
