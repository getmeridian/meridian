"""Ownership-safe Remnawave access and subscription delivery drivers."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import TypeVar, cast

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import (
    AccessUserPayload,
    ExternalSquadPayload,
    SubscriptionSettingsPayload,
    SubscriptionTemplatePayload,
    canonical_hash,
)
from meridian.reconciler.remnawave_drivers import RemnawaveDriverContext
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    postcondition_key,
)
from meridian.remnawave import (
    ExternalSquad,
    RemnawaveNetworkError,
    SubscriptionTemplate,
    User,
)

ValueT = TypeVar("ValueT")


def build_delivery_drivers(
    context: RemnawaveDriverContext,
) -> dict[str, AccessUserDriver | ExternalSquadDriver | SubscriptionTemplateDriver | SubscriptionSettingsDriver]:
    return {
        "access_user": AccessUserDriver(context),
        "external_squad": ExternalSquadDriver(context),
        "subscription_template": SubscriptionTemplateDriver(context),
        "subscription_settings": SubscriptionSettingsDriver(context),
    }


class AccessUserDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, AccessUserPayload)
        user = _bound_or_named_user(self.context, payload.username, binding)
        if user is None:
            return ResourceObservation(exists=False)
        squads = _resolved_ids(self.context, [payload.squad_ref], action.generation)
        external = _optional_remote_id(
            self.context,
            payload.external_squad_ref,
            action.generation,
        )
        matches = (
            user.username == payload.username
            and user.status == "ACTIVE"
            and user.description == "Managed by Meridian access"
            and sorted(user.active_internal_squad_uuids) == sorted(squads)
            and user.external_squad_uuid == external
        )
        return _observed(action, user.uuid, matches, _user_projection(user))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, AccessUserPayload)
        squads = _resolved_ids(self.context, [payload.squad_ref], action.generation)
        external = _optional_remote_id(
            self.context,
            payload.external_squad_ref,
            action.generation,
        )
        existing = _bound_or_named_user(self.context, payload.username, binding)
        if existing is None:
            user = _mutation(
                lambda: self.context.panel.create_access_user(
                    payload.username,
                    squad_uuids=squads,
                    external_squad_uuid=external,
                ),
                f"create access User {payload.username}",
            )
        else:
            if (
                binding is None
                and existing.description != "Managed by Meridian access"
                and not self.context.was_managed(action.resource.logical_id, existing.uuid)
            ):
                raise ResourceReconcileError(
                    f"Unmanaged User collides with requested username {payload.username!r}.",
                    hint="Rename the unmanaged User or explicitly recover its Meridian binding.",
                    category="user",
                )
            if existing.status != "ACTIVE":
                _mutation(
                    lambda: self.context.panel.enable_user(existing.uuid),
                    f"enable access User {payload.username}",
                )
            user = _mutation(
                lambda: self.context.panel.update_access_user(
                    existing.uuid,
                    squad_uuids=squads,
                    external_squad_uuid=external,
                ),
                f"update access User {payload.username}",
            )
        return ResourceApplyReceipt(remote_id=user.uuid)


class SubscriptionTemplateDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, SubscriptionTemplatePayload)
        template = _bound_or_named_template(self.context, payload, binding)
        if template is None:
            return ResourceObservation(exists=False)
        matches = _template_matches(template, payload)
        return _observed(action, template.uuid, matches, _template_projection(template))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, SubscriptionTemplatePayload)
        existing = _bound_or_named_template(self.context, payload, binding)
        if existing is None:
            existing = _mutation(
                lambda: self.context.panel.create_subscription_template(
                    payload.name,
                    payload.template_type,
                ),
                f"create subscription Template {payload.name}",
            )
        elif (
            binding is None
            and not _template_matches(existing, payload)
            and not _action_was_attempted(self.context, action)
        ):
            raise ResourceReconcileError(
                f"Unmanaged Template collides with owned name {payload.name!r}.",
                hint="Rename the unmanaged Template or explicitly recover its Meridian binding.",
                category="user",
            )
        updated = _mutation(
            lambda: self.context.panel.update_subscription_template(
                existing.uuid,
                name=payload.name,
                template_json=payload.template_json,
                encoded_template_yaml=_encoded_yaml(payload),
            ),
            f"update subscription Template {payload.name}",
        )
        return ResourceApplyReceipt(remote_id=updated.uuid)


class ExternalSquadDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, ExternalSquadPayload)
        squad = _bound_or_named_external_squad(self.context, payload, binding)
        if squad is None:
            return ResourceObservation(exists=False)
        templates = _resolved_templates(self.context, payload, action.generation)
        matches = squad.name == payload.name and _normalize_templates(squad.templates) == templates
        return _observed(action, squad.uuid, matches, _external_squad_projection(squad))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, ExternalSquadPayload)
        templates = _resolved_templates(self.context, payload, action.generation)
        existing = _bound_or_named_external_squad(self.context, payload, binding)
        if existing is None:
            existing = _mutation(
                lambda: self.context.panel.create_external_squad(
                    payload.name,
                ),
                f"create External Squad {payload.name}",
            )
        else:
            if (
                binding is None
                and not self.context.was_managed(action.resource.logical_id, existing.uuid)
                and _normalize_templates(existing.templates) != templates
                and not _action_was_attempted(self.context, action)
            ):
                raise ResourceReconcileError(
                    f"Unmanaged External Squad collides with owned name {payload.name!r}.",
                    hint="Rename the unmanaged Squad or explicitly recover its Meridian binding.",
                    category="user",
                )
        squad = _mutation(
            lambda: self.context.panel.update_external_squad(
                existing.uuid,
                name=payload.name,
                templates=templates,
            ),
            f"update External Squad {payload.name}",
        )
        return ResourceApplyReceipt(remote_id=squad.uuid)


class SubscriptionSettingsDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, SubscriptionSettingsPayload)
        settings = self.context.panel.get_subscription_settings()
        matches = (
            settings.profile_title == payload.profile_title and settings.randomize_hosts == payload.randomize_hosts
        )
        return _observed(
            action,
            settings.uuid,
            matches,
            {
                "profile_title": settings.profile_title,
                "randomize_hosts": settings.randomize_hosts,
            },
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, SubscriptionSettingsPayload)
        settings = self.context.panel.get_subscription_settings()
        updated = _mutation(
            lambda: self.context.panel.update_subscription_settings(
                settings.uuid,
                profile_title=payload.profile_title,
                randomize_hosts=payload.randomize_hosts,
            ),
            "update managed subscription title",
        )
        return ResourceApplyReceipt(remote_id=updated.uuid)


def _payload(
    action: ResourceAction,
    expected_type: type[ValueT],
) -> ValueT:
    payload = action.resource.payload
    if not isinstance(payload, expected_type):
        raise ResourceReconcileError(f"Delivery driver received {payload.kind}, expected {expected_type.__name__}.")
    return cast(ValueT, payload)


def _resolved_ids(
    context: RemnawaveDriverContext,
    logical_ids: list[str],
    generation: int,
) -> list[str]:
    remote_ids = [context.remote_id(logical_id, generation) for logical_id in logical_ids]
    if any(not remote_id for remote_id in remote_ids):
        raise ResourceReconcileError(f"Remote identities are incomplete for {', '.join(logical_ids)}.")
    return remote_ids


def _optional_remote_id(
    context: RemnawaveDriverContext,
    logical_id: str,
    generation: int,
) -> str:
    if not logical_id:
        return ""
    remote_id = context.remote_id(logical_id, generation)
    if not remote_id:
        raise ResourceReconcileError(f"Remote identity is missing for {logical_id}.")
    return remote_id


def _resolved_templates(
    context: RemnawaveDriverContext,
    payload: ExternalSquadPayload,
    generation: int,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for template_ref in payload.template_refs:
        remote_id = _optional_remote_id(context, template_ref, generation)
        template = _plan_payload(context, template_ref, SubscriptionTemplatePayload)
        output.append(
            {
                "template_uuid": remote_id,
                "template_type": template.template_type,
            }
        )
    return sorted(output, key=lambda item: item["template_type"])


def _plan_payload(
    context: RemnawaveDriverContext,
    logical_id: str,
    expected_type: type[ValueT],
) -> ValueT:
    payload = next(
        (resource.payload for resource in context.plan.resources if resource.logical_id == logical_id),
        None,
    )
    if not isinstance(payload, expected_type):
        raise ResourceReconcileError(f"Reviewed resource {logical_id!r} has the wrong type.")
    return cast(ValueT, payload)


def _bound_or_named_user(
    context: RemnawaveDriverContext,
    username: str,
    binding: ManagedResourceBinding | None,
) -> User | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_user_by_uuid(binding.remote_id)
    return context.panel.get_user(username)


def _bound_or_named_template(
    context: RemnawaveDriverContext,
    payload: SubscriptionTemplatePayload,
    binding: ManagedResourceBinding | None,
) -> SubscriptionTemplate | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_subscription_template(binding.remote_id)
    matches = [template for template in context.panel.list_subscription_templates() if template.name == payload.name]
    return _one_or_none(matches, f"Templates named {payload.name!r}")


def _bound_or_named_external_squad(
    context: RemnawaveDriverContext,
    payload: ExternalSquadPayload,
    binding: ManagedResourceBinding | None,
) -> ExternalSquad | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_external_squad(binding.remote_id)
    matches = [squad for squad in context.panel.list_external_squads() if squad.name == payload.name]
    return _one_or_none(matches, f"External Squads named {payload.name!r}")


def _template_matches(
    template: SubscriptionTemplate,
    payload: SubscriptionTemplatePayload,
) -> bool:
    if template.name != payload.name or template.template_type != payload.template_type:
        return False
    if payload.template_type == "XRAY_JSON":
        return template.template_json == payload.template_json
    return template.encoded_template_yaml == _encoded_yaml(payload)


def _encoded_yaml(payload: SubscriptionTemplatePayload) -> str | None:
    if not payload.template_yaml:
        return None
    return base64.b64encode(payload.template_yaml.encode("utf-8")).decode("ascii")


def _normalize_templates(templates: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        [
            {
                "template_uuid": item.get("template_uuid", ""),
                "template_type": item.get("template_type", ""),
            }
            for item in templates
        ],
        key=lambda item: item["template_type"],
    )


def _action_was_attempted(
    context: RemnawaveDriverContext,
    action: ResourceAction,
) -> bool:
    checkpoint = context.cluster.action_checkpoints.get(action.idempotency_key)
    return checkpoint is not None and checkpoint.attempts > 0


def _observed(
    action: ResourceAction,
    remote_id: str,
    matches: bool,
    projection: object,
) -> ResourceObservation:
    satisfied = sorted(
        postcondition_key(condition.kind, condition.target_ref, condition.detail)
        for condition in action.resource.postconditions
        if condition.kind == "exists"
    )
    return ResourceObservation(
        exists=True,
        observed_hash=action.expected_hash if matches else canonical_hash(projection),
        remote_id=remote_id,
        satisfied_postconditions=satisfied,
    )


def _user_projection(user: User) -> dict[str, object]:
    return {
        "username": user.username,
        "status": user.status,
        "description": user.description,
        "squads": sorted(user.active_internal_squad_uuids),
        "external_squad_uuid": user.external_squad_uuid,
    }


def _template_projection(template: SubscriptionTemplate) -> dict[str, object]:
    return {
        "name": template.name,
        "type": template.template_type,
        "template_json": template.template_json,
        "encoded_template_yaml": template.encoded_template_yaml,
    }


def _external_squad_projection(squad: ExternalSquad) -> dict[str, object]:
    return {
        "name": squad.name,
        "templates": _normalize_templates(squad.templates),
    }


def _one_or_none(values: list[ValueT], description: str) -> ValueT | None:
    if len(values) > 1:
        raise ResourceReconcileError(f"Remnawave contains duplicate {description}.")
    return values[0] if values else None


def _mutation(call: Callable[[], ValueT], description: str) -> ValueT:
    try:
        return call()
    except RemnawaveNetworkError as exc:
        raise UnknownResourceOutcome(f"Remnawave may have completed {description}; observation is required.") from exc
