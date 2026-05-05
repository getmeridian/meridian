"""Tests for reusable Pydantic input request models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.core.command_inputs import (
    ClientNameRequest,
    NodeAddRequest,
    RelayDeployRequest,
    ServerAddRequest,
)
from meridian.core.inputs import is_ip_deploy_target
from meridian.core.validation import validation_error_hint, wrap_validation_error


def test_ip_value_accepts_ipv4_and_ipv6() -> None:
    assert ServerAddRequest(ip="198.51.100.10").ip == "198.51.100.10"
    assert ServerAddRequest(ip="2001:db8::1").ip == "2001:db8::1"
    assert is_ip_deploy_target("2001:db8::1") is True


def test_ip_value_rejects_typos_with_readable_hint() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ServerAddRequest(ip="198.51.100")

    assert "ip: Enter a valid IP address." in validation_error_hint(exc_info.value)


def test_required_client_name_rejects_empty_and_invalid_values() -> None:
    with pytest.raises(ValidationError) as empty:
        ClientNameRequest(name="")
    with pytest.raises(ValidationError) as invalid:
        ClientNameRequest(name="bad name!")

    assert "name: Name is required." in validation_error_hint(empty.value)
    assert "name: Use letters, numbers, hyphens, and underscores." in validation_error_hint(invalid.value)


def test_node_add_request_validates_user_and_port() -> None:
    request = NodeAddRequest(ip="198.51.100.10", user="ubuntu", ssh_port=2222, name="edge-1")

    assert request.user == "ubuntu"
    assert request.ssh_port == 2222
    assert request.name == "edge-1"

    with pytest.raises(ValidationError) as exc_info:
        NodeAddRequest(ip="198.51.100.10", user="bad user", ssh_port=70000)

    hint = validation_error_hint(exc_info.value)
    assert "user: Use letters, numbers, dots, hyphens, and underscores." in hint
    assert "ssh_port" in hint


def test_relay_deploy_request_validates_names_ports_and_exit_selector() -> None:
    request = RelayDeployRequest(
        relay_ip="198.51.100.20",
        exit_arg="exit-1",
        relay_name="relay-1",
        listen_port=8443,
    )

    assert request.exit_arg == "exit-1"
    assert request.listen_port == 8443

    with pytest.raises(ValidationError) as exc_info:
        RelayDeployRequest(relay_ip="not-an-ip", exit_arg="bad selector", relay_name="bad name")

    hint = validation_error_hint(exc_info.value)
    assert "relay_ip: Enter a valid IP address." in hint
    assert "exit_arg: Names and IP addresses cannot contain spaces." in hint
    assert "relay_name: Use letters, numbers, hyphens, and underscores." in hint


def test_wrapped_validation_error_is_operator_readable() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NodeAddRequest(ip="not-an-ip", user="bad user", ssh_port=70000)

    error = wrap_validation_error("Invalid node add request", exc_info.value)

    assert str(error) == "Invalid node add request"
    assert "- ip: Enter a valid IP address." in error.hint
    assert "- user: Use letters, numbers, dots, hyphens, and underscores." in error.hint
    assert "- ssh_port:" in error.hint
    assert "ValidationError" not in error.hint
    assert "pydantic" not in error.hint.lower()
