"""Verify command for checking stored blobs against their recorded SHA-256."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import click

from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    ExitCode,
    is_json_output,
    output_error,
    output_result,
)
from magpie.cli.progress import count_progress
from magpie.ctl import CTLContext
from magpie.logging_config import configure_logging
from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.verify import VerifyResult, count_artifacts, resolve_scope, run_verify
from magpie.utils.formatting import format_size


@click.command()
@click.option(
    "--path",
    "path_prefix",
    default=None,
    help="Only verify artifacts under this logical path prefix.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=None,
    help="Stop after verifying this many blobs.",
)
@click.option(
    "--max-bytes",
    type=click.IntRange(min=1),
    default=None,
    help="Stop once this many bytes have been read.",
)
@click.option(
    "--max-issues",
    type=click.IntRange(min=1),
    default=1000,
    help="Report at most this many individual issues (counts stay complete).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress progress output.",
)
@click.pass_obj
def verify(
    ctx: CTLContext,
    path_prefix: str | None,
    limit: int | None,
    max_bytes: int | None,
    max_issues: int,
    quiet: bool,
) -> None:
    """Verify stored blobs against the SHA-256 recorded in their metadata.

    Re-reads every blob in storage (streaming, never loading a blob into
    memory) and compares its digest to the full hash recorded at upload time.
    Detects bit-rot, truncation, tampering, missing blobs, and missing or
    corrupt metadata sidecars.

    A full scrub re-reads every byte in storage. Use --path to scope the walk
    (for example, to crypto material) and --limit/--max-bytes to bound the
    work of a scheduled partial scrub.

    Exit codes (suitable for cron/monitoring):

    \b
    0  all verified blobs matched their recorded hash
    1  operational error (storage unreadable, invalid path)
    3  blobs or metadata sidecars are missing
    5  content mismatch: stored bytes do not match recorded SHA-256

    Examples:

        magpie-ctl verify

        magpie-ctl verify --path openvpn

        magpie-ctl verify --limit 500 --quiet

        magpie-ctl --format json verify --path openvpn
    """
    settings = ctx.settings
    storage_path = settings.storage_path

    # magpie-ctl leaves structlog on its library defaults, which render to
    # stdout; verify's own log events would then corrupt the JSON document and
    # interleave with the human report. Route them to stderr instead so both
    # the structured log stream and stdout stay usable.
    configure_logging(settings)

    try:
        scope = resolve_scope(storage_path, path_prefix)
    except FileNotFoundError as e:
        _fail(str(e), ErrorCode.IO_ERROR)
    except InvalidArtifactPathError as e:
        _fail(str(e), ErrorCode.VALIDATION_ERROR)

    if ctx.debug and not is_json_output():
        click.echo(f"Storage path: {storage_path}", err=True)
        click.echo(f"Scope: {scope}", err=True)

    result = _run_verify_with_progress(
        storage_path=storage_path,
        path_prefix=path_prefix,
        scope=scope,
        limit=limit,
        max_bytes=max_bytes,
        max_issues=max_issues,
        quiet=quiet or is_json_output(),
    )

    exit_code = verify_exit_code(result)

    if is_json_output():
        output_result(
            CommandResult(
                data={"path_prefix": path_prefix, **result.to_dict()},
                human_output="",
            )
        )
    else:
        _print_report(result, max_issues)

    if exit_code != ExitCode.SUCCESS:
        raise SystemExit(exit_code)


def verify_exit_code(result: VerifyResult) -> int:
    """Map a verification result to a process exit code.

    Content mismatches outrank missing files, which outrank I/O errors, so a
    monitoring check can key on the most severe finding.

    Args:
        result: Completed VerifyResult.

    Returns:
        Exit code: 0 clean, 5 mismatch, 3 missing blob/metadata, 1 other error.
    """
    if result.mismatched > 0:
        return ExitCode.INTEGRITY_ERROR
    if result.missing_blob > 0 or result.missing_metadata > 0:
        return ExitCode.NOT_FOUND
    if result.corrupt_metadata > 0 or result.errors > 0:
        return ExitCode.GENERAL_ERROR
    return ExitCode.SUCCESS


def _fail(message: str, code: str) -> NoReturn:
    """Report a pre-flight failure in the active output format and exit."""
    if is_json_output():
        output_error(code, message)
    raise click.ClickException(message)


def _run_verify_with_progress(
    storage_path: Path,
    path_prefix: str | None,
    scope: Path,
    limit: int | None,
    max_bytes: int | None,
    max_issues: int,
    quiet: bool,
) -> VerifyResult:
    """Run verification with a rich progress bar over artifact directories."""
    total_artifacts = count_artifacts(scope)

    with count_progress("Verifying blobs", total_artifacts, quiet=quiet) as (progress, task_id):

        def progress_callback(phase: str, current: int, total: int) -> None:
            if progress is not None and task_id is not None:
                progress.update(task_id, completed=current)

        return run_verify(
            storage_path=storage_path,
            path_prefix=path_prefix,
            limit=limit,
            max_bytes=max_bytes,
            max_issues=max_issues,
            progress_callback=progress_callback,
        )


def _print_report(result: VerifyResult, max_issues: int) -> None:
    """Print issues and a summary to stdout."""
    for issue in result.issues:
        location = issue.artifact_path
        if issue.blob_ref:
            location = f"{location}/{issue.blob_ref}"
        click.echo(f"{issue.status.value.upper()}: {location} - {issue.message}")
        if issue.expected_hash and issue.actual_hash:
            click.echo(f"  expected {issue.expected_hash}")
            click.echo(f"  actual   {issue.actual_hash}")

    hidden = result.total_issues - len(result.issues)
    if hidden > 0:
        click.echo(f"... {hidden} more issue(s) not shown (--max-issues {max_issues})")

    click.echo("")
    click.echo("Verify Summary:")
    click.echo(f"  Artifacts scanned: {result.artifacts_scanned}")
    click.echo(f"  Blobs scanned: {result.blobs_scanned} ({format_size(result.bytes_read)} read)")
    click.echo(f"  OK: {result.ok}")
    click.echo(f"  Mismatched: {result.mismatched}")
    click.echo(f"  Missing blob: {result.missing_blob}")
    click.echo(f"  Missing metadata: {result.missing_metadata}")
    click.echo(f"  Corrupt metadata: {result.corrupt_metadata}")
    click.echo(f"  Errors: {result.errors}")

    if result.stopped_early:
        click.echo("  Stopped early: work bound reached (--limit/--max-bytes)")
