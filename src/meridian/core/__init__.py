"""Meridian core API primitives.

Core modules expose typed request/result/event contracts used by CLI, future
UI clients, and automation adapters. They must not depend on Typer command
parsing or Rich console state.

Public symbols live in their submodules (``meridian.core.models``,
``meridian.core.deploy``, ``meridian.core.events``, etc.).  Import from
the specific module rather than from ``meridian.core`` directly.
"""
