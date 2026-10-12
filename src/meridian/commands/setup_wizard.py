"""Presentation-only resumable V4 setup journey.

One flow for everyone: questions use the real terms (exit, relay, SNI) and explain
each in a plain sentence where it first appears. Compilation, apply, and
verification stay in ``meridian.setup``.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal, cast

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian.config import DEFAULT_SNI
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
    DeliveryIntent,
    EgressPoolIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RouteMatchKind,
)
from meridian.i18n import t, words
from meridian.setup.runtime import SetupReview, SetupRuntime, SetupVerification
from meridian.setup.service import SetupDraftService
from meridian.setup.shelf import ServerShelf

from .wizard_prompts import Choice, Navigation, Prompter, RichWizardIO, WizardIO, parse_numbers, primary_word

WizardOutcome = Literal["completed", "saved"]
RegisterServer = Callable[[str, str, str, int], None]
Role = Literal["exit", "relay", "gateway", "control"]

_EXTRA_PATHS = ("xhttp", "wss", "hysteria2")
_PROTOCOL_LABELS = {"reality": "Reality", "xhttp": "XHTTP", "wss": "WSS", "hysteria2": "Hysteria2"}


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
        ask_language: bool = False,
        on_language: Callable[[str], None] | None = None,
    ) -> None:
        self.service = service
        self.shelf = shelf
        self.runtime = runtime
        self.register_server = register_server
        self.io = io or RichWizardIO()
        self.prompt = Prompter(self.io)
        self.auto_approve = auto_approve
        self.ask_language = ask_language
        self.on_language = on_language
        self._verification: SetupVerification | None = None
        self._reviewed_now = False

    def run(self) -> WizardOutcome:
        if self.ask_language:
            locale = self.prompt.language()
            if self.on_language is not None:
                self.on_language(locale)
        draft = self.service.load_or_create()
        self.prompt.say(t("wizard.intro"), style="bold")
        if draft.completed_stages:
            self.prompt.say(t("wizard.resuming", stage=t(f"stage.{draft.current_stage}")), style="info")
        self.prompt.say(t("wizard.navigation"), style="dim")
        while True:
            draft = self.service.load_or_create()
            stage = draft.current_stage
            try:
                outcome = self._run_stage(stage, draft)
            except Navigation as navigation:
                if navigation.command in {"save", "quit"}:
                    self.prompt.say(t("wizard.saved"), style="ok")
                    return "saved"
                self.service.back_to(_previous_stage(stage))
                continue
            except (ValidationError, ValueError) as exc:
                self.prompt.say(_validation_message(exc), style="error")
                continue
            if outcome == "completed":
                return outcome

    def _run_stage(self, stage: SetupStage, draft: SetupDraft) -> WizardOutcome | None:
        number = SETUP_STAGE_ORDER.index(stage) + 1
        self.io.write()
        self.prompt.say(f"{number}/{len(SETUP_STAGE_ORDER)} · {t(f'stage.{stage}')}", style="bold cyan")
        self.prompt.details = t(f"details.{stage}")
        handlers: dict[SetupStage, Callable[[SetupDraft], WizardOutcome | None]] = {
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

    # -- 1. servers ---------------------------------------------------------

    def _servers(self, draft: SetupDraft) -> None:
        self.prompt.explain(t("servers.explain"))
        adding = not self.shelf.items(draft.roles)
        while True:
            items = self.shelf.items(draft.roles)
            if not adding:
                for index, item in enumerate(items, 1):
                    ready = item.validation_state in {"validated", "key_ready"}
                    status = t("servers.ready") if ready else item.validation_state
                    self.prompt.say(f"  {index}. {item.title} — {item.ssh_user}@{item.host}:{item.ssh_port} ({status})")
                answer = self.prompt.ask(t("servers.pick"), default=primary_word("word.all")).lower()
                if answer in words("word.add"):
                    adding = True
                    continue
                indices = (
                    list(range(1, len(items) + 1)) if answer in words("word.all") else parse_numbers(answer, len(items))
                )
                self.service.select_servers([items[index - 1].server_ref for index in indices])
                return
            if not self._add_server(len(items)):
                continue
            if self.prompt.yes_no(t("servers.more"), default=False, hint=t("servers.more_hint")):
                continue
            items = self.shelf.items(draft.roles)
            if len(items) == 1:
                self.service.select_servers([items[0].server_ref])
                return
            adding = False

    def _add_server(self, count: int) -> bool:
        address = self.prompt.ask(t("servers.address"))
        if not address:
            raise ValueError(t("error.address_required"))
        default_name = "this-server" if address.lower() == "local" else f"server-{count + 1}"
        name = self.prompt.ask(t("servers.name"), default=default_name)
        try:
            self.register_server(address, name, "", 0)
        except typer.Exit:
            # Registration already explained the problem; let the user correct it.
            self.prompt.say(t("servers.retry"), style="dim")
            return False
        return True

    # -- 2. roles -----------------------------------------------------------

    def _roles(self, draft: SetupDraft) -> None:
        items = [item for item in self.shelf.items(draft.roles) if item.server_ref in draft.server_refs]
        titles = {item.server_ref: item.title for item in items}
        assignments: dict[str, Role] = {}
        if len(items) == 1:
            self.prompt.explain(t("roles.single", server=items[0].title))
            control_ref = items[0].server_ref
            assignments[control_ref] = "exit"
        else:
            self.prompt.explain(t("roles.explain"))
            control_ref = self.prompt.choose(
                t("roles.control"),
                [Choice(item.server_ref, item.title, item.host) for item in items],
            )
            for item in items:
                choices = [
                    Choice("exit", t("role.exit"), t("role.exit_hint")),
                    Choice("relay", t("role.relay"), t("role.relay_hint")),
                    Choice("gateway", t("role.gateway"), t("role.gateway_hint")),
                ]
                if item.server_ref == control_ref:
                    choices.append(Choice("control", t("role.control"), t("role.control_hint")))
                role = self.prompt.choose(t("roles.role_for", server=item.title), choices, default="exit")
                assignments[item.server_ref] = cast(Role, role)

        used: set[str] = set()
        exits: list[SetupExitRole] = []
        for ref, role in assignments.items():
            if role != "exit":
                continue
            exit_id = _unique_id(_resource_id(titles[ref], "exit"), used)
            self.prompt.say(t("roles.exit_header", server=titles[ref]), style="bold")
            region = self.prompt.ask(t("roles.region"))
            warp = self.prompt.yes_no(t("roles.warp"), default=False, hint=t("roles.warp_hint"))
            geo_block = self.prompt.yes_no(t("roles.geo_block"), default=True, hint=t("roles.geo_block_hint"))
            exits.append(SetupExitRole(id=exit_id, server_ref=ref, region=region, warp=warp, geo_block=geo_block))
        if not exits:
            raise ValueError(t("error.need_exit"))
        exit_choices = [Choice(exit_.id, titles[exit_.server_ref]) for exit_ in exits]

        relays: list[SetupRelayRole] = []
        gateways: list[SetupGatewayRole] = []
        for ref, role in assignments.items():
            if role == "relay":
                target = self._pick_exit(t("roles.relay_target", server=titles[ref]), exit_choices)
                relays.append(
                    SetupRelayRole(
                        id=_unique_id(f"relay-{_resource_id(titles[ref], 'relay')}", used),
                        hop_server_refs=[ref],
                        exit_ref=target,
                        protocol_path_ref=f"{target}-reality",
                    )
                )
            elif role == "gateway":
                bridge = self._pick_exit(t("roles.gateway_bridge", server=titles[ref]), exit_choices)
                gateways.append(
                    SetupGatewayRole(
                        id=_unique_id(f"gateway-{_resource_id(titles[ref], 'gateway')}", used),
                        server_ref=ref,
                        bridge_path_ref=f"{bridge}-reality",
                    )
                )

        self.prompt.explain(t("roles.panel_domain_hint", server=titles[control_ref]))
        public_hostname = self.prompt.ask(t("roles.panel_domain"))
        title = self.prompt.ask(t("roles.title"), default="Meridian")
        self.service.assign_roles(
            SetupRoleSelection(
                control=ControlPlaneIntent(server_ref=control_ref, public_hostname=public_hostname, title=title),
                exits=exits,
                transparent_relays=relays,
                routing_gateways=gateways,
            )
        )

    def _pick_exit(self, label: str, choices: Sequence[Choice]) -> str:
        if len(choices) == 1:
            return choices[0].value
        return self.prompt.choose(label, choices)

    # -- 3. connection methods ----------------------------------------------

    def _paths(self, draft: SetupDraft) -> None:
        if draft.roles is None:
            raise LocalStateError("Server roles are missing.")
        hosts = {item.server_ref: (item.title, item.host) for item in self.shelf.items(draft.roles)}
        self.prompt.explain(t("paths.sni_explain"))
        self.prompt.explain(t("paths.extras_explain"))
        selections: list[SetupExitPaths] = []
        for exit_ in draft.roles.exits:
            title, host = hosts.get(exit_.server_ref, (exit_.id, ""))
            self.prompt.say(t("paths.exit_header", server=title, address=host), style="bold")
            reality_sni = self.prompt.ask(t("paths.sni"), default=DEFAULT_SNI)
            paths = [ProtocolPathIntent(id=f"{exit_.id}-reality", protocol="reality", reality_sni=reality_sni)]
            extras = _extra_paths(self.prompt.ask(t("paths.extras"), default=primary_word("word.none")))
            domain = self.prompt.ask(t("paths.domain", address=host)) if extras else ""
            if extras and not domain:
                raise ValueError(t("error.domain_required"))
            if "wss" in extras:
                self.prompt.explain(t("paths.wss_warning"))
            for protocol in ("xhttp", "wss"):
                if protocol in extras:
                    paths.append(
                        ProtocolPathIntent(
                            id=f"{exit_.id}-{protocol}",
                            protocol=cast(Literal["xhttp", "wss"], protocol),
                            tls_sni=domain,
                            host=domain,
                            path=secrets.token_hex(8),
                        )
                    )
            if "hysteria2" in extras:
                paths.append(ProtocolPathIntent(id=f"{exit_.id}-hysteria2", protocol="hysteria2", tls_sni=domain))
            selections.append(SetupExitPaths(exit_ref=exit_.id, paths=paths))
        self.service.configure_paths(SetupPathSelection(exits=selections))

    # -- 4. routing (only routing gateways use it) ---------------------------

    def _routing(self, draft: SetupDraft) -> None:
        if draft.roles is None:
            raise LocalStateError("Server roles are missing.")
        exit_ids = [exit_.id for exit_ in draft.roles.exits]
        gateway_ids = [gateway.id for gateway in draft.roles.routing_gateways]
        if not gateway_ids:
            self.prompt.explain(t("routing.not_needed"))
            self.service.configure_routing(SetupRoutingSelection(default_egress_ref=exit_ids[0]))
            return
        self.prompt.explain(t("routing.explain"))
        pools: list[EgressPoolIntent] = []
        if len(exit_ids) > 1 and self.prompt.yes_no(t("routing.pool"), default=True, hint=t("routing.pool_hint")):
            pools.append(EgressPoolIntent(id="pool-main", exit_refs=exit_ids))
        targets = [Choice(ref, ref) for ref in [*exit_ids, *[pool.id for pool in pools]]]
        default_target = self.prompt.choose(t("routing.default"), targets, default=targets[-1].value)
        routes: list[OrderedRouteIntent] = []
        while self.prompt.yes_no(t("routing.add_rule"), default=False):
            match = self.prompt.choose(
                t("routing.match"),
                [Choice(kind, t(f"routing.match_{kind}")) for kind in ("country", "domain", "ip", "network")],
            )
            values = [value.strip() for value in self.prompt.ask(t("routing.values")).split(",") if value.strip()]
            block = self.prompt.yes_no(t("routing.block"), default=False)
            target = "" if block else self.prompt.choose(t("routing.target"), targets)
            source = gateway_ids[0]
            if len(gateway_ids) > 1:
                source = self.prompt.choose(t("routing.source"), [Choice(ref, ref) for ref in gateway_ids])
            routes.append(
                OrderedRouteIntent(
                    id=f"route-{len(routes) + 1}",
                    priority=(len(routes) + 1) * 10,
                    match=cast(RouteMatchKind, match),
                    match_values=values,
                    action="block" if block else "route",
                    target_ref=target,
                    source_gateway_ref=source,
                )
            )
        self.service.configure_routing(
            SetupRoutingSelection(default_egress_ref=default_target, egress_pools=pools, routes=routes)
        )

    # -- 5. users -------------------------------------------------------------

    def _access(self, draft: SetupDraft) -> None:
        self.prompt.explain(t("access.explain"))
        users = [value.strip() for value in self.prompt.ask(t("access.users"), default="default").split(",")]
        title = draft.roles.control.title if draft.roles is not None else "Meridian"
        self.service.configure_access(
            SetupAccessSelection(
                access=AccessIntent(users=[user for user in users if user]),
                delivery=DeliveryIntent(profile_title=title),
            )
        )

    # -- 6-9. review, apply, verify, handoff ------------------------------------

    def _review(self, draft: SetupDraft) -> None:
        review = self.runtime.review(draft)
        self._write_summary(draft, review)
        if not self.auto_approve and not self.prompt.yes_no(t("review.confirm"), default=True):
            raise Navigation("save")
        self.service.mark_reviewed(review.review_hash)
        self._reviewed_now = True

    def _apply(self, draft: SetupDraft) -> None:
        if not (self.auto_approve or self._reviewed_now) and not self.prompt.yes_no(t("apply.confirm"), default=True):
            raise Navigation("save")
        self.prompt.explain(t("apply.explain"))
        result = self.runtime.apply(draft)
        if not result.all_succeeded:
            raise LocalStateError(t("apply.failed"), hint=result.failure_summary or t("apply.failed_hint"))
        done = t("apply.done", count=len(result.results)) if result.changed else t("apply.unchanged")
        self.prompt.say(done, style="ok")
        self.service.mark_applied(result.plan_hash, review_hash=result.review_hash or result.plan_hash)

    def _verify(self, draft: SetupDraft) -> None:
        self.prompt.explain(t("verify.explain"))
        self._verification = self.runtime.verify(draft)
        for message in self._verification.warnings:
            self.io.write(f"[warn]{escape(message)}[/warn]")
        self.prompt.say(t("verify.done", count=self._verification.node_count), style="ok")
        self.service.mark_verified(datetime.now(UTC).isoformat().replace("+00:00", "Z"))

    def _handoff(self, draft: SetupDraft) -> WizardOutcome:
        verification = self._verification or self.runtime.verify(draft)
        self.prompt.say(t("handoff.ready"), style="bold green")
        for links in self.runtime.publish_handoff(sorted(verification.subscription_urls)):
            self.io.write(f"  [bold]{escape(links.username)}[/bold]")
            if links.page_url:
                self.io.write(f"    {escape(t('handoff.page'))} [bold]{escape(links.page_url)}[/bold]")
            else:
                self.io.write(f"    [warn]{escape(t('handoff.page_unavailable', error=links.error))}[/warn]")
            self.io.write(f"    {escape(t('handoff.subscription'))} {escape(links.subscription_url)}")
        self.prompt.explain(t("handoff.explain"))
        self.prompt.explain(t("handoff.next"))
        self.service.complete_handoff()
        return "completed"

    def _write_summary(self, draft: SetupDraft, review: SetupReview) -> None:
        intent = review.intent
        servers = {item.server_ref: item for item in self.shelf.items(draft.roles)}

        def server(ref: str) -> str:
            item = servers.get(ref)
            return f"{item.title} ({item.host})" if item else ref

        def row(label: str, value: str) -> None:
            self.io.write(f"  [bold]{escape(label):<18}[/bold] {escape(value)}")

        self.prompt.say(t("review.title"))
        control = server(intent.control.server_ref)
        row(
            t("review.control"),
            f"{control} · {intent.control.public_hostname}" if intent.control.public_hostname else control,
        )
        for exit_ in intent.exits:
            flags = [exit_.region.upper()] if exit_.region else []
            flags += [t("review.warp")] if exit_.warp else []
            flags += [t("review.geo_block")] if exit_.geo_block else []
            row(t("review.exit"), " · ".join([server(exit_.server_ref), *flags]))
            reality = next((path for path in exit_.paths if path.protocol == "reality"), None)
            methods = [_PROTOCOL_LABELS[path.protocol] for path in exit_.paths]
            row("", t("review.methods", methods=", ".join(methods)))
            if reality is not None:
                row("", t("review.camouflage", sni=reality.reality_sni))
        for relay in intent.transparent_relays:
            hops = " → ".join(server(ref) for ref in relay.hop_server_refs)
            row(t("review.relay"), f"{hops} → {relay.exit_ref}")
        for gateway in intent.routing_gateways:
            row(t("review.gateway"), server(gateway.server_ref))
        row(t("review.users"), ", ".join(intent.access.users))
        self.prompt.explain(t("review.ru_direct"))
        self.prompt.say(t("review.plan", hash=review.review_hash[:12], count=len(review.plan.resources)), style="dim")
        if review.retiring:
            self.prompt.say(t("review.removes", count=len(review.retiring)), style="warn")
            for binding in review.retiring:
                self.io.write(f"    - {escape(binding.logical_id)}")


def _extra_paths(answer: str) -> list[str]:
    chosen = [item.strip().lower() for item in answer.replace("،", ",").split(",") if item.strip()]
    if not chosen or (len(chosen) == 1 and chosen[0] in words("word.none")):
        return []
    unknown = sorted(set(chosen) - set(_EXTRA_PATHS))
    if unknown:
        raise ValueError(t("error.unknown_method", names=", ".join(unknown)))
    return [protocol for protocol in _EXTRA_PATHS if protocol in chosen]


def _previous_stage(stage: SetupStage) -> SetupStage:
    index = SETUP_STAGE_ORDER.index(stage)
    return SETUP_STAGE_ORDER[max(0, index - 1)]


def _resource_id(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower())
    slug = slug.strip("-_")
    return slug or fallback


def _unique_id(candidate: str, used: set[str]) -> str:
    value, suffix = candidate, 2
    while value in used:
        value, suffix = f"{candidate}-{suffix}", suffix + 1
    used.add(value)
    return value


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return str(first["msg"]).removeprefix("Value error, ")
    return str(exc)
