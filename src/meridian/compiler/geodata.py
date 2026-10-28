"""Refuse geo list names the servers' Xray data files lack.

Xray rejects a whole config over one unknown ``geosite:``/``geoip:`` name, so
an exit or gateway would stop converging; catching it here keeps it out of review.
"""

from __future__ import annotations

from difflib import get_close_matches

from meridian.compiler.errors import TopologyCompileError
from meridian.core.geodata_lists import GEOIP_LISTS, GEOSITE_LISTS
from meridian.core.topology import SetupIntent

_LISTS = {"geosite": GEOSITE_LISTS, "geoip": GEOIP_LISTS, "country": GEOIP_LISTS}


def validate_geodata_lists(intent: SetupIntent) -> None:
    """Fail on the first route or exit override naming a list the shipped geodata lacks."""
    for route in intent.routes:
        _check(f"Route {route.id}", route.match, route.match_values)
    for exit_ in intent.exits:
        for override in exit_.egress_overrides:
            _check(f"Exit {exit_.id} override {override.id}", override.match, override.values)


def _check(owner: str, kind: str, values: list[str]) -> None:
    known = _LISTS.get(kind)
    if known is None:
        return
    for value in values:
        name = value.lower()
        if name in known:
            continue
        label = "country" if kind == "country" else f"{kind} list"
        close = get_close_matches(name, known, n=1)
        hint = f" Did you mean {close[0]!r}?" if close else ""
        raise TopologyCompileError(
            f"{owner} names {label} {name!r}, which the servers' Xray data does not have.{hint} "
            "Edit or remove that rule."
        )
