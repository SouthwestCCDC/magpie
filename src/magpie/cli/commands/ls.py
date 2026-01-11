"""List command for viewing artifact versions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from magpie.cli import CLIContext

if TYPE_CHECKING:
    import httpx


@click.command(name="ls")
@click.argument("artifact_path", required=False)
@click.pass_obj
def ls(ctx: CLIContext, artifact_path: str | None) -> None:
    """List artifacts or versions.

    With no arguments, lists all available artifact paths.
    With a partial path, lists artifact paths under that prefix.
    With a full artifact path, lists all versions of that artifact.

    Examples:

        magpie ls                    # List all artifact paths

        magpie ls test               # List paths under test/

        magpie ls test/myartifact    # List versions of test/myartifact

        magpie ls /test/myartifact   # Leading slash is normalized
    """
    if not ctx.server:
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    with ctx.get_client() as client:
        # Case 1: No path provided - list all artifact paths
        if not artifact_path:
            _list_paths(ctx, client, "")
            return

        # Normalize path: strip leading slashes
        normalized_path = artifact_path.lstrip("/")

        if ctx.debug:
            click.echo(f"Listing for path: {normalized_path}...", err=True)

        # Case 2: Try to list versions at the exact path
        response = client.get(f"/api/v1/artifacts/{normalized_path}")

        if response.status_code == 200:
            data = response.json()
            versions = data.get("versions", [])

            if versions:
                # Found versions - display them in table format
                _display_versions_table(versions)
                return
            # No versions but path exists - fall through to prefix listing

        # Case 3: Treat as prefix and list matching paths
        _list_paths(ctx, client, normalized_path)


def _list_paths(ctx: CLIContext, client: "httpx.Client", prefix: str) -> None:
    """List artifact paths matching a prefix.

    Args:
        ctx: CLI context.
        client: HTTP client.
        prefix: Path prefix to filter by (empty string for all).
    """
    if ctx.debug:
        if prefix:
            click.echo(f"Listing paths with prefix: {prefix}...", err=True)
        else:
            click.echo("Listing all artifact paths...", err=True)

    response = client.get("/api/v1/artifacts", params={"prefix": prefix})

    if response.status_code != 200:
        _handle_error(response)

    data = response.json()
    paths = data.get("paths", [])

    if not paths:
        if prefix:
            click.echo(f"No artifacts found matching: {prefix}")
        else:
            click.echo("No artifacts found.")
        return

    # Display paths one per line
    for path in paths:
        click.echo(path)


def _display_versions_table(versions: list[dict]) -> None:
    """Display versions in table format.

    Args:
        versions: List of version dictionaries from API response.
    """
    # Print table header
    click.echo(f"{'HASH':<12} {'TAGS':<20} {'UPLOADED_BY':<15} {'UPLOADED_AT'}")
    click.echo("-" * 70)

    # Print each version
    for version in versions:
        hash_ref = version["hash_ref"]
        tags = ", ".join(version.get("tags", [])) or "(none)"
        uploaded_by = version.get("uploaded_by", "unknown")
        uploaded_at = _format_datetime(version.get("uploaded_at", ""))

        # Truncate tags if too long
        if len(tags) > 18:
            tags = tags[:15] + "..."

        click.echo(f"{hash_ref:<12} {tags:<20} {uploaded_by:<15} {uploaded_at}")


def _format_datetime(dt_str: str) -> str:
    """Format ISO datetime string for display."""
    if not dt_str:
        return "unknown"
    # Parse ISO format and return readable format
    try:
        # Handle both with and without timezone
        if "T" in dt_str:
            date_part = dt_str.split("T")[0]
            time_part = dt_str.split("T")[1][:8]  # HH:MM:SS
            return f"{date_part} {time_part}"
        return dt_str
    except Exception:
        return dt_str


def _handle_error(response: "httpx.Response") -> None:
    """Handle HTTP error responses."""
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    raise click.ClickException(f"List failed ({response.status_code}): {detail}")
