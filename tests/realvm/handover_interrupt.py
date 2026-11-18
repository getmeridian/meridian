"""Run a real apply, but exit after the service commits and before the local adoption checkpoint."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from meridian.cli import app
from meridian.cluster import ClusterConfig

INTERRUPTED = 86


def main() -> None:
    # This fault is confined to the lab launcher, never a switch in the installed CLI.
    if not os.environ.get("MERIDIAN_FLEET_ID") or not os.environ.get("MERIDIAN_HOME"):
        raise SystemExit("This drill requires a disposable real-VM fleet.")
    previous_save = ClusterConfig.save

    def save(cluster: ClusterConfig, path: Path | None = None) -> None:
        if cluster.people_adoption is not None and cluster.people_adoption.state == "committed":
            os._exit(INTERRUPTED)  # no finally handlers or backup hook, just like a process killed here
        previous_save(cluster, path)

    with patch.object(ClusterConfig, "save", save):
        app(args=["apply", "--yes"], prog_name="meridian")


if __name__ == "__main__":
    main()
