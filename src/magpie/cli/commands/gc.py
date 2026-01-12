"""GC command for triggering remote garbage collection."""

from __future__ import annotations

import click

from magpie.cli import CLIContext
from magpie.cli.utils import handle_http_error, mask_token


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
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    if not ctx.token:
        raise click.ClickException(
            "No token configured. Use --token or set MAGPIE_TOKEN. Admin token required."
        )

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
            raise click.ClickException(msg)
        if response.status_code == 403:
            msg = f"Admin token required for garbage collection (token: {mask_token(ctx.token)})"
            raise click.ClickException(msg)
        if response.status_code not in (200,):
            handle_http_error(response, "GC", ctx.token)

        data = response.json()

    # Display results
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
        f"  Space {'reclaimable' if dry_run else 'reclaimed'}: {_format_size(data['space_reclaimed_bytes'])}"
    )

    # Display items removed (directories + manifest files)
    items_removed = data.get("items_removed", 0)
    if items_removed > 0:
        action = "Would remove" if dry_run else "Removed"
        click.echo(f"  {action} empty items: {items_removed}")


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Human-readable size string (e.g., "1.5 MB").
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
