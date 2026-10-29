"""Exit countries: registry lookup, entry names, and where the country is kept."""

from __future__ import annotations

import json

import httpx

from meridian.compiler.models import HostPayload, NodeBindingPayload
from meridian.compiler.names import country_flag, exit_places, host_remark
from meridian.registry_country import registry_country
from meridian.setup.single_server import single_server_intent
from tests.support.topology import compile_panel_pass

_BOOTSTRAP = {
    "services": [
        [["2.0.0.0/8", "178.0.0.0/8"], ["https://rdap.db.ripe.net/"]],
        [["2.31.0.0/16"], ["http://rdap.example/", "https://rdap.narrow.example/"]],
    ]
}


def _rdap(record: dict[str, object], seen: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "data.iana.org":
            return httpx.Response(200, json=_BOOTSTRAP)
        return httpx.Response(200, content=json.dumps(record), headers={"content-type": "application/rdap+json"})

    return httpx.MockTransport(handle)


def test_country_comes_from_the_most_specific_registry_over_https() -> None:
    seen: list[str] = []
    assert registry_country("2.31.16.91", transport=_rdap({"country": "de"}, seen)) == "DE"
    assert seen == ["https://data.iana.org/rdap/ipv4.json", "https://rdap.narrow.example/ip/2.31.16.91"]


def test_unknown_or_malformed_answers_are_empty_not_errors() -> None:
    assert registry_country("2.31.16.91", transport=_rdap({}, [])) == ""
    assert registry_country("2.31.16.91", transport=_rdap({"country": "Germany"}, [])) == ""
    assert registry_country("9.9.9.9", transport=_rdap({"country": "US"}, [])) == ""  # no registry listed

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert registry_country("2.31.16.91", transport=httpx.MockTransport(down)) == ""


def test_documentation_addresses_are_never_looked_up() -> None:
    seen: list[str] = []
    assert registry_country("198.51.100.10", transport=_rdap({"country": "DE"}, seen)) == ""
    assert seen == []


def test_entry_names_lead_with_the_exit_country() -> None:
    assert country_flag("de") == "\U0001f1e9\U0001f1ea"
    assert exit_places([("main", "DE")]) == {"main": "\U0001f1e9\U0001f1ea DE"}
    assert exit_places([("main", "")]) == {"main": ""}
    assert exit_places([("main", "DE"), ("exit-2", "DE"), ("exit-3", "FI"), ("exit-4", "")]) == {
        "main": "\U0001f1e9\U0001f1ea main",
        "exit-2": "\U0001f1e9\U0001f1ea exit-2",
        "exit-3": "\U0001f1eb\U0001f1ee FI",
        "exit-4": "exit-4",
    }
    assert host_remark("reality", fingerprint="chrome", place="\U0001f1e9\U0001f1ea DE") == (
        "\U0001f1e9\U0001f1ea DE · Reality · Chrome"
    )


def test_compiled_hosts_and_node_carry_the_region() -> None:
    plan = compile_panel_pass(single_server_intent(None, server_ref="srv-a", address="198.51.100.10", region="NL"))

    remarks = {
        r.payload.remark for r in plan.resources if isinstance(r.payload, HostPayload) and not r.payload.is_hidden
    }
    nodes = [r.payload for r in plan.resources if isinstance(r.payload, NodeBindingPayload)]

    assert "\U0001f1f3\U0001f1f1 NL · Reality · Chrome" in remarks
    assert "Automatic" in remarks  # the virtual balancer entries keep their names
    assert [node.country_code for node in nodes] == ["NL"]


def test_redeploy_keeps_the_saved_country_unless_one_is_given() -> None:
    first = single_server_intent(None, server_ref="srv-a", address="198.51.100.10", region="NL")

    assert single_server_intent(first, server_ref="srv-a", address="198.51.100.10").exits[0].region == "NL"
    assert single_server_intent(first, server_ref="srv-a", address="198.51.100.10", region="SE").exits[0].region == (
        "SE"
    )


def test_deploy_looks_the_country_up_only_when_neither_given_nor_saved(monkeypatch) -> None:
    from meridian.commands import deploy
    from meridian.core.deploy import DeployRequest

    calls: list[str] = []
    monkeypatch.setattr(deploy, "registry_country", lambda ip: calls.append(ip) or "FI")
    saved = single_server_intent(None, server_ref="srv-a", address="198.51.100.10", region="NL")

    assert deploy._with_country(DeployRequest(), None, "srv-a", "198.51.100.10").country == "FI"
    assert deploy._with_country(DeployRequest(country="se"), None, "srv-a", "198.51.100.10").country == "SE"
    assert deploy._with_country(DeployRequest(), saved, "srv-a", "198.51.100.10").country == ""
    assert calls == ["198.51.100.10"]
