# Exit Codes Inventory for Magpie

This document contains a complete enumeration of all exit codes used in the magpie CLI and CTL commands.

## Exit Code Summary

| Code | Meaning | Usage |
|------|---------|-------|
| 0 | Success | Command completed successfully (default) |
| 1 | General error | All error conditions (ClickException, SystemExit(1), output_error) |

## Framework Behavior

### Click Framework
- `click.ClickException`: Always exits with code 1
- All CLI commands use Click's exception handling
- Unhandled exceptions also result in exit code 1

### Custom Exit Code Mechanisms

1. **`output_error()` function** (`src/magpie/cli/formatting.py:164`)
   - Default exit code: 1
   - Can specify custom exit code via `exit_code` parameter
   - Currently all calls use the default exit code 1

2. **`raise SystemExit(1)`** - Used in ctl commands for validation failures
3. **`raise click.ClickException()`** - Used throughout CLI commands

## Exit Code 1 Usage By Command

### CLI Commands (magpie)

All CLI commands raise `click.ClickException` for errors, which results in exit code 1:

- **status** (`src/magpie/cli/commands/status.py`)
  - Line 41: Config error (repo not initialized, missing token)
  - Line 54: Network/server error

- **push** (`src/magpie/cli/commands/push.py`)
  - Line 131: File not found
  - Line 140: Upload error

- **amend** (`src/magpie/cli/commands/amend.py`)
  - Line 44: Config error (repo not initialized)
  - Line 51: Artifact not found
  - Line 59: Network/server error
  - Line 83: Amend operation failed

- **ls** (`src/magpie/cli/commands/ls.py`)
  - Line 49: Config error (repo not initialized)
  - Line 65: Network/server error

- **config** (`src/magpie/cli/commands/config_cmd.py`)
  - Line 101: Config validation error
  - Line 107: Config file I/O error

- **url** (`src/magpie/cli/commands/url.py`)
  - Line 43: Config error (repo not initialized)
  - Line 51: Network/server error
  - Line 65: Tag not found

- **untag** (`src/magpie/cli/commands/untag.py`)
  - Line 42: Config error (repo not initialized)
  - Line 51: Network/server error
  - Line 66: Untag operation failed

- **get** (`src/magpie/cli/commands/get.py`)
  - Line 62: Config error (repo not initialized)
  - Line 70: Network/server error
  - Line 84: Tag not found
  - Line 135: Download failed (checksum mismatch)
  - Line 164: Download failed (temp file missing)
  - Line 197: Download failed (artifact path is directory)

- **info** (`src/magpie/cli/commands/info.py`)
  - Line 41: Config error (repo not initialized)
  - Line 49: Network/server error
  - Line 62: Tag not found

- **tag** (`src/magpie/cli/commands/tag.py`)
  - Line 42: Config error (repo not initialized)
  - Line 50: Network/server error
  - Line 66: Tag operation failed

### CTL Commands (magpie-ctl)

CTL commands use both `raise SystemExit(1)` and `click.ClickException`:

- **init** (`src/magpie/ctl/commands/init.py`)
  - Line 99: Provided admin token doesn't start with 'mgp_ADMIN_'
  - Line 111: Provided admin token is too short
  - Line 141: Token format validation failed
  - Line 180: Token creation failed (duplicate)

- **gc** (`src/magpie/ctl/commands/gc.py`)
  - Line 48: Config error (repo not initialized)
  - Line 55: Storage path doesn't exist
  - Line 73: Not all symlinks resolved (when --fail-unresolved used)
  - Line 79: Dangling symlinks found (when --fail-dangling used)
  - Line 105: Storage path doesn't exist (JSON output mode)
  - Line 110: Storage path doesn't exist (ClickException in human mode)
  - Line 150: GC operation failed

- **sync** (`src/magpie/ctl/commands/sync.py`)
  - Line 560: Sync tool not found (neither rclone nor aws CLI)
  - Line 568: S3 bucket not configured
  - Line 577: Storage path doesn't exist
  - Line 693: Backup operation failed
  - Line 752: Sync tool not found
  - Line 763: S3 bucket not configured
  - Line 772: Restore destination path validation error
  - Line 856: Restore operation failed
  - Line 1457: Sync tool not found
  - Line 1466: Storage path doesn't exist
  - Line 1577: Verify operation failed

- **flush-tag** (`src/magpie/ctl/commands/flush_tag.py`)
  - Line 56: Storage path doesn't exist (SystemExit)
  - Line 57: Storage path doesn't exist (ClickException)
  - Line 66: Artifact path validation failed
  - Line 72: Flush failed (SystemExit)
  - Line 73: Flush failed (ClickException)
  - Line 77: Tag parameter validation failed

- **token** (`src/magpie/ctl/commands/token.py`)
  - Line 67: Token creation failed
  - Line 73: Token listing failed
  - Line 207: Token revocation failed

## Error Codes for JSON Output

When using `--format json` or `--json-output`, errors include a code field in addition to exit code 1:

### Standard Error Codes (`src/magpie/cli/formatting.py:192`)

- **NOT_FOUND**: Resource not found (HTTP 404)
- **UNAUTHORIZED**: Authentication failed (HTTP 401)
- **FORBIDDEN**: Permission denied (HTTP 403)
- **CONFLICT**: Resource conflict (HTTP 409)
- **VALIDATION_ERROR**: Input validation failed (HTTP 400)
- **SERVER_ERROR**: Server-side error (HTTP 5xx)
- **NETWORK_ERROR**: Network connectivity issues
- **IO_ERROR**: File system I/O errors
- **CONFIG_ERROR**: Configuration errors

These codes are mapped from HTTP status codes via `http_status_to_error_code()` (line 206).

## Error Handling Flow

### CLI Commands (magpie)

1. **Configuration errors**: Check for repo initialization, read config
   - Exit code 1 via `click.ClickException`

2. **Network/HTTP errors**: API calls to server
   - Handled by `handle_response_error()` or `handle_http_error()`
   - Exit code 1 via `click.ClickException` (human mode) or `output_error()` (JSON mode)

3. **Operation failures**: Command-specific errors
   - Exit code 1 via `click.ClickException`

### CTL Commands (magpie-ctl)

1. **Validation errors**: Check inputs before operations
   - Exit code 1 via `raise SystemExit(1)` or `click.ClickException`

2. **Operation failures**: Storage operations, subprocess calls
   - Exit code 1 via `raise SystemExit(1)` or `click.ClickException`

## Custom Exceptions (Do Not Set Exit Codes)

The following custom exceptions are defined but handled internally and do not directly set exit codes:

- `ValidationError` (`src/magpie/validation.py:23`)
- `TokenError` (`src/magpie/auth/service.py:54`)
- `TokenExistsError` (`src/magpie/auth/service.py:63`)
- `TokenFormatError` (`src/magpie/auth/service.py:71`)
- `DurationParseError` (`src/magpie/cli/config.py:131`)
- `UploadSizeExceededError` (`src/magpie/server/routes/upload.py:23`)
- `ParseError` (`src/magpie/cli/commands/parse.py:40`)
- `CtlCommandError` (`src/magpie/server/subprocess_utils.py:15`)
- `StorageError` (`src/magpie/storage/exceptions.py:6`)
- `ArtifactNotFoundError` (`src/magpie/storage/exceptions.py:12`)
- `BlobExistsError` (`src/magpie/storage/exceptions.py:18`)
- `ManifestCorruptError` (`src/magpie/storage/exceptions.py:24`)
- `HashMismatchError` (`src/magpie/storage/exceptions.py:30`)
- `InvalidArtifactPathError` (`src/magpie/storage/exceptions.py:36`)

These exceptions are caught and re-raised as `click.ClickException` or handled via `output_error()`, ultimately resulting in exit code 1.

## Conclusion

**Magpie uses a simple two-state exit code system:**
- Exit code 0: Success
- Exit code 1: Any error condition

This follows standard Unix conventions where 0 means success and non-zero means failure. The specific error type is communicated through error messages (human mode) or error codes in JSON output (JSON mode), not through distinct exit codes.
