"""Info command for showing artifact metadata."""

from __future__ import annotations

import json

import click

from magpie.cli import CLIContext
from magpie.cli.commands.parse import ParseError, parse_artifact_ref
from magpie.cli.errors import handle_http_error
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    http_status_to_error_code,
    is_json_output,
    output_error,
    output_result,
)


@click.command()
@click.argument("artifact_ref")
@click.pass_obj
def info(ctx: CLIContext, artifact_ref: str) -> None:
    """Show detailed metadata for an artifact.

    ARTIFACT_REF is the artifact path and optional ref (path:ref format).
    If no ref is specified, "latest" is used.

    Examples:

        magpie info images/ubuntu:latest

        magpie info images/ubuntu:@abc12345

        magpie info builds/app
    """
    if not ctx.server:
        msg = "No server configured. Use --server or set MAGPIE_SERVER."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    try:
        parsed = parse_artifact_ref(artifact_ref)
    except ParseError as e:
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, str(e))
        else:
            raise click.ClickException(str(e))

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Fetching info for {parsed.path}:{parsed.ref}...", err=True)

        response = client.get(f"/api/v1/artifacts/{parsed.path}/{parsed.ref}/info")

        if response.status_code == 404:
            msg = f"Artifact not found: {parsed.path}:{parsed.ref}"
            if is_json_output():
                output_error(ErrorCode.NOT_FOUND, msg)
            else:
                raise click.ClickException(msg)
        if response.status_code != 200:
            if is_json_output():
                try:
                    detail = response.json().get("detail", response.text)
                except (json.JSONDecodeError, ValueError, KeyError):
                    detail = response.text
                output_error(http_status_to_error_code(response.status_code), detail)
            else:
                handle_http_error(response, "Info", ctx.token)

        data = response.json()

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "artifact": parsed.path,
                    "version": data["hash_ref"],
                    "hash": data["hash"],
                    "size": data.get("size"),
                    "tags": data.get("tags", []),
                    "source_uri": data.get("source_uri"),
                    "created": data.get("uploaded_at"),
                    "uploaded_by": data.get("uploaded_by"),
                },
                human_output="",
            )
        )
        return

    # Human output
    click.echo(f"Hash:        {data['hash']}")
    click.echo(f"Hash Ref:    {data['hash_ref']}")
    click.echo(f"Uploaded By: {data.get('uploaded_by', 'unknown')}")
    click.echo(f"Uploaded At: {data.get('uploaded_at', 'unknown')}")

    source_uri = data.get("source_uri")
    if source_uri:
        click.echo(f"Source URI:  {source_uri}")
    else:
        click.echo("Source URI:  (none)")

    tags = data.get("tags", [])
    if tags:
        click.echo(f"Tags:        {', '.join(tags)}")
    else:
        click.echo("Tags:        (none)")
