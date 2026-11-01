"""Verified passkeys share owner sessions and recovery without keeping device facts."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from meridian.control import passwords
from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings
from meridian.control.owner import ABSOLUTE_SECONDS, CONFIRM_SECONDS, MAX_SESSIONS, OwnerSession
from meridian.control.passkey_models import PasskeyName, RegistrationCompletion
from meridian.control.passkeys import CHALLENGE_SECONDS, MAX_CHALLENGES, MAX_PASSKEYS
from meridian.control.service import ControlService
from tests.support.control_service import FLEET_ID
from tests.support.passkeys import ORIGIN, PASSWORD, RP_ID, Authenticator, Clock, b64


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> ControlService:
    monkeypatch.setattr(passwords, "SCRYPT_PARAMS", (10, 8, 1))
    result = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    result.put_settings(ServiceSettings(profile_title="Meridian", public_url=f"{ORIGIN}/connect"))
    result.owner.complete_setup(result.owner.issue_setup_link(reset=False).token, PASSWORD)
    return result


@pytest.fixture
def session(service: ControlService) -> OwnerSession:
    return service.owner.login(PASSWORD, "")


def register(service: ControlService, session: OwnerSession, authenticator: Authenticator, **options: Any) -> str:
    begin = service.passkeys.begin_registration(session)
    request = RegistrationCompletion(
        challenge_id=begin.challenge_id,
        name="Desk key",
        credential=authenticator.registration(begin.public_key.to_data(), **options),
    )
    return service.passkeys.finish_registration(session, request).id


@pytest.mark.parametrize("algorithm", [-7, -8], ids=["ES256", "Ed25519"])
def test_registration_and_sign_in_use_real_signatures_and_existing_sessions(
    service: ControlService, session: OwnerSession, clock: Clock, algorithm: int
) -> None:
    authenticator = Authenticator(algorithm)
    register(service, session, authenticator)
    options = service.passkeys.begin_authentication()
    assert options.public_key.rp_id == RP_ID
    clock.now += 1
    signed_in = service.passkeys.finish_authentication(
        options.challenge_id, authenticator.assertion(options.public_key.to_data())
    )
    assert service.owner.authenticate(signed_in.token).expires_at == int(clock.now) + ABSOLUTE_SECONDS
    record = service.passkeys.list_passkeys().passkeys[0]
    assert record.name == "Desk key" and record.last_used_at == int(clock.now)


@pytest.mark.parametrize("phase", ["registration", "sign-in"])
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"origin": "https://other.example.test"}, "passkey_origin"),
        ({"rp_id": "other.example.test"}, "passkey_rp"),
        ({"challenge": b64(b"x" * 32)}, "passkey_invalid"),
        ({"crossOrigin": True}, "passkey_origin"),
        ({"flags": 0x41}, "passkey_verification"),
        ({"flags": 0x44}, "passkey_verification"),
    ],
)
def test_wrong_context_and_missing_user_verification_never_register_or_sign_in(
    service: ControlService, session: OwnerSession, phase: str, change: dict, code: str
) -> None:
    authenticator = Authenticator()
    if phase == "registration":
        with pytest.raises(ServiceError) as refused:
            register(service, session, authenticator, **change)
        assert service.passkeys.list_passkeys().passkeys == []
    else:
        register(service, session, authenticator)
        begin = service.passkeys.begin_authentication()
        # Assertions never carry the registration-only AT flag.
        change = {**change, "flags": change["flags"] & ~0x40} if "flags" in change else change
        with pytest.raises(ServiceError) as refused:
            service.passkeys.finish_authentication(
                begin.challenge_id, authenticator.assertion(begin.public_key.to_data(), **change)
            )
        assert service.passkeys.list_passkeys().passkeys[0].last_used_at is None
    assert refused.value.code == code


@pytest.mark.parametrize(
    ("previous", "next_count", "accepted"), [(5, 4, False), (5, 5, False), (5, 0, False), (0, 0, True), (0, 1, True)]
)
def test_counter_regressions_fail_but_authenticators_without_counters_work(
    service: ControlService, session: OwnerSession, previous: int, next_count: int, accepted: bool
) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator, count=previous)
    begin = service.passkeys.begin_authentication()
    credential = authenticator.assertion(begin.public_key.to_data(), count=next_count)
    if accepted:
        assert service.passkeys.finish_authentication(begin.challenge_id, credential).token
    else:
        with pytest.raises(ServiceError) as refused:
            service.passkeys.finish_authentication(begin.challenge_id, credential)
        assert refused.value.code == "passkey_counter"


def test_a_challenge_is_spent_even_when_its_signature_is_wrong(service: ControlService, session: OwnerSession) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    begin = service.passkeys.begin_authentication()
    credential = authenticator.assertion(begin.public_key.to_data())
    wrong = credential.model_copy(
        update={"response": credential.response.model_copy(update={"signature": b64(b"x" * 64)})}
    )
    with pytest.raises(ServiceError) as invalid:
        service.passkeys.finish_authentication(begin.challenge_id, wrong)
    assert invalid.value.code == "passkey_invalid"
    with pytest.raises(ServiceError) as replay:
        service.passkeys.finish_authentication(begin.challenge_id, credential)
    assert replay.value.code == "passkey_expired"


def test_a_concurrent_replay_opens_only_one_session(service: ControlService, session: OwnerSession) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    begin = service.passkeys.begin_authentication()
    credential = authenticator.assertion(begin.public_key.to_data())

    def sign_in() -> str:
        try:
            return service.passkeys.finish_authentication(begin.challenge_id, credential).token
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: sign_in(), range(2)))
    assert results.count("passkey_expired") == 1
    assert len(service.owner.list_sessions(session).sessions) == 3  # setup, password, and one passkey session


def test_removing_a_passkey_refuses_its_pending_assertion_and_ends_other_sessions(
    service: ControlService, session: OwnerSession
) -> None:
    authenticator = Authenticator()
    credential_id = register(service, session, authenticator)
    begin = service.passkeys.begin_authentication()
    service.passkeys.remove(session, credential_id)
    assert service.passkeys.list_passkeys().passkeys == []
    assert len(service.owner.list_sessions(session).sessions) == 1
    with pytest.raises(ServiceError) as refused:
        service.passkeys.finish_authentication(begin.challenge_id, authenticator.assertion(begin.public_key.to_data()))
    assert refused.value.code == "passkey_invalid"
    assert service.owner.login(PASSWORD, "").token


def test_passkey_step_up_confirms_the_same_session(
    service: ControlService, session: OwnerSession, clock: Clock
) -> None:
    authenticator = Authenticator()
    credential_id = register(service, session, authenticator)
    clock.now += CONFIRM_SECONDS + 1
    with pytest.raises(ServiceError) as stale:
        service.passkeys.rename(session, credential_id, "Travel key")
    assert stale.value.code == "reauth_required"
    begin = service.passkeys.begin_authentication(session=session)
    result = service.passkeys.finish_authentication(
        begin.challenge_id, authenticator.assertion(begin.public_key.to_data()), session=session
    )
    assert result.session_id == session.session_id and not result.token
    service.passkeys.rename(service.owner.authenticate(session.token), credential_id, "Travel key")
    assert service.passkeys.list_passkeys().passkeys[0].name == "Travel key"


@pytest.mark.parametrize("operation", ["add", "rename", "remove"])
def test_passkey_management_always_requires_recent_identity(
    service: ControlService, session: OwnerSession, clock: Clock, operation: str
) -> None:
    credential_id = register(service, session, Authenticator())
    clock.now += CONFIRM_SECONDS + 1
    action = {
        "add": lambda: service.passkeys.begin_registration(session),
        "rename": lambda: service.passkeys.rename(session, credential_id, "Travel key"),
        "remove": lambda: service.passkeys.remove(session, credential_id),
    }[operation]
    with pytest.raises(ServiceError) as refused:
        action()
    assert refused.value.code == "reauth_required"


def test_registration_keeps_its_proof_when_step_up_lapses_during_the_prompt(
    service: ControlService, session: OwnerSession, clock: Clock
) -> None:
    clock.now += CONFIRM_SECONDS - 1
    begin = service.passkeys.begin_registration(session)
    request = RegistrationCompletion(
        challenge_id=begin.challenge_id,
        name="Travel key",
        credential=Authenticator().registration(begin.public_key.to_data()),
    )
    clock.now += 2
    with pytest.raises(ServiceError) as stale:
        service.passkeys.finish_registration(session, request)
    assert stale.value.code == "reauth_required"
    assert service.passkeys.list_passkeys().passkeys == []
    service.owner.confirm(session, PASSWORD, "")
    assert service.passkeys.finish_registration(session, request).name == "Travel key"
    with pytest.raises(ServiceError) as replay:
        service.passkeys.finish_registration(session, request)
    assert replay.value.code == "passkey_expired"


def test_registration_keeps_its_proof_until_a_full_key_list_has_room(
    service: ControlService, session: OwnerSession
) -> None:
    for _ in range(MAX_PASSKEYS - 1):
        register(service, session, Authenticator())
    begin = service.passkeys.begin_registration(session)
    request = RegistrationCompletion(
        challenge_id=begin.challenge_id,
        name="Travel key",
        credential=Authenticator().registration(begin.public_key.to_data()),
    )
    other = service.owner.login(PASSWORD, "")
    last_key = register(service, other, Authenticator())
    with pytest.raises(ServiceError) as full:
        service.passkeys.finish_registration(session, request)
    assert full.value.code == "passkey_limit"
    service.passkeys.remove(session, last_key)
    assert service.passkeys.finish_registration(session, request).name == "Travel key"


def test_an_ended_session_cannot_finish_passkey_confirmation(service: ControlService, session: OwnerSession) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    begin = service.passkeys.begin_authentication(session=session)
    service.owner.logout(session)
    for _ in range(2):
        with pytest.raises(ServiceError) as refused:
            service.passkeys.finish_authentication(
                begin.challenge_id, authenticator.assertion(begin.public_key.to_data()), session=session
            )
        assert refused.value.code == "unauthenticated"
    assert service.passkeys.list_passkeys().passkeys[0].last_used_at is None


def test_root_recovery_clears_passkeys_and_invalidates_pending_proofs(
    service: ControlService, session: OwnerSession
) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    begin = service.passkeys.begin_authentication()
    service.owner.complete_setup(service.owner.issue_setup_link(reset=True).token, "a new recovery password")
    with pytest.raises(ServiceError):
        service.passkeys.finish_authentication(begin.challenge_id, authenticator.assertion(begin.public_key.to_data()))
    assert service.passkeys.list_passkeys().passkeys == []
    assert service.owner.login("a new recovery password", "").token


def test_registration_is_bound_to_its_session(service: ControlService, session: OwnerSession) -> None:
    begin = service.passkeys.begin_registration(session)
    request = RegistrationCompletion(
        challenge_id=begin.challenge_id,
        name="Desk key",
        credential=Authenticator().registration(begin.public_key.to_data()),
    )
    other = service.owner.login(PASSWORD, "")
    with pytest.raises(ServiceError) as refused:
        service.passkeys.finish_registration(other, request)
    assert refused.value.code == "passkey_expired"
    assert service.passkeys.list_passkeys().passkeys == []


def test_anonymous_challenges_evict_only_the_oldest_login_and_expire(
    service: ControlService, session: OwnerSession, clock: Clock
) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    requests = [service.passkeys.begin_authentication() for _ in range(MAX_CHALLENGES + 1)]
    with pytest.raises(ServiceError) as evicted:
        service.passkeys.finish_authentication(
            requests[0].challenge_id, authenticator.assertion(requests[0].public_key.to_data())
        )
    assert evicted.value.code == "passkey_expired"
    service.passkeys.finish_authentication(
        requests[1].challenge_id, authenticator.assertion(requests[1].public_key.to_data())
    )
    expired = service.passkeys.begin_authentication()
    clock.now += CHALLENGE_SECONDS
    with pytest.raises(ServiceError) as refused:
        service.passkeys.finish_authentication(
            expired.challenge_id, authenticator.assertion(expired.public_key.to_data(), count=7)
        )
    assert refused.value.code == "passkey_expired"
    assert service.passkeys.begin_authentication().challenge_id


def test_anonymous_login_flood_never_blocks_or_evicts_owner_proofs(
    service: ControlService, session: OwnerSession
) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    registration = service.passkeys.begin_registration(session)
    confirmation = service.passkeys.begin_authentication(session=session)
    other = service.owner.login(PASSWORD, "")
    for _ in range(MAX_CHALLENGES * 2):
        login = service.passkeys.begin_authentication()
    assert service.passkeys.begin_registration(other).challenge_id
    assert service.passkeys.begin_authentication(session=other).challenge_id
    assert len(service.passkeys._challenges) <= MAX_CHALLENGES + MAX_SESSIONS * 2
    service.passkeys.finish_registration(
        session,
        RegistrationCompletion(
            challenge_id=registration.challenge_id,
            name="Travel key",
            credential=Authenticator().registration(registration.public_key.to_data()),
        ),
    )
    assert (
        service.passkeys.finish_authentication(
            confirmation.challenge_id, authenticator.assertion(confirmation.public_key.to_data()), session=session
        ).session_id
        == session.session_id
    )
    assert service.passkeys.finish_authentication(
        login.challenge_id, authenticator.assertion(login.public_key.to_data(), count=7)
    ).token


def test_owner_challenges_are_bounded_per_live_session_and_kind(service: ControlService, session: OwnerSession) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    for _ in range(MAX_CHALLENGES + 1):
        current = service.owner.login(PASSWORD, "")
        replaced = service.passkeys.begin_registration(current)
        confirmation = service.passkeys.begin_authentication(session=current)
        registration = service.passkeys.begin_registration(current)
    assert len(service.passkeys._challenges) <= MAX_SESSIONS * 2
    with pytest.raises(ServiceError) as old:
        service.passkeys.finish_registration(
            current,
            RegistrationCompletion(
                challenge_id=replaced.challenge_id,
                name="Replaced request",
                credential=Authenticator().registration(replaced.public_key.to_data()),
            ),
        )
    assert old.value.code == "passkey_expired"
    service.passkeys.finish_registration(
        current,
        RegistrationCompletion(
            challenge_id=registration.challenge_id,
            name="Current request",
            credential=Authenticator().registration(registration.public_key.to_data()),
        ),
    )
    service.passkeys.finish_authentication(
        confirmation.challenge_id, authenticator.assertion(confirmation.public_key.to_data()), session=current
    )


def test_passkeys_are_bounded_and_duplicate_registration_is_refused(
    service: ControlService, session: OwnerSession
) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    begin = service.passkeys.begin_registration(session)
    assert begin.public_key.exclude_credentials[0].id == authenticator.id
    with pytest.raises(ServiceError) as duplicate:
        service.passkeys.finish_registration(
            session,
            RegistrationCompletion(
                challenge_id=begin.challenge_id,
                name="Desk key",
                credential=authenticator.registration(begin.public_key.to_data()),
            ),
        )
    assert duplicate.value.code == "passkey_exists"
    for _ in range(MAX_PASSKEYS - 1):
        register(service, session, Authenticator())
    with pytest.raises(ServiceError) as full:
        service.passkeys.begin_registration(session)
    assert full.value.code == "passkey_limit"


@pytest.mark.parametrize("url", ["", "https://198.51.100.10/connect", "https://[2001:db8::10]/connect"])
def test_bare_ip_fleets_keep_password_sign_in(service: ControlService, session: OwnerSession, url: str) -> None:
    service.put_settings(ServiceSettings(profile_title="Meridian", public_url=url))
    assert service.passkeys.state().reason == "no_domain"
    with pytest.raises(ServiceError) as refused:
        service.passkeys.begin_registration(session)
    assert refused.value.code == "passkey_no_domain"
    assert service.owner.login(PASSWORD, "").token


def test_a_domain_change_invalidates_a_pending_registration(service: ControlService, session: OwnerSession) -> None:
    begin = service.passkeys.begin_registration(session)
    credential = Authenticator().registration(begin.public_key.to_data())
    service.put_settings(ServiceSettings(profile_title="Meridian", public_url="https://other.example.test/connect"))
    with pytest.raises(ServiceError):
        service.passkeys.finish_registration(
            session, RegistrationCompletion(challenge_id=begin.challenge_id, name="Desk key", credential=credential)
        )
    assert service.passkeys.list_passkeys().passkeys == []


def test_stored_passkeys_keep_only_public_keys_and_owner_chosen_metadata(
    service: ControlService, session: OwnerSession, tmp_path: Path, clock: Clock
) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    with sqlite3.connect(tmp_path / "control.db") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(ui_passkeys)")}
        assert not columns & {"ip", "address", "user_agent", "aaguid", "transports", "device_type", "provider"}
    restarted = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    begin = restarted.passkeys.begin_authentication()
    assert restarted.passkeys.finish_authentication(
        begin.challenge_id, authenticator.assertion(begin.public_key.to_data())
    ).token


@pytest.mark.parametrize("name", ["", "   ", "key\nname", "x" * 65])
def test_friendly_names_are_bounded_plain_text(name: str) -> None:
    with pytest.raises(ValidationError):
        PasskeyName(name=name)


def test_wrong_owner_handle_is_refused(service: ControlService, session: OwnerSession) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    begin = service.passkeys.begin_authentication()
    authenticator.user_handle = b64(b"x" * 32)
    with pytest.raises(ServiceError) as refused:
        service.passkeys.finish_authentication(begin.challenge_id, authenticator.assertion(begin.public_key.to_data()))
    assert refused.value.code == "passkey_invalid"


def test_a_nonresident_key_can_return_a_null_owner_handle(service: ControlService, session: OwnerSession) -> None:
    authenticator = Authenticator()
    register(service, session, authenticator)
    begin = service.passkeys.begin_authentication()
    authenticator.user_handle = None
    assert service.passkeys.finish_authentication(
        begin.challenge_id, authenticator.assertion(begin.public_key.to_data())
    ).token
