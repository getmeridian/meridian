"""Read and write the editable fleet document against the saved-server shelf.

The document is the topology intent with saved server names (or addresses) in place
of `srv-*` profile IDs and without transport paths. Import maps names back through
the shelf, refuses servers it does not know, and carries the saved paths over, so a
file can never change what it does not show.
"""

from __future__ import annotations

import copy
import secrets
from collections.abc import Iterable
from typing import Any

import yaml
from pydantic import ValidationError

from meridian.core.fleet_document import (
    FLEET_DOCUMENT_SCHEMA,
    OMITTED_PATH_FIELDS,
    document_paths,
    intent_server_refs,
    map_server_refs,
)
from meridian.core.topology import ExitIntent, SetupIntent
from meridian.core.validation import CoreInputError, wrap_validation_error
from meridian.servers import ServerRegistry

DOCUMENT_HEADER = (
    "# Meridian fleet: edit this file, then run `meridian fleet import FILE`.\n"
    "# Servers are saved server names or addresses (`meridian server list`).\n"
    "# Reality keys, credentials, and transport paths stay in Meridian and are not listed.\n"
)
EXPORT_HINT = "Write a fresh one with: meridian fleet export > fleet.yml"
# Paths whose transport path is a random token, generated like `default_exit_paths()` does.
_TOKEN_PATH_PROTOCOLS = frozenset({"xhttp", "wss"})


def server_labels(registry: ServerRegistry, server_refs: Iterable[str]) -> dict[str, str]:
    """Each server's saved name, else its address; a ref missing from the shelf keeps its ID."""
    labels: dict[str, str] = {}
    for ref in server_refs:
        entry = registry.find(ref)
        labels[ref] = (entry.name or entry.host) if entry is not None else ref
    return labels


def fleet_document(intent: SetupIntent, registry: ServerRegistry) -> dict[str, Any]:
    """The secret-free, editable document for a fleet's intent."""
    data = intent.model_dump(mode="json")
    labels = server_labels(registry, intent_server_refs(intent))
    map_server_refs(data, lambda ref: labels.get(ref, ref))
    for path in document_paths(data):
        for field in OMITTED_PATH_FIELDS:
            path.pop(field, None)
    return {"schema": FLEET_DOCUMENT_SCHEMA, **data}


def dump_fleet_document(document: dict[str, Any]) -> str:
    """YAML text for the document, with a short header explaining it."""
    return DOCUMENT_HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)


def parse_fleet_document(text: str) -> dict[str, Any]:
    """The document's mapping, after checking it is YAML and names a schema this Meridian reads."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CoreInputError("The fleet file is not valid YAML", hint=str(exc)) from exc
    if not isinstance(data, dict):
        raise CoreInputError("The fleet file must be a YAML mapping", hint=EXPORT_HINT)
    schema = data.pop("schema", None)
    if schema != FLEET_DOCUMENT_SCHEMA:
        raise CoreInputError(
            f"Unsupported fleet file schema {schema!r}; this Meridian reads {FLEET_DOCUMENT_SCHEMA}",
            hint=EXPORT_HINT,
        )
    return data


def intent_from_document(document: dict[str, Any], current: SetupIntent, registry: ServerRegistry) -> SetupIntent:
    """The validated intent a parsed document describes, with shelf IDs and the saved transport paths."""
    data = copy.deepcopy(document)
    listed = sorted({field for path in document_paths(data) for field in path if field in OMITTED_PATH_FIELDS})
    if listed:
        raise CoreInputError(
            "The fleet file lists transport paths, which Meridian keeps itself",
            hint="Remove the `path:` lines from exits' paths; import keeps the saved paths and generates new ones.",
        )
    known = set(intent_server_refs(current))
    unknown: list[str] = []

    def resolve(label: str) -> str:
        entry = registry.find(label)
        if entry is not None:
            return entry.id
        if label not in known:
            unknown.append(label)
        return label

    map_server_refs(data, resolve)
    if unknown:
        names = ", ".join(sorted(set(unknown)))
        raise CoreInputError(
            f"The fleet file names servers that are not saved: {names}",
            hint="Save each one first with `meridian server add IP --name NAME`, or use a name from "
            "`meridian server list`.",
        )
    _carry_transport_paths(data, current)
    try:
        return SetupIntent.model_validate(data)
    except ValidationError as exc:
        raise wrap_validation_error("The fleet file does not describe a valid fleet", exc) from exc


def _carry_transport_paths(data: dict[str, Any], current: SetupIntent) -> None:
    """Give each XHTTP/WSS path its saved transport path (same exit, same protocol), else a new token."""
    exits: dict[str, ExitIntent] = {exit_.id: exit_ for exit_ in current.exits}
    for exit_ in data.get("exits") or []:
        if not isinstance(exit_, dict):
            continue
        saved = exits.get(str(exit_.get("id", "")))
        for path in exit_.get("paths") or []:
            if not isinstance(path, dict) or path.get("protocol") not in _TOKEN_PATH_PROTOCOLS:
                continue
            previous = next(
                (item for item in (saved.paths if saved else []) if item.protocol == path["protocol"]),
                None,
            )
            path["path"] = previous.path if previous is not None and previous.path else secrets.token_hex(8)
