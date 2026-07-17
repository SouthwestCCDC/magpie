"""Init command for initializing Magpie storage and admin token."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from magpie.auth.database import init_database
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenExistsError, TokenFormatError, TokenService
from magpie.auth.token_sink import TokenSinkError, deliver_admin_token
from magpie.cli.formatting import (
    CommandResult,
    is_json_output,
    output_result,
)
from magpie.ctl import CTLContext

if TYPE_CHECKING:
    from magpie.config import MagpieSettings

ADMIN_TOKEN_NAME = "admin"  # nosec B105 - not a password, just a token name


def _abort_on_sink_error(e: TokenSinkError) -> None:
    """Report a fatal token delivery failure and abort (fail-closed).

    A generated-but-undelivered admin token is a liability, not a degraded
    feature: the server must not start.
    """
    if is_json_output():
        output_result(
            CommandResult(
                data={"error": str(e)},
                human_output="",
            )
        )
    else:
        click.echo(f"Error: {e}", err=True)
    raise SystemExit(1)


def _echo_delivery_status(sink: str | None, settings: MagpieSettings) -> None:
    """Print a human-readable summary of how the admin token was delivered.

    The token value itself is never echoed here -- the ``stdout`` sink prints
    the value itself (with a warning) as part of delivery, so nothing further
    is added for that case.
    """
    if is_json_output() or sink == "stdout":
        return

    click.echo("")
    if sink == "file":
        click.echo(f"Admin token delivered via 'file' sink: {settings.admin_token_sink_file_path}")
    elif sink == "exec":
        click.echo("Admin token delivered via 'exec' sink.")
    elif sink == "discard":
        click.echo("No bootstrap admin token was retained (sink=discard).")
        click.echo("Mint one when ready via an interactive session:")
        click.echo("  magpie-ctl token create --name ops-admin --scope admin")


@click.command()
@click.option(
    "--reset-admin-token",
    is_flag=True,
    help="Regenerate break-glass admin token (revokes existing).",
)
@click.option(
    "--admin-token",
    default=None,
    help="Specify admin token instead of generating a random one. Must start with 'mgp_ADMIN_'.",
)
@click.pass_obj
def init(ctx: CTLContext, reset_admin_token: bool, admin_token: str | None) -> None:
    """Initialize Magpie storage and generate admin token.

    Creates storage directories and SQLite database if they don't exist.
    Generates a break-glass admin token on first run.

    The admin token is delivered via the sink configured by
    MAGPIE_ADMIN_TOKEN_SINK (file/exec/discard/stdout) -- it is never printed
    to stdout unless sink=stdout is explicitly chosen. A delivering sink
    (file/exec) that fails to deliver aborts init with a non-zero exit; the
    server does not start.

    Use --reset-admin-token to revoke the existing admin token and
    generate a new one (useful if the token was compromised). The new
    token is delivered through the same configured sink.

    Use --admin-token to specify a custom token instead of generating
    a random one. The token must start with 'mgp_ADMIN_'.

    Examples:

        magpie-ctl init

        magpie-ctl init --reset-admin-token

        magpie-ctl init --admin-token mgp_ADMIN_custom_token_here
    """
    # Args (for developers):
    #   ctx: Click context containing settings and debug flag.
    #   reset_admin_token: If True, revokes existing admin token before creating new one.
    #   admin_token: Optional custom token to use instead of generating random one.
    #       Must start with 'mgp_ADMIN_' and have content after the prefix.
    settings = ctx.settings

    # Create storage directories
    if ctx.debug:
        click.echo(f"Creating storage directory: {settings.storage_path}", err=True)
    settings.storage_path.mkdir(parents=True, exist_ok=True)

    if ctx.debug:
        click.echo(f"Creating temp directory: {settings.temp_path}", err=True)
    settings.temp_path.mkdir(parents=True, exist_ok=True)

    if not is_json_output():
        click.echo(f"Storage initialized at: {settings.storage_path}")

    # Initialize database
    if ctx.debug:
        click.echo(f"Initializing database: {settings.database_path}", err=True)
    init_database(settings.database_path)
    if not is_json_output():
        click.echo(f"Database initialized at: {settings.database_path}")

    # Create or reset admin token
    token_service = TokenService(settings)
    plaintext_token: str | None = None
    token_already_exists = False
    delivered_sink: str | None = None

    # Validate custom token format early (before revoking existing token)
    if admin_token is not None:
        if not admin_token.startswith("mgp_ADMIN_"):
            error_msg = "Provided token must start with 'mgp_ADMIN_' for admin scope"
            if is_json_output():
                output_result(
                    CommandResult(
                        data={"error": error_msg},
                        human_output="",
                    )
                )
            else:
                click.echo(f"Error: {error_msg}", err=True)
            raise SystemExit(1)
        if len(admin_token) <= len("mgp_ADMIN_"):
            error_msg = "Provided token is too short (must have content after 'mgp_ADMIN_' prefix)"
            if is_json_output():
                output_result(
                    CommandResult(
                        data={"error": error_msg},
                        human_output="",
                    )
                )
            else:
                click.echo(f"Error: {error_msg}", err=True)
            raise SystemExit(1)

    if reset_admin_token:
        # Revoke existing admin token first
        if ctx.debug:
            click.echo(f"Revoking existing admin token: {ADMIN_TOKEN_NAME}", err=True)
        revoked = token_service.revoke_token(ADMIN_TOKEN_NAME)
        if revoked and not is_json_output():
            click.echo(f"Revoked existing admin token: {ADMIN_TOKEN_NAME}")
        elif not revoked and not is_json_output():
            click.echo("No existing admin token found to revoke")

        # Create new admin token (with provided token if specified)
        try:
            plaintext_token = token_service.create_token(
                ADMIN_TOKEN_NAME, TokenScope.ADMIN, admin_token
            )
        except TokenFormatError as e:
            # Token format validation failed
            if is_json_output():
                output_result(
                    CommandResult(
                        data={
                            "error": str(e),
                        },
                        human_output="",
                    )
                )
            else:
                click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)

        # Deliver the new token via the configured sink. Fail-closed: a
        # delivery failure aborts init and the server does not start.
        try:
            deliver_admin_token(plaintext_token, settings, action="reset")
        except TokenSinkError as e:
            _abort_on_sink_error(e)
        delivered_sink = settings.admin_token_sink
        _echo_delivery_status(delivered_sink, settings)
    else:
        # Try to create admin token (may fail if already exists)
        try:
            plaintext_token = token_service.create_token(
                ADMIN_TOKEN_NAME, TokenScope.ADMIN, admin_token
            )
        except TokenExistsError:
            # Token already exists -- this is not first boot, so no new
            # token was generated and nothing needs to be delivered. The
            # sink does not need to be configured for this to succeed.
            token_already_exists = True
            plaintext_token = None
            if not is_json_output():
                click.echo("")
                click.echo("Admin token already exists. Use --reset-admin-token to regenerate.")
        except TokenFormatError as e:
            # Token format validation failed
            if is_json_output():
                output_result(
                    CommandResult(
                        data={
                            "error": str(e),
                        },
                        human_output="",
                    )
                )
            else:
                click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)
        else:
            # First boot: deliver the freshly generated token via the
            # configured sink. Fail-closed: a delivery failure aborts init
            # and the server does not start.
            try:
                deliver_admin_token(plaintext_token, settings, action="init")
            except TokenSinkError as e:
                _abort_on_sink_error(e)
            delivered_sink = settings.admin_token_sink
            _echo_delivery_status(delivered_sink, settings)

    # JSON output. The token value is only ever included when sink=stdout was
    # explicitly chosen -- for all other sinks, JSON output (which also goes
    # to stdout) must not leak it either.
    if is_json_output():
        json_token = plaintext_token if delivered_sink == "stdout" else None
        output_result(
            CommandResult(
                data={
                    "admin_token": json_token,
                    "admin_token_sink": delivered_sink,
                    "storage_path": str(settings.storage_path),
                    "database_path": str(settings.database_path),
                    "token_already_existed": token_already_exists,
                },
                human_output="",
            )
        )
