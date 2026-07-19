"""Config command for managing ~/.magpie/config.toml."""

from __future__ import annotations

from pathlib import Path

import click
import tomli_w

from magpie.cli import config as cli_config
from magpie.cli.errors import mask_token
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)

# Fields shown by 'magpie config --show', in display order.
_EFFECTIVE_FIELDS = ("server", "token", "timeout", "ca_cert")


def _write_config(config_path: Path, server: str | None, token: str | None) -> None:
    """Write configuration to TOML file.

    Args:
        config_path: Path to the config file.
        server: Server URL to write (or None to keep existing).
        token: Token to write (or None to keep existing).

    Note:
        Empty string values are treated the same as the existing value, not as
        a way to clear individual settings. Use --clear to remove all config.
    """
    # Load existing config to preserve values not being set
    existing = cli_config.load_config(config_path)
    final_server = server if server is not None else existing.client.server
    final_token = token if token is not None else existing.client.token

    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Build config dict for tomli_w
    client_config: dict[str, str] = {}
    if final_server:
        client_config["server"] = final_server
    if final_token:
        client_config["token"] = final_token

    config_data = {"client": client_config}
    config_path.write_bytes(tomli_w.dumps(config_data).encode())


@click.command("config")
@click.option(
    "--server",
    metavar="URL",
    help="Set the Magpie server URL.",
)
@click.option(
    "--token",
    metavar="TOKEN",
    help="Set the authentication token.",
)
@click.option(
    "--show",
    is_flag=True,
    help="Show effective configuration, with the source of each value.",
)
@click.option(
    "--clear",
    is_flag=True,
    help="Clear all configuration.",
)
@click.pass_context
def config_cmd(
    ctx: click.Context,
    server: str | None,
    token: str | None,
    show: bool,
    clear: bool,
) -> None:
    """Manage client configuration stored in ~/.magpie/config.toml.

    Configuration values are used when --server or --token are not provided
    on the command line, and MAGPIE_SERVER/MAGPIE_TOKEN environment variables
    are not set.

    Examples:

        magpie config --server http://localhost:8080 --token mgp_abc123

        magpie config --show

        magpie config --clear
    """
    config_path = cli_config.DEFAULT_CONFIG_PATH

    # Validate mutually exclusive options
    if clear and (server or token):
        msg = "Cannot use --clear with --server or --token."
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)
    if show and (server or token or clear):
        msg = "Cannot use --show with other options."
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)

    if show:
        _show_config(ctx, config_path)
        return

    if clear:
        _clear_config(config_path)
        return

    # If no options provided, show current (effective) config
    if server is None and token is None:
        _show_config(ctx, config_path)
        return

    # Set values
    _write_config(config_path, server, token)

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "server": server,
                    "token": mask_token(token) if token else None,
                    "path": str(config_path),
                },
                human_output="",
            )
        )
        return

    # Human output - show what was set
    if server:
        click.echo(f"Server set to: {server}")
    if token:
        click.echo(f"Token set to: {mask_token(token)}")

    click.echo(f"Configuration saved to {config_path}")


def _resolve_effective_config(
    ctx: click.Context, config_path: Path
) -> dict[str, cli_config.ResolvedValue]:
    """Resolve every effective config field, each with its source attribution."""
    return {
        "server": cli_config.resolve_server(ctx, config_path),
        "token": cli_config.resolve_token(ctx, config_path),
        "timeout": cli_config.resolve_timeout(ctx),
        "ca_cert": cli_config.resolve_ca_cert(ctx),
    }


def _source_label(resolved: cli_config.ResolvedValue) -> str:
    """Format a source annotation, e.g. '(env: MAGPIE_SERVER)' or '(default)'."""
    if resolved.source == "default":
        return "(default)"
    return f"({resolved.source}: {resolved.origin})"


def _display_value(field: str, resolved: cli_config.ResolvedValue) -> str:
    """Format a resolved value for human-readable display, masking the token."""
    if resolved.value is None:
        return "(not set)"
    if field == "token":
        return mask_token(str(resolved.value))
    if field == "timeout":
        # Timeout is stored as a float; show it as a plain integer when whole.
        timeout = float(resolved.value)
        return str(int(timeout)) if timeout == int(timeout) else str(timeout)
    return str(resolved.value)


def _json_value(field: str, resolved: cli_config.ResolvedValue) -> str | float | None:
    """Value for JSON output: masked for token, raw type preserved otherwise."""
    if resolved.value is None:
        return None
    if field == "token":
        return mask_token(str(resolved.value))
    return resolved.value


def _show_config(ctx: click.Context, config_path: Path) -> None:
    """Display the effective configuration, with the source of each value."""
    resolved = _resolve_effective_config(ctx, config_path)
    exists = config_path.exists()

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "path": str(config_path),
                    "exists": exists,
                    **{
                        field: {
                            "value": _json_value(field, resolved[field]),
                            "source": resolved[field].source,
                            "origin": resolved[field].origin,
                        }
                        for field in _EFFECTIVE_FIELDS
                    },
                },
                human_output="",
            )
        )
        return

    # Human output
    if exists:
        click.echo(f"Configuration file: {config_path}")
    else:
        click.echo(f"No configuration file found at {config_path}")
        click.echo("Run 'magpie config --server URL --token TOKEN' to create one.")
    click.echo()

    click.echo("Effective configuration:")
    click.echo()
    name_width = max(len(field) for field in _EFFECTIVE_FIELDS)
    value_width = max(len(_display_value(field, resolved[field])) for field in _EFFECTIVE_FIELDS)
    for field in _EFFECTIVE_FIELDS:
        value = _display_value(field, resolved[field])
        source = _source_label(resolved[field])
        click.echo(f"  {field:<{name_width}} = {value:<{value_width}}  {source}")


def _clear_config(config_path: Path) -> None:
    """Remove configuration file."""
    if not config_path.exists():
        # JSON output for missing config
        if is_json_output():
            output_result(
                CommandResult(
                    data={
                        "path": str(config_path),
                        "cleared": False,
                        "existed": False,
                    },
                    human_output="",
                )
            )
            return
        click.echo(f"No configuration file found at {config_path}")
        return

    config_path.unlink()

    # Remove parent directory if empty
    try:
        config_path.parent.rmdir()
    except OSError:
        # Directory not empty or other error, ignore
        pass

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "path": str(config_path),
                    "cleared": True,
                    "existed": True,
                },
                human_output="",
            )
        )
        return

    # Human output
    click.echo(f"Configuration cleared: {config_path}")
