"""The signed-node-command routes and their permission metadata."""

from meridian.control.command_models import NodeCommandRecord, NodeCommandRequest, NodeCommandTargets
from meridian.control.route_types import WRITE, ApiRoute, ErrorResponse, route


def node_command_routes(not_found: ErrorResponse) -> tuple[ApiRoute, ...]:
    return (
        route(
            "POST",
            "/api/nodes/{node_id}/commands",
            WRITE,
            "issue_command",
            NodeCommandRequest,
            NodeCommandRecord,
            "Queue a signed command for a node.",
            credentials=False,
            refusals=(not_found, ErrorResponse(409, ("conflict", "command_queue_full"))),
        ),
        route(
            "GET",
            "/api/nodes/{node_id}/commands/{command_id}",
            "read",
            "get_command",
            None,
            NodeCommandRecord,
            "Read a queued command and its result.",
            credentials=False,
            refusals=(not_found,),
        ),
        route(
            "GET",
            "/api/nodes/{node_id}/command-targets",
            "read",
            "command_targets",
            None,
            NodeCommandTargets,
            "List a node's managed command targets.",
            credentials=False,
            refusals=(not_found,),
        ),
    )
