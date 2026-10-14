"""The public command tree, read from the live Typer app and joined with core's output contracts.

Arguments, options, and help come from the commands themselves, so the catalog that
`meridian api commands` and `contracts/meridian/v1/commands.json` publish cannot drift
from the CLI; `core.command_catalog.COMMAND_OUTPUTS` adds only what Typer cannot know.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typer.main import get_command

from meridian import cli_types as click

if TYPE_CHECKING:
    from meridian.core.command_catalog import CommandContract


def cli_root() -> click.Group:
    """The root Click group of the `meridian` CLI."""
    from meridian.cli import app

    root = get_command(app)
    if not isinstance(root, click.Group):
        raise TypeError("Meridian CLI root is not a Click group")
    return root


def public_commands(root: click.Group | None = None) -> dict[str, click.Command]:
    """Every non-hidden leaf command, keyed by its dotted path (`client.add`)."""
    leaves: dict[str, click.Command] = {}

    def walk(group: click.Group, prefix: tuple[str, ...]) -> None:
        for name, command in group.commands.items():
            if command.hidden:
                continue
            path = (*prefix, name)
            if isinstance(command, click.Group):
                walk(command, path)
            else:
                leaves[".".join(path)] = command

    walk(root or cli_root(), ())
    return leaves


def long_options(command: click.Command, *, include_hidden: bool = True) -> list[str]:
    """Long-form options a command accepts, in declaration order (`--no-x` forms included)."""
    flags: list[str] = []
    for param in command.params:
        if isinstance(param, click.Option) and (include_hidden or not param.hidden):
            flags.extend(opt for opt in (*param.opts, *param.secondary_opts) if opt.startswith("--"))
    return flags


def supports_json(command: click.Command) -> bool:
    """A command emits a JSON envelope exactly when it declares `--json`."""
    return "--json" in long_options(command)


def command_path(ctx: click.Context, args: list[str]) -> tuple[list[str], click.Command | None, list[str]]:
    """Walk `args` from `ctx.command` down to the command they name.

    Returns the names consumed (including an unknown last name), the command reached
    (None when the last name is unknown), and the arguments left for it.
    """
    command: click.Command | None = ctx.command
    path: list[str] = []
    rest = list(args)
    while isinstance(command, click.Group):
        index = next((i for i, arg in enumerate(rest) if not arg.startswith("-")), None)
        if index is None:
            break
        path.append(rest[index])
        rest = rest[index + 1 :]
        command = command.get_command(ctx, path[-1])
    return path, command, rest


def requests_machine_output(ctx: click.Context, name: str, command: click.Command, args: list[str]) -> bool:
    """Whether `args` turn on one of the command's machine-output options.

    Click parses them leniently (no callbacks fire, errors are ignored), so value-taking
    options never pass for flags; machine-output options are those whose callback is
    `cli_options.enable_json_output`.
    """
    from meridian.cli_options import enable_json_output

    parsed = command.make_context(name, list(args), parent=ctx, resilient_parsing=True)
    return any(
        isinstance(param, click.Option)
        and getattr(param.callback, "__wrapped__", param.callback) is enable_json_output
        and bool(parsed.params.get(param.name or ""))
        for param in command.params
    )


def command_argv(path: str, command: click.Command) -> list[str]:
    """The command path plus its arguments: `NAME`, `NAME...` when repeated, `[NAME]` when optional."""
    argv = path.split(".")
    for param in command.params:
        if not isinstance(param, click.Argument):
            continue
        name = (param.metavar or param.name or "").upper()
        if param.nargs == -1:
            name += "..."
        argv.append(name if param.required else f"[{name}]")
    return argv


def _summary(command: click.Command) -> str:
    lines = (command.help or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def command_contracts(root: click.Group | None = None) -> list[CommandContract]:
    """Contracts of every JSON command, in stable command-name order."""
    from meridian.core.command_catalog import COMMAND_OUTPUTS, command_contract

    contracts = []
    for path, command in public_commands(root).items():
        output = COMMAND_OUTPUTS.get(path)
        if output is not None:
            contracts.append(
                command_contract(
                    path,
                    output,
                    argv=command_argv(path, command),
                    machine_flags=long_options(command),
                    description=_summary(command),
                )
            )
    return sorted(contracts, key=lambda contract: contract.command)


def command_catalog(*, include_schemas: bool = False) -> list[dict[str, Any]]:
    """Command contract metadata, optionally embedding envelope, data, failure, and error schemas."""
    from meridian.core.command_catalog import CommandCatalogEntry
    from meridian.core.schema import schema_for

    catalog: list[dict[str, Any]] = []
    for contract in command_contracts():
        entry = CommandCatalogEntry(
            **contract.model_dump(mode="json"),
            envelope=schema_for(contract.envelope_schema) if include_schemas else None,
            data=schema_for(contract.data_schema) if include_schemas else None,
            failure_data=schema_for(contract.failure_data_schema) if include_schemas else None,
            error=schema_for(contract.error_schema) if include_schemas else None,
        )
        catalog.append(entry.model_dump(mode="json", by_alias=True, exclude_none=True))
    return catalog
