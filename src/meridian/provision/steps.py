"""Core provisioning abstractions: Step, StepResult, ProvisionContext, Provisioner."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from typing import Protocol as TypingProtocol

from meridian.config import DEFAULT_SNI
from meridian.core.reporters import NoopReporter, Reporter
from meridian.provision.progress import NoopStepRenderer, StepRenderer

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.core.events import CoreEventType
    from meridian.core.models import EventLevel
    from meridian.core.output import OperationContext
    from meridian.remnawave import MeridianPanel

StepStatus = Literal["ok", "changed", "skipped", "failed"]


@dataclass
class StepResult:
    """Result of a provisioning step."""

    name: str  # Human-readable name: "Install Docker"
    status: StepStatus
    detail: str = ""  # Human-readable detail for tracing
    duration_ms: int = 0
    commands: list = field(default_factory=list)  # CommandResult traces for drill-down


@dataclass
class ProvisionContext:
    """Carries state through the provisioning pipeline.

    Typed fields for well-known configuration AND typed accessors for
    inter-step communication. The _state dict is kept for edge cases
    but typed properties are the preferred interface.
    """

    ip: str
    user: str = "root"
    domain: str = ""
    sni: str = DEFAULT_SNI
    xhttp_enabled: bool = True
    warp: bool = False  # route egress through Cloudflare WARP (SOCKS5 proxy mode)
    hysteria2: bool = True  # UDP/443 fallback (enabled by default)
    geo_block: bool = True  # block Russian domains/IPs at Xray routing level
    hosted_page: bool = False  # serve connection pages via HTTPS on server
    harden: bool = True  # enable SSH hardening + firewall (skip for shared servers)
    creds_dir: str = ""  # local credentials directory path
    is_panel_host: bool = True  # deploy Remnawave panel on this server

    results: list[StepResult] = field(default_factory=list)

    # Port layout — Xray ports configured in Remnawave config profile
    xhttp_port: int = 0  # computed from seed
    reality_port: int = 443  # 443 standalone, ~10443 domain mode
    wss_port: int = 0  # computed from seed (domain mode only)

    @property
    def panel_port(self) -> int:
        """Panel internal port (Remnawave: 3000, legacy 3x-ui: 2053)."""
        from meridian.config import REMNAWAVE_PANEL_PORT

        return REMNAWAVE_PANEL_PORT

    # Dynamic inter-step state
    _state: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def domain_mode(self) -> bool:
        return bool(self.domain)

    @property
    def needs_web_server(self) -> bool:
        """Whether this setup needs nginx (domain mode OR hosted page)."""
        return self.domain_mode or self.hosted_page

    def __getitem__(self, key: str) -> Any:
        return self._state[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._state[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self._state

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    # --- Typed accessors for inter-step state ---

    @property
    def panel_api(self) -> MeridianPanel | None:
        """Authenticated MeridianPanel client, set after panel setup."""
        return self._state.get("panel_api")

    @panel_api.setter
    def panel_api(self, value: MeridianPanel) -> None:
        self._state["panel_api"] = value

    @property
    def cluster(self) -> ClusterConfig | None:
        """ClusterConfig being built during deployment."""
        return self._state.get("cluster")

    @cluster.setter
    def cluster(self, value: ClusterConfig) -> None:
        self._state["cluster"] = value

    # Legacy typed accessors (kept for backward compat during migration)
    @property
    def panel(self) -> Any:
        """Legacy: PanelClient for 3x-ui. Use panel_api for Remnawave."""
        return self._state.get("panel")

    @panel.setter
    def panel(self, value: Any) -> None:
        self._state["panel"] = value

    @property
    def credentials(self) -> Any:
        """Legacy: ServerCredentials. Use cluster for 4.0."""
        return self._state.get("credentials")

    @credentials.setter
    def credentials(self, value: Any) -> None:
        self._state["credentials"] = value


class StepContext(TypingProtocol):
    """Minimal protocol for step contexts (ProvisionContext, RelayContext, etc.)."""

    results: list[StepResult]


class Step(TypingProtocol):
    """Protocol for provisioning steps.

    ``ctx`` is typed as ``Any`` because concrete steps use either
    ``ProvisionContext`` (deploy pipeline) or ``RelayContext`` (relay pipeline).
    Both satisfy ``StepContext`` but carry distinct extra fields.
    ``Provisioner.run()`` is similarly ``Any``-typed.
    """

    @property
    def name(self) -> str: ...

    def run(self, conn: Any, ctx: Any) -> StepResult: ...


class Provisioner:
    """Runs a list of steps, delegating progress output to a StepRenderer."""

    def __init__(self, steps: Sequence[Step]) -> None:
        self.steps = list(steps)

    def run(
        self,
        conn: Any,
        ctx: Any,
        *,
        reporter: Reporter = NoopReporter(),
        operation: OperationContext | None = None,
        renderer: StepRenderer | None = None,
    ) -> list[StepResult]:
        """Execute all steps, collecting results.

        Rendering is delegated to *renderer*. Pass ``RichStepRenderer()``
        for interactive CLI output or omit for silent headless execution.

        Accepts any context type (ProvisionContext, RelayContext, etc.)
        as long as steps can consume it.  If ``ctx`` has a ``results``
        attribute, each result is appended there too.
        """
        active_renderer: StepRenderer = renderer or NoopStepRenderer()
        results: list[StepResult] = []

        total = len(self.steps)
        for i, step in enumerate(self.steps):
            start = time.monotonic()
            _report_step_event(
                reporter,
                operation,
                "provision.step.started",
                step.name,
                index=i + 1,
                total=total,
            )
            with active_renderer.step_starting(step.name, index=i + 1, total=total):
                result = step.run(conn, ctx)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result.duration_ms = elapsed_ms

            results.append(result)
            if hasattr(ctx, "results"):
                ctx.results.append(result)

            if result.status == "failed":
                _report_step_event(
                    reporter,
                    operation,
                    "provision.step.failed",
                    result.name,
                    level="error",
                    index=i + 1,
                    total=total,
                    result=result,
                )
                active_renderer.step_failed(result)
                break
            elif result.status == "skipped":
                _report_step_event(
                    reporter,
                    operation,
                    "provision.step.completed",
                    result.name,
                    index=i + 1,
                    total=total,
                    result=result,
                )
                active_renderer.step_skipped(result)
            else:
                _report_step_event(
                    reporter,
                    operation,
                    "provision.step.completed",
                    result.name,
                    index=i + 1,
                    total=total,
                    result=result,
                )
                active_renderer.step_completed(result)

        return results


def _report_step_event(
    reporter: Reporter,
    operation: OperationContext | None,
    event_type: CoreEventType,
    step_name: str,
    *,
    level: EventLevel = "info",
    index: int,
    total: int,
    result: StepResult | None = None,
) -> None:
    """Emit a provisioning event when a reporter is attached."""
    if operation is None:
        return
    from meridian.core.reporters import emit_event

    data: dict[str, object] = {"step": step_name, "index": index, "total": total}
    if result is not None:
        data.update(
            {
                "status": result.status,
                "detail": result.detail,
                "duration_ms": result.duration_ms,
            }
        )
    emit_event(
        reporter,
        operation,
        event_type,
        level=level,
        phase="provision",
        message=step_name,
        data=data,
    )
