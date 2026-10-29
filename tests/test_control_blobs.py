"""The control service's blob store: mirroring pinned artifacts and serving them to agents."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from meridian.control.blobs import RETRY_SECONDS, Artifact, BlobStore, spec_artifacts
from meridian.control.enrollment import token_sha256
from meridian.control.models import NodeSpec
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from tests.support.agent_node import FLEET_ID, FakeNode

ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"
ARCHIVE = b"PK\x03\x04 a pinned archive"
SHA = hashlib.sha256(ARCHIVE).hexdigest()
URL = "https://downloads.example.com/archive.zip"
MIRROR = "https://mirror.example.com/archive.zip"


def _pin(resource_id: str, sources: list[str], member: str = "xray") -> dict[str, Any]:
    return {
        "id": resource_id,
        "kind": "binary_pin",
        "name": "xray",
        "version": "26.7.28",
        "sources": sources,
        "size": len(ARCHIVE),
        "sha256": SHA,
        "archive": "zip",
        "member": member,
        "install_path": f"/usr/local/lib/netagent/{resource_id}",
        "mode": "0755",
    }


def _spec(sources: list[str]) -> NodeSpec:
    return NodeSpec.model_validate({"resources": [_pin("bin", sources), _pin("geo", sources, member="geoip.dat")]})


class Upstream:
    """A fake https upstream: bodies per URL, recording each request."""

    def __init__(self, bodies: dict[str, bytes | Exception]) -> None:
        self.bodies = bodies
        self.requests: list[str] = []

    def __call__(self, url: str, destination: BinaryIO, limit: int) -> None:
        self.requests.append(url)
        body = self.bodies[url]
        if isinstance(body, Exception):
            raise body
        destination.write(body[: limit + 1])


def _store(root: Path, upstream: Upstream, clock: Callable[[], float] = time.monotonic) -> BlobStore:
    return BlobStore(root, download=upstream, spawn=lambda work: work(), clock=clock)


def test_only_pins_offering_the_service_source_are_mirrored_once_per_hash() -> None:
    assert spec_artifacts(_spec([URL])) == []
    assert spec_artifacts(_spec([URL, "service"])) == [Artifact(SHA, len(ARCHIVE), (URL,))]


def test_a_wanted_artifact_is_verified_and_stored_under_its_hash(tmp_path: Path) -> None:
    upstream = Upstream({URL: ARCHIVE})
    store = _store(tmp_path / "blobs", upstream)
    store.want(spec_artifacts(_spec([URL, "service"])))
    path = store.path(SHA)
    assert path is not None and path.read_bytes() == ARCHIVE
    assert path.stat().st_mode & 0o777 == 0o644
    store.want(spec_artifacts(_spec([URL, "service"])))
    assert upstream.requests == [URL], "a stored artifact is never downloaded again"


@pytest.mark.parametrize(
    "body",
    [ARCHIVE + b"!", ARCHIVE[:-1], b"x" * len(ARCHIVE), OSError("unreachable")],
    ids=["larger", "shorter", "other-hash", "unreachable"],
)
def test_a_bad_first_source_falls_through_to_the_next_and_leaves_nothing_behind(
    tmp_path: Path, body: bytes | Exception
) -> None:
    root = tmp_path / "blobs"
    store = _store(root, Upstream({URL: body, MIRROR: ARCHIVE}))
    store.want([Artifact(SHA, len(ARCHIVE), (URL, MIRROR))])
    assert store.path(SHA) is not None
    assert [entry.name for entry in root.iterdir()] == [SHA]


def test_a_failed_mirror_retries_when_agents_ask_at_most_once_a_minute(tmp_path: Path) -> None:
    now = [100.0]
    upstream = Upstream({URL: OSError("unreachable")})
    store = _store(tmp_path / "blobs", upstream, clock=lambda: now[0])
    store.want([Artifact(SHA, len(ARCHIVE), (URL,))])
    store.missed(SHA)
    store.missed("0" * 64)
    assert upstream.requests == [URL], "within a minute, and an unwanted hash, nothing is fetched"
    now[0] += RETRY_SECONDS
    upstream.bodies[URL] = ARCHIVE
    store.missed(SHA)
    assert upstream.requests == [URL, URL] and store.path(SHA) is not None


# -- HTTP ----------------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, list[float], Upstream]]:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    upstream = Upstream({URL: ARCHIVE})
    now = [100.0]
    blobs = _store(tmp_path / "blobs", upstream, clock=lambda: now[0])
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN), blobs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", now, upstream
    server.shutdown()
    server.server_close()


def _request(url: str, method: str = "GET", body: Any = None, token: str = "") -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _session(http: str) -> str:
    grant = json.loads(_request(f"{http}/api/nodes/main/enrollment", "POST", {}, ADMIN_TOKEN)[1])
    node = FakeNode()
    enrolled = json.loads(
        _request(f"{http}/agent/v1/enroll", "POST", node.enroll_request(grant["enrollment_token"]))[1]
    )
    node.certificate_pem = enrolled["certificate_pem"]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return str(json.loads(_request(f"{http}/agent/v1/hello", "POST", node.hello(stamp))[1])["session_token"])


def test_a_spec_put_mirrors_its_artifacts_and_agents_download_them_with_a_session(
    served: tuple[str, list[float], Upstream],
) -> None:
    http, _now, upstream = served
    assert (
        _request(f"{http}/api/nodes/main", "PUT", _spec([URL, "service"]).model_dump(mode="json"), ADMIN_TOKEN)[0]
        == 200
    )
    assert upstream.requests == [URL]
    assert _request(f"{http}/agent/v1/blobs/{SHA}")[0] == 401
    token = _session(http)
    assert _request(f"{http}/agent/v1/blobs/{SHA}", token=token) == (200, ARCHIVE)
    status, body = _request(f"{http}/agent/v1/blobs/{'0' * 64}", token=token)
    assert status == 404 and json.loads(body)["error"]["code"] == "not_found"


def test_a_missing_wanted_blob_is_fetched_again_after_an_agent_asks(
    served: tuple[str, list[float], Upstream],
) -> None:
    http, now, upstream = served
    upstream.bodies[URL] = OSError("unreachable")
    assert (
        _request(f"{http}/api/nodes/main", "PUT", _spec([URL, "service"]).model_dump(mode="json"), ADMIN_TOKEN)[0]
        == 200
    )
    token = _session(http)
    assert _request(f"{http}/agent/v1/blobs/{SHA}", token=token)[0] == 404
    upstream.bodies[URL] = ARCHIVE
    now[0] += RETRY_SECONDS
    # This miss starts the mirror (here synchronously); the agent's next pass gets the file.
    assert _request(f"{http}/agent/v1/blobs/{SHA}", token=token)[0] == 404
    assert _request(f"{http}/agent/v1/blobs/{SHA}", token=token) == (200, ARCHIVE)
    assert upstream.requests == [URL, URL]


def test_the_default_worker_mirrors_in_a_background_thread(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs", download=Upstream({URL: ARCHIVE}))
    store.want([Artifact(SHA, len(ARCHIVE), (URL,))])
    deadline = time.monotonic() + 5
    while store.path(SHA) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert store.path(SHA) is not None
