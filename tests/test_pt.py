"""Tests for the pt command, which dispatches to each tool."""

import importlib

import pytest

from papertools import __version__
from papertools.cli import COMMANDS, main


def test_help_lists_commands(capsys):
    """List every command with its description."""
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: pt <command>")
    for name, (_, description) in COMMANDS.items():
        assert f"{name}  " in out
        assert description in out


def test_no_arguments_prints_usage(capsys):
    """Exit with usage status when no command is given."""
    assert main([]) == 2
    assert "usage: pt" in capsys.readouterr().err


def test_version(capsys):
    """Print the package version."""
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"pt {__version__}"


def test_unknown_command_suggests(capsys):
    """Suggest the closest command for a typo."""
    assert main(["figpace"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'figpace'" in err
    assert "did you mean 'figspace'?" in err


def test_dispatch_passes_arguments(tmp_path):
    """Run the tool with the remaining arguments."""
    assert main(["figspace", "--page-limit", "0", str(tmp_path / "x.pdf")]) == 2


def test_tool_help_names_subcommand(capsys):
    """Show 'pt <tool>' in a tool's usage line."""
    with pytest.raises(SystemExit) as exc:
        main(["texabstract", "--help"])
    assert exc.value.code == 0
    assert "pt texabstract" in capsys.readouterr().out


@pytest.mark.parametrize("name", COMMANDS)
def test_commands_import(name):
    """Point every command at a module with a main function."""
    module = importlib.import_module(COMMANDS[name][0])
    assert callable(module.main)
