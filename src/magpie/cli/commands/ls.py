"""List command for viewing artifact versions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import httpx

from magpie.cli import CLIContext
from magpie.cli.commands.parse import ParseError, parse_artifact_path
from magpie.cli.errors import handle_response_error
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)


@click.command(name="ls")
@click.argument("artifact_path", required=False)
@click.option(
    "--recursive",
    "-r",
    is_flag=True,
    help="List artifacts recursively (default: only current level)",
)
@click.pass_obj
def ls(ctx: CLIContext, artifact_path: str | None, recursive: bool) -> None:
    """List artifacts or versions.

    With no arguments, lists artifact paths at the current level.
    With a partial path, lists artifact paths under that prefix.
    With a full artifact path, lists all versions of that artifact.

    By default, only lists artifacts at the current level. Use --recursive
    to list all artifacts recursively.

    Examples:

        magpie ls                    # List artifact paths (current level)

        magpie ls --recursive        # List all artifact paths recursively

        magpie ls test               # List paths under test/ (current level)

        magpie ls -r test            # List all paths under test/ recursively

        magpie ls test/myartifact    # List versions of test/myartifact

        magpie ls /test/myartifact   # Leading slash is normalized
    """
    if not ctx.server:
        msg = "No server configured. Use --server or set MAGPIE_SERVER."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)

    with ctx.get_client() as client:
        # Case 1: No path provided - list all artifact paths
        # Also treat slash-only paths ("/", "//", etc.) as empty to list all artifacts
        if not artifact_path or artifact_path.strip("/") == "":
            _list_paths(ctx, client, "", recursive)
            return

        # Parse path, stripping any ref if accidentally provided (e.g., path:tag)
        try:
            normalized_path = parse_artifact_path(artifact_path)
        except ParseError as e:
            if is_json_output():
                output_error(ErrorCode.VALIDATION_ERROR, str(e))
                return  # output_error never returns, but explicit for clarity
            raise click.ClickException(str(e))

        if ctx.debug:
            click.echo(f"Listing for path: {normalized_path}...", err=True)

        # Case 2: Try to list versions at the exact path
        response = client.get(f"/api/v1/artifacts/{normalized_path}")

        if response.status_code == 200:
            data = response.json()
            versions = data.get("versions", [])

            if versions:
                # Found versions - display them
                if is_json_output():
                    output_result(
                        CommandResult(
                            data={
                                "artifact": normalized_path,
                                "versions": [
                                    {
                                        "version": v["hash_ref"],
                                        "hash": v.get("hash", ""),
                                        "tags": v.get("tags", []),
                                        "created": v.get("uploaded_at", ""),
                                    }
                                    for v in versions
                                ],
                            },
                            human_output="",
                        )
                    )
                    return
                _display_versions_table(versions)
                return
            # No versions but path exists - fall through to prefix listing

        # Case 3: Treat as prefix and list matching paths
        _list_paths(ctx, client, normalized_path, recursive)


def _list_paths(ctx: CLIContext, client: "httpx.Client", prefix: str, recursive: bool) -> None:
    """List artifact paths matching a prefix.

    Args:
        ctx: CLI context.
        client: HTTP client.
        prefix: Path prefix to filter by (empty string for all).
        recursive: If True, list all artifacts recursively. If False, list only current level.
    """
    if ctx.debug:
        mode = "recursive" if recursive else "top-level only"
        if prefix:
            click.echo(f"Listing paths with prefix: {prefix} ({mode})...", err=True)
        else:
            click.echo(f"Listing artifact paths ({mode})...", err=True)

    response = client.get("/api/v1/artifacts", params={"prefix": prefix, "recursive": recursive})

    if response.status_code != 200:
        handle_response_error(response, "List", ctx.token)

    data = response.json()
    paths = data.get("paths", [])

    # Transform paths to immediate children for directory-style UX when not recursive
    if not recursive:
        paths = _extract_immediate_children(paths, prefix)

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "prefix": prefix,
                    "paths": paths,
                },
                human_output="",
            )
        )
        return

    # Human output
    if not paths:
        if prefix:
            click.echo(f"No artifacts found matching: {prefix}")
        else:
            click.echo("No artifacts found.")
        return

    # Display paths one per line
    for path in paths:
        click.echo(path)


def _extract_immediate_children(paths: list[str], prefix: str) -> list[str]:
    """Extract immediate children from full artifact paths.

    For non-recursive listing, shows only the immediate child level relative
    to the prefix, similar to filesystem ls behavior.

    Args:
        paths: Full artifact paths from storage.
        prefix: Current prefix (empty string for root).

    Returns:
        List of immediate child names, with directories marked with trailing /.

    Examples:
        prefix="" and paths=["test/artifact1", "test/artifact2", "test/sub/deep", "images/ubuntu"]
        -> ["images/", "test/"]

        prefix="test" and paths=["test/artifact1", "test/artifact2", "test/sub/deep"]
        -> ["artifact1", "artifact2", "sub/"]
    """
    # Normalize prefix - strip leading/trailing slashes for consistency
    normalized_prefix = prefix.strip("/")

    children: set[str] = set()

    for path in paths:
        # Remove prefix from path if present
        if normalized_prefix:
            # Path should start with prefix/ or be exactly prefix
            if path == normalized_prefix:
                # Prefix itself is an artifact
                children.add(normalized_prefix.split("/")[-1])
                continue
            elif path.startswith(normalized_prefix + "/"):
                # Get remainder after prefix
                remainder = path[len(normalized_prefix) + 1 :]
            else:
                # Path doesn't match prefix (shouldn't happen)
                continue
        else:
            # No prefix - working from root
            remainder = path

        # Extract first segment of remainder
        if "/" in remainder:
            # There's more depth - this is a directory
            first_segment = remainder.split("/", 1)[0]
            children.add(first_segment + "/")
        else:
            # No more slashes - this is an artifact at current level
            children.add(remainder)

    return sorted(children)


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
    except (ValueError, IndexError):
        return dt_str
