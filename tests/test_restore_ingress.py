"""Run the deployed retirement operations in a scratch host, including retry after TLS files disappeared."""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from meridian.backup.old_control import detach_control_ingress
from meridian.reconciler.server_retirement import remove_unused_certificate
from tests.support.mock_connection import MockConnection


@pytest.fixture
def host(tmp_path: Path):
    for directory in (
        "bin",
        "nginx/conf.d",
        "nginx/stream.d",
        "nginx/sites-available",
        "nginx/sites-enabled",
        "certs",
        "acme/tls",
    ):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "config.json").write_text(json.dumps({"fleet_id": "test-fleet", "admin_token_sha256": "a" * 64}))
    files = {
        "http": tmp_path / "nginx/conf.d/meridian-http.conf",
        "stream": tmp_path / "nginx/stream.d/meridian.conf",
        "cert": tmp_path / "certs/fullchain.pem",
        "key": tmp_path / "certs/key.pem",
        "foreign": tmp_path / "nginx/conf.d/independent.conf",
        "renew": tmp_path / "acme/tls/tls.conf",
        "challenge": tmp_path / "nginx/sites-available/meridian-v4-acme.conf",
        "enabled": tmp_path / "nginx/sites-enabled/meridian-v4-acme.conf",
    }
    files["http"].write_text(
        "# Meridian Proxy Configuration (IP)\n# Managed by Meridian\nssl_certificate " + str(files["cert"]) + ";\n"
    )
    files["stream"].write_text("# nginx SNI Router (stream module)\n# Managed by Meridian\n")
    files["cert"].write_text("certificate")
    files["key"].write_text("key")
    files["foreign"].write_text("server { listen 127.0.0.1:8989; }\n")
    files["renew"].write_text(f"Le_RealFullChainPath='{files['cert']}'\n")
    files["challenge"].write_text("# Managed by Meridian v4 for ACME HTTP-01.\n")
    files["enabled"].symlink_to(files["challenge"])
    # Minimal command boundary: behaves like nginx for missing literal certificate files.
    (tmp_path / "bin/nginx").write_text("""#!/usr/bin/env python3
import os,re,sys
from pathlib import Path
root=Path(os.environ["SCRATCH_NGINX"])
patterns=("conf.d/*.conf","stream.d/*.conf","sites-enabled/*")
text="\\n".join(file.read_text() for glob in patterns for file in root.glob(glob))
for location in re.findall(r"ssl_certificate(?:_key)?\\s+([^;]+);", re.sub(r"#[^\\n]*", "", text)):
    if not Path(location.strip().strip('"')).exists(): sys.exit(1)
if "depends-on-old-block" in text and "# Meridian Proxy Configuration" not in text: sys.exit(1)
if "depends-on-challenge" in text and not (root / "sites-available/meridian-v4-acme.conf").exists(): sys.exit(1)
if "-T" in sys.argv: print(text)
""")
    (tmp_path / "bin/systemctl").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SERVICE_CALLS"\n'
        'case "$*" in *ActiveState*) echo active;; *LoadState*) echo loaded;; esac\n'
    )
    for binary in (tmp_path / "bin").iterdir():
        binary.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path / "bin") + ":" + os.environ["PATH"],
        "SCRATCH_NGINX": str(tmp_path / "nginx"),
        "SERVICE_CALLS": str(tmp_path / "systemctl.log"),
    }
    capture = MockConnection()
    capture.when("python3", stdout="verified")
    detach_control_ingress(capture, "test-fleet", "b" * 64)
    launch = shlex.split(capture.calls[0])
    launch[2] = launch[2].replace("/etc/nginx/", str(tmp_path / "nginx") + "/")
    launch[3] = str(tmp_path / "config.json")

    def detach():
        return subprocess.run(launch, env=env, capture_output=True, text=True)

    capture = MockConnection()
    remove_unused_certificate(capture, "/etc/ssl/meridian/fullchain.pem", keep_challenge=False)
    retire_cmd = (
        capture.calls[0]
        .replace("/etc/ssl/meridian", str(tmp_path / "certs"))
        .replace("/etc/nginx", str(tmp_path / "nginx"))
        .replace("/root/.acme.sh", str(tmp_path / "acme"))
    )

    def retire():
        return subprocess.run(["sh", "-c", retire_cmd], env=env, capture_output=True, text=True)

    return files, detach, retire


@pytest.mark.parametrize("partial", [False, True])
def test_restore_detaches_old_ingress_and_keeps_other_nginx_sites_live(host, partial):
    f, detach, retire = host
    if partial:
        f["cert"].unlink()
        # A crash after staging only one file resumes without losing the other.
        staging = f["stream"].parents[1] / ".meridian-restore-pending"
        staging.mkdir(mode=0o700)
        f["stream"].rename(staging / f["stream"].name)
    for _ in range(2):
        result = detach()
        assert result.returncode == 0 and result.stdout.strip() == "verified", result.stderr
        assert not f["http"].exists() and not f["stream"].exists() and f["foreign"].exists()
        result = retire()
        assert result.returncode == 0, result.stderr
        assert not any(f[name].exists() for name in ("cert", "key", "renew", "challenge", "enabled"))


def test_certificate_retirement_deletes_only_the_matching_acme_entry(host):
    files, detach, retire = host
    acme = files["renew"].parent.parent
    files["renew"].parent.rename(acme / "vpn sample_ecc")
    records = {
        "other.example.com_ecc": "Le_RealFullChainPath='/elsewhere/fullchain.pem'",
        "unrelated_ecc": f"OtherLe_RealFullChainPath='{files['cert']}'",
    }
    for name, record in records.items():
        (acme / name).mkdir()
        (acme / name / "renew.conf").write_text(record + "\n")

    assert detach().returncode == 0
    result = retire()

    assert result.returncode == 0, result.stderr
    assert {p.name for p in acme.iterdir()} == records.keys()
    for name, record in records.items():
        assert (acme / name / "renew.conf").read_text() == record + "\n"


@pytest.mark.parametrize("variant", ["alias", "inline-key", "comment"])
def test_still_used_certificate_and_renewal_survive_retiring_the_old_control_ingress(host, variant):
    f, detach, retire = host
    alias = f["cert"].with_name("shared.pem")
    alias.symlink_to(f["cert"])
    if variant == "alias":
        cfg = f'server {{ ssl_certificate "{alias}"; }}'
    elif variant == "inline-key":
        other = alias.with_name("independent.pem")
        other.write_text("independent")
        cfg = f'server {{ ssl_certificate "{other}"; ssl_certificate_key "{f["key"]}"; }}'
    else:
        cfg = f'server {{ ssl_certificate "{f["cert"]}" # existing cert; keep its live renewal\n; }}'
    f["foreign"].write_text(cfg + "\n")
    assert detach().returncode == 0 and retire().returncode == 0
    assert all(f[name].exists() for name in ("cert", "key", "renew", "challenge", "enabled", "foreign"))


def test_failed_validation_restores_owned_fragments_and_unrecognized_paths_are_untouched(host):
    f, detach, retire = host
    f["foreign"].write_text("# depends-on-old-block\n")
    previous = [f["http"].read_bytes(), f["stream"].read_bytes()]
    assert detach().returncode != 0
    assert [f["http"].read_bytes(), f["stream"].read_bytes()] == previous
    assert retire().returncode == 0 and f["cert"].exists() and f["renew"].exists()
    f["http"].write_text("# operator-owned\n")
    assert detach().returncode != 0
    assert f["http"].read_text() == "# operator-owned\n" and f["stream"].exists()


def test_previous_address_aliasing_the_replacement_is_never_touched(host, tmp_path):
    f, detach, _ = host
    (tmp_path / "config.json").write_text(json.dumps({"fleet_id": "test-fleet", "admin_token_sha256": "b" * 64}))
    result = detach()
    assert result.returncode and "replacement" in result.stderr
    assert f["http"].exists() and f["stream"].exists() and f["cert"].exists()
    assert not (tmp_path / "systemctl.log").exists()


def test_an_unidentified_reachable_machine_never_enters_generic_retirement(host, tmp_path):
    f, detach, _ = host
    (tmp_path / "config.json").unlink()
    f["http"].unlink()
    f["stream"].unlink()
    assert detach().returncode != 0 and not (tmp_path / "systemctl.log").exists()


def test_an_unrelated_include_that_needs_the_challenge_configuration_is_rolled_back(host):
    f, detach, retire = host
    assert detach().returncode == 0
    f["foreign"].write_text("# depends-on-challenge\n")
    assert retire().returncode != 0
    assert f["enabled"].is_symlink() and f["challenge"].exists()
    assert f["cert"].exists() and f["renew"].exists()
    f["foreign"].write_text("server { listen 127.0.0.1:8989; }\n")
    assert retire().returncode == 0 and not f["cert"].exists()
