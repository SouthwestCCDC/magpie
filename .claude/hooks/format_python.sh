#!/bin/bash
# Claude Code hook: Auto-format Python files after Edit/Write
# Reads tool input from stdin (JSON format)

set -euo pipefail

# Extract file path from tool input JSON
file_path=$(jq -r '.tool_input.file_path // empty' 2>/dev/null || echo "")

# Only format Python files
if [[ "$file_path" == *.py ]] && [[ -f "$file_path" ]]; then
    # Run ruff format silently
    uv run ruff format "$file_path" 2>/dev/null || true
fi
