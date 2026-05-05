"""Validated command/input request models shared by CLI and Engine adapters."""

from __future__ import annotations

from meridian.core.inputs import (
    IPAddressValue,
    NameValue,
    OptionalSelectorValue,
    OptionalSshUserValue,
    PortValue,
    RequiredNameValue,
    SelectorValue,
    SshUserValue,
)
from meridian.core.models import CoreModel


class ClientNameRequest(CoreModel):
    """A command request that targets one client by name."""

    name: RequiredNameValue


class ServerAddRequest(CoreModel):
    """Request to register a known server."""

    ip: IPAddressValue
    name: NameValue = ""
    user: SshUserValue = "root"


class ServerRemoveRequest(CoreModel):
    """Request to remove a known server."""

    query: SelectorValue


class NodeAddRequest(CoreModel):
    """Request to provision and add an exit node."""

    ip: IPAddressValue
    name: NameValue = ""
    user: SshUserValue = "root"
    ssh_port: PortValue = 22
    sni: str = ""
    domain: str = ""
    harden: bool = True
    yes: bool = False


class NodeTargetRequest(CoreModel):
    """Request that targets an existing node by IP or name."""

    ip_or_name: SelectorValue
    user: OptionalSshUserValue = ""


class RelayDeployRequest(CoreModel):
    """Request to deploy a relay node."""

    relay_ip: IPAddressValue
    exit_arg: OptionalSelectorValue = ""
    user: SshUserValue = "root"
    relay_name: NameValue = ""
    listen_port: PortValue = 443
    yes: bool = False
    sni: str = ""
    ssh_port: PortValue = 22


class RelayTargetRequest(CoreModel):
    """Request that targets an existing relay."""

    relay_ip: IPAddressValue
    exit_arg: OptionalSelectorValue = ""
    user: OptionalSshUserValue = ""
    yes: bool = False
