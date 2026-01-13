"""Flush-tag command for removing a tag from all artifacts globally."""

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

# Tags that require --force to flush due to their common importance
PROTECTED_TAGS = frozenset({"latest", "stable", "production", "prod", "release"})


@click.command(name="flush-tag")
@click.argument("tag_name")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be affected without actually removing tags.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Required to flush protected tags (latest, stable, production, etc).",
)
@click.pass_obj
def flush_tag(ctx: CLIContext, tag_name: str, dry_run: bool, yes: bool, force: bool) -> None:
    """Remove a tag from all artifacts globally.

    TAG_NAME is the tag to remove from all artifacts.

    This operation walks the entire storage filesystem and removes the
    specified tag from every artifact that has it. Use --dry-run to see
    what would be affected without making changes.

    Examples:

        magpie flush-tag old-release --dry-run

        magpie flush-tag deprecated --yes

        magpie flush-tag latest --force --yes
    """
    if not ctx.server:
        msg = "No server configured. Use --server or set MAGPIE_SERVER."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    # Refuse to flush protected tags without --force
    if tag_name.lower() in PROTECTED_TAGS and not force:
        msg = (
            f"Tag '{tag_name}' is protected. Use --force to confirm you want to remove "
            f"this tag from ALL artifacts. Protected tags: {', '.join(sorted(PROTECTED_TAGS))}"
        )
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, msg)
        else:
            raise click.ClickException(msg)

    # In JSON mode, require --yes to skip confirmation (no interactive prompts)
    if is_json_output() and not dry_run and not yes:
        output_error(
            ErrorCode.VALIDATION_ERROR, "In JSON mode, use --yes to confirm destructive operations."
        )

    # Confirm before proceeding (unless --yes or --dry-run)
    if not dry_run and not yes:
        click.confirm(
            f"Remove tag '{tag_name}' from ALL artifacts? This walks the entire filesystem",
            abort=True,
        )

    with ctx.get_client() as client:
        if ctx.debug:
            action = "Would flush" if dry_run else "Flushing"
            click.echo(f"{action} tag '{tag_name}' globally...", err=True)

        params = {
            "confirm_walk_filesystem": "true",
        }
        if dry_run:
            params["dry_run"] = "true"

        response = client.post(
            f"/api/v1/tags/{tag_name}/flush",
            params=params,
        )

        if response.status_code == 400:
            if is_json_output():
                try:
                    detail = response.json().get("detail", response.text)
                except (json.JSONDecodeError, ValueError, KeyError):
                    detail = response.text
                output_error(http_status_to_error_code(response.status_code), detail)
            else:
                handle_http_error(response, "Flush", ctx.token)
        if response.status_code not in (200,):
            if is_json_output():
                try:
                    detail = response.json().get("detail", response.text)
                except (json.JSONDecodeError, ValueError, KeyError):
                    detail = response.text
                output_error(http_status_to_error_code(response.status_code), detail)
            else:
                handle_http_error(response, "Flush", ctx.token)

        data = response.json()
        affected_count = data.get("count", 0)
        affected_artifacts = data.get("affected_artifacts", [])

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "tag": tag_name,
                    "dry_run": dry_run,
                    "artifacts_affected": affected_count,
                    "artifacts": affected_artifacts,
                },
                human_output="",
            )
        )
        return

    # Human output
    if dry_run:
        click.echo(f"Would remove tag '{tag_name}' from {affected_count} artifact(s)")
    else:
        click.echo(f"Removed tag '{tag_name}' from {affected_count} artifact(s)")

    if affected_artifacts:
        click.echo("Affected artifacts:")
        for artifact in affected_artifacts:
            click.echo(f"  - {artifact}")
