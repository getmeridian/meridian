"""Ownership-safe Remnawave access and subscription delivery drivers."""

from __future__ import annotations

import base64
from typing import TypeVar, cast

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import (
    AccessUserPayload,
    ExternalSquadPayload,
    SubscriptionSettingsPayload,
    SubscriptionTemplatePayload,
)
from meridian.reconciler.remnawave_drivers import RemnawaveDriverContext
from meridian.reconciler.remnawave_retirement import ExternalSquadRetirement
from meridian.reconciler.remnawave_support import bound_or_named_user, mutation, observed, one_or_none, resolved_ids
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    payload_of,
)
from meridian.remnawave import (
    ExternalSquad,
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
        payload = payload_of(action, AccessUserPayload)
        user = bound_or_named_user(self.context, payload.username, binding)
        if user is None:
            return ResourceObservation(exists=False)
        squads = resolved_ids(self.context, [payload.squad_ref], action.generation)
        external = _optional_remote_id(
            self.context,
            payload.external_squad_ref,
            action.generation,
        )
        # Meridian owns a user's existence, name, and squads. Status (client disable, panel
        # limits and expiry), description, and quotas belong to the operator and the panel.
        matches = (
            user.username == payload.username
            and sorted(user.active_internal_squad_uuids) == sorted(squads)
            and user.external_squad_uuid == external
        )
        return observed(action, user.id, matches, _user_projection(user))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = payload_of(action, AccessUserPayload)
        squads = resolved_ids(self.context, [payload.squad_ref], action.generation)
        external = _optional_remote_id(
            self.context,
            payload.external_squad_ref,
            action.generation,
        )
        existing = bound_or_named_user(self.context, payload.username, binding)
        if existing is None:
            user = mutation(
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
                and "MERIDIAN_ACCESS" not in _user_tags(existing)
                and not self.context.was_managed(action.resource.logical_id, existing.id)
            ):
                raise ResourceReconcileError(
                    f"Unmanaged User collides with requested username {payload.username!r}.",
                    hint="Rename the unmanaged User or explicitly recover its Meridian binding.",
                    category="user",
                )
            user = mutation(
                lambda: self.context.panel.update_access_user(
                    existing.id,
                    squad_uuids=squads,
                    external_squad_uuid=external,
                ),
                f"update access User {payload.username}",
            )
        return ResourceApplyReceipt(remote_id=user.id)

    def retire(self, binding: ManagedResourceBinding) -> None:
        """Delete a user removed from the access list; already gone counts as done."""
        if binding.remote_id:
            mutation(lambda: self.context.panel.delete_user(binding.remote_id), f"delete User {binding.logical_id}")


class SubscriptionTemplateDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = payload_of(action, SubscriptionTemplatePayload)
        template = _bound_or_named_template(self.context, payload, binding)
        if template is None:
            return ResourceObservation(exists=False)
        matches = _template_matches(template, payload)
        return observed(action, template.uuid, matches, _template_projection(template))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = payload_of(action, SubscriptionTemplatePayload)
        existing = _bound_or_named_template(self.context, payload, binding)
        if existing is None:
            existing = mutation(
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
        updated = mutation(
            lambda: self.context.panel.update_subscription_template(
                existing.uuid,
                name=payload.name,
                template_json=payload.template_json,
                encoded_template_yaml=_encoded_yaml(payload),
            ),
            f"update subscription Template {payload.name}",
        )
        return ResourceApplyReceipt(remote_id=updated.uuid)


class ExternalSquadDriver(ExternalSquadRetirement):
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = payload_of(action, ExternalSquadPayload)
        squad = _bound_or_named_external_squad(self.context, payload, binding)
        if squad is None:
            return ResourceObservation(exists=False)
        templates = _resolved_templates(self.context, payload, action.generation)
        matches = squad.name == payload.name and _normalize_templates(squad.templates) == templates
        return observed(action, squad.uuid, matches, _external_squad_projection(squad))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = payload_of(action, ExternalSquadPayload)
        templates = _resolved_templates(self.context, payload, action.generation)
        existing = _bound_or_named_external_squad(self.context, payload, binding)
        if existing is None:
            existing = mutation(
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
        squad = mutation(
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
        payload = payload_of(action, SubscriptionSettingsPayload)
        settings = self.context.panel.get_subscription_settings()
        matches = (
            settings.profile_title == payload.profile_title
            and settings.profile_update_interval_hours == payload.update_interval_hours
            and settings.randomize_hosts == payload.randomize_hosts
        )
        return observed(
            action,
            settings.uuid,
            matches,
            {
                "profile_title": settings.profile_title,
                "update_interval_hours": settings.profile_update_interval_hours,
                "randomize_hosts": settings.randomize_hosts,
            },
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = payload_of(action, SubscriptionSettingsPayload)
        settings = self.context.panel.get_subscription_settings()
        updated = mutation(
            lambda: self.context.panel.update_subscription_settings(
                settings.uuid,
                profile_title=payload.profile_title,
                profile_update_interval_hours=payload.update_interval_hours,
                randomize_hosts=payload.randomize_hosts,
                # Merge the managed headers into the record just read; the
                # panel replaces customResponseHeaders wholesale.
                custom_response_headers=settings.custom_response_headers,
            ),
            "update managed subscription title and refresh interval",
        )
        return ResourceApplyReceipt(remote_id=updated.uuid)


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


def _bound_or_named_template(
    context: RemnawaveDriverContext,
    payload: SubscriptionTemplatePayload,
    binding: ManagedResourceBinding | None,
) -> SubscriptionTemplate | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_subscription_template(binding.remote_id)
    matches = [template for template in context.panel.list_subscription_templates() if template.name == payload.name]
    listed = one_or_none(matches, f"Templates named {payload.name!r}")
    # The list omits template content, so compare against the full record.
    return context.panel.get_subscription_template(listed.uuid) if listed is not None else None


def _bound_or_named_external_squad(
    context: RemnawaveDriverContext,
    payload: ExternalSquadPayload,
    binding: ManagedResourceBinding | None,
) -> ExternalSquad | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_external_squad(binding.remote_id)
    matches = [squad for squad in context.panel.list_external_squads() if squad.name == payload.name]
    return one_or_none(matches, f"External Squads named {payload.name!r}")


def _template_matches(
    template: SubscriptionTemplate,
    payload: SubscriptionTemplatePayload,
) -> bool:
    if template.name != payload.name or template.template_type != payload.template_type:
        return False
    if payload.template_json is not None:
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


def _user_projection(user: User) -> dict[str, object]:
    return {
        "username": user.username,
        "status": user.status,
        "description": user.description,
        "squads": sorted(user.active_internal_squad_uuids),
        "external_squad_uuid": user.external_squad_uuid,
    }


def _user_tags(user: User) -> set[str]:
    tag = user._raw.get("tag") if isinstance(user._raw, dict) else None
    return {tag} if isinstance(tag, str) and tag else set()


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
