from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from meridian.compiler.delivery import singbox_template
from tests.systemlab.subscription_client import (
    MIHOMO_CLIENT_TYPE,
    SINGBOX_CLIENT_TYPE,
    base64_endpoint_addresses,
    base64_vless_user_ids,
    check_with_engine,
    endpoint_addresses,
    engine_check_command,
    mihomo_endpoint_addresses,
    mihomo_vless_user_ids,
    missing_addresses,
    parse_base64_subscription,
    parse_mihomo_subscription,
    parse_singbox_subscription,
    parse_xray_subscription,
    replace_user_ids,
    route_only,
    select_outbound,
    singbox_endpoint_addresses,
    singbox_vless_user_ids,
    use_free_local_ports,
    xray_vless_user_ids,
)


def _canonical_config() -> dict:
    return {
        "inbounds": [
            {
                "tag": "SOCKS",
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
            },
            {
                "tag": "HTTP",
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
            },
        ],
        "outbounds": [
            {
                "tag": "MERIDIAN_PROXY_1",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "198.51.100.10",
                            "port": 443,
                            "users": [{"id": ("11111111-1111-1111-1111-111111111111")}],
                        }
                    ]
                },
            },
            {
                "tag": "MERIDIAN_PROXY_2",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "198.51.100.20",
                            "port": 443,
                            "users": [{"id": ("11111111-1111-1111-1111-111111111111")}],
                        }
                    ]
                },
            },
            {"tag": "BLOCK", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "network": "tcp,udp",
                    "balancerTag": "MERIDIAN_AUTO",
                }
            ],
            "balancers": [
                {
                    "tag": "MERIDIAN_AUTO",
                    "selector": ["MERIDIAN_PROXY"],
                }
            ],
        },
        "burstObservatory": {"subjectSelector": ["MERIDIAN_PROXY"]},
    }


def _mihomo_config() -> dict:
    return {
        "mixed-port": 7890,
        "proxies": [
            {
                "name": "exit-a",
                "type": "vless",
                "server": "198.51.100.10",
                "port": 443,
                "uuid": "11111111-1111-1111-1111-111111111111",
            },
            {
                "name": "exit-b",
                "type": "vless",
                "server": "198.51.100.20",
                "port": 443,
                "uuid": "11111111-1111-1111-1111-111111111111",
            },
        ],
        "proxy-groups": [
            {
                "name": "MERIDIAN AUTO",
                "type": "fallback",
                "proxies": ["exit-a", "exit-b"],
            }
        ],
        "rules": ["MATCH,MERIDIAN AUTO"],
    }


def _singbox_config() -> dict:
    """Meridian's sing-box template filled the way Remnawave fills it.

    Remnawave gives every urltest the proxies and every selector the urltests plus the proxies.
    """
    config = singbox_template()
    proxies = [
        {
            "type": "vless",
            "tag": "exit-a",
            "server": "198.51.100.10",
            "server_port": 443,
            "uuid": "11111111-1111-1111-1111-111111111111",
            "flow": "xtls-rprx-vision",
        },
        {
            "type": "hysteria2",
            "tag": "exit-b-hy2",
            "server": "198.51.100.20",
            "server_port": 443,
            "password": "11111111-1111-1111-1111-111111111111",
        },
    ]
    proxy_tags = [proxy["tag"] for proxy in proxies]
    urltest_tags = [outbound["tag"] for outbound in config["outbounds"] if outbound["type"] == "urltest"]
    for outbound in config["outbounds"]:
        if outbound["type"] == "urltest":
            outbound["outbounds"] = proxy_tags
        elif outbound["type"] == "selector":
            outbound["outbounds"] = urltest_tags + proxy_tags
    config["outbounds"].extend(proxies)
    return config


def _fake_engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> Path:
    """Put fake sing-box and mihomo on PATH; they log argv and act out *mode*."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv"
    script = f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > {log}
home=""
while [ $# -gt 0 ]; do
  case "$1" in -d|-D) home=$2; shift ;; esac
  shift
done
case {mode} in
  ok) echo "configuration test is successful" ;;
  reject) echo "proxy 0: invalid REALITY public key" >&2; exit 1 ;;
  download) touch "$home/geoip.metadb"; echo "Can't find MMDB, start download" ;;
esac
"""
    for name in ("sing-box", "mihomo"):
        engine = bin_dir / name
        engine.write_text(script, encoding="utf-8")
        engine.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return log


def test_validates_base64_proxy_urls_and_credentials() -> None:
    encoded = base64.b64encode(
        b"\n".join(
            [
                b"vless://11111111-1111-1111-1111-111111111111@198.51.100.10:443?type=tcp",
                b"vless://11111111-1111-1111-1111-111111111111@198.51.100.20:443?type=tcp",
            ]
        )
    ).decode("ascii")

    urls = parse_base64_subscription(encoded.rstrip("="))

    assert base64_endpoint_addresses(urls) == {"198.51.100.10", "198.51.100.20"}
    assert base64_vless_user_ids(urls) == {"11111111-1111-1111-1111-111111111111"}
    with pytest.raises(ValueError, match="not valid Base64"):
        parse_base64_subscription("nonempty but invalid")
    malformed = base64.b64encode(b"vless://not-a-uuid@198.51.100.10:443").decode("ascii")
    with pytest.raises(ValueError, match="invalid VLESS UUID"):
        parse_base64_subscription(malformed)


def test_validates_mihomo_fallback_graph_and_proxy_endpoints() -> None:
    config = parse_mihomo_subscription(yaml.safe_dump(_mihomo_config()))

    assert mihomo_endpoint_addresses(config) == {"198.51.100.10", "198.51.100.20"}
    assert mihomo_vless_user_ids(config) == {"11111111-1111-1111-1111-111111111111"}
    with pytest.raises(ValueError, match="no proxies"):
        parse_mihomo_subscription("rules:\n  - MATCH,MERIDIAN AUTO\n")

    unknown_member = _mihomo_config()
    unknown_member["proxy-groups"][0]["proxies"].append("missing")
    with pytest.raises(ValueError, match="unknown proxy"):
        parse_mihomo_subscription(yaml.safe_dump(unknown_member))


def test_parses_only_rendered_canonical_xray_documents() -> None:
    parsed = parse_xray_subscription(json.dumps([_canonical_config()]))

    assert endpoint_addresses(parsed) == {
        "198.51.100.10",
        "198.51.100.20",
    }
    assert xray_vless_user_ids(parsed) == {"11111111-1111-1111-1111-111111111111"}
    assert missing_addresses(
        parsed,
        ["198.51.100.10", "198.51.100.30"],
    ) == {"198.51.100.30"}

    with pytest.raises(ValueError, match="not JSON"):
        parse_xray_subscription("dmxlc3M6Ly8=")
    with pytest.raises(ValueError, match="no Meridian"):
        parse_xray_subscription('{"outbounds": [{"tag": "DIRECT"}]}')
    with pytest.raises(ValueError, match="no configs"):
        parse_xray_subscription("[]")
    with pytest.raises(ValueError, match="no Meridian proxy outbounds"):
        parse_xray_subscription(json.dumps([_canonical_config(), {"outbounds": [{"tag": "DIRECT"}]}]))
    primary, copy = _canonical_config(), _canonical_config()
    primary["remarks"], copy["remarks"] = "Automatic", "Automatic · Firefox"
    assert parse_xray_subscription(json.dumps([primary, copy]))["remarks"] == "Automatic"

    assert parse_xray_subscription(json.dumps(_canonical_config()))["routing"]


def test_selects_delivered_path_without_rebuilding_credentials() -> None:
    canonical = _canonical_config()
    selected = route_only(
        canonical,
        select_outbound(canonical, "198.51.100.20"),
    )

    assert selected["routing"]["rules"][-1]["outboundTag"] == ("MERIDIAN_PROXY_2")
    assert "balancers" not in selected["routing"]
    assert "burstObservatory" not in selected
    assert canonical["routing"]["balancers"]


def test_changes_only_test_credentials_and_local_listener_ports() -> None:
    canonical = _canonical_config()
    corrupted = replace_user_ids(
        canonical,
        "00000000-0000-0000-0000-000000000000",
    )
    runnable, socks_port = use_free_local_ports(corrupted)

    assert socks_port != 10808
    assert runnable["inbounds"][0]["port"] == socks_port
    assert {
        user["id"]
        for outbound in runnable["outbounds"][:2]
        for destination in outbound["settings"]["vnext"]
        for user in destination["users"]
    } == {"00000000-0000-0000-0000-000000000000"}
    assert canonical["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"].startswith("1111")


def test_assigns_distinct_local_ports_to_every_inbound() -> None:
    with patch(
        "meridian.xray_client._find_free_port",
        side_effect=[12000, 12000, 12001],
    ):
        runnable, socks_port = use_free_local_ports(_canonical_config())

    assert socks_port == 12000
    assert [inbound["port"] for inbound in runnable["inbounds"]] == [12000, 12001]


def test_mihomo_select_group_led_by_fallback_routes_through_it() -> None:
    """Meridian's profile matches into a hand-pick group whose default is the fallback group."""
    config = _mihomo_config()
    config["proxy-groups"].insert(0, {"name": "Meridian", "type": "select", "proxies": ["MERIDIAN AUTO", "exit-a"]})
    config["rules"] = ["MATCH,Meridian"]
    parse_mihomo_subscription(yaml.safe_dump(config))

    config["proxy-groups"][0]["proxies"] = ["exit-a", "MERIDIAN AUTO"]
    with pytest.raises(ValueError, match="fallback group"):
        parse_mihomo_subscription(yaml.safe_dump(config))


def test_validates_singbox_outbound_graph_and_proxy_endpoints() -> None:
    config = parse_singbox_subscription(json.dumps(_singbox_config()))

    assert singbox_endpoint_addresses(config) == {"198.51.100.10", "198.51.100.20"}
    assert singbox_vless_user_ids(config) == {"11111111-1111-1111-1111-111111111111"}
    with pytest.raises(ValueError, match="not JSON"):
        parse_singbox_subscription("proxies: []")


def _outbound(config: dict, tag: str) -> dict:
    return next(outbound for outbound in config["outbounds"] if outbound["tag"] == tag)


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        pytest.param(
            lambda c: _outbound(c, "Automatic")["outbounds"].append("missing"),
            "references an unknown outbound",
            id="dangling-member",
        ),
        pytest.param(lambda c: _outbound(c, "Automatic").update(outbounds=None), "is empty", id="empty-group"),
        pytest.param(
            lambda c: _outbound(c, "Meridian")["outbounds"].remove("Automatic"),
            "defaults to a non-member",
            id="default-not-member",
        ),
        pytest.param(
            lambda c: _outbound(c, "Meridian").update(default="exit-a"),
            "does not route through its urltest",
            id="selector-skips-urltest",
        ),
        pytest.param(
            lambda c: c["route"].update(final="direct"), "does not route through its urltest", id="final-direct"
        ),
        pytest.param(
            lambda c: c["outbounds"].append({"type": "direct", "tag": "direct"}),
            "nonempty and unique",
            id="duplicate-tag",
        ),
        pytest.param(lambda c: _outbound(c, "exit-a").update(uuid="nope"), "invalid VLESS UUID", id="invalid-uuid"),
        pytest.param(
            lambda c: _outbound(c, "exit-a").update(server_port="443"), "invalid proxy port", id="invalid-port"
        ),
    ],
)
def test_singbox_graph_errors_that_sing_box_check_lets_through(corrupt, message: str) -> None:
    """`sing-box check` decodes options but resolves no tags, so the lab checks the graph itself."""
    config = _singbox_config()
    corrupt(config)

    with pytest.raises(ValueError, match=message):
        parse_singbox_subscription(json.dumps(config))


@pytest.mark.parametrize(
    ("client_type", "expected"),
    [
        (SINGBOX_CLIENT_TYPE, ["sing-box", "check", "--disable-color", "-D", "HOME", "-c", "DOC"]),
        (MIHOMO_CLIENT_TYPE, ["mihomo", "-t", "-d", "HOME", "-f", "DOC"]),
    ],
    ids=["sing-box", "mihomo"],
)
def test_engine_check_runs_each_clients_own_validator(client_type: str, expected: list[str]) -> None:
    assert engine_check_command(client_type, Path("DOC"), Path("HOME")) == expected


def test_engine_check_refuses_formats_without_an_engine() -> None:
    with pytest.raises(ValueError, match="No client engine"):
        engine_check_command("json", Path("DOC"), Path("HOME"))


def test_mihomo_check_runs_against_a_copy_of_the_pinned_geodata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _fake_engines(tmp_path, monkeypatch, "ok")
    geodata = tmp_path / "geodata"
    geodata.mkdir()
    (geodata / "GeoSite.dat").write_bytes(b"pinned")

    output = check_with_engine(MIHOMO_CLIENT_TYPE, "proxies: []\n", tmp_path / "work", geodata_dir=geodata)

    assert output == "configuration test is successful"
    argv = log.read_text(encoding="utf-8").splitlines()
    home, document = Path(argv[argv.index("-d") + 1]), Path(argv[argv.index("-f") + 1])
    assert (home / "GeoSite.dat").read_bytes() == b"pinned"
    assert home != geodata
    assert document.read_text(encoding="utf-8") == "proxies: []\n"


def test_engine_rejection_fails_with_the_engines_own_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_engines(tmp_path, monkeypatch, "reject")

    with pytest.raises(ValueError, match=r"(?s)sing-box rejected .*exit 1.*invalid REALITY public key"):
        check_with_engine(SINGBOX_CLIENT_TYPE, "{}", tmp_path / "work")


def test_engine_that_downloads_rule_data_fails_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rule list the image does not pin would make the check depend on the network."""
    _fake_engines(tmp_path, monkeypatch, "download")

    with pytest.raises(ValueError, match="Mihomo downloaded geoip.metadb while checking"):
        check_with_engine(MIHOMO_CLIENT_TYPE, "proxies: []\n", tmp_path / "work")


def test_missing_engine_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(ValueError, match="sing-box is not installed"):
        check_with_engine(SINGBOX_CLIENT_TYPE, "{}", tmp_path / "work")
