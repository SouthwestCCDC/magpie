"""Info command for showing artifact metadata."""

from __future__ import annotations

import click

from magpie.cli import CLIContext
from magpie.cli.commands.parse import ParseError, parse_artifact_ref
from magpie.cli.utils import handle_http_error


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
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    try:
        parsed = parse_artifact_ref(artifact_ref)
    except ParseError as e:
        raise click.ClickException(str(e))

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Fetching info for {parsed.path}:{parsed.ref}...", err=True)

        response = client.get(f"/api/v1/artifacts/{parsed.path}/{parsed.ref}/info")

        if response.status_code == 404:
            raise click.ClickException(f"Artifact not found: {parsed.path}:{parsed.ref}")
        if response.status_code != 200:
            handle_http_error(response, "Info", ctx.token)

        data = response.json()

    # Display detailed metadata
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
