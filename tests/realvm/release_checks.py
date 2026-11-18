"""Check panel and browser changes on the b12 rehearsal's restored, disposable server."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from meridian.cluster import ClusterConfig
from meridian.control.snapshot import read_header
from meridian.core.apply import CompiledApplyResult
from meridian.host_runtime import AGENT_UNIT
from tests.realvm.handover_drill import HandoverDrill
from tests.realvm.upgrade_support import VerificationError, parse_userinfo

if TYPE_CHECKING:
    from tests.realvm.upgrade_verifier import UpgradeVerifier

SETTINGS = "api/settings/presentation"


class ManualFields(HTMLParser):
    """A browser without JavaScript can use only the server-rendered fallback."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.values: list[str] = []
        self.field: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "noscript":
            self.depth += 1
        fields = dict(attrs)
        if (
            self.depth
            and tag == "textarea"
            and "readonly" in fields
            and fields.get("aria-labelledby") == "manual-setup-title"
        ):
            self.field = []

    def handle_data(self, data: str) -> None:
        if self.field is not None:
            self.field.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self.field is not None:
            self.values.append("".join(self.field))
            self.field = None
        if tag == "noscript":
            self.depth = max(0, self.depth - 1)


def _request(client: httpx.Client, method: str, path: str, expected: int = 200, **kwargs: Any) -> httpx.Response:
    try:
        response = client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise VerificationError(f"The live release check could not reach the service ({type(exc).__name__}).") from None
    if response.status_code != expected:
        raise VerificationError(f"The live release check expected HTTP {expected}, got {response.status_code}.")
    return response


def _object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise VerificationError("The live control service did not return a JSON object.")


def restored_cluster(run: UpgradeVerifier) -> ClusterConfig:
    drill = HandoverDrill(run)
    drill.require_lab_workspace()
    if not drill.snapshot.is_file():
        raise VerificationError("Release checks require the snapshot saved by this b12 rehearsal.")
    cluster = ClusterConfig.load(Path(run.env["MERIDIAN_HOME"]) / "cluster.yml")
    header = read_header(drill.snapshot.read_bytes())
    record = cluster.control_restore
    endpoint = urlsplit(cluster.panel.url)
    if (
        not record
        or record.step != "done"
        or record.links_change
        or Path(record.snapshot).resolve() != drill.snapshot.resolve()
        or header.fleet_id != cluster.fleet_id
        or record.fleet_epoch <= header.fleet_epoch
        or endpoint.scheme != "https"
        or endpoint.hostname != run.ip
        or endpoint.port not in (None, 443)
        or endpoint.username is not None
        or endpoint.query
        or endpoint.fragment
        or not cluster.panel.secret_path
        or endpoint.path.strip("/") != cluster.panel.secret_path.strip("/")
        or cluster.panel.server_ip != run.ip
        or not cluster.panel.api_token
    ):
        raise VerificationError("The control endpoint cannot be tied to this run's completed same-address restore.")
    return cluster


@contextmanager
def stopped_agent(run: UpgradeVerifier) -> Iterator[None]:
    def command(label: str, cmd: str) -> str:
        return run.execute(label, ["ssh", f"root@{run.ip}", cmd], timeout=60).stdout.strip()

    def require_state(expected: str) -> None:
        if command("Check the node agent state", f"systemctl show -p ActiveState --value {AGENT_UNIT}") != expected:
            raise VerificationError(f"The disposable node agent did not report {expected}.")

    require_state("active")
    try:
        command("Stop the node agent for the service-only edit", f"systemctl stop {AGENT_UNIT}")
        require_state("inactive")
        yield
        require_state("inactive")
    finally:
        command("Restart the node agent", f"systemctl start {AGENT_UNIT}")
        require_state("active")


def verify_presentation(run: UpgradeVerifier, client: httpx.Client, fleet_id: str, epoch: int) -> None:
    health = _object(_request(client, "GET", "api/health"))
    if health.get("fleet_id") != fleet_id or health.get("fleet_epoch") != epoch:
        raise VerificationError("The live service does not own the snapshot's restored fleet and epoch.")
    before = _object(_request(client, "GET", SETTINGS))
    version = before.get("version")
    if type(version) is not int:
        raise VerificationError("The live settings have no integer version.")
    title = "Release rehearsal" if before.get("profile_title") != "Release rehearsal" else "Restored rehearsal"
    interval = 17 if before.get("update_interval_hours") != 17 else 19
    desired = {"profile_title": title, "update_interval_hours": interval}
    with stopped_agent(run):
        saved = _object(_request(client, "PATCH", SETTINGS, json={"expected_version": version, **desired}))
        saved_version = saved.get("version")
        if (
            type(saved_version) is not int
            or saved_version <= version
            or any(saved.get(k) != v for k, v in desired.items())
        ):
            raise VerificationError("Saving the service-only settings did not commit the requested new version.")
        stale = _object(
            _request(
                client, "PATCH", SETTINGS, 409, json={"expected_version": version, "profile_title": "Stale rehearsal"}
            )
        )
        error = stale.get("error")
        if not isinstance(error, dict) or error.get("code") != "stale_presentation":
            raise VerificationError("The settings API did not explain the stale write conflict.")
        if _object(_request(client, "GET", SETTINGS)) != saved:
            raise VerificationError("The rejected stale settings edit changed the saved view.")
    run.pass_check(
        "a live edit succeeded with the node agent stopped; a stale edit was refused and the agent restarted"
    )
    applied = CompiledApplyResult.model_validate(run.json_command(["apply", "--yes"]))
    if not applied.actions or not applied.all_succeeded:
        raise VerificationError("The reviewed apply after the settings edit did not succeed.")
    after = _object(_request(client, "GET", SETTINGS))
    if after.get("version") != saved_version or any(after.get(k) != v for k, v in desired.items()):
        raise VerificationError("The reviewed apply overwrote the panel's saved subscription settings.")
    run.pass_check("the title, refresh interval and settings version survived a reviewed apply")


def verify_nojs(client: httpx.Client, urls: Mapping[str, str]) -> None:
    if not urls:
        raise VerificationError("No restored person is available to check browser handoff.")
    for url in urls.values():
        response = _request(client, "GET", url, headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"})
        if response.headers.get("content-type", "").split(";")[0].strip() != "text/html" or "no-store" not in {
            part.lower().strip() for part in response.headers.get("cache-control", "").split(",")
        }:
            raise VerificationError("The browser response was not HTML with Cache-Control: no-store.")
        parser = ManualFields()
        parser.feed(response.text)
        parser.close()
        if parser.values != [url]:
            raise VerificationError("The no-JavaScript handoff did not contain exactly this person's subscription URL.")
        # Follow the copyable field as an app. Neither request carries the panel token.
        delivered = _request(client, "GET", url, headers={"User-Agent": "Happ/1.0"})
        try:
            parse_userinfo(delivered.headers.get("subscription-userinfo", ""))
            doc = delivered.json()
            profiles = doc if isinstance(doc, list) else [doc]
            if not any(isinstance(row, dict) and row.get("outbounds") for row in profiles):
                raise ValueError
        except ValueError:
            raise VerificationError("The no-JavaScript handoff did not deliver a working app profile.") from None


def verify_release_changes(run: UpgradeVerifier) -> None:
    cluster = restored_cluster(run)
    assert cluster.control_restore is not None  # verified above
    with httpx.Client(
        base_url=cluster.panel.url.rstrip("/") + "/",
        headers={"Authorization": f"Bearer {cluster.panel.api_token}"},
        timeout=30,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        verify_presentation(run, client, cluster.fleet_id, cluster.control_restore.fleet_epoch)
    _, urls = run.details(run.people())
    with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as anonymous:
        verify_nojs(anonymous, urls)
    run.pass_check("every restored person's no-JavaScript handoff delivered their own subscription to an anonymous app")
