"""Report dead, test-only, and unused-parameter code in src/meridian.

Deterministic, stdlib-only, ``ast``-based. Every definition in ``src/meridian``
(module-level function, class, constant; method, class attribute, instance
attribute) is a graph node. Edges come from references inside a node's body.
Liveness starts at production roots (module-level statements in src, every
reference in ``scripts/``, entry points, registration decorators, dunders,
allowlisted names) and is propagated through edges; a second pass adds every
reference in ``tests/`` as a root.

- ``dead``       — unreachable even from tests.
- ``test-only``  — reachable only once tests are roots (kept alive by its tests).
- ``unused-param`` — a parameter of a production-live function never read.

Module-level names resolve through imports (relative imports and package
re-exports included). Attribute access on anything that is not a module is
matched by name to every member with that name, and identifier-like strings
match any definition with that name (``getattr``, ``mock.patch.object``,
schema registries). Both over-approximate, so false positives stay rare.

Usage: ``python scripts/find_dead_code.py [--strict] [--allowlist PATH]``.
Exit 0 always, unless ``--strict`` and findings exist (exit 1).
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import tomllib
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE = "meridian"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_NAME = "scripts/dead_code_allowlist.txt"
ROOT_PROD = "<production>"
ROOT_TEST = "<tests>"

# Decorators that do not register a function anywhere; any other decorator
# (Typer ``@app.command``, FastAPI ``@router.get``, Pydantic validators) does.
TRANSPARENT_DECORATORS = frozenset(
    {
        "abstractmethod",
        "asynccontextmanager",
        "cache",
        "cached_property",
        "classmethod",
        "contextmanager",
        "dataclass",
        "deleter",
        "final",
        "getter",
        "lru_cache",
        "overload",
        "override",
        "property",
        "runtime_checkable",
        "setter",
        "staticmethod",
        "total_ordering",
        "wraps",
    }
)
# External bases whose class-level names are fields/members, not attributes.
DATA_BASES = frozenset(
    {"BaseModel", "RootModel", "TypedDict", "NamedTuple", "Enum", "StrEnum", "IntEnum", "Flag", "IntFlag", "Protocol"}
)
# External bases that never call subclass methods by name. Any other external
# base (TyperGroup, NodeVisitor, ...) may, so its subclasses' methods are live.
NO_OVERRIDE_BASES = DATA_BASES | frozenset(
    {
        "ABC",
        "BaseException",
        "Exception",
        "Generic",
        "KeyError",
        "LookupError",
        "OSError",
        "RuntimeError",
        "TypeError",
        "ValueError",
        "dict",
        "float",
        "int",
        "list",
        "object",
        "str",
        "tuple",
    }
)
IMPLICIT_METHODS = frozenset({"model_post_init"})
ATTR_BUILTINS = frozenset({"getattr", "hasattr", "setattr", "delattr"})
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DOTTED_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[.:][A-Za-z_][A-Za-z0-9_]*)+")
ANNOTATION_RE = re.compile(r"[A-Za-z_][\w.\[\], |]*")
JINJA_RE = re.compile(r"\{[{%](.*?)[%}]\}", re.S)

# Resolvers return ("module", dotted) / ("def", qualname) pairs: one local name
# may be imported from several places in different scopes.
Resolved = list[tuple[str, str]]


@dataclass
class Definition:
    qualname: str
    module: str
    name: str
    kind: str  # function | class | constant | method | attribute
    path: str
    line: int
    owner: str | None = None
    implicit: bool = False
    root: bool = False
    unused_params: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    qualname: str
    module: str
    bases: list[ast.expr]
    is_dataclass: bool
    method_names: set[str] = field(default_factory=set)


@dataclass
class Finding:
    path: str
    line: int
    category: str
    name: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.category}] {self.name}"


@dataclass
class SourceFile:
    path: Path
    rel: str
    module: str
    category: str  # src | test | script
    tree: ast.Module


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _registers(decorators: list[ast.expr]) -> bool:
    return any(_decorator_name(d) not in TRANSPARENT_DECORATORS for d in decorators)


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _is_sunder(name: str) -> bool:
    return len(name) > 2 and name[0] == name[-1] == "_" and name[1] != "_" and name[-2] != "_"


def _is_stub(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    for stmt in body:
        if isinstance(stmt, ast.Pass) or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)):
            continue
        if isinstance(stmt, ast.Raise) and stmt.exc is not None:
            exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                continue
        return False
    return True


def _unused_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = func.args
    params = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    used: set[str] = set()
    for stmt in func.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
                used.add(node.id)
    if used & {"locals", "vars"}:
        return []
    return [p for p in params if p not in used and not p.startswith("_") and p not in {"self", "cls"}]


def _module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _target_names(target: ast.expr) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _target_names(elt)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _assigned_names(stmt: ast.stmt) -> list[str]:
    if isinstance(stmt, ast.Assign):
        return [n for t in stmt.targets for n in _target_names(t)]
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        return list(_target_names(stmt.target))
    return []


def _nested_blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Statement lists that still execute at the enclosing (module/class) level."""
    if isinstance(stmt, ast.If):
        return [stmt.body, stmt.orelse]
    if isinstance(stmt, ast.Try):
        return [stmt.body, *(h.body for h in stmt.handlers), stmt.orelse, stmt.finalbody]
    if isinstance(stmt, ast.With):
        return [stmt.body]
    return []


class Analyzer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: list[SourceFile] = []
        self.modules: set[str] = set()
        self.defs: dict[str, Definition] = {}
        self.modlevel: dict[tuple[str, str], str] = {}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.members_by_name: dict[str, list[str]] = defaultdict(list)
        self.members_of: dict[str, list[str]] = defaultdict(list)
        self.classes: dict[str, ClassInfo] = {}
        self.bindings: dict[str, dict[str, list[tuple[str, ...]]]] = defaultdict(lambda: defaultdict(list))
        self.edges: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.value_used: set[str] = set()
        self.value_used_members: set[str] = set()
        self.stubs: set[str] = set()
        self.unpacked: list[list[str]] = []
        self.interface_methods: set[str] = set()

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        groups = (("src/meridian", "src"), ("tests", "test"), ("scripts", "script"))
        for top, category in groups:
            base = self.root / top
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.py")):
                if path.resolve() == Path(__file__).resolve():
                    continue  # this script's own strings name decorators, not code
                rel = path.relative_to(self.root)
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
                module = _module_name(rel)
                self.files.append(SourceFile(path, rel.as_posix(), module, category, tree))
                self.modules.add(module)
        for source in self.files:
            self._collect_bindings(source)
        for source in self.files:
            if source.category == "src":
                self._collect_definitions(source)
        self._classify_classes()
        self._mark_entry_points()

    def _resolve_relative(self, source: SourceFile, module: str | None, level: int) -> str:
        if level == 0:
            return module or ""
        package = source.module if source.path.name == "__init__.py" else source.module.rpartition(".")[0]
        parts = package.split(".")
        base = ".".join(parts[: len(parts) - (level - 1)])
        return f"{base}.{module}" if module else base

    def _collect_bindings(self, source: SourceFile) -> None:
        table = self.bindings[source.module]
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local, target = (alias.asname, alias.name) if alias.asname else (alias.name.split(".")[0],) * 2
                    if ("module", target) not in table[local]:
                        table[local].append(("module", target))
            elif isinstance(node, ast.ImportFrom):
                target = self._resolve_relative(source, node.module, node.level)
                for alias in node.names:
                    entry = ("from", target, alias.name)
                    if alias.name != "*" and entry not in table[alias.asname or alias.name]:
                        table[alias.asname or alias.name].append(entry)

    def _add(self, definition: Definition) -> Definition:
        existing = self.defs.get(definition.qualname)
        if existing is not None:
            existing.implicit = existing.implicit or definition.implicit
            existing.root = existing.root or definition.root
            return existing
        self.defs[definition.qualname] = definition
        if definition.owner:
            self.members_by_name[definition.name].append(definition.qualname)
            self.members_of[definition.owner].append(definition.qualname)
        else:
            self.modlevel[(definition.module, definition.name)] = definition.qualname
        self.by_name[definition.name].append(definition.qualname)
        return definition

    def _collect_definitions(self, source: SourceFile) -> None:
        mod = source.module

        def walk(body: list[ast.stmt]) -> None:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    d = self._add(Definition(f"{mod}.{stmt.name}", mod, stmt.name, "function", source.rel, stmt.lineno))
                    d.root = d.root or _registers(stmt.decorator_list) or _is_dunder(stmt.name)
                    d.unused_params = _unused_params(stmt)
                    if _is_stub(stmt):
                        self.stubs.add(d.qualname)
                elif isinstance(stmt, ast.ClassDef):
                    self._collect_class(source, stmt)
                elif names := [n for n in _assigned_names(stmt) if not _is_dunder(n)]:
                    for name in names:
                        self._add(Definition(f"{mod}.{name}", mod, name, "constant", source.rel, stmt.lineno))
                    if len(names) > 1:  # unpacking: a live sibling keeps the others
                        self.unpacked.append([f"{mod}.{name}" for name in names])
                for block in _nested_blocks(stmt):
                    walk(block)

        walk(source.tree.body)

    def _collect_class(self, source: SourceFile, node: ast.ClassDef) -> None:
        mod = source.module
        q = f"{mod}.{node.name}"
        self._add(Definition(q, mod, node.name, "class", source.rel, node.lineno))
        info = ClassInfo(q, mod, list(node.bases), any(_decorator_name(d) == "dataclass" for d in node.decorator_list))
        self.classes[q] = info

        def walk(body: list[ast.stmt]) -> None:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = stmt.name
                    d = self._add(Definition(f"{q}.{name}", mod, name, "method", source.rel, stmt.lineno, owner=q))
                    d.implicit = d.implicit or _registers(stmt.decorator_list)
                    d.unused_params = _unused_params(stmt)
                    if _is_stub(stmt) or any(_decorator_name(x) == "abstractmethod" for x in stmt.decorator_list):
                        self.stubs.add(d.qualname)
                    info.method_names.add(name)
                    self._collect_instance_attrs(source, stmt, info)
                elif isinstance(stmt, ast.ClassDef):
                    self._add(Definition(f"{q}.{stmt.name}", mod, stmt.name, "class", source.rel, stmt.lineno, owner=q))
                else:
                    for name in _assigned_names(stmt):
                        self._add(Definition(f"{q}.{name}", mod, name, "attribute", source.rel, stmt.lineno, owner=q))
                for block in _nested_blocks(stmt):
                    walk(block)

        walk(node.body)

    def _collect_instance_attrs(self, source: SourceFile, func: ast.AST, info: ClassInfo) -> None:
        for sub in ast.walk(func):
            targets: list[ast.expr] = []
            if isinstance(sub, ast.Assign):
                targets = list(sub.targets)
            elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)):
                targets = [sub.target]
            for target in targets:
                elts = target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
                for elt in elts:
                    if (
                        isinstance(elt, ast.Attribute)
                        and isinstance(elt.value, ast.Name)
                        and elt.value.id == "self"
                        and f"{info.qualname}.{elt.attr}" not in self.defs
                    ):
                        self._add(
                            Definition(
                                f"{info.qualname}.{elt.attr}",
                                info.module,
                                elt.attr,
                                "attribute",
                                source.rel,
                                elt.lineno,
                                owner=info.qualname,
                            )
                        )

    def _base_info(self, info: ClassInfo) -> tuple[list[str], list[str]]:
        """Return (src base qualnames, external base names) for a class."""
        local: list[str] = []
        external: list[str] = []
        for base in info.bases:
            expr = base.value if isinstance(base, ast.Subscript) else base
            found = [q for kind, q in self.resolve_expr(info.module, expr) if kind == "def" and q in self.classes]
            if found:
                local.extend(found)
                continue
            name = ""
            if isinstance(expr, ast.Name):
                imported = [b[2] for b in self.bindings[info.module].get(expr.id, []) if b[0] == "from"]
                name = imported[0] if imported else expr.id
            elif isinstance(expr, ast.Attribute):
                name = expr.attr
            external.append(name)
        return local, external

    def _classify_classes(self) -> None:
        data_like: dict[str, bool] = {}
        overrides: dict[str, bool] = {}

        def classify(q: str, seen: frozenset[str] = frozenset()) -> tuple[bool, bool]:
            if q in data_like:
                return data_like[q], overrides[q]
            info = self.classes[q]
            local, external = self._base_info(info)
            if {"Protocol", "ABC"} & set(external):
                self.interface_methods |= info.method_names
            data = info.is_dataclass or any(e in DATA_BASES for e in external)
            over = any(e not in NO_OVERRIDE_BASES for e in external)
            for base in local:
                if base not in seen and base != q:
                    d, o = classify(base, seen | {q})
                    data, over = data or d, over or o
            data_like[q], overrides[q] = data, over
            return data, over

        for q in sorted(self.classes):
            data, over = classify(q)
            for member in list(self.members_of.get(q, [])):
                d = self.defs[member]
                if data and d.kind == "attribute":
                    self._drop(member)
                    continue
                if _is_dunder(d.name) or _is_sunder(d.name) or d.name in IMPLICIT_METHODS:
                    d.implicit = True
                if over and d.kind == "method":
                    d.implicit = True

    def _drop(self, qualname: str) -> None:
        d = self.defs.pop(qualname)
        self.members_by_name[d.name].remove(qualname)
        self.members_of[d.owner or ""].remove(qualname)
        self.by_name[d.name].remove(qualname)

    def _mark_entry_points(self) -> None:
        pyproject = self.root / "pyproject.toml"
        if not pyproject.is_file():
            return
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        targets = list(project.get("scripts", {}).values())
        for group in project.get("entry-points", {}).values():
            targets.extend(group.values())
        for target in targets:
            module, _, attr = str(target).partition(":")
            for kind, q in self.resolve_symbol(module.strip(), attr.strip().split(".")[0]):
                if kind == "def":
                    self.defs[q].root = True

    # -- resolution ------------------------------------------------------
    def resolve_binding(self, binding: tuple[str, ...], seen: frozenset[tuple[str, str]]) -> Resolved:
        if binding[0] == "module":
            return [("module", binding[1])]
        module, name = binding[1], binding[2]
        if f"{module}.{name}" in self.modules:
            return [("module", f"{module}.{name}")]
        return self.resolve_symbol(module, name, seen)

    def resolve_symbol(self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()) -> Resolved:
        key = (module, name)
        if key in seen:
            return []
        if key in self.modlevel:
            return [("def", self.modlevel[key])]
        bindings = self.bindings.get(module, {}).get(name, [])
        if bindings:
            return [r for b in bindings for r in self.resolve_binding(b, seen | {key})]
        if f"{module}.{name}" in self.modules:
            return [("module", f"{module}.{name}")]
        return []

    def resolve_expr(self, module: str, expr: ast.expr) -> Resolved:
        if isinstance(expr, ast.Name):
            bindings = self.bindings.get(module, {}).get(expr.id, [])
            found = [r for b in bindings for r in self.resolve_binding(b, frozenset())]
            if (module, expr.id) in self.modlevel:
                found.append(("def", self.modlevel[(module, expr.id)]))
            return found
        if isinstance(expr, ast.Attribute):
            return [
                r
                for kind, base in self.resolve_expr(module, expr.value)
                if kind == "module"
                for r in self.resolve_symbol(base, expr.attr)
            ]
        return []

    def resolve_path(self, path: str) -> Resolved:
        parts = re.split(r"[.:]", path)
        for cut in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:cut])
            if module in self.modules:
                return self.resolve_symbol(module, parts[cut])
        return []

    # -- references ------------------------------------------------------
    def collect_references(self) -> None:
        for source in self.files:
            root = ROOT_TEST if source.category == "test" else ROOT_PROD
            collector = ReferenceCollector(self, source, root)
            if source.category == "src":
                collector.module_body(source.tree.body)
            else:
                collector.visit(source.tree)
        for group in self.unpacked:
            for name in group:
                self.edges[name].update(("def", other) for other in group if other != name)
        for path in sorted((self.root / "src" / PACKAGE / "templates").rglob("*.j2")):
            for block in JINJA_RE.findall(path.read_text(encoding="utf-8")):
                for token in IDENT_RE.findall(block):
                    self.edges[ROOT_PROD].add(("member", token))

    def targets(self, spec: tuple[str, str]) -> Iterable[str]:
        kind, value = spec
        if kind == "def":
            return (value,)
        if kind == "member":
            return self.members_by_name.get(value, ())
        return self.by_name.get(value, ())

    # -- liveness --------------------------------------------------------
    def liveness(self, extra_roots: set[str]) -> tuple[set[str], set[str]]:
        live: set[str] = set()
        pending: dict[str, set[str]] = defaultdict(set)
        queue: deque[str] = deque()

        def mark(q: str) -> None:
            if q in live or q not in self.defs:
                return
            owner = self.defs[q].owner
            if owner and owner not in live:
                pending[owner].add(q)
                return
            live.add(q)
            queue.append(q)
            for member in self.members_of.get(q, ()):
                if self.defs[member].implicit:
                    mark(member)
            for member in sorted(pending.pop(q, ())):
                mark(member)

        def drain() -> None:
            while queue:
                node = queue.popleft()
                for spec in sorted(self.edges.get(node, ())):
                    for target in self.targets(spec):
                        mark(target)

        for q in sorted(self.defs):
            if self.defs[q].root or q in extra_roots:
                mark(q)
        for spec in sorted(self.edges.get(ROOT_PROD, ())):
            for target in self.targets(spec):
                mark(target)
        drain()
        prod = set(live)
        for spec in sorted(self.edges.get(ROOT_TEST, ())):
            for target in self.targets(spec):
                mark(target)
        drain()
        return prod, live

    def findings(self, extra_roots: set[str]) -> tuple[list[Finding], set[str]]:
        prod, live = self.liveness(extra_roots)
        unreported = set()
        found: list[Finding] = []
        for q, d in sorted(self.defs.items()):
            if q in prod:
                continue
            unreported.add(q)
            if d.owner and d.owner not in prod:
                continue  # reported through its class
            found.append(Finding(d.path, d.line, "test-only" if q in live else "dead", q))
        family: dict[str, int] = defaultdict(int)
        for info in self.classes.values():
            for name in info.method_names:
                family[name] += 1
        for q in sorted(prod):
            d = self.defs[q]
            if not d.unused_params or q in self.stubs or d.implicit or d.root or q in self.value_used:
                continue
            if d.kind == "method" and (
                family[d.name] > 1 or d.name in self.interface_methods or d.name in self.value_used_members
            ):
                continue
            for param in d.unused_params:
                found.append(Finding(d.path, d.line, "unused-param", f"{q}({param})"))
        return found, unreported


class ReferenceCollector(ast.NodeVisitor):
    def __init__(self, analyzer: Analyzer, source: SourceFile, root: str) -> None:
        self.a = analyzer
        self.source = source
        self.module = source.module
        self.node = root
        self.called: set[int] = set()

    def emit(self, spec: tuple[str, str]) -> None:
        self.a.edges[self.node].add(spec)

    def under(self, node_name: str, items: Iterable[ast.AST]) -> None:
        saved, self.node = self.node, node_name
        for item in items:
            self.visit(item)
        self.node = saved

    @staticmethod
    def _signature_parts(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
        args = func.args
        parts: list[ast.AST] = [*func.decorator_list, *args.defaults]
        parts.extend(d for d in args.kw_defaults if d is not None)
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
            if arg is not None and arg.annotation is not None:
                parts.append(arg.annotation)
        if func.returns is not None:
            parts.append(func.returns)
        return parts

    def module_body(self, body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.under(f"{self.module}.{stmt.name}", [*self._signature_parts(stmt), *stmt.body])
            elif isinstance(stmt, ast.ClassDef):
                self.class_body(f"{self.module}.{stmt.name}", stmt)
            elif (names := _assigned_names(stmt)) and {"__all__", "__slots__"} & set(names):
                continue  # declarations, not references
            elif names and any(not _is_dunder(n) for n in names):
                target = next(n for n in names if not _is_dunder(n))
                self.under(f"{self.module}.{target}", [stmt])
            elif blocks := _nested_blocks(stmt):
                header: list[ast.AST] = [stmt.test] if isinstance(stmt, ast.If) else []
                if isinstance(stmt, ast.With):
                    header = [*stmt.items]
                elif isinstance(stmt, ast.Try):
                    header = [h.type for h in stmt.handlers if h.type is not None]
                for item in header:
                    self.visit(item)
                for block in blocks:
                    self.module_body(block)
            else:
                self.visit(stmt)

    def class_body(self, q: str, node: ast.ClassDef) -> None:
        self.under(q, [*node.decorator_list, *node.bases, *node.keywords])

        def walk(body: list[ast.stmt]) -> None:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.under(f"{q}.{stmt.name}", [*self._signature_parts(stmt), *stmt.body])
                elif isinstance(stmt, ast.ClassDef):
                    self.under(f"{q}.{stmt.name}", [stmt])
                elif (names := _assigned_names(stmt)) and "__slots__" in names:
                    continue
                elif names and f"{q}.{names[0]}" in self.a.defs:
                    self.under(f"{q}.{names[0]}", [stmt])
                elif blocks := _nested_blocks(stmt):
                    walk([s for block in blocks for s in block])
                else:
                    self.under(q, [stmt])

        walk(node.body)

    # -- visitors ----------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        self.called.add(id(node.func))
        func = node.func
        if isinstance(func, ast.Name) and func.id in ATTR_BUILTINS and len(node.args) >= 2:
            name = node.args[1]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                for kind, module in self.a.resolve_expr(self.module, node.args[0]):
                    if kind == "module":
                        self.emit_defs(self.a.resolve_symbol(module, name.value), node)
        self.generic_visit(node)

    def emit_defs(self, resolved: list[tuple[str, str]], node: ast.expr) -> None:
        for kind, q in resolved:
            if kind == "def":
                self.emit((kind, q))
                if id(node) not in self.called:
                    self.a.value_used.add(q)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Store):
            self.emit_defs(self.a.resolve_expr(self.module, node), node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        modules = [base for kind, base in self.a.resolve_expr(self.module, node.value) if kind == "module"]
        if modules:
            for module in modules:
                self.emit_defs(self.a.resolve_symbol(module, node.attr), node)
        else:
            receiver_is_self = isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")
            if not isinstance(node.ctx, ast.Store) or not receiver_is_self:
                self.emit(("member", node.attr))
                if id(node) not in self.called and not isinstance(node.ctx, ast.Store):
                    self.a.value_used_members.add(node.attr)
        self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Attribute):
            self.emit(("member", node.target.attr))
        elif isinstance(node.target, ast.Name):
            self.visit(ast.Name(id=node.target.id, ctx=ast.Load()))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str) or len(node.value) > 200:
            return
        text = node.value.strip()
        # Tests reach module attributes by string (monkeypatch.setattr, patch.object):
        # match any definition. Production strings match members only, so a
        # Literal value or dict key never keeps a same-named function alive.
        by_name = "any" if self.source.category == "test" else "member"
        if IDENT_RE.fullmatch(text):
            self.emit((by_name, text))
            self.emit_defs(self.a.resolve_expr(self.module, ast.Name(id=text, ctx=ast.Load())), node)
        elif DOTTED_RE.fullmatch(text):
            self.emit_defs(self.a.resolve_path(text), node)
            self.emit((by_name, re.split(r"[.:]", text)[-1]))
        elif ANNOTATION_RE.fullmatch(text):
            try:
                expr = ast.parse(text, mode="eval")
            except SyntaxError:
                return
            self.visit(expr)


def load_allowlist(path: Path) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    if not path.is_file():
        return entries
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        name, _, reason = raw.partition("#")
        if name.strip():
            entries.append((number, name.strip(), reason.strip()))
    return entries


def allowlist_matches(entry: str, name: str) -> bool:
    """Exact names match a definition (a class covers its members); ``pkg.*`` matches a prefix."""
    if entry.endswith(".*"):
        return name.startswith(entry[:-1])
    return name == entry or name.startswith(f"{entry}.")


def analyze(root: Path, allowlist: Path | None = None) -> list[Finding]:
    """Return sorted findings (including allowlist problems) for the tree at ``root``."""
    allowlist_path = allowlist if allowlist is not None else root / ALLOWLIST_NAME
    entries = load_allowlist(allowlist_path)
    analyzer = Analyzer(root)
    analyzer.load()
    analyzer.collect_references()
    raw, unreported = analyzer.findings(set())
    candidates = unreported | {f.name for f in raw}
    allowed = [name for _, name, _ in entries]
    roots: set[str] = set()
    for entry in allowed:
        if entry.endswith(".*"):
            roots.update(q for q in analyzer.defs if q.startswith(entry[:-1]))
        elif entry in analyzer.defs:
            roots.add(entry)
            roots.update(analyzer.members_of.get(entry, ()))
    found, _ = analyzer.findings(roots) if roots else (raw, unreported)
    results = [f for f in found if not any(allowlist_matches(entry, f.name) for entry in allowed)]
    try:
        allow_rel = allowlist_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        allow_rel = str(allowlist_path)
    for number, name, reason in entries:
        if not any(allowlist_matches(name, candidate) for candidate in candidates):
            results.append(Finding(allow_rel, number, "stale-allowlist", name))
        elif not reason:
            results.append(Finding(allow_rel, number, "allowlist-no-reason", name))
    return sorted(results, key=lambda f: (f.path, f.line, f.category, f.name))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository root")
    parser.add_argument("--allowlist", type=Path, default=None, help=f"default: <root>/{ALLOWLIST_NAME}")
    parser.add_argument("--strict", action="store_true", help="exit 1 when findings exist")
    args = parser.parse_args(argv)

    findings = analyze(args.root, args.allowlist)
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for finding in findings:
        print(finding.render())
        if annotate:
            location = f"file={finding.path},line={finding.line}"
            print(f"::warning {location},title=dead code::[{finding.category}] {finding.name}")
    counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        counts[finding.category] += 1
    order = ("dead", "test-only", "unused-param", "stale-allowlist", "allowlist-no-reason")
    summary = ", ".join(f"{counts[c]} {c}" for c in order if counts[c] or c in order[:3])
    print(f"dead-code: {summary}")
    return 1 if args.strict and findings else 0


if __name__ == "__main__":
    sys.exit(main())
