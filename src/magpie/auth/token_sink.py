"""Delivery sinks for the admin bootstrap token.

magpie is both the *generator* and the *deliverer* of the first-boot admin
token, so no secret needs to pre-exist for an operator to provision.
``deliver_admin_token`` dispatches to one of four sinks selected by
``MAGPIE_ADMIN_TOKEN_SINK``:

- ``file``: write the token to a root-only (0600) file.
- ``exec``: pipe the token to an operator-configured command via stdin only
  (never argv or env), so it can't leak via ``ps`` or ``/proc/<pid>/environ``.
- ``discard``: deliver nothing. The operator mints a token later via an
  interactive ``magpie-ctl token create --scope admin``, which prints to
  their exec session (not the container log stream) and is therefore safe.
- ``stdout``: print the token to stdout. Opt-in only; never the default.

Fail-closed: ``file`` and ``exec`` raise ``TokenSinkError`` on any delivery
failure. Callers MUST treat this as fatal and abort rather than start serving
with a generated-but-undelivered token. ``discard`` never raises -- silence is
the intended behavior, not a failure.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess  # nosec B404 - operator-configured command, no shell=True, token via stdin only
from typing import TYPE_CHECKING

import click

from magpie.cli.formatting import is_json_output

if TYPE_CHECKING:
    from magpie.config import MagpieSettings


class TokenSinkError(RuntimeError):
    """Raised when the admin token sink is unconfigured or fails to deliver.

    Never includes the token value in its message.
    """


def deliver_admin_token(token: str, settings: MagpieSettings, *, action: str) -> None:
    """Deliver a freshly generated or rotated admin token via the configured sink.

    Emits a valueless structured log event (``admin_token_generated``) naming
    the sink used -- never the token value -- once delivery succeeds (or is
    intentionally skipped, for ``discard``).

    Args:
        token: Plaintext admin token to deliver.
        settings: MagpieSettings with admin_token_sink and sink-specific options.
        action: Short label for what triggered delivery (e.g. "init" or
            "rotate"), included in the log event for auditability.

    Raises:
        TokenSinkError: If no sink is configured, or a delivering sink
            (file/exec) fails to deliver the token. Callers must abort
            startup/the command on this error and must not start serving.
    """
    sink = settings.admin_token_sink
    if sink is None:
        raise TokenSinkError(
            "MAGPIE_ADMIN_TOKEN_SINK is not set. An explicit sink is required "
            "(file, exec, discard, or stdout) -- there is no default, by design. "
            "See docs/installation.md for details."
        )

    if sink == "file":
        _deliver_file(token, settings)
    elif sink == "exec":
        _deliver_exec(token, settings)
    elif sink == "discard":
        pass  # Intentional non-delivery; not a failure.
    elif sink == "stdout":
        _deliver_stdout(token)
    else:  # pragma: no cover - guarded by Literal type at the config layer
        raise TokenSinkError(f"Unknown admin token sink: {sink}")

    _log_admin_token_generated(sink, action)


def _log_admin_token_generated(sink: str, action: str) -> None:
    """Emit a valueless structured log line naming the sink used -- never the token.

    Written directly to stderr (never stdout, which is reserved for command
    output -- e.g. `--format json` payloads). This is intentionally
    independent of structlog's global configuration: magpie-ctl commands
    don't consistently configure it, and structlog's per-module logger
    caching makes ad hoc reconfiguration within a single process unreliable
    (see the caution around `cache_logger_on_first_use` in gc.py).
    """
    event = {"event": "admin_token_generated", "sink": sink, "action": action}
    click.echo(json.dumps(event, sort_keys=True), err=True)


def _deliver_file(token: str, settings: MagpieSettings) -> None:
    """Write the token to a root-only (0600) file.

    Raises:
        TokenSinkError: If the path is unset, the file can't be written, or
            the path is a symlink (rejected -- see O_NOFOLLOW below).
    """
    path = settings.admin_token_sink_file_path
    if path is None:  # pragma: no cover - guarded by config's derive_paths()
        raise TokenSinkError("MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH is not set for sink=file")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_NOFOLLOW rejects the open outright if `path` is a symlink, so a
        # pre-placed symlink at the configured path can't redirect the
        # plaintext token to an attacker-controlled location. mode=0o600 on
        # O_CREAT only applies when the file is newly created -- if `path`
        # already exists (e.g. an attacker pre-placed a 0666 file to widen
        # the window), O_TRUNC truncates it but leaves its existing mode
        # alone. So fchmod the fd to 0600 BEFORE writing any token bytes,
        # rather than chmod-ing the path afterward: the token is never
        # written into a file with a wider-than-0600 mode, even briefly.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except BaseException:
            # fd isn't owned by anything yet (os.fdopen hasn't run) -- close
            # it ourselves so a failure here can't leak a file descriptor.
            os.close(fd)
            raise
        # os.fdopen takes ownership of fd from this point on and closes it
        # (even on error) via the context manager.
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
    except OSError as e:
        raise TokenSinkError(f"admin token file sink failed to write {path}: {e}") from e


def _deliver_exec(token: str, settings: MagpieSettings) -> None:
    """Pipe the token to a configured command's stdin; never argv or env.

    Raises:
        TokenSinkError: If the command is unset, unparsable, fails to start,
            times out, or exits non-zero.
    """
    command = settings.admin_token_sink_exec_command
    if not command:  # pragma: no cover - guarded by caller in practice
        raise TokenSinkError("MAGPIE_ADMIN_TOKEN_SINK_EXEC_COMMAND is not set for sink=exec")

    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise TokenSinkError(f"admin token exec sink command could not be parsed: {e}") from e

    if not argv:
        raise TokenSinkError("admin token exec sink command is empty after parsing")

    try:
        result = subprocess.run(  # nosec B603 - operator-configured command, token via stdin only
            argv,
            input=token.encode("utf-8"),
            capture_output=True,
            timeout=settings.admin_token_sink_exec_timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TokenSinkError(f"admin token exec sink failed to run {argv[0]!r}: {e}") from e

    if result.returncode != 0:
        # Deliberately excludes the child's stderr text: the token is on its
        # stdin, and a command that echoes stdin (or otherwise reflects its
        # input) on failure could leak the token into this exception's
        # message -- which callers may then print or log. Report only the
        # byte count, never the content.
        stderr_len = len(result.stderr)
        raise TokenSinkError(
            f"admin token exec sink command {argv[0]!r} exited with status "
            f"{result.returncode} ({stderr_len} byte(s) on stderr, omitted from this "
            "error to avoid the risk of an echoed token leaking into logs)"
        )


def _deliver_stdout(token: str) -> None:
    """Print the token to stdout with an explicit insecurity warning.

    In `--format json` mode, the caller is responsible for embedding the
    token in its own JSON payload instead -- printing this human-oriented
    banner there would corrupt that machine-readable stdout output. The
    warning itself is still surfaced, on stderr, in that mode.
    """
    if is_json_output():
        click.echo(
            "WARNING: MAGPIE_ADMIN_TOKEN_SINK=stdout is insecure -- the admin token "
            "is included in this JSON output and will be captured by container logs "
            "and any log shippers.",
            err=True,
        )
        return

    click.echo("")
    click.echo("=" * 60)
    click.echo("WARNING: MAGPIE_ADMIN_TOKEN_SINK=stdout is insecure -- this token")
    click.echo("WILL be captured by container logs and any log shippers.")
    click.echo("ADMIN TOKEN (store securely, only shown once!):")
    click.echo(token)
    click.echo("=" * 60)
