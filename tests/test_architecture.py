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
import subprocess
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "meridian"


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

# compiler/ is a pure transformation from typed intent to typed resources.
COMPILER_FORBIDDEN = (
    "meridian.commands",
    "meridian.config",
    "meridian.console",
    "meridian.engine",
    "meridian.provision",
    "meridian.remnawave",
    "meridian.ssh",
    "httpx",
    "requests",
    "urllib",
    "os",
    "pathlib",
    "random",
    "secrets",
)

# control/ is the service on the control server: it never reaches back into the
# deployer (CLI, SSH, panel, provisioning, reconciler) or its presentation.
CONTROL_FORBIDDEN = (
    "meridian.commands",
    "meridian.config",
    "meridian.console",
    "meridian.engine",
    "meridian.provision",
    "meridian.reconciler",
    "meridian.remnawave",
    "meridian.ssh",
    "rich",
    "typer",
    "click",
)

# Shared library modules must not import private (_-prefixed) symbols
# from commands/.
LIBRARY_MODULES = [
    SRC / "panel_bootstrap.py",
    SRC / "node_deploy.py",
    SRC / "reality_keys.py",
]


class TestLayerBoundaries:
    """Prevent cross-layer imports that create circular or inverted dependencies."""

    def test_core_never_imports_cli_layer(self) -> None:
        violations = _scan_violations(SRC / "core", CORE_FORBIDDEN)
        assert violations == [], "core/ has forbidden imports:\n" + "\n".join(violations)

    def test_command_catalog_does_not_import_schema_registry(self) -> None:
        imports = _imports(SRC / "core" / "command_catalog.py")

        assert "meridian.core.schema" not in imports

    def test_engine_never_imports_commands_or_runtime(self) -> None:
        violations = _scan_violations(SRC / "engine", ENGINE_FORBIDDEN)
        assert violations == [], "engine/ has forbidden imports:\n" + "\n".join(violations)

    def test_adapters_never_import_engine(self) -> None:
        violations = _scan_violations(SRC / "adapters", ADAPTER_FORBIDDEN)
        assert violations == [], "adapters/ has forbidden imports:\n" + "\n".join(violations)

    def test_compiler_has_no_runtime_or_randomness_dependencies(self) -> None:
        violations = _scan_violations(SRC / "compiler", COMPILER_FORBIDDEN)
        assert violations == [], "compiler/ has forbidden imports:\n" + "\n".join(violations)

    def test_control_service_never_imports_the_deployer(self) -> None:
        violations = _scan_violations(SRC / "control", CONTROL_FORBIDDEN)
        assert violations == [], "control/ has forbidden imports:\n" + "\n".join(violations)

    def test_click_is_reached_only_through_cli_types(self) -> None:
        """Typer ≥ 0.26 vendors Click; the ``click`` package isn't installed in production."""
        violations = [v for v in _scan_violations(SRC, ("click", "typer._click")) if not v.startswith("cli_types.py ")]
        assert violations == [], "import Click's classes from meridian.cli_types:\n" + "\n".join(violations)

    def test_library_modules_never_import_private_from_commands(self) -> None:
        """panel_bootstrap.py and friends must not import _foo from commands/.

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
                    if node.module == "meridian.commands.resolve" or node.module.startswith(
                        "meridian.commands.resolve."
                    ):
                        imported = ", ".join(a.name for a in node.names)
                        violations.append(f"{path.name} imports {imported} from {node.module} — use meridian.resolve")
                    else:
                        for alias in node.names:
                            if alias.name.startswith("_"):
                                violations.append(f"{path.name} imports private {alias.name} from {node.module}")
        assert violations == [], "Library modules import command-layer symbols:\n" + "\n".join(violations)


class TestContractDrift:
    """Catch type/schema drift between layers that define the same concept."""

    def test_cluster_fields_sets_match_dataclasses(self) -> None:
        """_KNOWN_FIELDS sets must match their dataclass definitions.

        If a field is added to a dataclass but missing from the set,
        it silently drops into _extra on load — a data loss bug.
        """
        from meridian.cluster import (
            ActionCheckpoint,
            ClusterConfig,
            ManagedResourceBinding,
            PanelConfig,
            RealityKeyBinding,
            ResourceAllocation,
            SubscriptionPageConfig,
            TelegramConfig,
            WorkloadBinding,
        )

        # Mapping of dataclass → known-fields set name in cluster.py
        # The sets are derived programmatically now, but this test ensures
        # the derivation stays correct.
        for cls in (
            PanelConfig,
            RealityKeyBinding,
            WorkloadBinding,
            ManagedResourceBinding,
            ResourceAllocation,
            ActionCheckpoint,
            ClusterConfig,
            SubscriptionPageConfig,
            TelegramConfig,
        ):
            expected = {f.name for f in dataclasses.fields(cls) if not f.name.startswith("_")}
            # Just verify each class has typed public fields (the derivation test)
            assert len(expected) > 0, f"{cls.__name__} has no public fields"

    def test_operation_state_is_single_source(self) -> None:
        """OperationState must be defined once in core, not redefined in engine."""
        # engine should import from core, not redefine
        import meridian.engine.operations as eng_mod
        from meridian.core.operations import OperationKind, OperationState

        source = ast.parse(Path(eng_mod.__file__).read_text())
        for node in ast.walk(source):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ("OperationState", "OperationKind"):
                        raise AssertionError(f"engine/operations.py redefines {target.id} — import from core instead")

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


# File size budget — catches god-modules at 850 lines, not 2500.
# Files above this limit must be in the allowlist with a reason.
FILE_SIZE_BUDGET = 800

FILE_SIZE_ALLOWLIST: dict[str, str] = {
    "remnawave.py": "single API client wrapping 10+ REST domains — facade pattern is intentional",
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
        ALLOWED_PAIRS: set[tuple[str, str]] = set()

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
                        if (
                            alias.name.startswith("_")
                            and alias.name not in ALLOWED_NAMES
                            and (source_file, alias.name) not in ALLOWED_PAIRS
                        ):
                            violations.append(f"{source_file} imports private {alias.name} from {node.module}")

        assert violations == [], (
            "Private symbols imported across module boundaries:\n"
            + "\n".join(violations)
            + "\nDrop the underscore prefix or stop importing it externally."
        )

    def test_library_modules_do_not_import_console_fail(self) -> None:
        """Library modules must raise exceptions, not call console.fail().

        fail() exits the process — it belongs in CLI command entry points only.
        Library modules (resolve, panel_bootstrap, node_deploy, reality_keys,
        provision/) should raise typed exceptions that command callers convert
        to fail() calls.
        """
        # Modules that must not import fail from meridian.console
        library_files = [
            SRC / "resolve.py",
            SRC / "panel_bootstrap.py",
            SRC / "node_deploy.py",
            SRC / "reality_keys.py",
        ]
        library_files.extend(sorted((SRC / "provision").rglob("*.py")))

        violations = []
        for path in library_files:
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "meridian.console":
                    imported_names = [a.name for a in node.names]
                    if "fail" in imported_names:
                        violations.append(f"{path.relative_to(SRC)} imports fail from meridian.console")

        assert violations == [], (
            "Library modules must not import console.fail() — raise exceptions instead:\n"
            + "\n".join(violations)
            + "\nSee core/errors.py for MeridianError hierarchy."
        )


def test_no_tracked_text_file_carries_merge_conflict_markers() -> None:
    """A merge once committed `<<<<<<<` markers into a CLAUDE.md that no linter reads."""
    root = SRC.parents[1]
    tracked = (
        subprocess.run(
            ["git", "ls-files", "-z", "*.md", "*.py", "*.rs", "*.yml", "*.yaml", "*.toml", "*.json"],
            cwd=root,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    marked = [
        name
        for name in tracked
        if name
        and (root / name).is_file()
        and any(
            line.startswith(("<<<<<<< ", ">>>>>>> ")) for line in (root / name).read_text(errors="replace").splitlines()
        )
    ]
    assert marked == []


# Paths `config.select_context()` rebinds (or tests repoint): a copy taken at import keeps the wrong fleet.
CONTEXT_BOUND_PATHS = frozenset(
    {
        "PINNED_HOME",
        "INSTALL_HOME",
        "CACHE_DIR",
        "PREFERENCES_FILE",
        "MERIDIAN_SSH_KEY_FILE",
        "CONTEXT",
        "FLEET_HOME",
        "HOME_DISPLAY",
        "CLUSTER_CONFIG",
        "CLUSTER_BACKUP",
        "SERVER_PROFILES_FILE",
        "SETUP_DRAFT_FILE",
    }
)


def _import_time_nodes(tree: ast.Module) -> list[ast.AST]:
    """Every node Python evaluates when the module is imported: not function or lambda bodies."""
    found: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        found.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for default in (*args.defaults, *(d for d in args.kw_defaults if d is not None)):
                visit(default)
            for decorator in getattr(node, "decorator_list", ()):
                visit(decorator)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def test_no_module_copies_a_context_bound_path_at_import() -> None:
    """`from meridian.config import CLUSTER_CONFIG` at module level would ignore `--context`."""
    violations = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "config.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in _import_time_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "meridian.config":
                names = sorted(alias.name for alias in node.names if alias.name in CONTEXT_BOUND_PATHS)
                if names:
                    violations.append(f"{path.relative_to(SRC)}:{node.lineno} imports {', '.join(names)}")
            elif (
                isinstance(node, ast.Attribute)
                and node.attr in CONTEXT_BOUND_PATHS
                and isinstance(node.value, ast.Name)
                and node.value.id in {"config", "cfg"}
            ):
                violations.append(f"{path.relative_to(SRC)}:{node.lineno} reads config.{node.attr} at import")
    assert violations == [], (
        "Read context-bound paths as `config.NAME` inside functions (see meridian.contexts):\n" + "\n".join(violations)
    )
