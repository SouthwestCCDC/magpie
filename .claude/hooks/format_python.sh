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
project_root=$(pwd)
resolved_path=$(realpath -m -- "$file_path" 2>/dev/null || echo "")

# Only format Python files within the project directory
if [[ -n "$resolved_path" ]] \
    && [[ "$resolved_path" == "$project_root"/* ]] \
    && [[ "$resolved_path" == *.py ]] \
    && [[ -f "$resolved_path" ]]; then
    # Run ruff format; log errors but don't fail the hook
    if ! uv run ruff format "$resolved_path" 2>>/tmp/claude_ruff_format.log; then
        echo "ruff format failed for '$resolved_path'. See /tmp/claude_ruff_format.log" >&2
    fi
fi
