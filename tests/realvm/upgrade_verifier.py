"""Local-only published-release upgrade journey, entered through verify/upgrade.sh."""

from __future__ import annotations

import ipaddress
import os
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from packaging.version import InvalidVersion, Version

from meridian.control.schema import SCHEMA_VERSION
from meridian.control_artifacts import agent_binary, source_checkout
from meridian.core.client_limits import parse_traffic_limit
from meridian.core.clients import ClientDetail
from meridian.core.errors import MeridianError
from meridian.host_runtime import CONTROL_SERVICE_STATE_DIR
from meridian.servers import ServerRegistry
from meridian.ssh import CommandResult
from meridian.update import get_pypi_releases
from tests.realvm.domain_support import is_rate_limited, verification_counts
from tests.realvm.upgrade_support import (
    SUBSCRIPTION_CASES,
    SubscriptionSnapshot,
    build_intent,
    client_detail,
    command_data,
    compare_subscriptions,
    confirmation_input,
    failure_excerpt,
    fleet_agent_version,
    is_pypi_unreachable,
    parse_userinfo,
    previous_published_version,
    redact_output,
)


class VerificationError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def select_previous_version(checkout: str, env: Mapping[str, str]) -> str:
    override = env.get("REALVM_UPGRADE_FROM", "")
    releases = [] if override.strip() else get_pypi_releases()
    if releases is None:
        raise VerificationError("PyPI could not be reached; no upgrade verdict is possible.", exit_code=75)
    try:
        return previous_published_version(checkout, releases, override)
    except ValueError as exc:
        raise VerificationError(str(exc), exit_code=2) from None


def preflight_upgrade(root: Path, env: Mapping[str, str]) -> tuple[str, str]:
    """Check the exact checkout and x86 build, then freeze the release before paying for a VM."""
    checkout = source_checkout()
    if checkout is None or checkout.resolve() != root.resolve():
        raise VerificationError(
            "Run the verifier through uv in this checkout after uv sync; its CLI must deploy this checkout's wheel.",
            exit_code=2,
        )
    try:
        expected_agent = agent_binary("x86_64").version
    except MeridianError as exc:
        raise VerificationError(f"{exc} {exc.hint}", exit_code=2) from None
    if not expected_agent:
        raise VerificationError("The checkout agent build has no .version sidecar. Run make agent first.", exit_code=2)
    return select_previous_version((root / "VERSION").read_text(), env), expected_agent


class UpgradeVerifier:
    def __init__(self, root: Path, work: Path, ip: str, env: Mapping[str, str]) -> None:
        self.root, self.work, self.ip = root, work, ip
        self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.known = (env.get("HCLOUD_TOKEN", ""), ip)
        self.env = dict(env)
        for key in (
            "MERIDIAN_CONTEXT",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "HCLOUD_TOKEN",
        ):
            self.env.pop(key, None)
        self.env.update(
            {
                "MERIDIAN_HOME": env.get("MERIDIAN_HOME", str(work / "home")),
                "MERIDIAN_DISABLE_UPDATE_CHECK": "1",
                "XDG_CACHE_HOME": str(work / "cache"),
                "UV_CACHE_DIR": str(work / "uv-cache"),
                "NO_COLOR": "1",
                "TERM": "dumb",
            }
        )
        self.old_env = {key: value for key, value in self.env.items() if key != "MERIDIAN_AGENT_BINARY"}
        self.current = ["uv", "run", "meridian"]
        self.previous_venv = work / "previous"
        self.previous = [str(self.previous_venv / "bin" / "meridian")]
        self.passed = 0
        self.command_count = 0

    def say(self, message: str) -> None:
        print(redact_output(message, self.known), flush=True)

    def pass_check(self, message: str) -> None:
        self.passed += 1
        self.say(f"  PASS: {message}")

    def execute(
        self,
        label: str,
        args: list[str],
        *,
        old: bool = False,
        stdin: str | None = None,
        allowed: tuple[int, ...] = (0,),
        certificates: bool = False,
        installation: bool = False,
        timeout: int = 900,
    ) -> CommandResult:
        self.command_count += 1
        self.say(f">>> {label}")
        started = time.monotonic()
        try:
            completed = subprocess.run(
                args,
                cwd=self.work if old or installation else self.root,
                env=self.old_env if old or installation else self.env,
                capture_output=True,
                text=True,
                timeout=timeout,
                **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}),
            )
        except (OSError, subprocess.SubprocessError):
            raise VerificationError(f"{label} could not finish. Check the local tools and SSH connection.") from None
        result = CommandResult(
            args=args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        output = result.stdout + "\n" + result.stderr
        (self.work / f"command-{self.command_count:02d}.log").write_text(output)
        if certificates and is_rate_limited(output):
            raise VerificationError(
                "Let's Encrypt refused certificate issuance because of a rate limit. "
                "Retry with a fresh VM IP or later.",
                exit_code=75,
            )
        if result.returncode not in allowed:
            if installation and is_pypi_unreachable(output):
                raise VerificationError(
                    "PyPI package downloads could not be reached; no upgrade verdict is possible.", exit_code=75
                )
            excerpt = failure_excerpt(output, self.known)
            raise VerificationError(
                f"{label} failed (exit {result.returncode}).\n{excerpt}", exit_code=2 if installation else 1
            )
        return result

    def json_command(self, args: list[str], *, old: bool = False, allowed: tuple[int, ...] = (0,)) -> dict[str, Any]:
        prefix = self.previous if old else self.current
        result = self.execute(
            f"{'published release' if old else 'checkout'}: {' '.join(args)}",
            [*prefix, "--json", *args],
            old=old,
            allowed=allowed,
        )
        return command_data(result.stdout)

    def install_previous(self, version: str) -> None:
        self.execute(
            "Create the isolated published-release venv",
            ["uv", "venv", "--python", sys.executable, str(self.previous_venv)],
            installation=True,
        )
        self.execute(
            "Install the published release from PyPI",
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(self.previous_venv / "bin" / "python"),
                "--default-index",
                "https://pypi.org/simple",
                f"meridian-vpn=={version}",
            ],
            installation=True,
        )
        reported = (
            self.execute("Read the installed release version", [*self.previous, "--version"], old=True)
            .stdout.strip()
            .removeprefix("meridian ")
        )
        try:
            matches = Version(reported) == Version(version)
        except InvalidVersion:
            matches = False
        if not matches:
            raise VerificationError("The isolated venv is not running the selected published release.")

    def prepare_ssh(self) -> None:
        """Pin throwaway host keys for SSH, SCP, and Meridian's ssh-keygen lookups."""
        # The kept fleet retains its SSH wrappers; raw captures and the old venv still disappear.
        fleet_workspace = Path(self.env["MERIDIAN_HOME"]).parent
        ssh_root = fleet_workspace / "ssh"
        ssh_root.mkdir(parents=True, mode=0o700)
        known_hosts = ssh_root / "known_hosts"
        scanned = self.execute("Capture the VM's SSH host keys", ["ssh-keyscan", "-T", "15", self.ip], timeout=30)
        if not scanned.stdout.strip():
            raise VerificationError("The VM did not return SSH host keys.")
        known_hosts.write_text(scanned.stdout)
        config = ssh_root / "ssh_config"
        settings = (
            "Host *\n"
            f"  UserKnownHostsFile {shlex.quote(str(known_hosts))}\n"
            "  StrictHostKeyChecking yes\n  BatchMode yes\n"
            "  ServerAliveInterval 15\n  ServerAliveCountMax 4\n"
        )
        public_key = self.env.get("MERIDIAN_TEST_SSH_KEY", "")
        if public_key.endswith(".pub"):
            settings += f"  IdentityFile {shlex.quote(public_key[:-4])}\n  IdentitiesOnly yes\n  IdentityAgent none\n"
        config.write_text(settings)
        bin_dir = ssh_root / "bin"
        bin_dir.mkdir()
        wrappers = {
            "ssh": f'exec /usr/bin/ssh -F {shlex.quote(str(config))} "$@"\n',
            "scp": f'exec /usr/bin/scp -F {shlex.quote(str(config))} "$@"\n',
            "ssh-keygen": (
                'case " $* " in *" -F "*|*" -R "*) '
                f'exec /usr/bin/ssh-keygen -f {shlex.quote(str(known_hosts))} "$@";; esac\n'
                'exec /usr/bin/ssh-keygen "$@"\n'
            ),
        }
        for name, body in wrappers.items():
            path = bin_dir / name
            path.write_text("#!/bin/sh\n" + body)
            path.chmod(0o700)
        for env in (self.env, self.old_env):
            env["PATH"] = f"{bin_dir}:{env.get('PATH', os.defpath)}"
        (fleet_workspace / "env.sh").write_text(
            f"export MERIDIAN_HOME={shlex.quote(self.env['MERIDIAN_HOME'])}\n"
            "export MERIDIAN_DISABLE_UPDATE_CHECK=1\n"
            f'export PATH={shlex.quote(str(bin_dir))}:"$PATH"\n'
        )

    def deploy_previous(self) -> None:
        home = Path(self.env["MERIDIAN_HOME"])
        if (home / "cluster.yml").exists() or (home / "servers.json").exists():
            raise VerificationError("The upgrade verifier needs an empty isolated MERIDIAN_HOME.", exit_code=2)
        self.execute(
            "Save the server with the published release", [*self.previous, "server", "add", f"root@{self.ip}"], old=True
        )
        server = ServerRegistry(home / "servers.json").find(self.ip)
        if server is None:
            raise VerificationError("The published release did not save the server.")
        intent = self.work / "intent.json"
        intent.write_text(build_intent(server.id, self.ip))
        self.execute(
            "Deploy the published release on the bare IP",
            [*self.previous, "setup", "--intent", str(intent), "--yes"],
            old=True,
            certificates=True,
            timeout=1800,
        )
        self.pass_check("published release deployed")

    def set_limited_client(self, expiry: str) -> None:
        self.execute(
            "Create bob with the published release", [*self.previous, "client", "add", "bob", "--yes"], old=True
        )
        help_text = self.execute(
            "Detect the published release's client set options", [*self.previous, "client", "set", "--help"], old=True
        ).stdout
        try:
            flags, stdin = confirmation_input(help_text, required=("--limit", "--expires"))
        except ValueError as exc:
            raise VerificationError(str(exc), exit_code=2) from None
        self.execute(
            "Set bob's 50GB limit and fixed UTC expiry",
            [*self.previous, "client", "set", "bob", "--limit", "50GB", "--expires", expiry, *flags],
            old=True,
            stdin=stdin,
        )

    def people(self, *, old: bool = False) -> list[str]:
        try:
            names = [row["username"] for row in self.json_command(["client", "list"], old=old)["clients"]]
            if names and all(isinstance(name, str) and name for name in names) and len(set(names)) == len(names):
                return sorted(names)
        except (KeyError, TypeError):
            pass
        raise VerificationError("Client list returned no complete, unique set of people.")

    def details(self, people: list[str], *, old: bool = False) -> tuple[dict[str, ClientDetail], dict[str, str]]:
        clients, urls = {}, {}
        for person in people:
            data = self.json_command(["client", "show", person], old=old)
            clients[person] = client_detail(data)
            url = data.get("handoff", {}).get("subscription_url", "")
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != self.ip
                or parsed.port not in (None, 443)
                or parsed.username
                or not parsed.path
            ):
                raise VerificationError("Client show returned no subscription URL on the test VM.")
            urls[person] = url
        return clients, urls

    def capture(self, urls: Mapping[str, str], phase: str) -> dict[tuple[str, str], SubscriptionSnapshot]:
        captured = {}
        saved = self.work / phase
        saved.mkdir()
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            for index, (person, url) in enumerate(sorted(urls.items())):
                for case in SUBSCRIPTION_CASES:
                    label = redact_output(f"{person} / {case.label}", self.known)
                    self.say(f">>> Capture {phase}: {label}")
                    try:
                        response = client.get(url, headers={"User-Agent": case.user_agent})
                    except httpx.HTTPError:
                        raise VerificationError(
                            f"{label}: subscription request failed; check the VM's HTTPS service."
                        ) from None
                    if response.status_code != 200 or not response.content:
                        raise VerificationError(
                            f"{label}: subscription returned HTTP {response.status_code} or an empty document."
                        )
                    header = response.headers.get("subscription-userinfo", "")
                    try:
                        parse_userinfo(header)
                    except ValueError as exc:
                        raise VerificationError(f"{label}: {exc}") from None
                    captured[(person, case.label)] = SubscriptionSnapshot(response.content, header)
                    (saved / f"{index}-{case.name}.body").write_bytes(response.content)
                    (saved / f"{index}-{case.name}.userinfo").write_text(header)
        return captured

    def verify_schema(self) -> None:
        database = f"{CONTROL_SERVICE_STATE_DIR}/control.db"
        script = (
            "import sqlite3; "
            f"db=sqlite3.connect({('file:' + database + '?mode=ro')!r}, uri=True); "
            "print(db.execute(\"SELECT value FROM meta WHERE key='schema_version'\").fetchone()[0]); db.close()"
        )
        result = self.execute(
            "Read the control service schema over SSH",
            ["ssh", f"root@{self.ip}", "python3 -c " + shlex.quote(script)],
            timeout=30,
        )
        if result.stdout.strip() != str(SCHEMA_VERSION):
            raise VerificationError(
                f"The control service schema does not equal checkout SCHEMA_VERSION ({SCHEMA_VERSION})."
            )
        self.pass_check(f"control service schema equals SCHEMA_VERSION ({SCHEMA_VERSION})")

    def wait_for(self, label: str, predicate: Callable[[], bool], *, seconds: int = 90) -> None:
        deadline = time.monotonic() + seconds
        while True:
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise VerificationError(f"{label} did not become ready within {seconds} seconds.")
            time.sleep(5)

    def plan_converged(self) -> bool:
        data = self.json_command(["plan"], allowed=(0, 2, 3))
        return data.get("converged") is True and data.get("exit_code") == 0

    def agent_current(self, expected: str) -> bool:
        data = self.json_command(["fleet", "status"], allowed=(0, 3))
        try:
            return fleet_agent_version(data) == expected
        except ValueError:
            return False

    def run(self) -> None:
        version, expected_agent = preflight_upgrade(self.root, self.env)
        self.say(f"Upgrade from published {version} to the checkout")
        self.install_previous(version)
        self.prepare_ssh()
        self.deploy_previous()
        expiry = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
        self.set_limited_client(expiry)
        people = self.people(old=True)
        if not {"default", "bob"}.issubset(people):
            raise VerificationError("The published release did not create default and bob.")
        before_clients, urls = self.details(people, old=True)
        bob = before_clients["bob"]
        if bob.traffic_limit_bytes != parse_traffic_limit("50GB") or bob.expires_at[:10] != expiry:
            raise VerificationError("The published release did not save bob's requested limit and expiry.")
        self.pass_check("bob has a traffic limit and fixed expiry")
        before = self.capture(urls, "before")
        self.pass_check(f"captured {len(before)} documents and usage headers")
        self.json_command(["plan"], allowed=(0, 2))
        self.execute("Apply the checkout in place", [*self.current, "apply", "--yes"], certificates=True, timeout=1800)
        self.pass_check("checkout applied in place")
        self.verify_schema()
        self.wait_for("Declarative plan convergence", self.plan_converged)
        self.pass_check("JSON plan reports converged")
        after_people = self.people()
        if after_people != people:
            raise VerificationError("The set of people changed during the upgrade.")
        after_clients, after_urls = self.details(after_people)
        if after_urls != urls:
            raise VerificationError("A person's subscription URL changed during the upgrade.")
        failures = compare_subscriptions(before, self.capture(urls, "after"))
        if failures:
            raise VerificationError("Subscriptions changed:\n" + "\n".join(failures))
        self.pass_check("all documents and subscription-userinfo headers are byte-identical")
        preserved = (
            "id",
            "status",
            "traffic_limit_bytes",
            "expires_at",
            "traffic_used_bytes",
            "lifetime_traffic_bytes",
            "traffic_reset",
            "traffic_reset_at",
        )
        changed = [field for field in preserved if getattr(bob, field) != getattr(after_clients["bob"], field)]
        if changed:
            raise VerificationError("bob's client metadata changed: " + ", ".join(changed))
        self.pass_check("bob's limit, expiry, and usage metadata survived")
        self.wait_for("The checkout's agent version", lambda: self.agent_current(expected_agent))
        self.pass_check("fleet status reports the checkout's built agent version")
        # Traffic probes come last: they must not change the usage headers being compared.
        test = self.execute("Run meridian test end to end", [*self.current, "--json", "test"])
        passed, failed, _warnings, _skipped = verification_counts(test.stdout)
        if failed or passed <= 0:
            raise VerificationError("meridian test produced no passing end-to-end verdict.")
        self.pass_check("meridian test passes end to end")
        self.say(f"PASS: {self.passed}  FAIL: 0")


def main() -> int:
    env = dict(os.environ)
    if env.get("CI") or env.get("GITHUB_ACTIONS"):
        print("Real-VM verification is local-only and cannot run in CI.")
        return 2
    if not env.get("MERIDIAN_FLEET_ID"):
        env.pop("MERIDIAN_HOME", None)
    try:
        ip = str(ipaddress.IPv4Address(env.get("TARGET_IP", "")))
    except ValueError:
        print("TARGET_IP must name the test VM's IPv4 address.")
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="meridian-realvm-upgrade-") as temporary:
            UpgradeVerifier(Path(__file__).parents[2], Path(temporary), ip, env).run()
    except VerificationError as exc:
        prefix = "INCONCLUSIVE" if exc.exit_code == 75 else "FAIL"
        print(f"{prefix}: {redact_output(str(exc), known=(env.get('HCLOUD_TOKEN', ''), ip))}")
        return exc.exit_code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL: The verifier could not read the required evidence ({type(exc).__name__}).")
        return 1
    except KeyboardInterrupt:
        print("Upgrade verification interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
