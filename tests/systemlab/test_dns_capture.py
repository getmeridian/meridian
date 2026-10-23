"""The exit DNS capture finds plain questions and nothing else."""

from __future__ import annotations

import struct

import pytest

from tests.systemlab.dns_capture import count_questions, wire_name


def _udp_query(hostname: str) -> bytes:
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    # Some stub resolvers randomize case (DNS 0x20); the capture must still match.
    question = wire_name(hostname).swapcase() + struct.pack("!HH", 1, 1)
    return b"\x45" + bytes(27) + header + question


def test_wire_name_is_length_prefixed_labels() -> None:
    assert wire_name("API.ipify.org.") == b"\x03api\x05ipify\x03org\x00"


@pytest.mark.parametrize("hostname", ["", "a..b", "x" * 64 + ".example"], ids=["empty", "empty-label", "long-label"])
def test_wire_name_refuses_non_dns_names(hostname: str) -> None:
    with pytest.raises(ValueError):
        wire_name(hostname)


def test_counts_plain_questions_but_not_sni_or_host_headers() -> None:
    packets = [
        _udp_query("api.ipify.org"),
        b"\x16\x03\x01" + b"\x00" * 40 + b"api.ipify.org",  # TLS SNI is dotted
        b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\n\r\n",
        _udp_query("control.example.com"),
    ]

    counts = count_questions(packets, ["api.ipify.org", "control.example.com", "v4.ident.me"])

    assert counts == {"api.ipify.org": 1, "control.example.com": 1, "v4.ident.me": 0}


def test_a_parent_name_also_counts_its_subdomain_questions() -> None:
    # A suffix match errs toward detection; an unrelated name never matches.
    counts = count_questions([_udp_query("api.ipify.org")], ["ipify.org", "ident.me"])

    assert counts == {"ipify.org": 1, "ident.me": 0}
