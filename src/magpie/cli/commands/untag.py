"""Untag command for removing artifact tags."""

from __future__ import annotations

import click

from magpie.cli import CLIContext
from magpie.cli.errors import handle_http_error
from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path


@click.command()
@click.argument("artifact_path")
@click.argument("tag_name")
@click.pass_obj
def untag(ctx: CLIContext, artifact_path: str, tag_name: str) -> None:
    """Remove a tag from an artifact.

    ARTIFACT_PATH is the artifact path (e.g., images/ubuntu).

    TAG_NAME is the tag to remove.

    Examples:

        magpie untag images/ubuntu v1.0

        magpie untag builds/app old-release
    """
    if not ctx.server:
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    # Normalize artifact path
    try:
        artifact_path = normalize_artifact_path(artifact_path)
    except InvalidArtifactPathError as e:
        raise click.ClickException(str(e))

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Removing tag '{tag_name}' from {artifact_path}...", err=True)

        response = client.delete(
            f"/api/v1/artifacts/{artifact_path}/tags/{tag_name}",
        )

        if response.status_code == 404:
            raise click.ClickException(f"Tag not found: {artifact_path}:{tag_name}")
        if response.status_code != 204:
            handle_http_error(response, "Untag", ctx.token)

    click.echo(f"Removed tag '{tag_name}' from {artifact_path}")
