"""Amend command for updating artifact metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import httpx

from magpie.cli import CLIContext
from magpie.cli.commands.get import parse_artifact_ref


@click.command()
@click.argument("artifact_ref")
@click.option("--source-uri", help="Update source URI (use empty string to clear).")
@click.pass_obj
def amend(ctx: CLIContext, artifact_ref: str, source_uri: str | None) -> None:
    """Amend metadata for an artifact.

    ARTIFACT_REF is the artifact path and optional ref (path:ref format).
    If no ref is specified, "latest" is used.

    Currently supports updating the source_uri field.

    Examples:

        magpie amend images/ubuntu:latest --source-uri https://github.com/example/repo

        magpie amend images/ubuntu:@abc12345 --source-uri ""

        magpie amend builds/app --source-uri https://ci.example.com/builds/123
    """
    if not ctx.server:
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    if source_uri is None:
        raise click.ClickException("No metadata updates specified. Use --source-uri to update.")

    path, ref = parse_artifact_ref(artifact_ref)

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Amending metadata for {path}:{ref}...", err=True)

        # Build request body
        body: dict[str, str | None] = {}
        if source_uri is not None:
            # Empty string means clear the field (set to null)
            body["source_uri"] = source_uri if source_uri else None

        response = client.patch(
            f"/api/v1/artifacts/{path}/{ref}",
            json=body,
        )

        if response.status_code == 404:
            raise click.ClickException(f"Artifact not found: {path}:{ref}")
        if response.status_code != 200:
            _handle_error(response)

        data = response.json()

    # Display updated metadata
    click.echo(f"Updated: {path}:{data['hash_ref']}")
    click.echo(f"  Source URI: {data.get('source_uri') or '(none)'}")
    click.echo(f"  Tags: {', '.join(data.get('tags', []))}")


def _handle_error(response: "httpx.Response") -> None:
    """Handle HTTP error responses."""
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    raise click.ClickException(f"Amend failed ({response.status_code}): {detail}")
