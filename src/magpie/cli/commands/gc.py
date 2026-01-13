"""GC command for triggering remote garbage collection."""

from __future__ import annotations

import json

import click

from magpie.cli import CLIContext
from magpie.cli.errors import handle_http_error, mask_token
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    http_status_to_error_code,
    is_json_output,
    output_error,
    output_result,
)
from magpie.utils.formatting import format_size


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview what would be deleted without making changes.",
)
@click.pass_obj
def gc(ctx: CLIContext, dry_run: bool) -> None:
    """Trigger remote garbage collection (requires admin token).

    Walks all artifact directories on the server and:
    - Identifies blobs not referenced by any tag
    - Deletes untagged blobs older than retention period
    - Reconciles symlinks to match manifests

    Use --dry-run to see what would be deleted without making changes.

    Examples:

        magpie gc --dry-run

        magpie gc
    """
    if not ctx.server:
        msg = "No server configured. Use --server or set MAGPIE_SERVER."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)

    if not ctx.token:
        msg = "No token configured. Use --token or set MAGPIE_TOKEN. Admin token required."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)

    with ctx.get_client() as client:
        if ctx.debug:
            action = "Previewing" if dry_run else "Running"
            click.echo(f"{action} garbage collection...", err=True)

        params = {}
        if dry_run:
            params["dry_run"] = "true"

        response = client.post("/api/v1/gc", params=params)

        if response.status_code == 401:
            msg = f"Authentication failed. Check your token (token: {mask_token(ctx.token)})"
            if is_json_output():
                output_error(ErrorCode.UNAUTHORIZED, msg)
                return  # output_error never returns, but explicit for clarity
            raise click.ClickException(msg)
        if response.status_code == 403:
            msg = f"Admin token required for garbage collection (token: {mask_token(ctx.token)})"
            if is_json_output():
                output_error(ErrorCode.FORBIDDEN, msg)
                return  # output_error never returns, but explicit for clarity
            raise click.ClickException(msg)
        if response.status_code not in (200,):
            if is_json_output():
                try:
                    detail = response.json().get("detail", response.text)
                except (json.JSONDecodeError, ValueError, KeyError):
                    detail = response.text
                output_error(http_status_to_error_code(response.status_code), detail)
                return  # output_error never returns, but explicit for clarity
            handle_http_error(response, "GC", ctx.token)

        data = response.json()

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "dry_run": dry_run,
                    "blobs_removed": data["blobs_deleted"],
                    "bytes_reclaimed": data["space_reclaimed_bytes"],
                    "errors": [],  # Server-side GC currently doesn't report individual errors
                    "artifacts_scanned": data.get("artifacts_scanned", 0),
                    "blobs_found": data.get("blobs_found", 0),
                    "symlinks_checked": data.get("symlinks_checked", 0),
                    "symlinks_fixed": data.get("symlinks_fixed", 0),
                    "items_removed": data.get("items_removed", 0),
                },
                human_output="",
            )
        )
        return

    # Human output
    if dry_run:
        click.echo("GC Preview (dry run):")
    else:
        click.echo("GC Complete:")

    click.echo(f"  Artifacts scanned: {data['artifacts_scanned']}")
    click.echo(f"  Blobs found: {data['blobs_found']}")
    click.echo(f"  Symlinks checked: {data['symlinks_checked']}")
    click.echo(f"  Symlinks fixed: {data['symlinks_fixed']}")

    action = "Would delete" if dry_run else "Deleted"
    click.echo(f"  {action}: {data['blobs_deleted']} blob(s)")
    click.echo(
        f"  Space {'reclaimable' if dry_run else 'reclaimed'}: {format_size(data['space_reclaimed_bytes'])}"
    )

    # Display items removed (directories + manifest files)
    items_removed = data.get("items_removed", 0)
    if items_removed > 0:
        action = "Would remove" if dry_run else "Removed"
        click.echo(f"  {action} empty items: {items_removed}")
