"""Status command for checking server health and connectivity."""

from __future__ import annotations

import json

import click

from magpie.cli import CLIContext
from magpie.cli.errors import handle_http_error
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    http_status_to_error_code,
    is_json_output,
    output_error,
    output_result,
)
from magpie.utils.formatting import format_size


@click.command()
@click.pass_obj
def status(ctx: CLIContext) -> None:
    """Check server health, connectivity, and status (admin only).

    Displays server connection information and storage statistics.
    Requires an admin token.

    Examples:

        magpie status

        magpie --format json status
    """
    if not ctx.server:
        msg = "No server configured. Use --server or set MAGPIE_SERVER."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Checking status of {ctx.server}...", err=True)

        try:
            response = client.get("/api/v1/status")
        except Exception as e:
            msg = f"Failed to connect to server: {e}"
            if is_json_output():
                output_error(ErrorCode.NETWORK_ERROR, msg)
                return
            raise click.ClickException(msg)

        if response.status_code != 200:
            if is_json_output():
                try:
                    detail = response.json().get("detail", response.text)
                except (json.JSONDecodeError, ValueError, KeyError):
                    detail = response.text
                output_error(http_status_to_error_code(response.status_code), detail)
                return
            handle_http_error(response, "Status check", ctx.token)

        data = response.json()

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "server": ctx.server,
                    "status": data["status"],
                    "version": data["version"],
                    "storage": data["storage"],
                },
                human_output="",
            )
        )
        return

    # Human output
    click.echo(f"Server:    {ctx.server}")
    click.echo(f"Status:    {data['status'].upper()}")
    click.echo(f"Version:   {data['version']}")

    # Storage stats
    storage = data["storage"]
    size_str = format_size(storage["total_size_bytes"])
    click.echo(f"Storage:   {size_str} used")
    click.echo(f"Artifacts: {storage['artifact_count']} total")
    click.echo(f"Blobs:     {storage['blob_count']} total")
