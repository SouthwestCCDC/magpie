"""Magpie-ctl - server administration tool.

This CLI is for server-side administration and uses MagpieSettings
(environment variables) rather than client config files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from magpie.config import get_settings

if TYPE_CHECKING:
    from magpie.config import MagpieSettings


class CTLContext:
    """Context object passed to ctl commands."""

    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
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
@click.pass_context
def cli(ctx: click.Context, debug: bool) -> None:
    """Magpie server administration."""
    ctx.obj = CTLContext(debug=debug)

    if debug:
        settings = get_settings()
        click.echo(f"Storage path: {settings.storage_path}", err=True)
        click.echo(f"Database path: {settings.database_path}", err=True)


@cli.command()
def version() -> None:
    """Show version."""
    from magpie import __version__

    click.echo(f"magpie-ctl {__version__}")


# Register subcommands
from magpie.ctl.commands import flush_tag, gc, init, token  # noqa: E402

cli.add_command(init)
cli.add_command(gc)
cli.add_command(flush_tag)
cli.add_command(token)


# Alias for entry point compatibility
main = cli
