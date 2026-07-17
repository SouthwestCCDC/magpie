"""Init command for initializing Magpie storage and admin token."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from magpie.auth.database import get_connection, get_token_by_name, init_database
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenError, TokenExistsError, TokenFormatError, TokenService
from magpie.auth.token_sink import TokenSinkError, deliver_admin_token
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)
from magpie.ctl import CTLContext

if TYPE_CHECKING:
    from magpie.config import MagpieSettings

ADMIN_TOKEN_NAME = "admin"  # nosec B105 - not a password, just a token name


def _admin_token_exists(settings: MagpieSettings) -> bool:
    """Check (read-only) whether an admin token is already persisted.

    Used to decide whether a new admin token candidate needs to be
    generated and delivered at all, and (for --reset-admin-token) purely
    for the "revoked existing" vs. "no existing token" status message --
    it never gates a DB write on its own.
    """
    conn = get_connection(settings.database_path)
    try:
        return get_token_by_name(conn, ADMIN_TOKEN_NAME) is not None
    finally:
        conn.close()


def _abort_on_sink_error(e: TokenSinkError) -> None:
    """Report a fatal token delivery failure and abort (fail-closed).

    Called BEFORE any database mutation for the generated/candidate token,
    for every admin-token-delivering path (first-boot init,
    --reset-admin-token, and token rotate admin in token.py) -- so a
    delivery failure here never destroys an existing, working admin token
    and never leaves an orphaned, undelivered one in the database. See
    docs/installation.md "Admin Token Delivery" for the full guarantee.
    """
    if is_json_output():
        output_error(ErrorCode.IO_ERROR, str(e))  # writes to stderr and exits; never returns
    else:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


def _abort_on_persist_error(e: TokenError) -> None:
    """Report a fatal (and extremely unlikely) persistence failure and abort.

    Reached only if a hash collision occurs while persisting a token whose
    value has *already* been successfully delivered via the sink -- the
    delivered value exists at the sink but couldn't be written to the
    database. This is exceptionally rare (see TokenService.replace_token);
    surfaced as a hard error rather than silently continuing.
    """
    if is_json_output():
        output_error(ErrorCode.CONFLICT, str(e))  # writes to stderr and exits; never returns
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
    to stdout unless sink=stdout is explicitly chosen. Delivery is always
    attempted BEFORE any database change: a delivering sink (file/exec) that
    fails leaves the database untouched. On first boot that means no admin
    token exists yet and the server does not start (matching entrypoint.sh's
    retry-on-failure behavior); for --reset-admin-token (typically run via
    `docker exec` against an already-running server), it means the existing
    admin token is left valid and unchanged -- nothing is lost, and the
    command can simply be retried once the sink is fixed.

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

    token_service = TokenService(settings)
    plaintext_token: str | None = None
    token_already_exists = False
    delivered_sink: str | None = None

    # Validate custom token format early (cheap, no side effects; before any
    # generation, delivery, or database change).
    if admin_token is not None:
        if not admin_token.startswith("mgp_ADMIN_"):
            error_msg = "Provided token must start with 'mgp_ADMIN_' for admin scope"
            if is_json_output():
                output_error(ErrorCode.VALIDATION_ERROR, error_msg)  # exits; never returns
            click.echo(f"Error: {error_msg}", err=True)
            raise SystemExit(1)
        if len(admin_token) <= len("mgp_ADMIN_"):
            error_msg = "Provided token is too short (must have content after 'mgp_ADMIN_' prefix)"
            if is_json_output():
                output_error(ErrorCode.VALIDATION_ERROR, error_msg)  # exits; never returns
            click.echo(f"Error: {error_msg}", err=True)
            raise SystemExit(1)

    if reset_admin_token:
        # Determine the plaintext to deliver -- nothing is persisted yet.
        plaintext_candidate = (
            admin_token
            if admin_token is not None
            else token_service.generate_plaintext_token(TokenScope.ADMIN)
        )

        # Deliver BEFORE touching the database. Fail-closed: on failure, no
        # DB change has happened at all -- any existing admin token is left
        # exactly as it was.
        try:
            deliver_admin_token(plaintext_candidate, settings, action="reset")
        except TokenSinkError as e:
            _abort_on_sink_error(e)

        delivered_sink = settings.admin_token_sink
        had_existing = _admin_token_exists(settings)

        if delivered_sink == "discard":
            # Explicit non-retention: the operator's --reset-admin-token
            # request still means "revoke whatever is there," but the newly
            # generated value is never persisted -- matching discard's
            # "retain no usable token" contract.
            if ctx.debug:
                click.echo(f"Revoking existing admin token: {ADMIN_TOKEN_NAME}", err=True)
            token_service.revoke_token(ADMIN_TOKEN_NAME)
            plaintext_token = None
            if not is_json_output():
                click.echo(
                    f"Revoked existing admin token: {ADMIN_TOKEN_NAME}"
                    if had_existing
                    else "No existing admin token found to revoke"
                )
                _echo_delivery_status(delivered_sink, settings)
        else:
            # Delivery succeeded: atomically revoke-if-present + persist the
            # delivered value as a single DB transaction.
            try:
                token_service.replace_token(ADMIN_TOKEN_NAME, TokenScope.ADMIN, plaintext_candidate)
            except TokenError as e:
                _abort_on_persist_error(e)
            plaintext_token = plaintext_candidate
            if not is_json_output():
                click.echo(
                    f"Revoked existing admin token: {ADMIN_TOKEN_NAME}"
                    if had_existing
                    else "No existing admin token found to revoke"
                )
                _echo_delivery_status(delivered_sink, settings)
    else:
        # First boot only: check existence first (read-only) so a token is
        # never generated and delivered needlessly when one already exists.
        if _admin_token_exists(settings):
            token_already_exists = True
            if not is_json_output():
                click.echo("")
                click.echo("Admin token already exists. Use --reset-admin-token to regenerate.")
        else:
            plaintext_candidate = (
                admin_token
                if admin_token is not None
                else token_service.generate_plaintext_token(TokenScope.ADMIN)
            )

            # Deliver BEFORE touching the database. Fail-closed: on failure,
            # no admin token exists in the database, and the server does
            # not start (entrypoint.sh removes the incomplete database and
            # retries on next boot).
            try:
                deliver_admin_token(plaintext_candidate, settings, action="init")
            except TokenSinkError as e:
                _abort_on_sink_error(e)

            delivered_sink = settings.admin_token_sink

            if delivered_sink == "discard":
                # Generate-and-immediately-drop: never persisted.
                plaintext_token = None
                if not is_json_output():
                    _echo_delivery_status(delivered_sink, settings)
            else:
                try:
                    plaintext_token = token_service.create_token(
                        ADMIN_TOKEN_NAME, TokenScope.ADMIN, plaintext_candidate
                    )
                except TokenExistsError:
                    # Lost a narrow race against a concurrent init (only
                    # possible outside entrypoint.sh's flock-protected first
                    # boot, e.g. two manual invocations at once). The token
                    # we just delivered is not the one that ended up active;
                    # report the existing-token case instead.
                    token_already_exists = True
                    plaintext_token = None
                    delivered_sink = None
                    if not is_json_output():
                        click.echo("")
                        click.echo(
                            "Admin token already exists (lost a race with a concurrent "
                            "init). Use --reset-admin-token to regenerate."
                        )
                except TokenFormatError as e:  # pragma: no cover - guarded by validation above
                    if is_json_output():
                        output_error(ErrorCode.VALIDATION_ERROR, str(e))  # exits; never returns
                    click.echo(f"Error: {e}", err=True)
                    raise SystemExit(1)
                else:
                    if not is_json_output():
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
