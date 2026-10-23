"""Presentation-only resumable setup journey.

One flow for everyone: questions use the real terms (exit, relay, SNI) and explain
each in a plain sentence where it first appears. Compilation, apply, and
verification stay in ``meridian.setup``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, NamedTuple, cast

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian.config import DEFAULT_SNI
from meridian.core.defaults import REALITY_SNI_CANDIDATES
from meridian.core.errors import LocalStateError
from meridian.core.inputs import (
    DISPLAY_NAME_MAX_LENGTH,
    is_ip,
    validate_display_name_value,
    validate_hostname_value,
    validate_optional_country_code_value,
    validate_optional_hostname_value,
)
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
    RouteMatchKind,
    SetupIntent,
    TopologyServerShelfItem,
    TrafficRouteAction,
    assign_access_usernames,
)
from meridian.i18n import t, words
from meridian.registry_country import registry_country
from meridian.servers import suggest_server_names
from meridian.setup.default_paths import default_exit_paths
from meridian.setup.runtime import SetupReview, SetupRuntime, SetupVerification
from meridian.setup.service import SetupDraftService
from meridian.setup.shelf import ServerShelf

from .wizard_prompts import Choice, Navigation, Prompter, WizardIO, parse_numbers, primary_word
from .wizard_terminal import wizard_io

if TYPE_CHECKING:
    from meridian.diagnostics.camouflage import OwnHostname

WizardOutcome = Literal["completed", "saved"]
RegisterServer = Callable[[str, str, str, int], None]


class CamouflageSites(NamedTuple):
    """What a saved server can camouflage as: sites in its own network, then reachable well-known ones."""

    nearby: list[str]
    reachable: list[str]


SuggestSni = Callable[[str], CamouflageSites]
# Where a self-steal domain resolves, seen from the exit (server ref, hostname).
CheckOwnDomain = Callable[[str, str], "OwnHostname"]
# The camouflage choice for the operator's own domain; no hostname equals it (hostnames need a dot).
OWN_DOMAIN = "own-domain"
# Rule targets that are not an exit or pool; the colon keeps them apart from topology IDs.
_RULE_ACTIONS: dict[str, TrafficRouteAction] = {":direct": "direct", ":block": "block"}
Role = Literal["exit", "relay", "gateway", "control"]

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
        suggest_sni: SuggestSni | None = None,
        check_own_domain: CheckOwnDomain | None = None,
    ) -> None:
        self.service = service
        self.shelf = shelf
        self.runtime = runtime
        self.register_server = register_server
        self.io = io or wizard_io()
        self.prompt = Prompter(self.io)
        self.auto_approve = auto_approve
        self.ask_language = ask_language
        self.on_language = on_language
        self.suggest_sni = suggest_sni
        self.check_own_domain = check_own_domain
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
        self.prompt.say(t("wizard.navigation_keys" if self.prompt.pickers else "wizard.navigation"), style="dim")
        while True:
            draft = self.service.load_or_create()
            stage = draft.current_stage
            try:
                try:
                    outcome = self._run_stage(stage, draft)
                except (ValidationError, ValueError) as exc:
                    message = _validation_message(exc)
                    if self.auto_approve:
                        raise LocalStateError(message, hint=t("wizard.error_hint")) from exc
                    self.prompt.say(message, style="error")
                    if stage not in _QUESTION_STAGES:
                        self._after_error(stage)  # an automatic stage would fail the same way again
                    continue
            except Navigation as navigation:
                if navigation.command in {"save", "quit"}:
                    self.prompt.say(t("wizard.saved"), style="ok")
                    return "saved"
                self.service.back_to(_previous_stage(stage))
                continue
            if outcome == "completed":
                return outcome

    def _after_error(self, stage: SetupStage) -> None:
        """Let the reader fix an earlier answer, retry, or stop; returns only to retry."""
        previous = _previous_stage(stage)
        choice = self.prompt.choose(
            t("wizard.error_next"),
            [
                Choice("back", t("wizard.error_back", stage=t(f"stage.{previous}"))),
                Choice("retry", t("wizard.error_retry")),
                Choice("save", t("wizard.error_save")),
            ],
            default="back",
        )
        if choice != "retry":
            raise Navigation(choice)

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

    # 1. servers
    def _servers(self, draft: SetupDraft) -> None:
        self.prompt.explain(t("servers.explain"))
        adding = not self.shelf.items(draft.roles)
        while True:
            items = self.shelf.items(draft.roles)
            if not adding and self.prompt.pickers is not None:
                picked = self.prompt.pickers.multiselect(
                    t("servers.pick_list"),
                    [Choice(item.server_ref, item.title, _server_hint(item)) for item in items],
                    checked=[item.server_ref for item in items],
                    actions=[Choice("", t("servers.add_action"))],
                    details=self.prompt.details,
                )
                if picked == [""]:
                    adding = True
                    continue
                self.service.select_servers(picked)
                return
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
            if not self._add_server([item.title for item in items]):
                continue
            if self.prompt.yes_no(t("servers.more"), default=False, hint=t("servers.more_hint")):
                continue
            items = self.shelf.items(draft.roles)
            if len(items) == 1:
                self.service.select_servers([items[0].server_ref])
                return
            adding = False

    def _add_server(self, taken: list[str]) -> bool:
        address = self.prompt.ask(t("servers.address"), validate=_required(t("error.address_required")))
        ideas = suggest_server_names(taken)
        if address.lower() == "local":
            default_name = "this-server"
        else:
            default_name = ideas[0] if ideas else f"server-{len(taken) + 1}"
        if ideas:
            self.prompt.say(t("servers.name_ideas", names=", ".join(ideas)), style="dim")
        name = self.prompt.ask(t("servers.name"), default=default_name)
        try:
            self.register_server(address, name, "", 0)
        except typer.Exit:
            # Registration already explained the problem; let the user correct it.
            self.prompt.say(t("servers.retry"), style="dim")
            return False
        return True

    # 2. roles
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
        # Per-destination overrides are edited with `meridian route`; keep them when an exit is re-asked.
        overrides = {exit_.server_ref: exit_.egress_overrides for exit_ in (draft.roles.exits if draft.roles else [])}
        regions = {exit_.server_ref: exit_.region for exit_ in (draft.roles.exits if draft.roles else [])}
        hosts = {item.server_ref: item.host for item in items}
        for ref, role in assignments.items():
            if role != "exit":
                continue
            exit_id = _unique_id(_resource_id(titles[ref], "exit"), used)
            self.prompt.say(t("roles.exit_header", server=titles[ref]), style="bold")
            # The registry lookup runs only for a server without a saved answer.
            region = self.prompt.ask(
                t("roles.region"),
                default=regions.get(ref) or registry_country(hosts[ref]),
                validate=validate_optional_country_code_value,
            )
            warp = self.prompt.yes_no(t("roles.warp"), default=False, hint=t("roles.warp_hint"))
            geo_block = self.prompt.yes_no(t("roles.geo_block"), default=True, hint=t("roles.geo_block_hint"))
            exits.append(
                SetupExitRole(
                    id=exit_id,
                    server_ref=ref,
                    region=region,
                    warp=warp,
                    geo_block=geo_block,
                    egress_overrides=overrides.get(ref, []),
                )
            )
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
        public_hostname = self.prompt.ask(t("roles.panel_domain"), validate=validate_optional_hostname_value)
        title = self.prompt.ask(t("roles.title"), default="Meridian")
        self.service.assign_roles(
            SetupRoleSelection(
                control=ControlPlaneIntent(server_ref=control_ref, public_hostname=public_hostname, title=title),
                exits=exits,
                transparent_relays=relays,
                routing_gateways=gateways,
                # Fleet settings the wizard never asks about survive re-answering the roles.
                harden=draft.roles.harden if draft.roles else True,
                forward_client_ips=draft.roles.forward_client_ips if draft.roles else False,
            )
        )

    def _pick_exit(self, label: str, choices: Sequence[Choice]) -> str:
        if len(choices) == 1:
            return choices[0].value
        return self.prompt.choose(label, choices)

    # 3. connection methods
    def _paths(self, draft: SetupDraft) -> None:
        if draft.roles is None:
            raise LocalStateError("Server roles are missing.")
        hosts = {item.server_ref: (item.title, item.host) for item in self.shelf.items(draft.roles)}
        self.prompt.explain(t("paths.sni_explain"))
        self.prompt.explain(t("paths.methods_explain"))
        selections: list[SetupExitPaths] = []
        for exit_ in draft.roles.exits:
            title, host = hosts.get(exit_.server_ref, (exit_.id, ""))
            self.prompt.say(t("paths.exit_header", server=title, address=host), style="bold")
            sites = CamouflageSites([], [])
            if self.suggest_sni is not None:
                self.prompt.say(t("paths.sni_probe", server=title), style="dim")
                sites = self.suggest_sni(exit_.server_ref)
                if not sites.nearby and not sites.reachable:
                    self.prompt.say(t("paths.sni_unreachable", server=title), style="warn")
            hints = {sni: t("paths.sni_reachable") for sni in sites.reachable}
            hints.update({sni: t("paths.sni_nearby") for sni in sites.nearby})
            candidates = list(dict.fromkeys([*sites.nearby, *sites.reachable, *REALITY_SNI_CANDIDATES]))
            reality_sni = self.prompt.choose(
                t("paths.sni"),
                [
                    *(Choice(sni, sni, hints.get(sni, "")) for sni in candidates),
                    Choice(OWN_DOMAIN, t("paths.sni_own"), t("paths.sni_own_hint")),
                ],
                default=next(iter([*sites.nearby, *sites.reachable]), DEFAULT_SNI),
                other=t("paths.sni_custom"),
                validate_other=validate_hostname_value,
            )
            self_steal = reality_sni == OWN_DOMAIN
            if self_steal:
                reality_sni = self._own_domain(exit_.server_ref, host)
            domain = self.prompt.ask(t("paths.domain", address=host), validate=validate_optional_hostname_value)
            if domain:
                self.prompt.explain(t("paths.wss_warning"))
            paths = default_exit_paths(
                exit_.id, address=host, reality_sni=reality_sni, domain=domain, self_steal=self_steal
            )
            selections.append(SetupExitPaths(exit_ref=exit_.id, paths=paths))
        self.service.configure_paths(SetupPathSelection(exits=selections))

    def _own_domain(self, server_ref: str, address: str) -> str:
        """Ask for the operator's self-steal domain and say where it points from the exit."""
        self.prompt.explain(t("paths.own_explain", address=address))
        hostname = self.prompt.ask(t("paths.own_domain", address=address), validate=_own_hostname_value)
        if self.check_own_domain is not None:
            found = self.check_own_domain(server_ref, hostname)
            if found.state == "unresolved":
                self.prompt.say(t("paths.own_unresolved", sni=hostname, address=address), style="warn")
            elif found.state == "elsewhere":
                self.prompt.say(
                    t("paths.own_elsewhere", sni=hostname, addresses=", ".join(found.addresses), address=address),
                    style="warn",
                )
        return hostname

    # 4. routing (only routing gateways use it)
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
                [
                    Choice(kind, t(f"routing.match_{kind}"))
                    for kind in ("country", "domain", "geosite", "ip", "network")
                ],
            )
            values = [value.strip() for value in self.prompt.ask(t("routing.values")).split(",") if value.strip()]
            target = self.prompt.choose(
                t("routing.target"),
                [
                    *targets,
                    Choice(":direct", t("routing.target_direct"), t("routing.target_direct_hint")),
                    Choice(":block", t("routing.target_block")),
                ],
            )
            action = _RULE_ACTIONS.get(target, "route")
            source = gateway_ids[0]
            if len(gateway_ids) > 1:
                source = self.prompt.choose(t("routing.source"), [Choice(ref, ref) for ref in gateway_ids])
            routes.append(
                OrderedRouteIntent(
                    id=f"route-{len(routes) + 1}",
                    priority=(len(routes) + 1) * 10,
                    match=cast(RouteMatchKind, match),
                    match_values=values,
                    action=action,
                    target_ref=target if action == "route" else "",
                    source_gateway_ref=source,
                )
            )
        self.service.configure_routing(
            SetupRoutingSelection(default_egress_ref=default_target, egress_pools=pools, routes=routes)
        )

    # 5. users
    def _access(self, draft: SetupDraft) -> None:
        self.prompt.explain(t("access.explain"))
        previous = draft.access.access if draft.access is not None else None
        default = ", ".join(previous.display_name(user) for user in previous.users) if previous else "default"
        answer = self.prompt.ask(t("access.users"), default=default, validate=_check_users)
        access = _access_intent(_split_names(answer), previous)
        renamed = [f"{name} → {user}" for user, name in access.display_names.items()]
        if renamed:
            self.prompt.say(t("access.usernames", pairs=", ".join(renamed)), style="dim")
        title = draft.roles.control.title if draft.roles is not None else "Meridian"
        self.service.configure_access(SetupAccessSelection(access=access, delivery=DeliveryIntent(profile_title=title)))

    # 6-9. review, apply, verify, handoff
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
            self.io.write(f"  [bold]{escape(links.name)}[/bold]")
            if links.page_url:
                self.io.write(f"    {escape(t('handoff.page'))} [bold]{escape(links.page_url)}[/bold]")
            else:
                self.io.write(f"    [warn]{escape(t('handoff.page_unavailable', error=links.error))}[/warn]")
            self.io.write(f"    {escape(t('handoff.subscription'))} {escape(links.subscription_url)}")
        self.prompt.explain(t("handoff.explain"))
        if verification.panel_url:
            self._admin_panel(verification)
        self.prompt.explain(t("handoff.next"))
        self.service.complete_handoff()
        return "completed"

    def _admin_panel(self, verification: SetupVerification) -> None:
        """Where the Remnawave panel lives, for the curious; the password stays behind `meridian panel`."""
        self.io.write("")
        self.io.write(f"  [bold]{escape(t('handoff.admin_title'))}[/bold]")
        self.io.write(f"    {escape(t('handoff.admin_url'))} [bold]{escape(verification.panel_url)}[/bold]")
        if verification.admin_user:
            self.io.write(f"    {escape(t('handoff.admin_login'))} {escape(verification.admin_user)}")
        self.io.write(f"    {escape(t('handoff.admin_password'))} meridian panel --show-password")
        self.io.write(f"    [dim]{escape(t('handoff.admin_warning'))}[/dim]")

    def _write_summary(self, draft: SetupDraft, review: SetupReview) -> None:
        intent = review.intent
        servers = {item.server_ref: item for item in self.shelf.items(draft.roles)}

        def server(ref: str) -> str:
            item = servers.get(ref)
            return f"{item.title} ({item.host})" if item else ref

        def row(label: str, value: str) -> None:
            self.io.write(f"  [bold]{escape(label):<20}[/bold] {escape(value)}")

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
                camouflage = "review.camouflage_own" if reality.self_steal else "review.camouflage"
                row("", t(camouflage, sni=reality.reality_sni))
        for relay in intent.transparent_relays:
            hops = " → ".join(server(ref) for ref in relay.hop_server_refs)
            row(t("review.relay"), f"{hops} → {relay.exit_ref}")
        for gateway in intent.routing_gateways:
            row(t("review.gateway"), server(gateway.server_ref))
            for values, target in _gateway_rules(intent, gateway.id):
                row("", t("review.rule", values=values, target=target))
        row(t("review.users"), ", ".join(intent.access.display_name(user) for user in intent.access.users))
        self.prompt.explain(t("review.ru_direct"))
        self.prompt.say(t("review.plan", hash=review.review_hash[:12], count=len(review.plan.resources)), style="dim")
        if review.retiring:
            self.prompt.say(t("review.removes", count=len(review.retiring)), style="warn")
            for binding in review.retiring:
                self.io.write(f"    - {escape(binding.logical_id)}")


# Stages that ask questions: an error there re-asks them. The rest run on their own.
_QUESTION_STAGES: frozenset[SetupStage] = frozenset({"servers", "roles", "paths", "routing", "access"})


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


def _gateway_rules(intent: SetupIntent, gateway_ref: str) -> list[tuple[str, str]]:
    """A gateway's rules as (what, where) in first-match order, ending with where the rest goes."""
    routes = intent.gateway_routes(gateway_ref)
    everything = t("review.everything_else")
    rules = [(", ".join(route.match_values) or everything, _rule_target(route)) for route in routes]
    if not any(route.match == "all" for route in routes):
        rules.append((everything, intent.default_egress_ref))
    return rules


def _rule_target(route: OrderedRouteIntent) -> str:
    if route.action == "direct":
        return t("routing.target_direct")
    if route.action == "block":
        return t("routing.target_block")
    return route.target_ref


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return str(first["msg"]).removeprefix("Value error, ")
    return str(exc)


def _server_hint(item: TopologyServerShelfItem) -> str:
    """``user@host:port · status`` for a server row."""
    status = t("servers.ready") if item.validation_state in {"validated", "key_ready"} else item.validation_state
    return f"{item.ssh_user}@{item.host}:{item.ssh_port} · {status}"


def _required(message: str) -> Callable[[str], None]:
    def check(value: str) -> None:
        if not value.strip():
            raise ValueError(message)

    return check


def _split_names(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def _own_hostname_value(value: str) -> str:
    hostname = validate_hostname_value(value)
    if is_ip(hostname):
        raise ValueError(t("paths.own_not_ip"))
    return hostname


def _check_users(value: str) -> None:
    names = _split_names(value)
    if not names:
        raise ValueError(t("error.users_required"))
    for name in names:
        try:
            validate_display_name_value(name)
        except ValueError:
            raise ValueError(t("error.user_name", name=name, max=DISPLAY_NAME_MAX_LENGTH)) from None
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError(t("error.users_twice"))


def _access_intent(names: Sequence[str], previous: AccessIntent | None) -> AccessIntent:
    """Panel usernames for typed names; a name already in the draft keeps its username."""
    users = assign_access_usernames(names, known=previous)
    return AccessIntent(
        users=users,
        display_names={user: name for user, name in zip(users, names, strict=True) if user != name},
    )
