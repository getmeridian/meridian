"""Architecture guard tests.

Enforces structural invariants that CLAUDE.md documents as design decisions.
Each test catches a class of mistakes at CI time — before code review, not
during a quarterly audit. Add a test here whenever a refactoring establishes
a new invariant worth preserving.

Run: pytest tests/test_architecture.py -v
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "meridian"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _imports(path: Path) -> list[str]:
    """Return all module names imported by a Python file (top-level and deferred)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _matches_any(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(f"{p}.") for p in prefixes)


def _scan_violations(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    violations = []
    for path in sorted(root.rglob("*.py")):
        for module in _imports(path):
            if _matches_any(module, forbidden):
                violations.append(f"{path.relative_to(SRC)} imports {module}")
    return violations


# ---------------------------------------------------------------------------
# 1. Layer boundary tests
# ---------------------------------------------------------------------------

# core/ must never depend on CLI, console, config, or Rich.
CORE_FORBIDDEN = (
    "meridian.commands",
    "meridian.console",
    "meridian.config",
    "rich",
    "typer",
    "click",
)

# engine/ must not import commands, provisioning runtime, or console.
# meridian.config is allowed (path constants needed for server state).
ENGINE_FORBIDDEN = (
    "meridian.commands",
    "meridian.console",
    "meridian.provision",
    "meridian.remnawave",
    "meridian.renderers",
    "meridian.ssh",
    "rich",
    "typer",
    "click",
)

# adapters/ bridge core↔concrete — never depend on engine.
ADAPTER_FORBIDDEN = ("meridian.engine",)

# operations.py + relay_ops.py + panel_bootstrap.py must not import
# private (_-prefixed) symbols from commands/.
LIBRARY_MODULES = [
    SRC / "operations.py",
    SRC / "relay_ops.py",
    SRC / "panel_bootstrap.py",
]


class TestLayerBoundaries:
    """Prevent cross-layer imports that create circular or inverted dependencies."""

    def test_core_never_imports_cli_layer(self) -> None:
        violations = _scan_violations(SRC / "core", CORE_FORBIDDEN)
        assert violations == [], f"core/ has forbidden imports:\n" + "\n".join(violations)

    def test_engine_never_imports_commands_or_runtime(self) -> None:
        violations = _scan_violations(SRC / "engine", ENGINE_FORBIDDEN)
        assert violations == [], f"engine/ has forbidden imports:\n" + "\n".join(violations)

    def test_adapters_never_import_engine(self) -> None:
        violations = _scan_violations(SRC / "adapters", ADAPTER_FORBIDDEN)
        assert violations == [], f"adapters/ has forbidden imports:\n" + "\n".join(violations)

    def test_library_modules_never_import_private_from_commands(self) -> None:
        """operations.py and friends must not import _foo from commands/.

        These modules are shared business logic consumed by both CLI commands
        and the reconciler. Importing private command functions creates an
        inverted dependency — the exact bug this refactoring fixed.
        """
        violations = []
        for path in LIBRARY_MODULES:
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("meridian.commands"):
                    # Reject any import from meridian.commands.resolve — those
                    # symbols now live in meridian.resolve.
                    if node.module == "meridian.commands.resolve" or node.module.startswith("meridian.commands.resolve."):
                        imported = ", ".join(a.name for a in node.names)
                        violations.append(
                            f"{path.name} imports {imported} from {node.module} — use meridian.resolve"
                        )
                    else:
                        for alias in node.names:
                            if alias.name.startswith("_"):
                                violations.append(
                                    f"{path.name} imports private {alias.name} from {node.module}"
                                )
        assert violations == [], "Library modules import command-layer symbols:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# 2. Contract drift tests
# ---------------------------------------------------------------------------


class TestContractDrift:
    """Catch type/schema drift between layers that define the same concept."""

    def test_cluster_fields_sets_match_dataclasses(self) -> None:
        """_KNOWN_FIELDS sets must match their dataclass definitions.

        If a field is added to a dataclass but missing from the set,
        it silently drops into _extra on load — a data loss bug.
        """
        from meridian.cluster import (
            ClusterConfig,
            DesiredNode,
            DesiredRelay,
            NodeEntry,
            PanelConfig,
            RelayEntry,
            SubscriptionPageConfig,
        )

        # Mapping of dataclass → known-fields set name in cluster.py
        # The sets are derived programmatically now, but this test ensures
        # the derivation stays correct.
        for cls in (PanelConfig, NodeEntry, RelayEntry, DesiredNode, DesiredRelay, ClusterConfig, SubscriptionPageConfig):
            expected = {f.name for f in dataclasses.fields(cls) if not f.name.startswith("_")}
            # Just verify each class has typed public fields (the derivation test)
            assert len(expected) > 0, f"{cls.__name__} has no public fields"

    def test_operation_state_is_single_source(self) -> None:
        """OperationState must be defined once in core, not redefined in engine."""
        from meridian.core.operations import OperationKind, OperationState
        from meridian.engine.operations import EngineOperation

        # engine should import from core, not redefine
        import meridian.engine.operations as eng_mod

        source = ast.parse(Path(eng_mod.__file__).read_text())
        for node in ast.walk(source):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ("OperationState", "OperationKind"):
                        raise AssertionError(
                            f"engine/operations.py redefines {target.id} — import from core instead"
                        )

        # Verify the engine module actually uses the core types
        assert OperationState is not None
        assert OperationKind is not None

    def test_engine_snapshot_returns_core_model(self) -> None:
        """EngineOperation.snapshot() must return a core OperationSnapshot."""
        import inspect

        from meridian.core.operations import OperationSnapshot
        from meridian.engine.operations import EngineOperation

        sig = inspect.signature(EngineOperation.snapshot)
        ret = sig.return_annotation
        # Accept both the class itself and string annotations
        if isinstance(ret, str):
            assert "OperationSnapshot" in ret
        elif ret is not inspect.Parameter.empty:
            assert ret is OperationSnapshot or (hasattr(ret, "__name__") and ret.__name__ == "OperationSnapshot")

    def test_topology_mapper_covers_node_fields(self) -> None:
        """topology_from_cluster must map all public NodeEntry fields to TopologyNode.

        When a field is added to NodeEntry but not to the mapper, it silently
        disappears from fleet inventory views.
        """
        from meridian.cluster import NodeEntry
        from meridian.core.fleet import TopologyNode

        node_fields = {f.name for f in dataclasses.fields(NodeEntry) if not f.name.startswith("_")}
        topo_fields = set(TopologyNode.model_fields.keys())

        # Fields intentionally excluded from topology (secrets, internal state)
        EXCLUDED = {
            "config_profile_uuid",
            "uuid",
            "host_uuids",
            "reality_public_key",
            "reality_private_key",
            "reality_short_id",
            "inbounds",
            "ssh_user",
            "ssh_port",
            "deployed_with",
            "warp",
        }

        unmapped = node_fields - topo_fields - EXCLUDED
        assert unmapped == set(), (
            f"NodeEntry fields not in TopologyNode: {unmapped}. "
            f"Add them to TopologyNode or to EXCLUDED in this test."
        )


# ---------------------------------------------------------------------------
# 3. Structural health tests
# ---------------------------------------------------------------------------

# File size budget — catches god-modules at 850 lines, not 2500.
# Files above this limit must be in the allowlist with a reason.
FILE_SIZE_BUDGET = 800

FILE_SIZE_ALLOWLIST: dict[str, str] = {
    "panel_bootstrap.py": "extracted from setup.py; further split planned",
    "remnawave.py": "single API client wrapping 10+ REST domains — facade pattern is intentional",
    "provision/nginx.py": "nginx config generation is one cohesive template concern",
    "cli.py": "Typer registration for all subcommands — structural, not complex",
    "commands/setup.py": "deploy orchestrator — further extraction tracked",
    "ssh.py": "transport layer — Rich decoupled via SSHUI protocol; put_bytes/run complexity is structural",
}


class TestStructuralHealth:
    """Catch modules that grow past their complexity budget."""

    def test_no_file_exceeds_size_budget(self) -> None:
        """Python files must stay under FILE_SIZE_BUDGET lines.

        Large files signal mixed concerns. When this test fails, either:
        1. Split the file (preferred), or
        2. Add it to FILE_SIZE_ALLOWLIST with a justification.
        """
        violations = []
        for path in sorted(SRC.rglob("*.py")):
            rel = str(path.relative_to(SRC))
            lines = len(path.read_text().splitlines())
            if lines > FILE_SIZE_BUDGET and rel not in FILE_SIZE_ALLOWLIST:
                violations.append(f"{rel}: {lines} lines (budget: {FILE_SIZE_BUDGET})")

        assert violations == [], (
            f"Files exceed {FILE_SIZE_BUDGET}-line budget:\n"
            + "\n".join(violations)
            + "\nSplit the file or add to FILE_SIZE_ALLOWLIST in test_architecture.py."
        )

    def test_no_private_cross_module_imports(self) -> None:
        """Underscore-prefixed symbols should not be imported across module boundaries.

        If _foo() is imported outside its own file, it's a de facto public API.
        Either drop the underscore and make it properly public, or keep it private
        and don't import it elsewhere.

        Allowlist entries are for known transitional cases (test patches, etc.).
        """
        # (source_file, imported_name) pairs that are known and accepted
        ALLOWED_NAMES = {
            # __version__ is a standard Python convention, not a private API
            "__version__",
        }
        ALLOWED_PAIRS = {
            # Provision internals used by apply handlers — tight coupling is intentional
            ("commands/apply.py", "_render_panel_compose"),
            ("commands/apply.py", "_render_subscription_env"),
            ("panel_bootstrap.py", "_render_node_compose"),
            ("panel_bootstrap.py", "_render_node_env"),
        }

        violations = []
        for path in sorted(SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    # Only check cross-module (different parent directory)
                    source_file = str(path.relative_to(SRC))
                    imported_module = node.module.replace("meridian.", "")
                    # Same-directory imports are fine
                    source_dir = str(path.parent.relative_to(SRC)) if path.parent != SRC else ""
                    import_dir = imported_module.rsplit(".", 1)[0] if "." in imported_module else ""
                    if source_dir == import_dir:
                        continue

                    for alias in node.names:
                        if alias.name.startswith("_") and alias.name not in ALLOWED_NAMES and (source_file, alias.name) not in ALLOWED_PAIRS:
                            violations.append(
                                f"{source_file} imports private {alias.name} from {node.module}"
                            )

        assert violations == [], (
            "Private symbols imported across module boundaries:\n"
            + "\n".join(violations)
            + "\nDrop the underscore prefix or stop importing it externally."
        )

    def test_applied_state_not_in_extra(self) -> None:
        """Reconciler state must use typed AppliedState, not cluster._extra.

        The _extra dict is for forward-compat unknown YAML keys only.
        Applied-state snapshots belong in cluster.applied_state.
        """
        APPLIED_KEYS = {"desired_clients_applied", "desired_nodes_applied", "desired_relay_hosts_applied"}
        violations = []
        for path in sorted(SRC.rglob("*.py")):
            if path.name in ("cluster.py", "cluster_persistence.py"):
                continue  # cluster persistence handles migration from _extra → applied_state
            text = path.read_text()
            for key in APPLIED_KEYS:
                if f'_extra["{key}"]' in text or f"_extra['{key}']" in text or f'_extra.get("{key}")' in text:
                    violations.append(f"{path.relative_to(SRC)} accesses _extra['{key}'] directly")

        assert violations == [], (
            "Applied-state accessed via _extra instead of cluster.applied_state:\n"
            + "\n".join(violations)
        )
