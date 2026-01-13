"""URL command for outputting download URLs for scripting."""

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
def url(ctx: CLIContext, artifact_ref: str) -> None:
    """Output download URL for scripting (curl/wget).

    ARTIFACT_REF is the artifact path and optional ref (path:ref format).
    If no ref is specified, "latest" is used.

    Outputs a bare URL suitable for piping to curl or wget.

    Examples:

        magpie url images/ubuntu:latest

        curl -O $(magpie url images/ubuntu:latest)

        wget $(magpie url builds/app:v1.0)
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
            click.echo(f"Resolving {parsed.path}:{parsed.ref}...", err=True)

        # Get info to resolve ref to hash_ref
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
                handle_http_error(response, "URL resolution")

        data = response.json()
        hash_ref = data["hash_ref"]

    # Build URL - Hash refs use blobs/ subdirectory, tags are symlinks at root
    if parsed.ref.startswith("@"):
        # User requested hash directly - use blobs path
        blob_name = hash_ref.lstrip("@")
        download_url = f"{ctx.server}/artifacts/{parsed.path}/blobs/{blob_name}"
    else:
        # User requested tag - use tag symlink path
        download_url = f"{ctx.server}/artifacts/{parsed.path}/{parsed.ref}"

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={"url": download_url},
                human_output="",
            )
        )
        return

    # Human output - bare URL for scripting
    click.echo(download_url)
