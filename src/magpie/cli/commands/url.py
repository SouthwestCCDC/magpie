"""URL command for outputting download URLs for scripting."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import httpx

from magpie.cli import CLIContext
from magpie.cli.commands.parse import ParseError, parse_artifact_ref


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
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    try:
        parsed = parse_artifact_ref(artifact_ref)
    except ParseError as e:
        raise click.ClickException(str(e))

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Resolving {parsed.path}:{parsed.ref}...", err=True)

        # Get info to resolve ref to hash_ref
        response = client.get(f"/api/v1/artifacts/{parsed.path}/{parsed.ref}/info")

        if response.status_code == 404:
            raise click.ClickException(f"Artifact not found: {parsed.path}:{parsed.ref}")
        if response.status_code != 200:
            _handle_error(response)

        data = response.json()
        hash_ref = data["hash_ref"]

    # Output bare URL (no newline issues - click.echo adds one)
    # Hash refs use blobs/ subdirectory, tags are symlinks at root
    if parsed.ref.startswith("@"):
        # User requested hash directly - use blobs path
        blob_name = hash_ref.lstrip("@")
        download_url = f"{ctx.server}/artifacts/{parsed.path}/blobs/{blob_name}"
    else:
        # User requested tag - use tag symlink path
        download_url = f"{ctx.server}/artifacts/{parsed.path}/{parsed.ref}"
    click.echo(download_url)


def _handle_error(response: "httpx.Response") -> None:
    """Handle HTTP error responses."""
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    raise click.ClickException(f"URL resolution failed ({response.status_code}): {detail}")
