"""Reviewed Remnawave client templates for honest fallback behavior."""

from __future__ import annotations

from typing import Any

_PROBE_URL = "https://www.apple.com/library/test/success.html"


def xray_json_template() -> dict[str, Any]:
    """Route through generated Meridian Host outbounds and block all-down."""
    return {
        "remnawave": {
            "injectHosts": [
                {
                    "selector": {
                        "type": "tagRegex",
                        "pattern": "^MERIDIAN_V4_XRAY_EDGE$",
                    },
                    "selectFrom": "HIDDEN",
                    "tagPrefix": "MERIDIAN_PROXY",
                }
            ]
        },
        "log": {"loglevel": "warning"},
        "dns": {"servers": ["1.1.1.1", "8.8.8.8"]},
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "ip": ["geoip:private"],
                    "outboundTag": "BLOCK",
                },
                {
                    "type": "field",
                    "network": "tcp,udp",
                    "balancerTag": "MERIDIAN_AUTO",
                },
            ],
            "balancers": [
                {
                    "tag": "MERIDIAN_AUTO",
                    "selector": ["MERIDIAN_PROXY"],
                    "strategy": {
                        "type": "leastLoad",
                        "settings": {
                            "maxRTT": "1s",
                            "expected": 1,
                            "baselines": ["1s"],
                            "tolerance": 0.01,
                        },
                    },
                    "fallbackTag": "BLOCK",
                }
            ],
        },
        "burstObservatory": {
            "subjectSelector": ["MERIDIAN_PROXY"],
            "pingConfig": {
                "destination": _PROBE_URL,
                "connectivity": "",
                "interval": "1m",
                "sampling": 1,
                "timeout": "3s",
            },
        },
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
            {"tag": "DIRECT", "protocol": "freedom"},
            {"tag": "BLOCK", "protocol": "blackhole"},
        ],
    }


def mihomo_template() -> str:
    """Use all generated Hosts in one ordered health-checked fallback group."""
    return """\
mixed-port: 7890
allow-lan: false
mode: rule
log-level: warning
ipv6: true
proxies: # LEAVE THIS LINE!
proxy-groups:
  - name: MERIDIAN AUTO
    type: fallback
    url: https://www.apple.com/library/test/success.html
    interval: 300
    lazy: true
    proxies: # LEAVE THIS LINE!
rules:
  - MATCH,MERIDIAN AUTO
"""
