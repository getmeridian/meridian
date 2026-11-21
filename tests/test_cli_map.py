"""The developer command map must render with Meridian's installed CLI parser."""

from scripts.cli_map import collect, render


def test_map_renders_argument_names_and_inferred_option_types() -> None:
    commands = collect()
    node_add = next(command for command in commands if command.path == "node add")
    assert any(param.kind == "argument" and param.name == "SERVER" and param.required for param in node_add.params)
    port = next(param for param in node_add.params if param.name == "--ssh-port")
    assert "int" in port.metavar.lower()
    assert "meridian node add" in render(commands)
