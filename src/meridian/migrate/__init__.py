"""`meridian migrate`: move a Remnawave fleet onto Meridian's own control service in place (#95).

Back up the panel, import the fleet's users with their credentials and usage,
install the service beside the panel, cut over, apply the Meridian backend
through the reviewed apply path, then remove Remnawave. Every step is
checkpointed in `ClusterConfig.control_migration`, so a re-run continues.
"""

from __future__ import annotations

from meridian.core.errors import MeridianError


class MigrationError(MeridianError):
    """A migration step could not run or refused to; the checkpoint stays where it was."""
