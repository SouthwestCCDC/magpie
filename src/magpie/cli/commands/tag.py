"""Tag command for creating artifact tags."""

from __future__ import annotations

import click

from magpie.cli import CLIContext
from magpie.cli.commands.parse import ParseError, parse_artifact_ref
from magpie.cli.errors import handle_http_error


@click.command()
@click.argument("artifact_ref")
@click.option("--as", "tag_name", required=True, help="Tag name to create.")
@click.pass_obj
def tag(ctx: CLIContext, artifact_ref: str, tag_name: str) -> None:
    """Create a tag pointing to an artifact version.

    ARTIFACT_REF is the artifact path and optional ref (path:ref format).
    If no ref is specified, "latest" is used.

    Examples:

        magpie tag images/ubuntu:latest --as v1.0

        magpie tag images/ubuntu:@abc12345 --as stable

        magpie tag builds/app --as release-1.0
    """
    if not ctx.server:
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    try:
        parsed = parse_artifact_ref(artifact_ref)
    except ParseError as e:
        raise click.ClickException(str(e))

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Creating tag '{tag_name}' on {parsed.path}:{parsed.ref}...", err=True)

        response = client.post(
            f"/api/v1/artifacts/{parsed.path}/{parsed.ref}/tags",
            json={"tag_name": tag_name},
        )

        if response.status_code == 404:
            raise click.ClickException(f"Artifact not found: {parsed.path}:{parsed.ref}")
        if response.status_code != 200:
            handle_http_error(response, "Tag", ctx.token)

        data = response.json()

    click.echo(f"Tagged {data['hash_ref']} as '{tag_name}'")
    click.echo(f"All tags: {', '.join(data.get('tags', []))}")
