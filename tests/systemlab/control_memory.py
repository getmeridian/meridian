"""Exercise expensive control-service reads and enforce a cgroup memory budget."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient, ControlServiceError
from meridian.core.delivery_explain import DeliveryExplainRequest, DeliveryExplanation
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from tests.systemlab.subscription_client import (
    parse_base64_subscription,
    parse_mihomo_subscription,
    parse_singbox_subscription,
    parse_xray_subscription,
)

# 160 MiB leaves 96 MiB below MemoryMax and 51 MiB above the observed 109 MiB VM peak.
CONTROL_MEMORY_BUDGET_BYTES = 160 * 1024 * 1024
BURST_CONCURRENCY = 4
# .test is in geosite:private, so this reserved example.com name reaches category-ru; documentation
# addresses are geoip:private, so a public resolver address walks geoip:private and geoip:ru.
EXPLANATION_DESTINATIONS = ("memory-budget.example.com:443", "1.1.1.1:443")
# ctld mirrors the Xray archive in the background after the node spec arrives; the agent may not wait for it.
GEODATA_WAIT_SECONDS = 180.0
SUBSCRIPTION_READERS: dict[str, Callable[[str], object]] = {
    "": parse_base64_subscription,
    "json": parse_xray_subscription,
    "mihomo": parse_mihomo_subscription,
    "singbox": parse_singbox_subscription,
}


def require_geodata_explanation(explanation: DeliveryExplanation | None) -> None:
    if explanation is None or not explanation.geodata_available:
        raise ValueError("Pinned Xray geodata is unavailable to the control service.")
    if (
        explanation.policy != "ru-split"
        or explanation.status != "default"
        or explanation.action is None
        or explanation.action.kind != "proxy"
    ):
        raise ValueError("The memory workload must traverse the ru-split geodata rules to the default proxy.")


def wait_for_geodata(
    client: ControlClient,
    *,
    timeout: float = GEODATA_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    deadline = clock() + timeout
    request = DeliveryExplainRequest(destination=EXPLANATION_DESTINATIONS[0])
    while not client.explain_delivery_policy(request).geodata_available:
        if clock() >= deadline:
            raise ValueError(f"Pinned Xray geodata is unavailable to the control service after {timeout:.0f}s.")
        sleep(2.0)


def exercise_memory_paths(client: ControlClient, username: str, public: httpx.Client, public_url: str) -> None:
    for destination in EXPLANATION_DESTINATIONS:
        require_geodata_explanation(client.explain_delivery_policy(DeliveryExplainRequest(destination=destination)))
    preview = client.preview_subscription(
        SubscriptionPreviewRequest(username=username, suffix="json", destination=EXPLANATION_DESTINATIONS[1])
    )
    require_geodata_explanation(preview.explanation)
    if preview.placeholder is not None or not preview.included:
        raise ValueError("The memory workload needs an active person with deployed connections.")

    def fetch(suffix: str) -> None:
        label = suffix or "base64"
        try:
            response = public.get(public_url + (f"/{suffix}" if suffix else ""), headers={"Accept": "text/plain"})
        except httpx.HTTPError:
            raise ValueError(f"The {label} subscription burst could not reach the control service.") from None
        if response.status_code != 200:
            raise ValueError(f"The {label} subscription burst returned HTTP {response.status_code}.")
        try:
            SUBSCRIPTION_READERS[suffix](response.text)
        except ValueError:
            raise ValueError(f"The {label} subscription burst returned an invalid document.") from None

    with ThreadPoolExecutor(max_workers=BURST_CONCURRENCY) as burst:
        for _ in burst.map(fetch, tuple(SUBSCRIPTION_READERS) * 8):
            pass
    print("    control memory workload: geodata explanation, person preview, 32 fetches in four formats", flush=True)


def check_memory_peak(peak: int, maximum: int) -> None:
    if peak <= 0 or peak >= 2**63:
        raise ValueError("Control service memory peak is unavailable.")
    if maximum <= CONTROL_MEMORY_BUDGET_BYTES or maximum >= 2**63:
        raise ValueError("Control service MemoryMax must leave finite headroom above the memory budget.")
    print(
        f"    ctld memory peak: {peak / 1024**2:.1f} MiB ({peak} bytes); "
        f"budget {CONTROL_MEMORY_BUDGET_BYTES / 1024**2:.1f} MiB; MemoryMax {maximum / 1024**2:.1f} MiB",
        flush=True,
    )
    if peak > CONTROL_MEMORY_BUDGET_BYTES:
        raise ValueError("Control service memory peak exceeds the System Lab budget.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("exercise")
    check = commands.add_parser("check")
    check.add_argument("peak", type=int)
    check.add_argument("maximum", type=int)
    args = parser.parse_args()
    try:
        if args.command == "check":
            check_memory_peak(args.peak, args.maximum)
            return
        cluster = ClusterConfig.load()
        with (
            ControlClient(cluster.panel.url, cluster.panel.api_token) as client,
            httpx.Client(timeout=20, limits=httpx.Limits(max_connections=BURST_CONCURRENCY)) as public,
        ):
            user = client.get_user("solo")
            if user is None or not user.sub_token:
                raise ValueError("The single-server memory workload needs solo's subscription.")
            wait_for_geodata(client)
            exercise_memory_paths(client, "solo", public, cluster.panel.subscription_url(user.sub_token))
    except (ValueError, ControlServiceError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
