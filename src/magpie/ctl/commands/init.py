"""Init command for initializing Magpie storage and admin token."""

from __future__ import annotations

import click

from magpie.auth.database import init_database
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.ctl import CTLContext

ADMIN_TOKEN_NAME = "admin"


@click.command()
@click.option(
    "--reset-admin-token",
    is_flag=True,
    help="Regenerate break-glass admin token (revokes existing).",
)
@click.pass_obj
def init(ctx: CTLContext, reset_admin_token: bool) -> None:
    """Initialize Magpie storage and generate admin token.

    Creates storage directories and SQLite database if they don't exist.
    Generates a break-glass admin token on first run.

    The admin token is printed to stdout and is ONLY VISIBLE ONCE.
    Store it securely!

    Use --reset-admin-token to revoke the existing admin token and
    generate a new one (useful if the token was compromised).

    Examples:

        magpie-ctl init

        magpie-ctl init --reset-admin-token
    """
    settings = ctx.settings

    # Create storage directories
    if ctx.debug:
        click.echo(f"Creating storage directory: {settings.storage_path}", err=True)
    settings.storage_path.mkdir(parents=True, exist_ok=True)

    if ctx.debug:
        click.echo(f"Creating temp directory: {settings.temp_path}", err=True)
    settings.temp_path.mkdir(parents=True, exist_ok=True)

    click.echo(f"Storage initialized at: {settings.storage_path}")

    # Initialize database
    if ctx.debug:
        click.echo(f"Initializing database: {settings.database_path}", err=True)
    init_database(settings.database_path)
    click.echo(f"Database initialized at: {settings.database_path}")

    # Create or reset admin token
    token_service = TokenService(settings)

    if reset_admin_token:
        # Revoke existing admin token first
        if ctx.debug:
            click.echo(f"Revoking existing admin token: {ADMIN_TOKEN_NAME}", err=True)
        revoked = token_service.revoke_token(ADMIN_TOKEN_NAME)
        if revoked:
            click.echo(f"Revoked existing admin token: {ADMIN_TOKEN_NAME}")
        else:
            click.echo(f"No existing admin token found to revoke")

        # Create new admin token
        plaintext_token = token_service.create_token(ADMIN_TOKEN_NAME, TokenScope.ADMIN)
        click.echo("")
        click.echo("=" * 60)
        click.echo("NEW ADMIN TOKEN (store securely, only shown once!):")
        click.echo(plaintext_token)
        click.echo("=" * 60)
    else:
        # Try to create admin token (may fail if already exists)
        try:
            plaintext_token = token_service.create_token(ADMIN_TOKEN_NAME, TokenScope.ADMIN)
            click.echo("")
            click.echo("=" * 60)
            click.echo("ADMIN TOKEN (store securely, only shown once!):")
            click.echo(plaintext_token)
            click.echo("=" * 60)
        except ValueError:
            # Token already exists
            click.echo("")
            click.echo("Admin token already exists. Use --reset-admin-token to regenerate.")
