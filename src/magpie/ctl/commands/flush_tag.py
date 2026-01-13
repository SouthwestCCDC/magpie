"""Flush-tag command for removing a tag from all artifacts globally."""

from __future__ import annotations

import json

import click

from magpie.ctl import CTLContext
from magpie.storage.service import StorageService
from magpie.validation import ValidationError


@click.command(name="flush-tag")
@click.argument("tag_name")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be affected without actually removing tags.",
)
@click.option(
    "--json-output",
    is_flag=True,
    help="Output results as JSON (for server subprocess integration).",
)
@click.pass_obj
def flush_tag(
    ctx: CTLContext,
    tag_name: str,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Remove a tag from all artifacts globally.

    TAG_NAME is the tag to remove from all artifacts.

    This operation walks the entire storage filesystem and removes the
    specified tag from every artifact that has it. Use --dry-run to see
    what would be affected without making changes.

    Examples:

        magpie-ctl flush-tag old-release --dry-run

        magpie-ctl flush-tag deprecated

        magpie-ctl flush-tag release --json-output
    """
    settings = ctx.settings
    storage_path = settings.storage_path

    if not storage_path.exists():
        if json_output:
            error_data = {"error": f"Storage path does not exist: {storage_path}"}
            click.echo(json.dumps(error_data))
            raise SystemExit(1)
        raise click.ClickException(f"Storage path does not exist: {storage_path}")

    if ctx.debug and not json_output:
        click.echo(f"Storage path: {storage_path}", err=True)
        action = "Would flush" if dry_run else "Flushing"
        click.echo(f"{action} tag '{tag_name}' globally...", err=True)

    # Use StorageService for the actual flush operation
    storage_service = StorageService(settings)
    try:
        result = storage_service.flush_tag(tag_name, dry_run=dry_run)
    except ValidationError as e:
        if json_output:
            error_data = {"error": str(e)}
            click.echo(json.dumps(error_data))
            raise SystemExit(1)
        raise click.ClickException(str(e))

    if json_output:
        output = {
            "tag_name": result.tag_name,
            "dry_run": dry_run,
            "count": result.count,
            "affected_artifacts": result.affected_artifacts,
        }
        click.echo(json.dumps(output))
        return

    # Human-readable output
    if dry_run:
        click.echo(f"Would remove tag '{tag_name}' from {result.count} artifact(s)")
    else:
        click.echo(f"Removed tag '{tag_name}' from {result.count} artifact(s)")

    if result.affected_artifacts:
        click.echo("Affected artifacts:")
        for artifact in result.affected_artifacts:
            click.echo(f"  - {artifact}")
