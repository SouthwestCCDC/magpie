#!/bin/bash
# Claude Code hook: Auto-format Python files after Edit/Write
# Reads tool input from stdin (JSON format)
#
# Note: Ensure this script is executable after cloning:
#   chmod +x .claude/hooks/format_python.sh

set -euo pipefail

# Extract file path from tool input JSON with error handling
file_path=""
if jq_output=$(jq -r '.tool_input.file_path // empty' 2>/dev/null); then
    file_path="$jq_output"
else
    # JSON parsing failed; skip formatting silently
    exit 0
fi

# Skip if no file path
[[ -z "$file_path" ]] && exit 0

# Security: Validate path is within project directory
# Use CLAUDE_PROJECT_DIR if available, otherwise pwd
project_root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
resolved_path=$(realpath -m -- "$file_path" 2>/dev/null)

# Determine log file location (project-local)
log_dir="$project_root/.claude/logs"
mkdir -p "$log_dir" 2>/dev/null || true
log_file="$log_dir/ruff_format.log"

# Only format Python files within the project directory
if [[ -n "$resolved_path" ]] \
    && [[ "$resolved_path" == "$project_root"/* ]] \
    && [[ "$resolved_path" == *.py ]] \
    && [[ -f "$resolved_path" ]]; then
    # Run ruff format; log errors but don't fail the hook
    if ! uv run ruff format "$resolved_path" 2>>"$log_file"; then
        echo "ruff format failed for '$resolved_path'. See $log_file" >&2
    fi
fi
