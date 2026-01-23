"""Token command for managing authentication tokens."""

from __future__ import annotations

import click

from magpie.cli import CLIContext
from magpie.cli.errors import handle_response_error
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)


@click.group()
def token() -> None:
    """Manage authentication tokens."""
    pass


@token.command(name="create")
@click.option(
    "--name", required=True, help="Token name (alphanumeric, dots, underscores, hyphens)."
)
@click.option(
    "--scope",
    type=click.Choice(["read", "write", "admin"], case_sensitive=False),
    default="read",
    help="Token scope (default: read).",
)
@click.pass_obj
def create_token(ctx: CLIContext, name: str, scope: str) -> None:
    """Create a new authentication token.

    Requires admin scope. The plaintext token is only returned once and
    cannot be retrieved later. Make sure to save it securely.

    Examples:

        magpie token create --name my-token --scope read

        magpie token create --name deploy-bot --scope write

        magpie token create --name admin-user --scope admin
    """
    if not ctx.server:
        msg = "No server configured. Use --server or set MAGPIE_SERVER."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Creating token '{name}' with scope '{scope}'...", err=True)
        response = client.post(
            "/api/v1/tokens",
            json={"name": name, "scope": scope.lower()},
        )

        if response.status_code != 200:
            handle_response_error(response, "Token create", ctx.token)

        data = response.json()

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "name": data["name"],
                    "token": data["token"],
                    "scope": data["scope"],
                },
                human_output="",  # Not used for JSON
            )
        )
        return

    # Human output
    click.echo(f"Created token: {data['name']}")
    click.echo(f"Scope: {data['scope']}")
    click.echo()
    click.echo("Token (save this - it will only be shown once):")
    click.echo(f"  {data['token']}")
