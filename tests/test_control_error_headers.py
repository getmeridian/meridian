"""Service refusals preserve their requested delay at the HTTP boundary."""

from pathlib import Path

import httpx
import pytest

from meridian.control.errors import ServiceError
from meridian.control.passkeys import PasskeyRegistry
from tests.support.control_service import serve_control


@pytest.mark.parametrize(("code", "status", "delay"), [("passkey_busy", 429, 300), ("conflict", 409, 17)])
def test_service_errors_with_retry_after_emit_the_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, status: int, delay: int
) -> None:
    def refused(*args: object, **kwargs: object) -> None:
        raise ServiceError(code, "Try again later.", status, retry_after=delay)

    monkeypatch.setattr(PasskeyRegistry, "begin_authentication", refused)
    with serve_control(tmp_path, public_path="/console") as url:
        reply = httpx.post(url + "/auth/passkeys/login/begin", json={}, headers={"X-Meridian-Request": "1"})
    assert reply.status_code == status
    assert reply.headers.get("Retry-After") == str(delay)
