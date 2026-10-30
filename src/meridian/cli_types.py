"""Typer's command classes: the only way Meridian walks its own command tree.

Typer 0.26 vendored Click as ``typer._click``, stopped installing the ``click``
package, and reshaped it:
a group is a ``TyperGroup``, no longer a ``click.Group``. Typer's own signatures take
and return its vendored ``Command`` and ``Context``, so annotations use those, and
type checks use the public ``typer.core`` classes.
"""

from __future__ import annotations

from typer._click import Command, Context
from typer.core import TyperArgument as Argument
from typer.core import TyperGroup as Group
from typer.core import TyperOption as Option

__all__ = ["Argument", "Command", "Context", "Group", "Option"]
