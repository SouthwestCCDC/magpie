"""Config command for managing ~/.magpie/config.toml."""

from __future__ import annotations

from pathlib import Path

import click

from magpie.cli import config as cli_config


def _write_config(config_path: Path, server: str | None, token: str | None) -> None:
    """Write configuration to TOML file.

    Args:
        config_path: Path to the config file.
        server: Server URL to write (or None to keep existing).
        token: Token to write (or None to keep existing).
    """
    # Load existing config to preserve values not being set
    existing = cli_config.load_config(config_path)
    final_server = server if server is not None else existing.client.server
    final_token = token if token is not None else existing.client.token

    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Write TOML manually (tomllib is read-only)
    lines = ["[client]"]
    if final_server:
        # Escape backslashes and quotes in TOML strings
        escaped_server = final_server.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'server = "{escaped_server}"')
    if final_token:
        escaped_token = final_token.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'token = "{escaped_token}"')

    config_path.write_text("\n".join(lines) + "\n")


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
    help="Show current configuration.",
)
@click.option(
    "--clear",
    is_flag=True,
    help="Clear all configuration.",
)
def config_cmd(
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
        raise click.ClickException("Cannot use --clear with --server or --token.")
    if show and (server or token or clear):
        raise click.ClickException("Cannot use --show with other options.")

    if show:
        _show_config(config_path)
        return

    if clear:
        _clear_config(config_path)
        return

    # If no options provided, show current config
    if server is None and token is None:
        _show_config(config_path)
        return

    # Set values
    _write_config(config_path, server, token)

    # Show what was set
    if server:
        click.echo(f"Server set to: {server}")
    if token:
        click.echo(f"Token set to: {'*' * 8}{token[-4:] if len(token) > 4 else '****'}")

    click.echo(f"Configuration saved to {config_path}")


def _show_config(config_path: Path) -> None:
    """Display current configuration."""
    if not config_path.exists():
        click.echo(f"No configuration file found at {config_path}")
        click.echo("Run 'magpie config --server URL --token TOKEN' to create one.")
        return

    config = cli_config.load_config(config_path)
    click.echo(f"Configuration file: {config_path}")
    click.echo()

    if config.client.server:
        click.echo(f"  server = {config.client.server}")
    else:
        click.echo("  server = (not set)")

    if config.client.token:
        # Mask token for security, show only last 4 chars
        masked = "*" * 8 + config.client.token[-4:] if len(config.client.token) > 4 else "****"
        click.echo(f"  token  = {masked}")
    else:
        click.echo("  token  = (not set)")


def _clear_config(config_path: Path) -> None:
    """Remove configuration file."""
    if not config_path.exists():
        click.echo(f"No configuration file found at {config_path}")
        return

    config_path.unlink()
    click.echo(f"Configuration cleared: {config_path}")

    # Remove parent directory if empty
    try:
        config_path.parent.rmdir()
    except OSError:
        # Directory not empty or other error, ignore
        pass
