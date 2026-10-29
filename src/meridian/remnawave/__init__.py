"""What remains of the Remnawave adapter: migrate's reader, the shared models, and the error types."""

from .client import RemnawaveReader
from .models import Host, Node, SubscriptionDocument, User
from .runtime import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
)

# The canonical Xray JSON document type in a subscription URL.
XRAY_JSON_CLIENT_TYPE = "json"

__all__ = [
    "Host",
    "Node",
    "RemnawaveAuthError",
    "RemnawaveError",
    "RemnawaveNetworkError",
    "RemnawaveNotFoundError",
    "RemnawaveReader",
    "SubscriptionDocument",
    "User",
    "XRAY_JSON_CLIENT_TYPE",
]
