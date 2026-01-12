"""Amend command for updating artifact metadata."""

from __future__ import annotations

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
        if is_json_output():
            output_error(
                ErrorCode.CONFIG_ERROR, "No server configured. Use --server or set MAGPIE_SERVER."
            )
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    if source_uri is None:
        if is_json_output():
            output_error(
                ErrorCode.VALIDATION_ERROR,
                "No metadata updates specified. Use --source-uri to update.",
            )
        raise click.ClickException("No metadata updates specified. Use --source-uri to update.")

    try:
        parsed = parse_artifact_ref(artifact_ref)
    except ParseError as e:
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, str(e))
        raise click.ClickException(str(e))

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Amending metadata for {parsed.path}:{parsed.ref}...", err=True)

        # Build request body
        body: dict[str, str | None] = {}
        updated_fields: list[str] = []
        if source_uri is not None:
            # Empty string means clear the field (set to null)
            body["source_uri"] = source_uri if source_uri else None
            updated_fields.append("source_uri")

        response = client.patch(
            f"/api/v1/artifacts/{parsed.path}/{parsed.ref}",
            json=body,
        )

        if response.status_code == 404:
            if is_json_output():
                output_error(ErrorCode.NOT_FOUND, f"Artifact not found: {parsed.path}:{parsed.ref}")
            raise click.ClickException(f"Artifact not found: {parsed.path}:{parsed.ref}")
        if response.status_code != 200:
            if is_json_output():
                try:
                    detail = response.json().get("detail", response.text)
                except Exception:
                    detail = response.text
                output_error(http_status_to_error_code(response.status_code), detail)
            handle_http_error(response, "Amend", ctx.token)

        data = response.json()

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "artifact": parsed.path,
                    "version": data["hash_ref"],
                    "updated_fields": updated_fields,
                },
                human_output="",
            )
        )
        return

    # Human output
    click.echo(f"Updated: {parsed.path}:{data['hash_ref']}")
    click.echo(f"  Source URI: {data.get('source_uri') or '(none)'}")
    click.echo(f"  Tags: {', '.join(data.get('tags', []))}")
