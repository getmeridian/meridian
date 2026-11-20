"""Only the same packaged nginx that a last-controller teardown inspected may be stopped."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from meridian.provision import teardown_nginx as nginx

BIN = "/usr/sbin/nginx"
CONF = "/etc/nginx/nginx.conf"
UNIT = "/usr/lib/systemd/system/nginx.service"
DEFAULTS = "/etc/default/nginx"
DROPIN = "/etc/systemd/system/nginx.service.d/restart.conf"
EXEC = "{ path=/usr/sbin/nginx ; argv[]=%s ; ignore_errors=no ; pid=9 ; status=0 }"
LAUNCH = BIN + " -c ${CONFFILE}"


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path.resolve()

    def path(location: str) -> Path:
        return root / location.lstrip("/")

    for location, contents in (
        (BIN, "packaged executable"),
        (CONF, "events {} http {}"),
        (UNIT, "packaged vendor unit"),
        (DEFAULTS, '# Distribution defaults\nDAEMON_ARGS=""\n# CONFFILE="/etc/nginx/nginx.conf"\n'),
        (DROPIN, "[Service]\nRestart=on-failure\nRestartSec=5\n"),
        ("/proc/99/cmdline", f"nginx: master process {BIN} -c {CONF}\x00\x00"),
        ("/proc/99/environ", f"LANG=C\x00CONFFILE={CONF}\x00"),
    ):
        file = path(location)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(contents)

    path("/lib").symlink_to(root / "usr/lib")
    path("/proc/99/exe").symlink_to(path(BIN))
    path("/proc/99/root").symlink_to(root)
    props = {
        "LoadState": "loaded",
        "NeedDaemonReload": "no",
        "ActiveState": "active",
        "MainPID": "99",
        "FragmentPath": UNIT,
        "ExecStart": EXEC % LAUNCH,
        "DropInPaths": DROPIN,
        "Environment": "CONFFILE=" + CONF,
        "EnvironmentFiles": DEFAULTS + " (ignore_errors=yes)",
        "PassEnvironment": "",
        "UnsetEnvironment": "",
    }
    # Deliberately use pre-usrmerge spelling in nginx-common's package manifest.
    sums = {
        p: hashlib.md5(path(p).read_bytes(), usedforsecurity=False).hexdigest()
        for p in (BIN, "/lib/systemd/system/nginx.service")
    }

    def output(*args):
        if args[0] == "systemctl":
            return "\n".join(f"{key}={value}" for key, value in props.items())
        if args[1] == "-W":
            return "install ok installed"
        assert args[:2] == ("dpkg-query", "--control-show")
        location = BIN if args[2] == "nginx" else "/lib/systemd/system/nginx.service"
        return f"{sums[location]}  {location.lstrip('/')}\n"

    monkeypatch.setattr(nginx, "_ROOT", root)
    monkeypatch.setattr(nginx, "_output", output)
    return SimpleNamespace(path=path, props=props)


@pytest.mark.parametrize("package", ["nginx.org", "Debian", "inactive"])
def test_supported_packaged_invocations(host, package):
    if package == "Debian":
        launch = BIN + " -g daemon on; master_process on;"
        host.props.update(
            FragmentPath="/lib/systemd/system/nginx.service",
            ExecStart=EXEC % launch,
            Environment="",
            EnvironmentFiles="",
        )
        host.path("/proc/99/cmdline").write_bytes(("nginx: master process " + launch).encode() + b"\x00")
    if package == "inactive":
        host.props.update(ActiveState="inactive", MainPID="0")
        host.path("/proc/99/exe").unlink()
    nginx.verify_nginx_runtime()


@pytest.mark.parametrize(
    "case",
    [
        "custom-unit",
        "edited-vendor",
        "edited-binary",
        "alternate-start",
        "extra-directive",
        "alternate-env",
        "alternate-envfile",
        "inherited-env",
        "custom-dropin",
        "stale-daemon",
        "different-process",
        "live-config",
        "live-env",
        "different-root",
        "not-master",
        "stale-pid",
    ],
)
def test_ambiguous_or_custom_runtime_is_refused_without_disclosing_values(host, case):
    private = "/owner-only/config"
    if case == "custom-unit":
        host.props["FragmentPath"] = "/etc/systemd/system/nginx.service"
        host.path(host.props["FragmentPath"]).write_text(host.path(UNIT).read_text())
    elif case == "edited-vendor":
        host.path(UNIT).write_text("custom ExecStart, same vendor path")
    elif case == "edited-binary":
        host.path(BIN).write_text("locally replaced executable")
    elif case == "alternate-start":
        host.props["ExecStart"] = EXEC % (BIN + " -c " + private)
    elif case == "extra-directive":
        host.props["ExecStart"] = EXEC % (BIN + " -c " + CONF + " -g include /another;")
    elif case == "alternate-env":
        host.path(DEFAULTS).write_text("CONFFILE=" + private)
    elif case == "alternate-envfile":
        host.props["EnvironmentFiles"] += "\n" + private
    elif case == "inherited-env":
        host.props["PassEnvironment"] = "LD_PRELOAD"
    elif case == "custom-dropin":
        host.path(DROPIN).write_text("[Service]\nEnvironment=SECRET=owner-only")
    elif case == "stale-daemon":
        host.props["NeedDaemonReload"] = "yes"
    elif case == "different-process":
        host.path("/proc/99/exe").unlink()
        host.path("/proc/99/exe").symlink_to(host.path(UNIT))
    elif case == "live-config":
        host.path("/proc/99/cmdline").write_bytes(("nginx: master process " + BIN + " -c " + private).encode())
    elif case == "live-env":
        host.path("/proc/99/environ").write_bytes(b"LD_PRELOAD=/owner-only/library\x00")
    elif case == "different-root":
        host.path("/proc/99/root").unlink()
        host.path("/proc/99/root").mkdir()
    elif case == "not-master":
        host.path("/proc/99/cmdline").write_bytes(b"nginx: worker process\x00")
    else:
        host.props.update(ActiveState="failed", MainPID="99")
    with pytest.raises(ValueError, match="Cannot verify the packaged nginx service") as exc:
        nginx.verify_nginx_runtime()
    assert "owner-only" not in str(exc.value)
