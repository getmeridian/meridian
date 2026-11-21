"""Set the global SSH authentication defaults on the target. Uses only system Python."""

from __future__ import annotations

import fcntl
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

POLICY = (
    b"PasswordAuthentication no\nKbdInteractiveAuthentication no\nChallengeResponseAuthentication no\nDebianBanner no\n"
)


class HardeningError(Exception):
    """Safe to show: never include raw sshd configuration or private command output."""


def effective(main: Path) -> dict[str, str]:
    for flag in ("-t", "-T"):
        result = subprocess.run(["sshd", flag, "-f", str(main)], capture_output=True)
        if result.returncode:
            raise HardeningError("sshd config validation failed; inspect the configuration with sshd -t.")
    return dict(line.split(" ", 1) for line in result.stdout.decode(errors="replace").splitlines() if " " in line)


def mismatch(settings: dict[str, str]) -> str:
    for key in ("passwordauthentication", "kbdinteractiveauthentication", "debianbanner"):
        if key == "debianbanner" and key not in settings:
            continue
        if settings.get(key) != "no":
            return f"effective sshd setting mismatch: expected '{key} no'"
    return ""


def supports_debian_banner(main: Path) -> bool:
    # Use the host keys and paths from this configuration, even on a nonstandard install.
    # Some builds accept the directive but omit it from sshd -T.
    return subprocess.run(["sshd", "-t", "-f", str(main), "-o", "DebianBanner=no"], capture_output=True).returncode == 0


def reload_sshd() -> None:
    for unit in ("ssh.service", "sshd.service"):
        if subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0:
            if subprocess.run(["systemctl", "reload", unit], capture_output=True).returncode:
                raise HardeningError("sshd reload failed.")
            return
    raise HardeningError("No active ssh or sshd service to reload.")


def contents(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise HardeningError(f"Refusing to replace a linked or non-regular SSH file: {path.name}.")
    return path.read_bytes()


def harden(directory: Path) -> bool:
    """Reload only validated changes. Preserve the main file's settings, mode and owner."""
    main = directory / "sshd_config"
    snippets = directory / "sshd_config.d"
    dropin = snippets / "00-hardening.conf"
    pending = directory / ".00-hardening-reload-pending"
    marker = b"SSH policy reload pending\n"
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    with ExitStack() as temporary:
        temporary.callback(os.close, fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HardeningError("Another SSH configuration change is running. Retry when it finishes.") from exc
        original = contents(main)
        if original is None or snippets.is_symlink():
            raise HardeningError("Cannot identify the regular main SSH configuration or its drop-in directory.")
        previous = contents(dropin)
        pending_state = contents(pending)
        if pending_state not in (None, marker):
            raise HardeningError("An unrecognized SSH reload marker exists. Its file was kept.")
        # Some sshd builds do not implement DebianBanner; it is optional, unlike authentication.
        auth_only = POLICY.removesuffix(b"DebianBanner no\n")
        desired = POLICY if supports_debian_banner(main) else auth_only
        if previous is not None and previous.strip() not in (POLICY.strip(), auth_only.strip()):
            raise HardeningError("00-hardening.conf contains other settings. Review it before hardening SSH.")

        def scratch(parent: Path) -> Path:
            handle, name = tempfile.mkstemp(prefix=".ssh-config-", dir=parent)
            os.close(handle)
            file = Path(name)
            temporary.callback(file.unlink, missing_ok=True)
            return file

        def replace(path: Path, before: bytes | None, after: bytes | None, undo: ExitStack) -> None:
            if contents(path) != before:
                raise HardeningError("The SSH configuration changed during review. Retry with the current files.")
            saved = None
            new = scratch(path.parent)
            if after is not None:
                new.write_bytes(after)
                if before is None:
                    new.chmod(0o644)
                else:
                    info = path.stat()
                    os.chown(new, info.st_uid, info.st_gid)
                    shutil.copystat(path, new)
            if before is not None:
                saved = scratch(path.parent)
                saved.unlink()
                os.link(path, saved)  # retain the exact inode and metadata for rollback
            if after is None:
                path.unlink()
            else:
                os.replace(new, path)
            if saved is None:
                undo.callback(path.unlink, missing_ok=True)
            else:
                undo.callback(os.replace, saved, path)

        changed = False
        attempted_reload = False
        finished = False
        with ExitStack() as undo:

            def update(path: Path, before: bytes | None, after: bytes | None) -> None:
                nonlocal changed
                if not changed and pending_state is None:
                    replace(pending, None, marker, undo)
                replace(path, before, after, undo)
                changed = True

            try:
                if not snippets.exists():
                    snippets.mkdir(mode=0o755)
                    # Sibling temp files must be gone before removing a newly created directory.
                    temporary.callback(lambda: None if finished else snippets.rmdir())
                if previous != desired:
                    update(dropin, previous, desired)
                for name in ("00-meridian.conf", "99-meridian.conf"):
                    legacy = snippets / name
                    if legacy.is_file() and not legacy.is_symlink() and legacy.read_bytes().strip() == POLICY.strip():
                        update(legacy, contents(legacy), None)
                problem = mismatch(effective(main))
                if problem:
                    # A wildcard would activate every dormant provider file. Prepend only ours:
                    # sshd uses the first global value and an appended Include can be inside Match.
                    prefix = b'Include "' + os.fsencode(dropin) + b'"\n'
                    if original.startswith(prefix):
                        raise HardeningError(problem)
                    candidate = scratch(directory)
                    candidate.write_bytes(prefix + original)
                    if remaining := mismatch(effective(candidate)):
                        raise HardeningError(remaining)
                    update(main, original, prefix + original)
                    if remaining := mismatch(effective(main)):
                        raise HardeningError(remaining)
                if changed or pending_state:
                    attempted_reload = True
                    reload_sshd()
                    pending.unlink()
                undo.pop_all()
                finished = True
            except (OSError, HardeningError):
                undo.close()
                if attempted_reload:
                    # A unit can signal sshd and then report failure. Restore the previous
                    # validated configuration to the running listener as well as the filesystem.
                    try:
                        effective(main)
                        reload_sshd()
                    except (OSError, HardeningError) as exc:
                        raise HardeningError(
                            "SSH reload failed. Previous files restored; check the active SSH service."
                        ) from exc
                raise
        return changed or pending_state is not None


if __name__ == "__main__":
    try:
        print("changed" if harden(Path("/etc/ssh")) else "ok")
    except (HardeningError, OSError) as exc:
        sys.exit(str(exc))
