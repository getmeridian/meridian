"""Control service: the fleet CA, one-time enrollment, NodeProof hellos, sessions, and revocation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from meridian.control.errors import ServiceError
from meridian.control.pki import agent_uri, service_uri
from meridian.control.service import ControlService
from meridian.core.agent_protocol import EnrollRequest, EnrollResponse, HelloResponse, RenewRequest
from meridian.core.agent_signing import DESIRED_STATE_CONTEXT, sign_proof, verify
from meridian.core.version_skew import UNSUPPORTED_PROTOCOL_RETRY_SECONDS
from tests.support.agent_node import FLEET_ID, FakeNode, csr_pem


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def stamp(self, offset: float = 0) -> str:
        return datetime.fromtimestamp(self.now + offset, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> ControlService:
    return ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)


def _enroll(service: ControlService, node: FakeNode) -> EnrollResponse:
    grant = service.grant_enrollment(node.node_id)
    response = service.authority.enroll(EnrollRequest.model_validate(node.enroll_request(grant.enrollment_token)))
    node.certificate_pem = response.certificate_pem
    return response


def _hello(service: ControlService, node: FakeNode, clock: Clock, **kwargs: Any) -> HelloResponse:
    return service.authority.hello(node.hello(clock.stamp(), **kwargs))[1]


def _refused(code: str, call: Any) -> ServiceError:
    with pytest.raises(ServiceError) as raised:
        call()
    assert raised.value.code == code, str(raised.value)
    return raised.value


def test_the_ca_and_service_certificate_survive_a_restart(tmp_path: Path, service: ControlService) -> None:
    again = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    assert again.authority.ca_sha256 == service.authority.ca_sha256
    assert again.authority.service_key_id == service.authority.service_key_id


def test_documents_are_signed_under_the_service_certificate_hash(service: ControlService) -> None:
    node = FakeNode()
    response = _enroll(service, node)
    ca = x509.load_pem_x509_certificate(response.ca_certificate_pem.encode())
    service_cert = x509.load_pem_x509_certificate(response.service_certificate_pem.encode())
    service_cert.verify_directly_issued_by(ca)
    san = service_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.UniformResourceIdentifier) == [service_uri(FLEET_ID)]
    assert hashlib.sha256(ca.public_bytes(Encoding.DER)).hexdigest() == service.authority.ca_sha256

    key_id = hashlib.sha256(service_cert.public_bytes(Encoding.DER)).hexdigest()
    assert key_id == service.authority.service_key_id
    document = {"revision": 1, "resources": []}
    document["signature"] = service.authority.sign(document, DESIRED_STATE_CONTEXT)
    assert verify(document, DESIRED_STATE_CONTEXT, service.authority.service_public_key(), key_id)


def test_enrollment_issues_a_node_certificate_for_the_csr_key_with_the_contract_san(service: ControlService) -> None:
    node = FakeNode()
    grant = service.grant_enrollment("main")
    assert grant.ca_sha256 == service.authority.ca_sha256
    response = service.authority.enroll(EnrollRequest.model_validate(node.enroll_request(grant.enrollment_token)))

    certificate = x509.load_pem_x509_certificate(response.certificate_pem.encode())
    ca = x509.load_pem_x509_certificate(response.ca_certificate_pem.encode())
    certificate.verify_directly_issued_by(ca)
    assert certificate.public_key() == node.key.public_key()
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.UniformResourceIdentifier) == [agent_uri(FLEET_ID, "main")]
    assert (response.fleet_id, response.node_id, response.fleet_epoch) == (FLEET_ID, "main", 1)


def test_an_enrollment_token_works_once(service: ControlService) -> None:
    node = FakeNode()
    grant = service.grant_enrollment("main")
    request = EnrollRequest.model_validate(node.enroll_request(grant.enrollment_token))
    service.authority.enroll(request)
    _refused("enrollment_token_invalid", lambda: service.authority.enroll(request))


@pytest.mark.parametrize(
    "spoil",
    [
        lambda body, clock: body.update(enrollment_token="x" * 43),
        lambda body, clock: body.update(node_id="other"),
        lambda body, clock: body.update(fleet_id="11111111-2222-4333-8444-555555555555"),
        lambda body, clock: setattr(clock, "now", clock.now + 31 * 60),
    ],
    ids=["wrong-token", "other-node", "other-fleet", "expired"],
)
def test_a_token_that_does_not_match_is_refused(service: ControlService, clock: Clock, spoil: Any) -> None:
    grant = service.grant_enrollment("main")
    body = FakeNode().enroll_request(grant.enrollment_token)
    spoil(body, clock)
    _refused("enrollment_token_invalid", lambda: service.authority.enroll(EnrollRequest.model_validate(body)))


def test_a_newer_grant_replaces_the_unspent_one(service: ControlService) -> None:
    first = service.grant_enrollment("main")
    service.grant_enrollment("main")
    body = FakeNode().enroll_request(first.enrollment_token)
    _refused("enrollment_token_invalid", lambda: service.authority.enroll(EnrollRequest.model_validate(body)))


def test_a_csr_without_an_ed25519_key_is_refused_and_keeps_the_token(service: ControlService) -> None:
    node = FakeNode()
    grant = service.grant_enrollment("main")
    body = node.enroll_request(grant.enrollment_token)
    body["csr_pem"] = csr_pem(generate_private_key(SECP256R1()))
    _refused("invalid_request", lambda: service.authority.enroll(EnrollRequest.model_validate(body)))
    service.authority.enroll(EnrollRequest.model_validate(node.enroll_request(grant.enrollment_token)))


def test_hello_opens_a_session_stored_only_as_a_hash(tmp_path: Path, service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    welcome = _hello(service, node, clock)

    assert welcome.capabilities == ["long_poll", "xray_config", "xray_users", "traffic_stats"]
    assert welcome.poll.stats_interval_seconds == 60
    assert service.authority.authenticate(welcome.session_token).node_id == "main"
    database = sqlite3.connect(tmp_path / "control.db")
    dump = "\n".join(database.iterdump())
    assert welcome.session_token not in dump
    assert hashlib.sha256(welcome.session_token.encode()).hexdigest() in dump


@pytest.mark.parametrize(
    ("offset", "code"),
    [(-301, "clock_skew"), (301, "clock_skew"), (-290, None), (290, None)],
    ids=["too-old", "too-new", "late-ok", "early-ok"],
)
def test_proof_time_must_be_within_300_seconds(
    service: ControlService, clock: Clock, offset: int, code: str | None
) -> None:
    node = FakeNode()
    _enroll(service, node)
    body = node.hello(clock.stamp(offset))
    if code is None:
        service.authority.hello(body)
    else:
        _refused(code, lambda: service.authority.hello(body))


@pytest.mark.parametrize(
    "protocols", [["meridian.agent/v2"], ["meridian.agent/v9", "meridian.agent/v2"]], ids=["newer", "several"]
)
def test_a_hello_in_no_protocol_the_service_speaks_is_refused_with_a_long_retry(
    service: ControlService, clock: Clock, protocols: list[str]
) -> None:
    node = FakeNode()
    _enroll(service, node)
    body = node.hello(clock.stamp())
    # A newer agent's hello need not even parse as this protocol's: the refusal comes first.
    body["protocols"], body["future_field"] = protocols, True

    refused = _refused("unsupported_protocol", lambda: service.authority.hello(body))

    assert (refused.status, refused.retry_after) == (400, UNSUPPORTED_PROTOCOL_RETRY_SECONDS)
    assert "meridian apply" in str(refused)


def test_an_agent_offering_this_protocol_among_others_is_welcomed_in_it(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    body = node.hello(clock.stamp())
    body["protocols"] = ["meridian.agent/v1", "meridian.agent/v2"]
    raw = node.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    del body["proof"]["signature"]
    body["proof"]["signature"] = sign_proof(body, raw)

    assert service.authority.hello(body)[1].protocol == "meridian.agent/v1"


def test_a_replayed_hello_is_refused(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    body = node.hello(clock.stamp(), nonce="n" * 32)
    service.authority.hello(body)
    _refused("unauthenticated", lambda: service.authority.hello(body))
    _refused("unauthenticated", lambda: service.authority.hello(node.hello(clock.stamp(), nonce="n" * 32)))


@pytest.mark.parametrize(
    ("spoil", "code"),
    [
        (lambda body: body.update(agent_version="9.9.9"), "unauthenticated"),
        (lambda body: body.update(node_id="other"), "unauthenticated"),
        (lambda body: body.update(protocols=["meridian.agent/v2"]), "unsupported_protocol"),
        (lambda body: body.update(fleet_id="11111111-2222-4333-8444-555555555555"), "unauthenticated"),
    ],
    ids=["tampered-after-signing", "other-node", "unknown-protocol", "other-fleet"],
)
def test_a_proof_that_does_not_cover_the_body_is_refused(
    service: ControlService, clock: Clock, spoil: Any, code: str
) -> None:
    node = FakeNode()
    _enroll(service, node)
    body = node.hello(clock.stamp())
    spoil(body)
    _refused(code, lambda: service.authority.hello(body))


def test_a_certificate_from_another_ca_is_refused(tmp_path: Path, service: ControlService, clock: Clock) -> None:
    other = ControlService(tmp_path / "other.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    node = FakeNode()
    _enroll(other, node)
    _refused("unauthenticated", lambda: _hello(service, node, clock))


def test_an_agent_on_a_newer_epoch_is_told_the_service_is_superseded(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    _refused("epoch_superseded", lambda: _hello(service, node, clock, highest_fleet_epoch=2))


def test_re_enrollment_revokes_the_old_certificate_and_its_sessions(service: ControlService, clock: Clock) -> None:
    old = FakeNode()
    _enroll(service, old)
    session = _hello(service, old, clock).session_token
    fresh = FakeNode()
    _enroll(service, fresh)

    _refused("unauthenticated", lambda: service.authority.authenticate(session))
    _refused("credential_revoked", lambda: _hello(service, old, clock))
    _hello(service, fresh, clock)


def test_deleting_a_node_revokes_its_credentials(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    service.delete_node("main")
    _refused("credential_revoked", lambda: _hello(service, node, clock))


def test_a_session_expires(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    session = _hello(service, node, clock).session_token
    clock.now += 3601
    _refused("credential_expired", lambda: service.authority.authenticate(session))


def test_an_expired_node_certificate_cannot_open_a_session(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    clock.now += 91 * 86_400
    _refused("credential_expired", lambda: _hello(service, node, clock))


def test_renew_certifies_a_fresh_key_and_the_old_certificate_stays_valid(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    welcome = _hello(service, node, clock)
    session = service.authority.authenticate(welcome.session_token)
    old_pem = node.certificate_pem
    node.rotate_key()
    renewed = service.authority.renew(session, RenewRequest(csr_pem=csr_pem(node.key)))

    node.certificate_pem = renewed.certificate_pem
    _hello(service, node, clock)
    previous = FakeNode()
    previous.certificate_pem = old_pem
    _refused("unauthenticated", lambda: _hello(service, previous, clock))  # another key cannot use it


def test_renew_after_comes_from_the_node_certificate(service: ControlService, clock: Clock) -> None:
    node = FakeNode()
    _enroll(service, node)
    welcome = _hello(service, node, clock)
    renew_after = datetime.strptime(welcome.renew_after, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    assert 59 * 86_400 < renew_after - clock.now <= 60 * 86_400


def test_a_skeleton_document_is_signed_again_under_the_certificate(tmp_path: Path) -> None:
    from tests.test_control_service import _spec

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    service.put_node_spec("main", _spec())
    database = sqlite3.connect(tmp_path / "control.db")
    document = json.loads(database.execute("SELECT document FROM nodes").fetchone()[0])
    document["signature"]["key_id"] = "0" * 64
    database.execute("UPDATE nodes SET document = ?", (json.dumps(document),))
    database.commit()

    restarted = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    assert restarted.node_status("main").revision == 2
