"""Magpie-ctl - server administration tool.

This CLI is for server-side administration and uses MagpieSettings
(environment variables) rather than client config files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from magpie.cli.formatting import OutputFormat
from magpie.config import get_settings

if TYPE_CHECKING:
    from magpie.config import MagpieSettings


class CTLContext:
    """Context object passed to ctl commands."""

    def __init__(
        self,
        debug: bool = False,
        output_format: OutputFormat = OutputFormat.HUMAN,
    ) -> None:
        self.debug = debug
        self.output_format = output_format
        self._settings: "MagpieSettings | None" = None

    @property
    def settings(self) -> "MagpieSettings":
        """Get MagpieSettings (lazy loaded)."""
        if self._settings is None:
            self._settings = get_settings()
        return self._settings


pass_context = click.make_pass_decorator(CTLContext)


@click.group()
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug output.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice([f.value for f in OutputFormat], case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    help="Output format (default: human).",
)
@click.pass_context
def cli(ctx: click.Context, debug: bool, output_format: str) -> None:
    """Magpie server administration."""
    resolved_format = OutputFormat(output_format)
    ctx.obj = CTLContext(debug=debug, output_format=resolved_format)

    if debug:
        settings = get_settings()
        click.echo(f"Storage path: {settings.storage_path}", err=True)
        click.echo(f"Database path: {settings.database_path}", err=True)
        click.echo(f"Format: {resolved_format.value}", err=True)


@cli.command()
def version() -> None:
    """Show version."""
    from magpie import __version__

    click.echo(f"magpie-ctl {__version__}")


# Register subcommands
from magpie.ctl.commands import (  # noqa: E402
    flush_tag,
    gc,
    init,
    migrate,
    sync,
    token,
    verify,
)

cli.add_command(init)
cli.add_command(gc)
cli.add_command(flush_tag)
cli.add_command(sync)
cli.add_command(token)
cli.add_command(migrate)
cli.add_command(verify)


# Alias for entry point compatibility
main = cli
