"""Init command for initializing Magpie storage and admin token."""

from __future__ import annotations

import click

from magpie.auth.database import init_database
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.cli.formatting import (
    CommandResult,
    is_json_output,
    output_result,
)
from magpie.ctl import CTLContext

ADMIN_TOKEN_NAME = "admin"  # nosec B105 - not a password, just a token name


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

    The admin token is printed to stdout and is ONLY VISIBLE ONCE.
    Store it securely!

    Use --reset-admin-token to revoke the existing admin token and
    generate a new one (useful if the token was compromised).

    Use --admin-token to specify a custom token instead of generating
    a random one. The token must start with 'mgp_ADMIN_'.

    Examples:

        magpie-ctl init

        magpie-ctl init --reset-admin-token

        magpie-ctl init --admin-token mgp_ADMIN_custom_token_here
    """
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
        except ValueError as e:
            # Token validation failed
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

        if not is_json_output():
            click.echo("")
            click.echo("=" * 60)
            click.echo("NEW ADMIN TOKEN (store securely, only shown once!):")
            click.echo(plaintext_token)
            click.echo("=" * 60)
    else:
        # Try to create admin token (may fail if already exists)
        try:
            plaintext_token = token_service.create_token(
                ADMIN_TOKEN_NAME, TokenScope.ADMIN, admin_token
            )
            if not is_json_output():
                click.echo("")
                click.echo("=" * 60)
                click.echo("ADMIN TOKEN (store securely, only shown once!):")
                click.echo(plaintext_token)
                click.echo("=" * 60)
        except ValueError as e:
            # Check if it's a token validation error or token already exists
            if "already exists" in str(e):
                # Token already exists
                token_already_exists = True
                if not is_json_output():
                    click.echo("")
                    click.echo(
                        "Admin token already exists. Use --reset-admin-token to regenerate."
                    )
            else:
                # Token validation failed
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

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "admin_token": plaintext_token,
                    "storage_path": str(settings.storage_path),
                    "database_path": str(settings.database_path),
                    "token_already_existed": token_already_exists,
                },
                human_output="",
            )
        )
