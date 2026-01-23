"""Magpie CLI - client tool for interacting with artifacts server."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Callable, TypeVar

import click

from magpie.cli.client import get_client
from magpie.cli.config import get_ca_cert, get_server, get_timeout, get_token
from magpie.cli.formatting import OutputFormat
from magpie.config import get_settings

if TYPE_CHECKING:
    import httpx

F = TypeVar("F", bound=Callable[..., object])


def sentry_wrapper(func: F) -> F:
    """Wrap CLI command to capture exceptions in Sentry if configured.

    Only initializes Sentry if MAGPIE_SENTRY_DSN is set.
    """

    @wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        settings = get_settings()

        if not settings.sentry_dsn:
            return func(*args, **kwargs)

        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment="cli",
            send_default_pii=False,
        )

        try:
            return func(*args, **kwargs)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise

    return wrapper  # type: ignore[return-value]


class CLIContext:
    """Context object passed to CLI commands."""

    def __init__(
        self,
        server: str,
        token: str,
        debug: bool = False,
        timeout: float = 600.0,
        output_format: OutputFormat = OutputFormat.HUMAN,
        ca_cert: str | None = None,
    ) -> None:
        self.server = server
        self.token = token
        self.debug = debug
        self.timeout = timeout
        self.output_format = output_format
        self.ca_cert = ca_cert

    def get_client(self) -> "httpx.Client":
        """Get configured httpx client."""
        return get_client(self.server, self.token, self.timeout, ca_cert=self.ca_cert)


pass_context = click.make_pass_decorator(CLIContext)


@click.group()
@click.option(
    "--server",
    envvar="MAGPIE_SERVER",
    help="Magpie server URL (overrides config file).",
)
@click.option(
    "--token",
    envvar="MAGPIE_TOKEN",
    help="Authentication token (overrides config file).",
)
@click.option(
    "--timeout",
    type=float,
    envvar="MAGPIE_TIMEOUT",
    help="HTTP request timeout in seconds (default: 600).",
)
@click.option(
    "--ca-cert",
    envvar="MAGPIE_CA_CERT",
    help="Path to additional CA certificate for HTTPS verification.",
)
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
def cli(
    ctx: click.Context,
    server: str | None,
    token: str | None,
    timeout: float | None,
    ca_cert: str | None,
    debug: bool,
    output_format: str,
) -> None:
    """Magpie: Content-addressed artifact storage client."""
    resolved_server = get_server(cli_override=server)
    resolved_token = get_token(cli_override=token)
    resolved_timeout = get_timeout(cli_override=timeout)
    resolved_ca_cert = get_ca_cert(cli_override=ca_cert)
    resolved_format = OutputFormat(output_format)

    ctx.obj = CLIContext(
        server=resolved_server,
        token=resolved_token,
        debug=debug,
        timeout=resolved_timeout,
        output_format=resolved_format,
        ca_cert=resolved_ca_cert,
    )

    if debug:
        click.echo(f"Server: {resolved_server}", err=True)
        click.echo(f"Token: {'***' if resolved_token else '(none)'}", err=True)
        click.echo(f"Timeout: {resolved_timeout}s", err=True)
        click.echo(f"CA Cert: {resolved_ca_cert or '(system trust store)'}", err=True)
        click.echo(f"Format: {resolved_format.value}", err=True)


@cli.command()
def version() -> None:
    """Show version."""
    from magpie import __version__

    click.echo(f"magpie {__version__}")


# Register subcommands
from magpie.cli.commands import (  # noqa: E402
    amend,
    config_cmd,
    flush_tag,
    gc,
    get,
    info,
    ls,
    push,
    status,
    tag,
    token,
    untag,
    url,
)

cli.add_command(push)
cli.add_command(get)
cli.add_command(ls)
cli.add_command(info)
cli.add_command(url)
cli.add_command(tag)
cli.add_command(token)
cli.add_command(untag)
cli.add_command(flush_tag)
cli.add_command(amend)
cli.add_command(gc)
cli.add_command(status)
cli.add_command(config_cmd, name="config")


# Keep the old 'main' as an alias for backwards compatibility with entry point
main = sentry_wrapper(cli)
