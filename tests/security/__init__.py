"""Security test suite for magpie.

This package contains tests for various security attack vectors including:
- SQL injection via token/artifact names
- Unicode normalization attacks on paths
- Symlink attacks in artifact paths
- Path traversal attacks with ../ patterns
- Null byte injection in paths
"""
