"""Token commands for managing API tokens."""

from __future__ import annotations

import click

from magpie.auth.database import get_connection, get_token_by_name, list_tokens
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenError, TokenService
from magpie.auth.token_sink import TokenSinkError, deliver_admin_token
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)
from magpie.ctl import CTLContext
from magpie.ctl.commands.init import ADMIN_TOKEN_NAME
from magpie.validation import ValidationError


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
    except ValidationError as e:
        # Token name validation failed
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, str(e))
        else:
            raise click.ClickException(str(e))
    except ValueError as e:
        # Token name already exists
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


@token.command("rotate")
@click.argument("name")
@click.pass_obj
def token_rotate(ctx: CTLContext, name: str) -> None:
    """Rotate an API token.

    Revokes the existing token and creates a new one with the same name
    and scope. This is an atomic operation.

    The new token is printed to stdout and is ONLY VISIBLE ONCE. Store it
    securely! EXCEPTION: rotating the break-glass admin token (name "admin")
    delivers the new token via the same MAGPIE_ADMIN_TOKEN_SINK used by
    `magpie-ctl init`, not directly to stdout, to keep its delivery model
    consistent across its whole lifecycle. A delivering sink (file/exec)
    that fails aborts the rotation with a non-zero exit.

    NAME is the token name to rotate.

    Examples:

        magpie-ctl token rotate ci-reader

        magpie-ctl token rotate compromised-token
    """
    settings = ctx.settings
    token_service = TokenService(settings)

    if ctx.debug:
        click.echo(f"Rotating token '{name}'...", err=True)

    # The break-glass admin token is delivered through the same configured
    # sink as init/reset-admin-token, never printed directly, so its
    # delivery model stays consistent across its whole lifecycle. Handled
    # entirely separately from the generic path below: delivery is always
    # attempted BEFORE any database change, so a delivery failure leaves the
    # existing admin token completely untouched (see
    # magpie.ctl.commands.init._abort_on_sink_error for the shared
    # rationale).
    if name == ADMIN_TOKEN_NAME:
        conn = get_connection(settings.database_path)
        try:
            existing = get_token_by_name(conn, name)
        finally:
            conn.close()

        if existing is None:
            msg = f"Token not found: {name}"
            if is_json_output():
                output_error(ErrorCode.NOT_FOUND, msg)
            else:
                raise click.ClickException(msg)

        new_plaintext = token_service.generate_plaintext_token(existing.scope)

        try:
            deliver_admin_token(new_plaintext, settings, action="rotate")
        except TokenSinkError as e:
            # No DB mutation has happened -- the prior admin token, if any,
            # remains valid and unchanged.
            if is_json_output():
                output_error(ErrorCode.IO_ERROR, str(e))
            else:
                raise click.ClickException(str(e))

        sink = settings.admin_token_sink

        if sink == "discard":
            # Explicit non-retention: rotating still means "revoke the old
            # token" (that's the operator's explicit ask), but the freshly
            # delivered replacement is never persisted -- matching
            # discard's "retain no usable token" contract.
            token_service.revoke_token(name)
            new_token: str | None = None
            scope = existing.scope
        else:
            # Delivery succeeded: atomically revoke-old + persist the
            # delivered value as a single DB transaction.
            try:
                rotate_result = token_service.rotate_token(name, plaintext_token=new_plaintext)
            except TokenError as e:
                # Hash collision during persistence (extremely unlikely):
                # the value was already delivered but couldn't be written.
                if is_json_output():
                    output_error(ErrorCode.CONFLICT, str(e))
                else:
                    raise click.ClickException(str(e))
            if rotate_result is None:
                # Extremely unlikely TOCTOU: the token was deleted between
                # our lookup above and this atomic rotate.
                msg = f"Token not found: {name}"
                if is_json_output():
                    output_error(ErrorCode.NOT_FOUND, msg)
                else:
                    raise click.ClickException(msg)
            new_token, scope = rotate_result

        if is_json_output():
            output_result(
                CommandResult(
                    data={
                        "token": new_token if sink == "stdout" else None,
                        "admin_token_sink": sink,
                        "name": name,
                        "scope": scope.value,
                    },
                    human_output="",
                )
            )
            return

        click.echo("")
        click.echo(f"TOKEN ROTATED: {name} (scope: {scope.value})")
        if sink == "file":
            click.echo(f"Delivered via 'file' sink: {settings.admin_token_sink_file_path}")
        elif sink == "exec":
            click.echo("Delivered via 'exec' sink.")
        elif sink == "discard":
            click.echo("No bootstrap admin token was retained (sink=discard).")
            click.echo("Mint one when ready via an interactive session:")
            click.echo("  magpie-ctl token create --name ops-admin --scope admin")
        return

    try:
        result = token_service.rotate_token(name)
    except ValidationError as e:
        # Token name validation failed
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, str(e))
        else:
            raise click.ClickException(str(e))
    except TokenError as e:
        # Hash collision during rotation (extremely unlikely)
        if is_json_output():
            output_error(ErrorCode.CONFLICT, str(e))
        else:
            raise click.ClickException(str(e))

    if result is None:
        msg = f"Token not found: {name}"
        if is_json_output():
            output_error(ErrorCode.NOT_FOUND, msg)
        else:
            raise click.ClickException(msg)

    # Unpack the result (new_token, scope)
    new_token, scope = result

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "token": new_token,
                    "name": name,
                    "scope": scope.value,
                },
                human_output="",
            )
        )
        return

    # Human output
    click.echo("")
    click.echo("=" * 60)
    click.echo(f"TOKEN ROTATED: {name} (scope: {scope.value})")
    click.echo("Save this token - it will NOT be shown again!")
    click.echo("")
    click.echo(new_token)
    click.echo("=" * 60)
