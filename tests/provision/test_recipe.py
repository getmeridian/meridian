"""Tests for provisioning recipe graph primitives."""

from __future__ import annotations

import pytest

from meridian.provision import build_setup_steps
from meridian.provision.recipe import Operation, Recipe, RecipeValidationError, Resource, op
from meridian.provision.steps import ProvisionContext, StepResult


class DummyStep:
    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, conn: object, ctx: object) -> StepResult:
        return StepResult(name=self.name, status="ok")


def test_recipe_orders_by_declared_resources_stably() -> None:
    ctx = object()
    first = op(DummyStep("first"), provides=[Resource.SYSTEM_PACKAGES])
    second = op(
        DummyStep("second"),
        requires=[Resource.SYSTEM_PACKAGES],
        provides=[Resource.DOCKER_INSTALLED],
    )
    third = op(DummyStep("third"), requires=[Resource.DOCKER_INSTALLED])

    recipe = Recipe((third, second, first))

    assert [step.name for step in recipe.steps(ctx)] == ["first", "second", "third"]


def test_recipe_reports_missing_dependency() -> None:
    recipe = Recipe((op(DummyStep("needs docker"), requires=[Resource.DOCKER_INSTALLED]),))

    with pytest.raises(RecipeValidationError, match="needs docker: missing docker_installed"):
        recipe.steps(object())


def test_recipe_skips_inactive_operations() -> None:
    ctx = object()
    inactive = op(
        DummyStep("inactive"),
        requires=[Resource.DOCKER_INSTALLED],
        provides=[Resource.NGINX_INSTALLED],
        when=lambda _ctx: False,
    )
    active = op(DummyStep("active"), provides=[Resource.SYSTEM_PACKAGES])

    assert [step.name for step in Recipe((inactive, active)).steps(ctx)] == ["active"]


def test_operation_delegates_wrapped_step_attributes() -> None:
    class PackageStep(DummyStep):
        _packages = ["curl"]

    operation = op(PackageStep("packages"))

    assert operation._packages == ["curl"]


def test_setup_pipeline_steps_are_operations_with_resources() -> None:
    ctx = ProvisionContext(ip="198.51.100.1", domain="example.com")
    steps = build_setup_steps(ctx)

    assert all(isinstance(step, Operation) for step in steps)
    by_name = {step.name: step for step in steps}
    assert Resource.DOCKER_INSTALLED in by_name["Deploy Remnawave panel"].requires
    assert Resource.NGINX_INSTALLED in by_name["Configure nginx"].requires
    assert Resource.TLS_CERTIFICATE in by_name["Deploy PWA assets"].requires
