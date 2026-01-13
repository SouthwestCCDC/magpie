"""Token commands for managing API tokens."""

from __future__ import annotations

import click

from magpie.auth.database import get_connection, list_tokens
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)
from magpie.ctl import CTLContext


@click.group()
@click.pass_obj
def token(ctx: CTLContext) -> None:
    """Manage API tokens."""
    pass


@token.command("create")
@click.option("--name", required=True, help="Human-readable token name (must be unique).")
@click.option(
    "--scope",
    type=click.Choice(["read", "write", "admin"], case_sensitive=False),
    required=True,
    help="Token permission scope.",
)
@click.pass_obj
def token_create(ctx: CTLContext, name: str, scope: str) -> None:
    """Create a new API token.

    The token is printed to stdout and is ONLY VISIBLE ONCE.
    Store it securely!

    Examples:

        magpie-ctl token create --name ci-reader --scope read

        magpie-ctl token create --name deployer --scope write

        magpie-ctl token create --name ops-admin --scope admin
    """
    settings = ctx.settings
    token_service = TokenService(settings)

    # Convert string scope to TokenScope enum
    token_scope = TokenScope(scope.lower())

    if ctx.debug:
        click.echo(f"Creating token '{name}' with scope '{scope}'...", err=True)

    try:
        plaintext_token = token_service.create_token(name, token_scope)
    except ValueError as e:
        if is_json_output():
            output_error(ErrorCode.CONFLICT, str(e))
        else:
            raise click.ClickException(str(e))

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "token": plaintext_token,
                    "name": name,
                    "scope": scope.lower(),
                },
                human_output="",
            )
        )
        return

    # Human output
    click.echo("")
    click.echo("=" * 60)
    click.echo(f"TOKEN CREATED: {name} (scope: {scope})")
    click.echo("Save this token - it will NOT be shown again!")
    click.echo("")
    click.echo(plaintext_token)
    click.echo("=" * 60)


@token.command("list")
@click.pass_obj
def token_list(ctx: CTLContext) -> None:
    """List all API tokens.

    Displays token names, scopes, status, and creation dates.
    Token hashes are never displayed for security.

    Examples:

        magpie-ctl token list
    """
    settings = ctx.settings

    if ctx.debug:
        click.echo(f"Listing tokens from {settings.database_path}...", err=True)

    conn = get_connection(settings.database_path)
    try:
        tokens = list_tokens(conn)
    finally:
        conn.close()

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "tokens": [
                        {
                            "name": tok.name,
                            "scope": tok.scope.value,
                            "created": tok.created_at.isoformat(),
                            "enabled": tok.enabled,
                        }
                        for tok in tokens
                    ],
                },
                human_output="",
            )
        )
        return

    # Human output
    if not tokens:
        click.echo("No tokens found.")
        return

    # Table header
    click.echo("")
    click.echo(f"{'NAME':<20} {'SCOPE':<10} {'ENABLED':<10} {'CREATED_AT'}")
    click.echo("-" * 70)

    # Table rows
    for tok in tokens:
        enabled_str = "yes" if tok.enabled else "no"
        created_str = tok.created_at.strftime("%Y-%m-%d %H:%M:%S")
        click.echo(f"{tok.name:<20} {tok.scope.value:<10} {enabled_str:<10} {created_str}")

    click.echo("")
    click.echo(f"Total: {len(tokens)} token(s)")


@token.command("revoke")
@click.argument("name")
@click.pass_obj
def token_revoke(ctx: CTLContext, name: str) -> None:
    """Revoke an API token.

    Permanently deletes the token from the database. This action cannot
    be undone - a new token must be created if access is needed again.

    NAME is the token name to revoke.

    Examples:

        magpie-ctl token revoke ci-reader

        magpie-ctl token revoke compromised-token
    """
    settings = ctx.settings
    token_service = TokenService(settings)

    if ctx.debug:
        click.echo(f"Revoking token '{name}'...", err=True)

    revoked = token_service.revoke_token(name)

    if revoked:
        # JSON output
        if is_json_output():
            output_result(
                CommandResult(
                    data={
                        "name": name,
                        "revoked": True,
                    },
                    human_output="",
                )
            )
            return
        # Human output
        click.echo(f"Token '{name}' has been revoked.")
    else:
        msg = f"Token not found: {name}"
        if is_json_output():
            output_error(ErrorCode.NOT_FOUND, msg)
        else:
            raise click.ClickException(msg)
