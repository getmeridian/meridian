"""Native teardown refuses uncertain ownership before deletion, even with the discard flag."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from meridian.provision import teardown_host as host

MARKER = "fw-0123456789abcdef"
HASH = "a" * 64


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path.resolve()
    for attr, rel in (
        ("NGINX", "etc/nginx"),
        ("TLS", "etc/ssl/meridian"),
        ("UNITS", "etc/systemd/system"),
        ("ACME", "root/.acme.sh"),
        ("CERTBOT", "etc/letsencrypt/renewal"),
        ("CONFIG", "etc/ctld/config.json"),
        ("STATE", "var/lib/ctld"),
    ):
        path = root / rel
        (path.parent if attr == "CONFIG" else path).mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(host, attr, path)
    monkeypatch.setattr(host, "JOURNALS", root)
    monkeypatch.setattr(host, "UFW_FILES", (root / "user.rules", root / "user6.rules"))
    monkeypatch.setattr(host, "verify_nginx_runtime", lambda: None)
    monkeypatch.setattr(host, "RUNTIMES", [host.CONFIG.parent, host.STATE])
    mounts = root / "mountinfo"
    mounts.write_text("1 2 0:5 / / rw - overlay overlay rw\n")
    monkeypatch.setattr(host, "MOUNTS", mounts)
    monkeypatch.setattr(host, "LEGACY", (str(root / "opt/3x-ui"),))
    monkeypatch.setattr(host.shutil, "which", lambda name: "/sbin/ufw")
    host.CONFIG.write_text(json.dumps({"fleet_id": "fleet", "admin_token_sha256": HASH}))
    db_path = host.STATE / "control.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE apply_fence(id INTEGER,record TEXT);
            INSERT INTO apply_fence VALUES (1,'{"fleet_id":"fleet","generation":1,"plan_hash":"topo"}');
            CREATE TABLE backup_progress(id INTEGER,changes INTEGER);
            INSERT INTO backup_progress VALUES (1,2);
            CREATE TABLE backup_receipts(device_id TEXT,changes INTEGER);
            CREATE TABLE backup_devices(device_id TEXT,revoked_at TEXT);
            INSERT INTO backup_receipts VALUES ('laptop',2);
            INSERT INTO backup_devices VALUES ('laptop',NULL);
        """)
    fragment = host.NGINX / "conf.d/meridian-http.conf"
    fragment.parent.mkdir()
    fragment.write_text("# Meridian Proxy Configuration\n# Managed by Meridian\nserver { listen 443; }\n")
    own_cert = host.TLS / "fullchain.pem"
    own_cert.write_text("own cert")
    (host.TLS / "key.pem").write_text("own key")
    renewal = host.ACME / "my certificate" / "cert.conf"
    renewal.parent.mkdir()
    renewal.write_text(f"Le_Domain='host'\nLe_RealFullChainPath='{own_cert}'\n")
    for name, exe in host.EXEC.items():
        (host.UNITS / (name + ".service")).write_text("ExecStart=" + exe + "\n")
    ctx = SimpleNamespace(
        dump="",
        reload_failure=False,
        race=False,
        dropin="",
        active=set([*host.EXEC, "nginx"]),
        disabled=set(),
        calls=[],
        removed_rules=[],
    )

    def command(*args: str) -> str:
        ctx.calls.append(args)
        if args == ("/usr/sbin/nginx", "-c", "/etc/nginx/nginx.conf", "-T"):
            main = f"# configuration file {host.NGINX}/nginx.conf:\nevents {{}} http {{}}\n"
            return (
                main
                + (f"# configuration file {fragment}:\n{fragment.read_text()}" if fragment.is_file() else "")
                + ctx.dump
            )
        if args == ("/usr/sbin/nginx", "-c", "/etc/nginx/nginx.conf", "-t"):
            return ""
        if args[:2] == ("systemctl", "show"):
            unit = args[2].removesuffix(".service")
            field = args[4]
            return {
                "LoadState": "loaded",
                "FragmentPath": str(host.UNITS / (unit + ".service")),
                "DropInPaths": ctx.dropin if unit == "nginx" else "",
                "UnitFileState": "disabled" if unit in ctx.disabled else "enabled",
                "ActiveState": "active" if unit in ctx.active else "inactive",
            }[field]
        if args[:2] == ("systemctl", "stop"):
            if args[2] == "ctld" and ctx.race:
                with sqlite3.connect(db_path) as db:
                    db.execute("UPDATE backup_progress SET changes=3")
            ctx.active.discard(args[2])
            return ""
        if args[:2] == ("systemctl", "start"):
            ctx.active.add(args[2])
            return ""
        if args[:2] == ("systemctl", "enable"):
            ctx.disabled.discard(args[2])
            return ""
        if args[:2] == ("systemctl", "disable"):
            ctx.disabled.add(args[2])
            return ""
        if args == ("systemctl", "reload", "nginx") and ctx.reload_failure:
            ctx.reload_failure = False
            raise host.Refuse("reload failed")
        if args in (("systemctl", "reload", "nginx"), ("systemctl", "daemon-reload")):
            return ""
        if args == ("ufw", "status", "numbered"):
            return (
                "[ 1] 443/tcp ALLOW IN Anywhere\n"
                f"[ 2] 80/tcp ALLOW IN Anywhere # {MARKER}\n"
                "[ 3] 8443/tcp ALLOW IN Anywhere # operator\n"
            )
        if args[:3] == ("ufw", "--force", "delete"):
            ctx.removed_rules.append(int(args[3]))
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(host, "command", command)
    ctx.run = lambda gen="2", fleet="fleet", preflight=False: host.teardown(
        fleet, HASH, gen, [str(own_cert)], [MARKER], [], [1, "topo"], preflight=preflight
    )
    ctx.fragment, ctx.cert, ctx.renewal, ctx.database = fragment, own_cert, renewal, db_path
    return ctx


def test_dedicated_retirement_preserves_unowned_files_and_closes_the_web_server(fixture):
    independent = host.TLS / "operator.pem"
    independent.write_text("not Meridian's")
    renewal_note = fixture.renewal.parent / "operator-note.txt"
    renewal_note.write_text("keep")
    fixture.run()
    assert renewal_note.read_text() == "keep"
    assert not fixture.active and fixture.disabled == {*host.EXEC, "nginx"}
    assert all(not p.exists() for p in [fixture.fragment, fixture.cert, fixture.database, fixture.renewal])
    assert independent.read_text() == "not Meridian's"
    assert fixture.removed_rules == [2]


@pytest.mark.parametrize(
    "foreign",
    [
        "nginx",
        "renewal",
        "dropin",
        "wrong-fleet",
        "stale-topology",
        "old panel",
        "unit",
        "path",
        "certbot",
        "nested volume",
    ],
)
def test_refuses_before_stopping_any_service_even_with_discard(fixture, foreign):
    if foreign == "nginx":
        fixture.dump += f'# configuration file {host.NGINX}/shop.conf:\nlog_format x "#"; server{{listen 9231 ssl;}}'
    if foreign == "renewal":
        other = host.ACME / "shop" / "shop.conf"
        other.parent.mkdir()
        other.write_text("Le_Domain='shop.test'\n")
    if foreign == "certbot":
        (host.CERTBOT / "mail.conf").write_text("authenticator = webroot")
    if foreign == "nested volume":
        host.MOUNTS.write_text(f"1 2 0:5 / {host.STATE}/operator rw - ext4 /dev/test rw\n")
    if foreign == "dropin":
        fixture.dropin = str(host.UNITS / "nginx.service.d/operator.conf")
    if foreign == "old panel":
        Path(host.LEGACY[0]).mkdir(parents=True)
    if foreign == "stale-topology":
        with sqlite3.connect(fixture.database) as db:
            db.execute("UPDATE apply_fence SET record='{}'")
    if foreign == "unit":
        (host.UNITS / "netagent.service").write_text("ExecStart=/usr/local/bin/unrelated")
    if foreign == "path":
        fixture.fragment.unlink()
        fixture.fragment.symlink_to(fixture.cert)
    with pytest.raises(host.Refuse):
        fixture.run("discard", "other" if foreign == "wrong-fleet" else "fleet")
    assert fixture.database.exists() and fixture.cert.exists()
    assert not any(c[:2] == ("systemctl", "stop") for c in fixture.calls)


@pytest.mark.parametrize("cause", ["concurrent edit", "reload"])
def test_restores_controller_and_ingress_on_failed_final_check(fixture, cause):
    fixture.race = cause == "concurrent edit"
    fixture.reload_failure = cause == "reload"
    with pytest.raises(host.Refuse):
        fixture.run()
    assert fixture.database.exists() and fixture.fragment.exists() and fixture.cert.exists()
    assert fixture.active == {*host.EXEC, "nginx"} and not fixture.disabled
    assert not list(host.NGINX.glob(".meridian-teardown-*"))


def test_stale_or_unconfirmed_copy_needs_distinct_discard(fixture):
    with sqlite3.connect(fixture.database) as db:
        db.execute("UPDATE backup_devices SET revoked_at='yesterday'")
    with pytest.raises(host.Refuse):
        fixture.run()
    assert fixture.database.exists()
    fixture.run("discard")
    assert not fixture.database.exists()


def test_preflight_is_read_only_and_rechecks_topology_without_requiring_a_backup(fixture):
    with sqlite3.connect(fixture.database) as db:
        db.execute("DELETE FROM backup_receipts")
    assert fixture.run(preflight=True) == "ready"
    assert fixture.database.is_file() and fixture.fragment.is_file() and not fixture.disabled
    assert not list(host.JOURNALS.glob(".meridian-retirement-*"))
    with sqlite3.connect(fixture.database) as db:
        db.execute("UPDATE apply_fence SET record='{}'")
    with pytest.raises(host.Refuse):
        fixture.run(preflight=True)
    assert not any(c[:2] == ("systemctl", "stop") for c in fixture.calls)


def test_disable_failure_restores_all_enabled_units_and_the_data(fixture, monkeypatch):
    original = host.command

    def run(*args):
        if args[:2] == ("systemctl", "disable") and fixture.disabled:
            raise host.Refuse("disable failed")
        return original(*args)

    monkeypatch.setattr(host, "command", run)
    with pytest.raises(host.Refuse, match="disable failed"):
        fixture.run()
    assert fixture.database.exists() and fixture.fragment.exists()
    assert not fixture.disabled and fixture.active == {*host.EXEC, "nginx"}
    assert fixture.removed_rules == []


def test_partial_postcommit_firewall_error_resumes_without_reopening_or_discarding(fixture, monkeypatch):
    seen = []

    def clean(markers):
        seen.append(set(markers))
        if len(seen) == 1:
            raise host.Refuse("an owned UFW rule could not be removed")

    monkeypatch.setattr(host, "clean_firewall", clean)
    with pytest.raises(host.Refuse, match="Retirement committed"):
        fixture.run()
    assert not fixture.database.exists() and not fixture.fragment.exists() and not fixture.active
    assert fixture.run(preflight=True) == "pending"
    assert fixture.run("None") == "retired"
    assert seen == [{MARKER}, {MARKER}]
    assert fixture.run(preflight=True) == "complete" and fixture.run("None") == "retired"
    assert len(seen) == 2


def test_postcommit_disk_failure_retains_staged_data_and_never_erases_a_replacement(fixture, monkeypatch):
    from meridian.provision import teardown_journal

    original = teardown_journal.shutil.rmtree

    def fail_data(path):
        if path.name.endswith("-ctld"):
            raise OSError("disk I/O error")
        return original(path)

    with monkeypatch.context() as m:
        m.setattr(teardown_journal.shutil, "rmtree", fail_data)
        with pytest.raises(host.Refuse, match="Retirement committed"):
            fixture.run()
    host.STATE.mkdir()
    independent = host.STATE / "replacement"
    independent.write_text("new")
    with pytest.raises(host.Refuse, match="reused"):
        fixture.run("None")
    assert independent.read_text() == "new"
    independent.unlink()
    host.STATE.rmdir()
    assert fixture.run("None") == "retired"


def test_precommit_failed_stage_restores_files_and_their_services(fixture, monkeypatch):
    from meridian.provision.teardown_journal import Retirement

    move = Retirement.move

    def fail_midway(self, path, signature):
        if path == fixture.cert:
            raise OSError("could not move certificate")
        return move(self, path, signature)

    monkeypatch.setattr(Retirement, "move", fail_midway)
    with pytest.raises(OSError, match="could not move"):
        fixture.run()
    assert fixture.database.exists() and fixture.cert.exists() and fixture.fragment.exists()
    assert not fixture.disabled and fixture.active == {*host.EXEC, "nginx"}
    assert fixture.removed_rules == []


def test_renewal_during_review_preserves_new_certificate_and_restores_prior_service(fixture, monkeypatch):
    from meridian.provision.teardown_journal import JournalError, Retirement

    move = Retirement.move

    def renew(self, path, signature):
        if path == fixture.cert:
            new = path.parent / "issued"
            new.write_text("new from ACME")
            new.replace(path)
        return move(self, path, signature)

    monkeypatch.setattr(Retirement, "move", renew)
    with pytest.raises(JournalError):
        fixture.run()
    assert fixture.cert.read_text() == "new from ACME" and fixture.database.exists() and fixture.fragment.exists()
    assert not fixture.disabled and fixture.active == {*host.EXEC, "nginx"} and not fixture.removed_rules


def test_second_firewall_delete_can_fail_without_restarting_the_server_or_removing_other_rules(fixture, monkeypatch):
    original = host.command
    rules = ["operator", MARKER, MARKER]
    attempts = []

    def command(*args):
        if args == ("ufw", "status", "numbered"):
            return "\n".join(f"[ {i}] 443/tcp ALLOW IN Anywhere # {m}" for i, m in enumerate(rules, 1))
        if args[:3] == ("ufw", "--force", "delete"):
            attempts.append(int(args[3]))
            if len(attempts) == 2:
                raise host.Refuse("UFW is busy")
            rules.pop(int(args[3]) - 1)
            return ""
        return original(*args)

    monkeypatch.setattr(host, "command", command)
    with pytest.raises(host.Refuse, match="Retirement committed"):
        fixture.run()
    assert rules == ["operator", MARKER] and not fixture.active
    assert fixture.run("None") == "retired"
    assert rules == ["operator"] and attempts == [3, 2, 2]


@pytest.mark.parametrize("persisted", [True, False])
def test_inactive_ufw_with_stored_owned_rules_refuses_before_stopping_or_registering_a_backup(
    fixture, monkeypatch, persisted
):
    original = host.command
    if persisted:
        host.UFW_FILES[1].write_text("### tuple ### allow ... comment=" + MARKER.encode().hex())

    def command(*args):
        if args == ("ufw", "status", "numbered"):
            return "Status: inactive"
        if args == ("ufw", "show", "added"):
            return "" if persisted else f"ufw allow 443/tcp comment '{MARKER}'"
        return original(*args)

    monkeypatch.setattr(host, "command", command)
    with pytest.raises(host.Refuse, match="stored but inactive"):
        fixture.run(preflight=True)
    assert fixture.database.exists() and not fixture.disabled
    assert not list(host.JOURNALS.glob(".meridian-retirement-*"))


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_external_database_stays_private_and_teardown_refuses_before_touching_it(fixture, link, tmp_path):
    outside = tmp_path / "operator.db"
    fixture.database.rename(outside)
    if link == "symlink":
        fixture.database.symlink_to(outside)
    else:
        os.link(outside, fixture.database)
    with pytest.raises(host.Refuse):
        fixture.run("discard", preflight=True)
    assert outside.is_file() and fixture.fragment.is_file()
    assert not any(c[:2] == ("systemctl", "stop") for c in fixture.calls)
    assert not list(host.JOURNALS.glob(".meridian-retirement-*"))
