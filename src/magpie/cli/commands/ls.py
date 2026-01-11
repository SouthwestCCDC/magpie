"""List command for viewing artifact versions."""

from __future__ import annotations

import click

from magpie.cli import CLIContext


@click.command(name="ls")
@click.argument("artifact_path")
@click.pass_obj
def ls(ctx: CLIContext, artifact_path: str) -> None:
    """List all versions of an artifact.

    ARTIFACT_PATH is the artifact path to list (e.g., "images/ubuntu").

    Examples:

        magpie ls images/ubuntu

        magpie ls builds/app
    """
    if not ctx.server:
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    with ctx.get_client() as client:
        if ctx.debug:
            click.echo(f"Listing versions for {artifact_path}...", err=True)

        response = client.get(f"/api/v1/artifacts/{artifact_path}")

        if response.status_code == 404:
            click.echo(f"No artifact found at: {artifact_path}")
            return
        if response.status_code != 200:
            _handle_error(response)

        data = response.json()

    versions = data.get("versions", [])

    if not versions:
        click.echo(f"No versions found for: {artifact_path}")
        return

    # Print table header
    click.echo(f"{'HASH':<12} {'TAGS':<20} {'UPLOADED_BY':<15} {'UPLOADED_AT'}")
    click.echo("-" * 70)

    # Print each version
    for version in versions:
        hash_ref = version["hash_ref"]
        tags = ", ".join(version.get("tags", [])) or "(untagged)"
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
