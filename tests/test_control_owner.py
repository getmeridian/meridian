"""The web owner's credentials: scrypt passwords, RFC 6238 TOTP, guess limits, setup links, and sessions."""

from __future__ import annotations

import sqlite3
import threading
import time as real_time
from pathlib import Path

import pytest

from meridian.control import owner as owner_module
from meridian.control import passwords
from meridian.control.errors import ServiceError
from meridian.control.login_limits import FREE_FAILURES, LockedOut, LoginLimiter
from meridian.control.owner import (
    ABSOLUTE_SECONDS,
    CONFIRM_SECONDS,
    IDLE_SECONDS,
    MAX_SESSIONS,
    SETUP_LINK_SECONDS,
    OwnerAuth,
)
from meridian.control.passwords import (
    hash_password,
    otpauth_uri,
    totp_code,
    totp_match,
    totp_step,
    verify_password,
)
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.store import Store

PRODUCTION_SCRYPT = passwords.SCRYPT_PARAMS
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # b"12345678901234567890", RFC 6238 appendix B
PASSWORD = "correct horse battery"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """New hashes use a fast cost here; verification reads the cost from each stored hash either way."""
    monkeypatch.setattr(passwords, "SCRYPT_PARAMS", (10, 8, 1))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def owner(tmp_path: Path, clock: Clock) -> OwnerAuth:
    return OwnerAuth(Store(tmp_path / "control.db"), clock=clock, limiter=LoginLimiter(clock=clock))


def set_up(owner: OwnerAuth, password: str = PASSWORD) -> str:
    """Create the owner through a setup link; returns the session token."""
    grant = owner.issue_setup_link(reset=False)
    return owner.complete_setup(grant.token, password).token


def enable_totp(owner: OwnerAuth, clock: Clock, token: str) -> str:
    session = owner.authenticate(token)
    secret, _ = owner.begin_totp(session, account="Meridian")
    owner.enable_totp(session, totp_code(secret, totp_step(clock.now)))
    clock.now += 30  # the next code is a new step
    return secret


# -- passwords and TOTP ------------------------------------------------------------------------


def test_shipped_scrypt_cost_meets_owasp_at_32_mib() -> None:
    log_n, r, p = PRODUCTION_SCRYPT
    assert 128 * r * 2**log_n == 32 * 1024 * 1024  # memory per hash, under the service's 256 MB cap
    assert 2**log_n * p >= 2**15 * 3  # OWASP's equivalent of N=2^17, p=1 at this memory


def test_password_hash_is_salted_scrypt_and_verifies_in_constant_time() -> None:
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first.startswith("scrypt$10$8$1$") and first != second
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong horse battery", first)


@pytest.mark.parametrize("stored", ["", "plain", "bcrypt$1$2$3$a$b", "scrypt$40$8$1$AA$AA"], ids=str)
def test_malformed_password_hash_never_matches(stored: str) -> None:
    assert not verify_password(PASSWORD, stored)


def test_password_hash_matches_composed_and_decomposed_forms() -> None:
    assert verify_password("café au lait!", hash_password("café au lait!"))


@pytest.mark.parametrize(
    ("time", "code"),
    [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037")],
    ids=str,
)
def test_totp_matches_rfc_6238_vectors(time: int, code: str) -> None:
    assert totp_code(RFC_SECRET, totp_step(time)) == code


def test_totp_accepts_one_step_either_side_and_never_reuses_a_step() -> None:
    now = 1_800_000_015.0
    step = totp_step(now)
    for offset in (-1, 0, 1):
        assert totp_match(RFC_SECRET, totp_code(RFC_SECRET, step + offset), now=now, last_step=None) == step + offset
    assert totp_match(RFC_SECRET, totp_code(RFC_SECRET, step + 2), now=now, last_step=None) is None
    assert totp_match(RFC_SECRET, totp_code(RFC_SECRET, step), now=now, last_step=step) is None
    assert totp_match(RFC_SECRET, totp_code(RFC_SECRET, step - 1), now=now, last_step=step) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "١٢٣٤٥٦"], ids=repr)
def test_totp_refuses_malformed_codes(code: str) -> None:
    assert totp_match(RFC_SECRET, code, now=1_800_000_000.0, last_step=None) is None


def test_otpauth_uri_names_issuer_and_parameters() -> None:
    uri = otpauth_uri(RFC_SECRET, account="My fleet", issuer="Meridian")
    assert uri.startswith("otpauth://totp/Meridian%3AMy%20fleet?secret=" + RFC_SECRET)
    assert "issuer=Meridian" in uri and "digits=6" in uri and "period=30" in uri


# -- guess limits -----------------------------------------------------------------------------


def test_limiter_locks_after_free_failures_and_doubles_the_wait(clock: Clock) -> None:
    limiter = LoginLimiter(clock=clock, free_failures=2)
    for _ in range(2):
        limiter.check("owner")
        limiter.failed("owner")
    with pytest.raises(LockedOut) as first:
        limiter.check("owner")
    limiter.check("setup-link")  # buckets lock alone
    clock.now += first.value.retry_after
    limiter.check("owner")
    limiter.failed("owner")
    with pytest.raises(LockedOut) as second:
        limiter.check("owner")
    assert second.value.retry_after == 2 * first.value.retry_after
    assert second.value.status == 429
    limiter.succeeded("owner")
    limiter.check("owner")


def test_guessing_setup_links_never_locks_sign_in(owner: OwnerAuth) -> None:
    """Every browser reaches the service as nginx's loopback; one shared source bucket made the doors one door."""
    set_up(owner)
    for _ in range(FREE_FAILURES * 2):
        with pytest.raises(ServiceError):
            owner.check_setup_link("x" * 43)
    assert owner.login(PASSWORD, "").token


def test_failed_sign_ins_never_lock_a_signed_in_owner(owner: OwnerAuth, clock: Clock) -> None:
    session = owner.authenticate(set_up(owner))
    for _ in range(FREE_FAILURES + 1):
        with pytest.raises(ServiceError):
            owner.login("wrong password!", "")
    owner.confirm(session, PASSWORD, "")
    secret, _ = owner.begin_totp(session, account="Meridian")
    owner.enable_totp(session, totp_code(secret, totp_step(clock.now)))


def test_a_sessions_wrong_passwords_lock_only_that_session(owner: OwnerAuth) -> None:
    mine = owner.authenticate(set_up(owner))
    other = owner.authenticate(owner.login(PASSWORD, "").token)
    for _ in range(FREE_FAILURES):
        with pytest.raises(ServiceError):
            owner.confirm(other, "wrong password!", "")
    with pytest.raises(LockedOut):
        owner.confirm(other, PASSWORD, "")
    owner.confirm(mine, PASSWORD, "")
    assert owner.login(PASSWORD, "").token


# -- setup links ------------------------------------------------------------------------------


def test_setup_link_works_once(owner: OwnerAuth) -> None:
    grant = owner.issue_setup_link(reset=False)
    assert grant.purpose == "setup"
    assert owner.check_setup_link(grant.token) == "setup"
    owner.complete_setup(grant.token, PASSWORD)
    with pytest.raises(ServiceError) as refused:
        owner.complete_setup(grant.token, "another password!")
    assert refused.value.code == "link_invalid"
    assert owner.status().exists


def test_setup_link_expires_after_fifteen_minutes(owner: OwnerAuth, clock: Clock) -> None:
    grant = owner.issue_setup_link(reset=False)
    clock.now += SETUP_LINK_SECONDS
    with pytest.raises(ServiceError, match="expired"):
        owner.complete_setup(grant.token, PASSWORD)


def test_a_new_setup_link_voids_the_last(owner: OwnerAuth) -> None:
    first = owner.issue_setup_link(reset=False)
    owner.issue_setup_link(reset=False)
    with pytest.raises(ServiceError, match="expired"):
        owner.check_setup_link(first.token)


def test_setup_links_are_refused_once_an_owner_exists_except_reset(owner: OwnerAuth, clock: Clock) -> None:
    pending = owner.issue_setup_link(reset=False)
    set_up(owner)  # another link wins the race
    with pytest.raises(ServiceError):
        owner.complete_setup(pending.token, PASSWORD)
    with pytest.raises(ServiceError) as exists:
        owner.issue_setup_link(reset=False)
    assert exists.value.code == "owner_exists" and exists.value.status == 409
    assert owner.issue_setup_link(reset=True).purpose == "reset"


def test_reset_link_replaces_the_password_turns_totp_off_and_ends_sessions(owner: OwnerAuth, clock: Clock) -> None:
    token = set_up(owner)
    enable_totp(owner, clock, token)
    grant = owner.issue_setup_link(reset=True)
    fresh = owner.complete_setup(grant.token, "a brand new passphrase")
    with pytest.raises(ServiceError):
        owner.authenticate(token)
    assert owner.authenticate(fresh.token)
    assert not owner.status().totp_enabled
    owner.login("a brand new passphrase", "")
    with pytest.raises(ServiceError):
        owner.login(PASSWORD, "")


def test_weak_password_is_refused_without_spending_the_link(owner: OwnerAuth) -> None:
    grant = owner.issue_setup_link(reset=False)
    with pytest.raises(ServiceError) as weak:
        owner.complete_setup(grant.token, "short")
    assert weak.value.code == "weak_password"
    owner.complete_setup(grant.token, PASSWORD)


def test_guessing_setup_links_locks_out(owner: OwnerAuth) -> None:
    for _ in range(FREE_FAILURES):
        with pytest.raises(ServiceError) as refused:
            owner.check_setup_link("x" * 43)
        assert refused.value.code == "link_invalid"  # LockedOut is a ServiceError too
    with pytest.raises(LockedOut):
        owner.check_setup_link("x" * 43)


# -- sign-in and TOTP -------------------------------------------------------------------------


def test_login_refuses_before_setup(owner: OwnerAuth) -> None:
    with pytest.raises(ServiceError) as refused:
        owner.login(PASSWORD, "")
    assert refused.value.code == "setup_required"


def test_wrong_password_and_wrong_code_read_the_same(owner: OwnerAuth, clock: Clock) -> None:
    secret = enable_totp(owner, clock, set_up(owner))
    good_code = totp_code(secret, totp_step(clock.now))
    errors = []
    for password, code in (("wrong password!", good_code), (PASSWORD, "000000"), (PASSWORD, "")):
        with pytest.raises(ServiceError) as refused:
            owner.login(password, code)
        errors.append((refused.value.code, str(refused.value), refused.value.status))
    assert len(set(errors)) == 1 and errors[0][2] == 401
    assert owner.login(PASSWORD, good_code).token


def test_a_totp_code_signs_in_only_once(owner: OwnerAuth, clock: Clock) -> None:
    secret = enable_totp(owner, clock, set_up(owner))
    code = totp_code(secret, totp_step(clock.now))
    owner.login(PASSWORD, code)
    with pytest.raises(ServiceError):
        owner.login(PASSWORD, code)


def test_login_locks_the_account_after_five_failures(owner: OwnerAuth) -> None:
    set_up(owner)
    for _ in range(5):
        with pytest.raises(ServiceError):
            owner.login("wrong password!", "")
    with pytest.raises(LockedOut):
        owner.login(PASSWORD, "")  # even the right password waits


@pytest.mark.parametrize("recover", ["reset link", "password change"])
def test_a_new_password_lifts_the_sign_in_lockout(owner: OwnerAuth, recover: str) -> None:
    """The owner who forgot their password (or an attacker's guesses) must not lock out the new one."""
    session = owner.authenticate(set_up(owner))
    for _ in range(FREE_FAILURES):
        with pytest.raises(ServiceError):
            owner.login("wrong password!", "")
    if recover == "reset link":
        owner.complete_setup(owner.issue_setup_link(reset=True).token, "a brand new password")
    else:
        owner.change_password(session, "a brand new password")
    assert owner.login("a brand new password", "").token


def _at_once(count: int, guess) -> list[BaseException | None]:  # type: ignore[no-untyped-def]
    """Run `guess` on `count` threads released together; each thread's exception or None."""
    start = threading.Barrier(count)
    outcomes: list[BaseException | None] = [None] * count

    def run(index: int) -> None:
        start.wait()
        try:
            guess()
        except BaseException as exc:  # noqa: BLE001 - the outcome is the assertion
            outcomes[index] = exc

    threads = [threading.Thread(target=run, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


def test_concurrent_guesses_never_hash_past_the_lock(owner: OwnerAuth, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checking the lock before a slow hash let every request already waiting pass it at once."""
    set_up(owner)
    hashed: list[str] = []

    def slow_verify(password: str, stored: str) -> bool:
        hashed.append(password)
        real_time.sleep(0.02)
        return verify_password(password, stored)

    monkeypatch.setattr(owner_module, "verify_password", slow_verify)
    outcomes = _at_once(20, lambda: owner.login("wrong password!", ""))
    assert len(hashed) == FREE_FAILURES
    assert sum(isinstance(item, LockedOut) for item in outcomes) == 20 - FREE_FAILURES


def test_totp_turns_on_only_with_a_matching_code_and_ends_other_sessions(owner: OwnerAuth, clock: Clock) -> None:
    token = set_up(owner)
    other = owner.login(PASSWORD, "").token
    session = owner.authenticate(token)
    secret, uri = owner.begin_totp(session, account="Meridian")
    assert secret in uri and not owner.status().totp_enabled
    with pytest.raises(ServiceError) as wrong:
        owner.enable_totp(session, "000000")
    assert wrong.value.code == "wrong_code"
    owner.enable_totp(session, totp_code(secret, totp_step(clock.now)))
    assert owner.status().totp_enabled
    with pytest.raises(ServiceError):
        owner.authenticate(other)


def test_sensitive_changes_need_a_recent_confirmation(owner: OwnerAuth, clock: Clock) -> None:
    session = owner.authenticate(set_up(owner))
    clock.now += CONFIRM_SECONDS + 1
    session = owner.authenticate(session_token := owner.login(PASSWORD, "").token)
    clock.now += CONFIRM_SECONDS + 1
    session = owner.authenticate(session_token)
    for change in (
        lambda: owner.begin_totp(session, account="Meridian"),
        lambda: owner.disable_totp(session),
        lambda: owner.change_password(session, "another passphrase"),
    ):
        with pytest.raises(ServiceError) as refused:
            change()
        assert refused.value.code == "reauth_required"
    owner.confirm(session, PASSWORD, "")
    owner.change_password(owner.authenticate(session_token), "another passphrase")


def test_password_change_ends_every_other_session(owner: OwnerAuth) -> None:
    first = set_up(owner)
    second = owner.login(PASSWORD, "").token
    owner.change_password(owner.authenticate(first), "another passphrase")
    owner.authenticate(first)
    with pytest.raises(ServiceError):
        owner.authenticate(second)


def test_a_reset_while_the_old_password_hashes_opens_no_session(
    owner: OwnerAuth, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hash runs outside the store lock; a session written later trusted a password already replaced."""
    set_up(owner)
    reset = owner.issue_setup_link(reset=True)

    def verify_then_reset(password: str, stored: str) -> bool:
        matched = verify_password(password, stored)
        owner.complete_setup(reset.token, "a brand new passphrase")
        return matched

    monkeypatch.setattr(owner_module, "verify_password", verify_then_reset)
    with pytest.raises(ServiceError) as refused:
        owner.login(PASSWORD, "")
    assert refused.value.code == "wrong_credentials"
    with sqlite3.connect(tmp_path / "control.db") as db:
        assert db.execute("SELECT COUNT(*) FROM ui_sessions").fetchone() == (1,)  # only the reset's own


def test_a_session_ended_while_confirming_stays_ended(owner: OwnerAuth, monkeypatch: pytest.MonkeyPatch) -> None:
    session = owner.authenticate(set_up(owner))

    def verify_then_sign_out(password: str, stored: str) -> bool:
        owner.logout(session)
        return verify_password(password, stored)

    monkeypatch.setattr(owner_module, "verify_password", verify_then_sign_out)
    with pytest.raises(ServiceError) as refused:
        owner.confirm(session, PASSWORD, "")
    assert refused.value.code == "unauthenticated"


def test_a_session_that_expired_while_confirming_stays_ended(
    owner: OwnerAuth, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expired rows linger until `authenticate` meets them, so the confirmation must check expiry itself."""
    session = owner.authenticate(set_up(owner))

    def slow_verify(password: str, stored: str) -> bool:
        clock.now += IDLE_SECONDS + 1
        return verify_password(password, stored)

    monkeypatch.setattr(owner_module, "verify_password", slow_verify)
    with pytest.raises(ServiceError) as refused:
        owner.confirm(session, PASSWORD, "")
    assert refused.value.code == "unauthenticated"


def test_an_expired_session_holds_no_recent_confirmation(owner: OwnerAuth, clock: Clock) -> None:
    session = owner.authenticate(set_up(owner))
    clock.now += ABSOLUTE_SECONDS
    with pytest.raises(ServiceError) as refused:
        owner.hold_recent(session)
    assert refused.value.code == "unauthenticated"


def test_a_session_ended_while_its_new_password_hashes_changes_nothing(
    owner: OwnerAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = owner.authenticate(set_up(owner))
    other = owner.login(PASSWORD, "").token

    def hash_then_revoke(password: str) -> str:
        owner.revoke_others(owner.authenticate(other))
        return hash_password(password)

    monkeypatch.setattr(owner_module, "hash_password", hash_then_revoke)
    with pytest.raises(ServiceError) as refused:
        owner.change_password(session, "another passphrase")
    assert refused.value.code == "unauthenticated"
    owner.authenticate(other)  # the revoked session did not sign out the live one
    assert owner.login(PASSWORD, "").token


# -- sessions ---------------------------------------------------------------------------------


def test_session_ends_after_idle_and_absolute_limits(owner: OwnerAuth, clock: Clock) -> None:
    token = set_up(owner)
    clock.now += IDLE_SECONDS
    with pytest.raises(ServiceError):
        owner.authenticate(token)
    token = owner.login(PASSWORD, "").token
    for _ in range(ABSOLUTE_SECONDS // (IDLE_SECONDS // 2)):
        clock.now += IDLE_SECONDS // 2
        try:
            owner.authenticate(token)
        except ServiceError:
            break
    else:
        pytest.fail("an active session outlived the absolute limit")
    assert clock.now - 1_800_000_000.0 >= ABSOLUTE_SECONDS


def test_a_session_revoked_after_authorizing_cannot_sign_out_the_one_that_revoked_it(owner: OwnerAuth) -> None:
    stale = owner.authenticate(set_up(owner))
    survivor = owner.login(PASSWORD, "").token
    owner.revoke_others(owner.authenticate(survivor))

    with pytest.raises(ServiceError) as error:
        owner.revoke_others(stale)

    assert error.value.status == 401
    owner.authenticate(survivor)


def test_sessions_store_only_token_hashes_and_no_address(owner: OwnerAuth, tmp_path: Path) -> None:
    token = set_up(owner)
    with sqlite3.connect(tmp_path / "control.db") as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(ui_sessions)")]
        dump = "\n".join(db.iterdump())
    assert token not in dump and "127.0.0.1" not in dump
    assert not {"ip", "address", "user_agent", "source"} & set(columns)


def test_sign_out_others_keeps_only_this_session(owner: OwnerAuth) -> None:
    mine = set_up(owner)
    others = [owner.login(PASSWORD, "").token for _ in range(2)]
    session = owner.authenticate(mine)
    assert len(owner.list_sessions(session).sessions) == 3
    assert owner.revoke_others(session) == 2
    listed = owner.list_sessions(session).sessions
    assert [item.current for item in listed] == [True]
    for token in others:
        with pytest.raises(ServiceError):
            owner.authenticate(token)


def test_session_count_is_capped(owner: OwnerAuth) -> None:
    tokens = [set_up(owner)] + [owner.login(PASSWORD, "").token for _ in range(MAX_SESSIONS)]
    with pytest.raises(ServiceError):
        owner.authenticate(tokens[0])
    owner.authenticate(tokens[-1])


def test_logout_ends_the_session(owner: OwnerAuth) -> None:
    token = set_up(owner)
    owner.logout(owner.authenticate(token))
    with pytest.raises(ServiceError):
        owner.authenticate(token)


# -- schema -----------------------------------------------------------------------------------


def test_schema_step_eight_adds_the_ui_tables_to_an_older_database(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    fleet_id = "0f1e2d3c-4b5a-4968-8776-655443322110"
    ControlService(database, fleet_id=fleet_id, version="4.0.0")
    db = sqlite3.connect(database)
    for table in ("ui_owner", "ui_sessions", "ui_setup_links"):
        db.execute(f"DROP TABLE {table}")
    db.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
    db.commit()
    db.close()
    service = ControlService(database, fleet_id=fleet_id, version="4.0.0")
    assert not service.owner.status().exists
    db = sqlite3.connect(database)
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    version = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    db.close()
    assert {"ui_owner", "ui_sessions", "ui_setup_links"} <= tables
    assert version == str(SCHEMA_VERSION)
