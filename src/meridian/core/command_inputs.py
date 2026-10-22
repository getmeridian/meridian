"""Validated command/input request models shared by CLI and Engine adapters."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from meridian.core.inputs import (
    HostnameValue,
    IPAddressValue,
    NameValue,
    OptionalCountryCodeValue,
    OptionalHostnameValue,
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
    ssh_port: PortValue = 22


class ServerRemoveRequest(CoreModel):
    """Request to remove a known server."""

    query: SelectorValue


class NodeAddRequest(CoreModel):
    """Request to provision and add an exit node."""

    ip: IPAddressValue
    name: NameValue = ""
    user: SshUserValue = "root"
    ssh_port: PortValue = 22
    sni: OptionalHostnameValue = ""
    domain: OptionalHostnameValue = ""
    country: OptionalCountryCodeValue = ""
    harden: bool = True
    yes: bool = False
    # Camouflage with the operator's own hostname (sni, else domain) instead of a borrowed site.
    self_steal: bool = False

    @model_validator(mode="after")
    def self_steal_needs_a_hostname(self) -> Self:
        if self.self_steal and not (self.sni or self.domain):
            raise ValueError("Self-steal needs your own domain: pass it with --sni or --domain.")
        return self

    @property
    def reality_sni(self) -> str:
        """The camouflage hostname asked for; empty lets Meridian pick a borrowed one."""
        return self.sni or (self.domain if self.self_steal else "")


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
    sni: OptionalHostnameValue = ""
    ssh_port: PortValue = 22


class RelayTargetRequest(CoreModel):
    """Request that targets an existing relay."""

    relay_ip: SelectorValue
    exit_arg: OptionalSelectorValue = ""
    user: OptionalSshUserValue = ""
    yes: bool = False


class CamouflageChangeRequest(CoreModel):
    """Request to give an existing exit or relay a new Reality camouflage SNI."""

    target: SelectorValue
    sni: HostnameValue
    exit_arg: OptionalSelectorValue = ""
    yes: bool = False
    self_steal: bool = False
