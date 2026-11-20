"""Disabling the distribution's stock site must not deactivate a site's own access and error logs."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess

import pytest

from meridian.provision.nginx import DISABLE_STOCK_NGINX_SITE


@pytest.mark.skipif(not shutil.which("md5sum"), reason="nginx uses Debian's conffile and checksum tools")
@pytest.mark.parametrize(
    "case", ["unmodified package", "edited package", "different site", "plain file", "unknown package"]
)
def test_only_the_unmodified_package_default_is_disabled(tmp_path, case: str) -> None:
    root = tmp_path / "etc/nginx"
    target = root / "sites-available/default"
    live = root / "sites-enabled/default"
    target.parent.mkdir(parents=True)
    live.parent.mkdir()
    target.write_text("distribution site\n")
    digest = hashlib.md5(target.read_bytes(), usedforsecurity=False).hexdigest()  # dpkg's conffile format
    if case == "different site":
        other = root / "sites-available/operator"
        other.write_text("operator site\n")
        live.symlink_to(other)
    elif case == "plain file":
        live.write_text("operator site\n")
    else:
        live.symlink_to(target)
    if case == "edited package":
        target.write_text("operator site using the default name\n")
    metadata = f" {target} {digest}" if case != "unknown package" else ""
    query = tmp_path / "dpkg-query"
    query.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(metadata)}\n")
    query.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    subprocess.run(["sh", "-c", DISABLE_STOCK_NGINX_SITE.replace("/etc/nginx", str(root))], env=env, check=True)
    assert live.exists() == (case != "unmodified package")
    assert target.is_file()
