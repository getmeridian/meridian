"""Check provider configuration preservation on disk and, on Debian/Ubuntu, with OpenSSH's parser."""

from __future__ import annotations

import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from meridian.provision import ssh_hardening as policy

LEGACY_POLICY = (
    b"PasswordAuthentication no\nKbdInteractiveAuthentication no\nChallengeResponseAuthentication no\nDebianBanner no\n"
)


def _settings(password: str = "no") -> dict[str, str]:
    return {"passwordauthentication": password, "kbdinteractiveauthentication": password, "debianbanner": "no"}


@pytest.fixture
def provider(tmp_path):
    directory = tmp_path / "ssh"
    conf = directory / "sshd_config.d"
    conf.mkdir(parents=True)
    main = directory / "sshd_config"
    # Do not append global configuration after this provider's Match block.
    main.write_bytes(b"# provided image\nPort 2222\nPasswordAuthentication yes\nMatch User archive\n  PermitTTY no\n")
    main.chmod(0o640)
    return main, conf / "00-hardening.conf"


def _explicit_include(main: Path, fragment: Path) -> bool:
    directives = [tokens for line in main.read_text().splitlines() if (tokens := shlex.split(line, comments=True))]
    return bool(directives and directives[0][0].lower() == "include" and directives[0][1:] == [str(fragment)])


@pytest.mark.parametrize("supports_banner", [False, True])
def test_a_preexisting_fragment_is_reconnected_once_without_touching_provider_rules(
    provider, monkeypatch, supports_banner
):
    main, managed = provider
    managed.write_bytes(policy.POLICY)  # e.g. an earlier run left the file outside the include graph
    other = managed.with_name("00-cloud.conf")
    other.write_bytes(b"# provider-managed\nMatch User backups\n  KbdInteractiveAuthentication no\n")
    original, other_original = main.read_bytes(), other.read_bytes()
    reloads = []
    monkeypatch.setattr(policy, "supports_debian_banner", lambda _main: supports_banner)
    monkeypatch.setattr(policy, "reload_sshd", lambda: reloads.append(True))
    monkeypatch.setattr(
        policy, "effective", lambda path: _settings("no" if _explicit_include(path, managed) else "yes")
    )

    assert policy.harden(main.parent) is True
    after = main.read_bytes()
    assert _explicit_include(main, managed) and after.endswith(original)
    assert (b"DebianBanner no" in managed.read_bytes()) is supports_banner
    assert other.read_bytes() == other_original and stat.S_IMODE(main.stat().st_mode) == 0o640
    assert policy.harden(main.parent) is False
    assert main.read_bytes() == after and reloads == [True]


@pytest.mark.parametrize("failure", ["validation", "reload"])
def test_failed_change_restores_existing_files_and_does_not_touch_provider_snippets(provider, monkeypatch, failure):
    main, managed = provider
    original = main.read_bytes()
    legacy = managed.with_name("99-meridian.conf")
    legacy.write_bytes(LEGACY_POLICY)
    other = managed.with_name("50-provider.conf")
    other.write_bytes(b"AllowTcpForwarding no\n")
    reloads = []
    monkeypatch.setattr(policy, "supports_debian_banner", lambda _main: True)

    def check(path):
        if _explicit_include(path, managed):
            if failure == "validation":
                raise policy.HardeningError("invalid candidate")
            return _settings()
        return _settings("yes")

    def reload():
        reloads.append(True)
        if len(reloads) == 1:
            raise policy.HardeningError("reload refused")

    monkeypatch.setattr(policy, "effective", check)
    monkeypatch.setattr(policy, "reload_sshd", reload)
    with pytest.raises(policy.HardeningError):
        policy.harden(main.parent)

    assert main.read_bytes() == original and not managed.exists()
    assert legacy.read_bytes() == LEGACY_POLICY
    assert stat.S_IMODE(main.stat().st_mode) == 0o640
    assert other.read_bytes() == b"AllowTcpForwarding no\n"
    assert bool(reloads) is (failure == "reload")


def test_migrates_owned_legacy_bytes_and_preserves_a_modified_legacy_file(provider, monkeypatch):
    main, managed = provider
    legacy, modified = (managed.with_name(f"{order}-meridian.conf") for order in ("00", "99"))
    legacy.write_bytes(LEGACY_POLICY)
    modified.write_bytes(b"# owner policy\nPermitTTY no\n")
    monkeypatch.setattr(policy, "supports_debian_banner", lambda _main: True)
    monkeypatch.setattr(policy, "effective", lambda _main: _settings())
    monkeypatch.setattr(policy, "reload_sshd", lambda: None)

    assert policy.harden(main.parent) is True
    assert managed.read_bytes() == policy.POLICY and not legacy.exists()
    assert modified.read_bytes() == b"# owner policy\nPermitTTY no\n"


def test_unknown_file_at_the_managed_name_is_left_for_the_owner(provider, monkeypatch):
    main, managed = provider
    original = main.read_bytes()
    managed.write_bytes(b"# provider policy\nPermitTTY no\n")
    monkeypatch.setattr(policy, "supports_debian_banner", lambda _main: True)
    monkeypatch.setattr(policy, "effective", lambda _main: _settings())
    monkeypatch.setattr(policy, "reload_sshd", lambda: pytest.fail("must not reload a colliding policy"))
    with pytest.raises(policy.HardeningError):
        policy.harden(main.parent)
    assert managed.read_bytes() == b"# provider policy\nPermitTTY no\n" and main.read_bytes() == original


def test_failed_write_on_a_provider_without_a_snippets_directory_leaves_the_original_layout(provider, monkeypatch):
    main, managed = provider
    original = main.read_bytes()
    managed.parent.rmdir()
    replace = policy.os.replace

    def refuse_new_fragment(source, dest):
        if dest == managed:
            raise PermissionError("read-only policy directory")
        return replace(source, dest)

    monkeypatch.setattr(policy, "supports_debian_banner", lambda _main: True)
    monkeypatch.setattr(policy.os, "replace", refuse_new_fragment)
    monkeypatch.setattr(policy, "reload_sshd", lambda: pytest.fail("no valid policy to reload"))
    with pytest.raises((policy.HardeningError, OSError)):
        policy.harden(main.parent)
    assert main.read_bytes() == original and set(main.parent.iterdir()) == {main}


@pytest.mark.skipif(
    not Path("/etc/debian_version").exists() or not shutil.which("sshd") or not shutil.which("ssh-keygen"),
    reason="requires Debian/Ubuntu OpenSSH server tools; no listener or system SSH config is used",
)
@pytest.mark.parametrize("layout", ["no include", "early setting", "earlier snippet", "stock wildcard"])
def test_openssh_reads_the_policy_before_provider_defaults_and_keeps_the_match(provider, monkeypatch, layout):
    main, managed = provider
    sshd = shutil.which("sshd")
    assert sshd
    key = main.parent / "host_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    wildcard = f'Include "{managed.parent}/*.conf"\n'
    provider_text = "PasswordAuthentication yes\nKbdInteractiveAuthentication yes\n"
    config = {
        "no include": provider_text,
        "early setting": provider_text + wildcard,
        "earlier snippet": wildcard,
        "stock wildcard": wildcard,
    }[layout]
    other = managed.with_name("00-a-provider.conf" if layout == "earlier snippet" else "99-provider.conf")
    # The owner requires a public key and an interactive second factor for one account.
    match = (
        "Match User archive\n"
        "  AuthenticationMethods publickey,keyboard-interactive\n"
        "  KbdInteractiveAuthentication yes\n"
        "  AllowTcpForwarding no\n"
    )
    other.write_text(provider_text + (match if layout == "stock wildcard" else ""))
    other_original = other.read_bytes()
    original = (config + f'Port 2222\nHostKey "{key}"\n' + ("" if layout == "stock wildcard" else match)).encode()
    main.write_bytes(original)
    reloads = []
    monkeypatch.setattr(policy, "reload_sshd", lambda: reloads.append(True))

    assert policy.harden(main.parent) is True
    after = main.read_bytes()
    assert after.endswith(original) and other.read_bytes() == other_original
    if layout == "stock wildcard":
        assert after == original
    for user, forwarding, kbd, methods in (
        ("archive", "no", "yes", "publickey,keyboard-interactive"),
        ("ordinary", "yes", "no", "any"),
    ):
        conditions = f"user={user},host=client.test,addr=192.0.2.1"
        actual = subprocess.run(
            [sshd, "-T", "-f", str(main), "-C", conditions], capture_output=True, text=True, check=True
        )
        settings = {line.partition(" ")[0]: line.partition(" ")[2] for line in actual.stdout.splitlines()}
        assert settings["passwordauthentication"] == "no" and settings["kbdinteractiveauthentication"] == kbd
        assert settings["authenticationmethods"] == methods
        assert settings["allowtcpforwarding"] == forwarding and settings["port"] == "2222"
    assert policy.harden(main.parent) is False
    assert main.read_bytes() == after and reloads == [True]
