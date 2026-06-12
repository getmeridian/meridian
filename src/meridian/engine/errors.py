"""Shared Engine error type for CLI/API adapters to render.

The canonical definition now lives in ``meridian.core.errors``.
This module re-exports it so existing ``from meridian.engine.errors import EngineError``
imports keep working without a mass migration.
"""

from __future__ import annotations

from meridian.core.errors import EngineError

__all__ = ["EngineError"]
