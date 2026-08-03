"""Presentation-only resumable V4 setup journey."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from pydantic import ValidationError

from meridian.compiler.models import (
    ConfigProfilePayload,
    HostPayload,
    ResourcePlan,
)
from meridian.config import DEFAULT_SNI
from meridian.console import confirm, err_console, prompt
from meridian.core.errors import LocalStateError
from meridian.core.setup import (
    SETUP_STAGE_ORDER,
    SetupAccessSelection,
    SetupDraft,
    SetupExitPaths,
    SetupExitRole,
    SetupGatewayRole,
    SetupPathSelection,
    SetupRelayRole,
    SetupRoleSelection,
    SetupRoutingSelection,
    SetupStage,
)
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    DeliveryFormat,
    DeliveryIntent,
    EgressPoolIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RouteMatchKind,
)
from meridian.setup.runtime import SetupRuntime, SetupVerification
from meridian.setup.service import SetupDraftService
from meridian.setup.shelf import ServerShelf

WizardOutcome = Literal["completed", "saved"]
RegisterServer = Callable[[str, str, str, int], None]

_DETAILS: dict[SetupStage, str] = {
    "servers": (
        "Saved servers are reusable SSH connection cards. Meridian stores "
        "stable IDs, never passwords, in setup progress."
    ),
    "roles": (
        "The control server runs Remnawave. Exits carry traffic. Transparent "
        "relays forward bytes, while routing gateways choose an exit by policy."
    ),
    "paths": (
        "Reality is always enabled. XHTTP/WSS need a domain and path; "
        "Hysteria2 uses UDP and cannot pass through a transparent relay."
    ),
    "routing": (
        "A gateway can send traffic to a specific exit or a health-checked "
        "pool. Ordered rules use first-match behavior and pools fail closed."
    ),
    "access": (
        "Users live in Remnawave. Xray JSON and Mihomo can receive automatic "
        "fallback; Base64 receives explicit alternatives."
    ),
    "review": (
        "The hash covers the complete deterministic resource graph. Apply "
        "refuses to run if any reviewed choice changes."
    ),
    "apply": (
        "Each remote action is observed, checkpointed, and safe to resume. "
        "Prior generations stay active until the new generation converges."
    ),
    "verify": (
        "Verification checks the panel, managed nodes, and canonical Remnawave subscription content before handoff."
    ),
    "handoff": (
        "Share the canonical subscription URL. Client applications choose "
        "their supported format from the same Remnawave user."
    ),
}


class WizardIO(Protocol):
    def write(self, message: str = "") -> None: ...

    def ask(self, label: str, *, default: str = "") -> str: ...

    def approve(self, label: str) -> bool: ...


class RichWizardIO:
    """Typer/Rich-backed input adapter."""

    def write(self, message: str = "") -> None:
        err_console.print(message)

    def ask(self, label: str, *, default: str = "") -> str:
        return prompt(label, default=default)

    def approve(self, label: str) -> bool:
        return confirm(label)


class _Navigation(Exception):
    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__(command)


class SetupWizard:
    """Collect one stage at a time over the setup application services."""

    def __init__(
        self,
        *,
        service: SetupDraftService,
        shelf: ServerShelf,
        runtime: SetupRuntime,
        register_server: RegisterServer,
        io: WizardIO | None = None,
        auto_approve: bool = False,
    ) -> None:
        self.service = service
        self.shelf = shelf
        self.runtime = runtime
        self.register_server = register_server
        self.io = io or RichWizardIO()
        self.auto_approve = auto_approve
        self._verification: SetupVerification | None = None

    def run(self) -> WizardOutcome:
        draft = self.service.load_or_create()
        if draft.completed_stages:
            self.io.write(f"[info]Resuming setup at {draft.current_stage} (revision {draft.revision}).[/info]")
        self.io.write("[dim]At any prompt: back, details, save, or quit.[/dim]")
        while True:
            draft = self.service.load_or_create()
            stage = draft.current_stage
            try:
                outcome = self._run_stage(stage, draft)
            except _Navigation as navigation:
                if navigation.command in {"save", "quit"}:
                    self.io.write("[ok]Progress saved. Run `meridian setup` to resume.[/ok]")
                    return "saved"
                if navigation.command == "back":
                    self.service.back_to(_previous_stage(stage))
                    continue
                raise
            except (ValidationError, ValueError) as exc:
                self.io.write(f"[error]{_validation_message(exc)}[/error]")
                continue
            if outcome == "completed":
                return outcome

    def _run_stage(
        self,
        stage: SetupStage,
        draft: SetupDraft,
    ) -> WizardOutcome | None:
        self.io.write()
        self.io.write(f"[bold cyan]{stage.title()}[/bold cyan]")
        handlers: dict[
            SetupStage,
            Callable[[SetupDraft], WizardOutcome | None],
        ] = {
            "servers": self._servers,
            "roles": self._roles,
            "paths": self._paths,
            "routing": self._routing,
            "access": self._access,
            "review": self._review,
            "apply": self._apply,
            "verify": self._verify,
            "handoff": self._handoff,
        }
        return handlers[stage](draft)

    def _servers(self, draft: SetupDraft) -> None:
        while True:
            items = self.shelf.items(draft.roles)
            if items:
                for index, item in enumerate(items, 1):
                    status = (
                        "[green]ready[/green]"
                        if item.validation_state in {"validated", "key_ready"}
                        else f"[yellow]{item.validation_state}[/yellow]"
                    )
                    self.io.write(f"  {index}. {item.title} — {item.ssh_user}@{item.host}:{item.ssh_port} ({status})")
                answer = self._ask(
                    "Select server numbers (comma-separated), or add",
                    default="all",
                    stage="servers",
                )
                if answer.lower() != "add":
                    indices = _indices(
                        answer,
                        len(items),
                        allow_all=True,
                    )
                    self.service.select_servers([items[index - 1].server_ref for index in indices])
                    return

            self.io.write("[dim]Register a server. SSH access is validated before it can receive a role.[/dim]")
            host = self._ask("Server IP address", stage="servers")
            title = self._ask(
                "Server name",
                default=host,
                stage="servers",
            )
            user = self._ask(
                "SSH user",
                default="root",
                stage="servers",
            )
            port = int(
                self._ask(
                    "SSH port",
                    default="22",
                    stage="servers",
                )
            )
            self.register_server(host, title, user, port)

    def _roles(self, draft: SetupDraft) -> None:
        items = [item for item in self.shelf.items(draft.roles) if item.server_ref in draft.server_refs]
        self._write_server_choices(items)
        control_index = _one_index(
            self._ask(
                "Control-plane server number",
                default="1",
                stage="roles",
            ),
            len(items),
        )
        exit_indices = _indices(
            self._ask(
                "Exit server numbers",
                default="all",
                stage="roles",
            ),
            len(items),
            allow_all=True,
        )
        exits: list[SetupExitRole] = []
        for ordinal, index in enumerate(exit_indices, 1):
            item = items[index - 1]
            default_id = _resource_id(item.title, f"exit-{ordinal}")
            exit_id = self._ask(
                f"Exit ID for {item.title}",
                default=default_id,
                stage="roles",
            )
            region = self._ask(
                f"Two-letter region for {exit_id} (optional)",
                stage="roles",
            )
            warp = self._yes_no(
                f"Route {exit_id} outbound traffic through WARP?",
                default=False,
                stage="roles",
            )
            exits.append(
                SetupExitRole(
                    id=exit_id,
                    server_ref=item.server_ref,
                    region=region,
                    warp=warp,
                )
            )

        relays: list[SetupRelayRole] = []
        while self._yes_no(
            "Add a transparent Realm relay chain?",
            default=False,
            stage="roles",
        ):
            hop_indices = _indices(
                self._ask(
                    "Relay hop server numbers in client-to-exit order",
                    stage="roles",
                ),
                len(items),
            )
            exit_ref = self._choose_name(
                "Relay destination exit",
                [exit_.id for exit_ in exits],
                stage="roles",
            )
            relay_id = self._ask(
                "Relay chain ID",
                default=f"relay-{len(relays) + 1}",
                stage="roles",
            )
            custom_sni = self._ask(
                "Custom Reality SNI for this relay (optional)",
                stage="roles",
            )
            relays.append(
                SetupRelayRole(
                    id=relay_id,
                    hop_server_refs=[items[index - 1].server_ref for index in hop_indices],
                    exit_ref=exit_ref,
                    protocol_path_ref=f"{exit_ref}-reality",
                    reality_sni=custom_sni,
                )
            )

        gateways: list[SetupGatewayRole] = []
        while self._yes_no(
            "Add an Xray routing gateway?",
            default=False,
            stage="roles",
        ):
            gateway_index = _one_index(
                self._ask(
                    "Routing gateway server number",
                    stage="roles",
                ),
                len(items),
            )
            gateway_id = self._ask(
                "Routing gateway ID",
                default=f"gateway-{len(gateways) + 1}",
                stage="roles",
            )
            bridge_exit = self._choose_name(
                "Reality blueprint exit",
                [exit_.id for exit_ in exits],
                stage="roles",
            )
            gateways.append(
                SetupGatewayRole(
                    id=gateway_id,
                    server_ref=items[gateway_index - 1].server_ref,
                    bridge_path_ref=f"{bridge_exit}-reality",
                )
            )

        control = items[control_index - 1]
        public_hostname = self._ask(
            "Public panel hostname (optional)",
            stage="roles",
        )
        title = self._ask(
            "Deployment title",
            default="Meridian",
            stage="roles",
        )
        self.service.assign_roles(
            SetupRoleSelection(
                control=ControlPlaneIntent(
                    server_ref=control.server_ref,
                    public_hostname=public_hostname,
                    title=title,
                ),
                exits=exits,
                transparent_relays=relays,
                routing_gateways=gateways,
            )
        )

    def _paths(self, draft: SetupDraft) -> None:
        if draft.roles is None:
            raise LocalStateError("Server roles are missing.")
        selections: list[SetupExitPaths] = []
        for exit_ in draft.roles.exits:
            self.io.write(f"[bold]{exit_.id}[/bold]")
            reality_sni = self._ask(
                "Reality camouflage SNI",
                default=DEFAULT_SNI,
                stage="paths",
            )
            paths = [
                ProtocolPathIntent(
                    id=f"{exit_.id}-reality",
                    protocol="reality",
                    reality_sni=reality_sni,
                )
            ]
            extras = {
                item.strip().lower()
                for item in self._ask(
                    "Optional paths (xhttp,wss,hysteria2 or none)",
                    default="none",
                    stage="paths",
                ).split(",")
                if item.strip().lower() != "none"
            }
            unknown = extras - {"xhttp", "wss", "hysteria2"}
            if unknown:
                raise ValueError("Unknown protocol path: " + ", ".join(sorted(unknown)))
            domain = ""
            if extras:
                domain = self._ask(
                    "TLS hostname for optional paths",
                    stage="paths",
                )
            if "xhttp" in extras:
                paths.append(
                    ProtocolPathIntent(
                        id=f"{exit_.id}-xhttp",
                        protocol="xhttp",
                        tls_sni=domain,
                        host=domain,
                        path=self._ask(
                            "XHTTP path",
                            default=f"/{exit_.id}-xhttp",
                            stage="paths",
                        ),
                    )
                )
            if "wss" in extras:
                paths.append(
                    ProtocolPathIntent(
                        id=f"{exit_.id}-wss",
                        protocol="wss",
                        tls_sni=domain,
                        host=domain,
                        path=self._ask(
                            "WebSocket path",
                            default=f"/{exit_.id}-ws",
                            stage="paths",
                        ),
                    )
                )
            if "hysteria2" in extras:
                paths.append(
                    ProtocolPathIntent(
                        id=f"{exit_.id}-hysteria2",
                        protocol="hysteria2",
                        tls_sni=domain,
                    )
                )
            selections.append(SetupExitPaths(exit_ref=exit_.id, paths=paths))
        self.service.configure_paths(SetupPathSelection(exits=selections))

    def _routing(self, draft: SetupDraft) -> None:
        if draft.roles is None:
            raise LocalStateError("Server roles are missing.")
        exit_ids = [exit_.id for exit_ in draft.roles.exits]
        gateway_ids = [gateway.id for gateway in draft.roles.routing_gateways]
        pools: list[EgressPoolIntent] = []
        if (
            gateway_ids
            and len(exit_ids) > 1
            and self._yes_no(
                "Create a health-checked all-exit failover pool?",
                default=True,
                stage="routing",
            )
        ):
            pools.append(
                EgressPoolIntent(
                    id="pool-main",
                    exit_refs=exit_ids,
                )
            )
        targets = [*exit_ids, *[pool.id for pool in pools]]
        default_target = self._choose_name(
            "Default egress",
            targets,
            default=targets[-1],
            stage="routing",
        )
        routes: list[OrderedRouteIntent] = []
        while gateway_ids and self._yes_no(
            "Add an ordered routing rule?",
            default=False,
            stage="routing",
        ):
            match = self._choose_name(
                "Rule match",
                ["country", "domain", "ip", "network"],
                stage="routing",
            )
            values = [
                value.strip()
                for value in self._ask(
                    f"{match.title()} values (comma-separated)",
                    stage="routing",
                ).split(",")
                if value.strip()
            ]
            block = self._yes_no(
                "Block matching traffic?",
                default=False,
                stage="routing",
            )
            target = ""
            if not block:
                target = self._choose_name(
                    "Rule egress",
                    targets,
                    stage="routing",
                )
            routes.append(
                OrderedRouteIntent(
                    id=f"route-{len(routes) + 1}",
                    priority=(len(routes) + 1) * 10,
                    match=cast(RouteMatchKind, match),
                    match_values=values,
                    action="block" if block else "route",
                    target_ref=target,
                    source_gateway_ref=self._choose_name(
                        "Source gateway",
                        gateway_ids,
                        stage="routing",
                    ),
                )
            )
        self.service.configure_routing(
            SetupRoutingSelection(
                default_egress_ref=default_target,
                egress_pools=pools,
                routes=routes,
            )
        )

    def _access(self, draft: SetupDraft) -> None:
        users = [
            value.strip()
            for value in self._ask(
                "Access user names (comma-separated)",
                default="default",
                stage="access",
            ).split(",")
            if value.strip()
        ]
        title = draft.roles.control.title if draft.roles is not None else "Meridian"
        profile_title = self._ask(
            "Subscription profile title",
            default=title,
            stage="access",
        )
        formats = [
            value.strip().lower()
            for value in self._ask(
                "Delivery formats",
                default="base64,xray_json,mihomo",
                stage="access",
            ).split(",")
            if value.strip()
        ]
        self.service.configure_access(
            SetupAccessSelection(
                access=AccessIntent(users=users),
                delivery=DeliveryIntent(
                    profile_title=profile_title,
                    formats=cast(list[DeliveryFormat], formats),
                ),
            )
        )

    def _review(self, draft: SetupDraft) -> None:
        review = self.runtime.review(draft)
        self._write_plan(review.plan)
        if not self.auto_approve and not self._approve(
            "Apply this exact reviewed topology?",
            stage="review",
        ):
            raise _Navigation("save")
        self.service.mark_reviewed(review.plan.plan_hash)

    def _apply(self, draft: SetupDraft) -> None:
        if not self.auto_approve and not self._approve(
            "Start checkpointed apply now?",
            stage="apply",
        ):
            raise _Navigation("save")
        result = self.runtime.apply(draft)
        if not result.all_succeeded:
            failures = "; ".join(f"{item.action.resource.logical_id}: {item.error}" for item in result.failed)
            raise LocalStateError(
                "Setup apply did not converge.",
                hint=failures or "Fix the failed resource and resume setup.",
            )
        self.io.write(
            f"[ok]{'Applied' if result.changed else 'Already converged'}: {len(result.results)} resources.[/ok]"
        )
        self.service.mark_applied(result.plan_hash)

    def _verify(self, draft: SetupDraft) -> None:
        self._verification = self.runtime.verify(draft)
        self.io.write(f"[ok]Verified {self._verification.node_count} managed node(s) and canonical subscriptions.[/ok]")
        self.service.mark_verified(datetime.now(UTC).isoformat().replace("+00:00", "Z"))

    def _handoff(self, draft: SetupDraft) -> WizardOutcome:
        verification = self._verification or self.runtime.verify(draft)
        self.io.write("[bold green]Meridian is ready.[/bold green]")
        for username, url in sorted(verification.subscription_urls.items()):
            self.io.write(f"  {username}: [bold]{url}[/bold]")
        self.io.write(
            "[dim]Import this URL in a supported app. Remnawave serves "
            "the correct client format from the canonical subscription.[/dim]"
        )
        self.service.complete_handoff()
        return "completed"

    def _ask(
        self,
        label: str,
        *,
        default: str = "",
        stage: SetupStage,
    ) -> str:
        while True:
            value = self.io.ask(label, default=default).strip()
            command = value.lower()
            if command == "details":
                self.io.write(f"[dim]{_DETAILS[stage]}[/dim]")
                continue
            if command in {"back", "save", "quit"}:
                raise _Navigation(command)
            return value

    def _approve(
        self,
        label: str,
        *,
        stage: SetupStage,
    ) -> bool:
        answer = self._ask(
            f"{label} [yes/no]",
            default="yes",
            stage=stage,
        ).lower()
        if answer not in {"yes", "y", "no", "n"}:
            raise ValueError("Answer yes or no.")
        return answer in {"yes", "y"}

    def _yes_no(
        self,
        label: str,
        *,
        default: bool,
        stage: SetupStage,
    ) -> bool:
        answer = self._ask(
            f"{label} [yes/no]",
            default="yes" if default else "no",
            stage=stage,
        ).lower()
        if answer not in {"yes", "y", "no", "n"}:
            raise ValueError("Answer yes or no.")
        return answer in {"yes", "y"}

    def _choose_name(
        self,
        label: str,
        choices: Sequence[str],
        *,
        default: str = "",
        stage: SetupStage,
    ) -> str:
        self.io.write("  " + ", ".join(f"{index}={choice}" for index, choice in enumerate(choices, 1)))
        answer = self._ask(
            label,
            default=default or "1",
            stage=stage,
        )
        if answer in choices:
            return answer
        return choices[_one_index(answer, len(choices)) - 1]

    def _write_server_choices(self, items: Sequence[object]) -> None:
        for index, item in enumerate(items, 1):
            title = getattr(item, "title")
            host = getattr(item, "host")
            self.io.write(f"  {index}. {title} — {host}")

    def _write_plan(self, plan: ResourcePlan) -> None:
        profiles = [
            resource.payload for resource in plan.resources if isinstance(resource.payload, ConfigProfilePayload)
        ]
        hosts = [
            resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, HostPayload) and not resource.payload.is_hidden
        ]
        self.io.write(
            f"  Plan: [bold]{plan.plan_hash}[/bold]\n"
            f"  Workloads: {len(profiles)}\n"
            f"  Published paths: {len(hosts)}\n"
            f"  Managed resources: {len(plan.resources)}"
        )


def _previous_stage(stage: SetupStage) -> SetupStage:
    index = SETUP_STAGE_ORDER.index(stage)
    return SETUP_STAGE_ORDER[max(0, index - 1)]


def _indices(
    value: str,
    count: int,
    *,
    allow_all: bool = False,
) -> list[int]:
    if allow_all and value.lower() == "all":
        return list(range(1, count + 1))
    try:
        values = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise ValueError("Enter comma-separated server numbers.") from exc
    if not values or any(index < 1 or index > count for index in values):
        raise ValueError(f"Choose server numbers from 1 to {count}.")
    if len(values) != len(set(values)):
        raise ValueError("Choose each server once.")
    return values


def _one_index(value: str, count: int) -> int:
    values = _indices(value, count)
    if len(values) != 1:
        raise ValueError("Choose exactly one number.")
    return values[0]


def _resource_id(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower())
    slug = slug.strip("-_")
    return slug or fallback


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return str(first["msg"]).removeprefix("Value error, ")
    return str(exc)
