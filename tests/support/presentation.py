"""A deployed CLI context backed by the real HTTP service, with no SSH server."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings
from meridian.core.server_allowances import ServerAllowanceSpec, ServerMetadata
from meridian.core.topology import SetupIntent
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent


@contextmanager
def deployed(directory: Path, intent: SetupIntent | None = None) -> Iterator[ControlClient]:
    intent = intent or single_exit_intent()
    with serve_control(directory) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
        ClusterConfig(topology_intent=intent, panel=PanelConfig(url=url, api_token=ADMIN_TOKEN)).save()
        client.put_settings(
            ServiceSettings(
                profile_title=intent.delivery.profile_title,
                update_interval_hours=intent.delivery.update_interval_hours,
                owner_contact=intent.delivery.owner_contact,
                servers={
                    ref: ServerAllowanceSpec(metadata=intent.server_metadata.get(ref, ServerMetadata()))
                    for ref in intent.declared_server_refs()
                },
            ),
            adopt_presentation=True,
        )
        yield client
