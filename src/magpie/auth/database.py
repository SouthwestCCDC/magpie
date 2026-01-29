"""SQLite database operations for token storage."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from magpie.auth.models import Token, TokenScope

# SQL schema for tokens table
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    name TEXT PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,
    scope TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


def init_database(db_path: Path) -> None:
    """Initialize the SQLite database with tokens table and WAL mode.

    Creates the database file and tokens table if they don't exist.
    Enables WAL (Write-Ahead Logging) mode for better concurrent access.

    Args:
        db_path: Path to the SQLite database file.
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # Enable WAL mode for better concurrent read/write performance
        conn.execute("PRAGMA journal_mode=WAL;")

        # Create tokens table
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a database connection with WAL mode enabled.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        SQLite connection configured with WAL mode.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def save_token(conn: sqlite3.Connection, token: Token) -> None:
    """Save a token to the database.

    Token Prefix Convention:
        - Regular tokens: mgp_ prefix
        - Break-glass admin token: mgp_ADMIN_ prefix

    Args:
        conn: Database connection.
        token: Token instance to save.

    Raises:
        sqlite3.IntegrityError: If token name or hash already exists.
    """
    conn.execute(
        """
        INSERT INTO tokens (name, token_hash, scope, enabled, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            token.name,
            token.token_hash,
            token.scope.value,
            1 if token.enabled else 0,
            token.created_at.isoformat(),
        ),
    )
    conn.commit()


def get_token_by_hash(conn: sqlite3.Connection, token_hash: str) -> Token | None:
    """Look up a token by its hash.

    Args:
        conn: Database connection.
        token_hash: SHA-256 hash of the token to find.

    Returns:
        Token instance if found, None otherwise.
    """
    cursor = conn.execute(
        """
        SELECT name, token_hash, scope, enabled, created_at
        FROM tokens
        WHERE token_hash = ?
        """,
        (token_hash,),
    )
    row = cursor.fetchone()

    if row is None:
        return None

    return Token(
        name=row["name"],
        token_hash=row["token_hash"],
        scope=TokenScope(row["scope"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def get_token_by_name(conn: sqlite3.Connection, name: str) -> Token | None:
    """Look up a token by its name.

    Args:
        conn: Database connection.
        name: Name of the token to find.

    Returns:
        Token instance if found, None otherwise.
    """
    cursor = conn.execute(
        """
        SELECT name, token_hash, scope, enabled, created_at
        FROM tokens
        WHERE name = ?
        """,
        (name,),
    )
    row = cursor.fetchone()

    if row is None:
        return None

    return Token(
        name=row["name"],
        token_hash=row["token_hash"],
        scope=TokenScope(row["scope"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def list_tokens(conn: sqlite3.Connection) -> list[Token]:
    """List all tokens in the database.

    For administrative purposes. Returns all tokens regardless of enabled status.

    Args:
        conn: Database connection.

    Returns:
        List of all Token instances.
    """
    cursor = conn.execute(
        """
        SELECT name, token_hash, scope, enabled, created_at
        FROM tokens
        ORDER BY created_at DESC
        """
    )

    tokens = []
    for row in cursor:
        tokens.append(
            Token(
                name=row["name"],
                token_hash=row["token_hash"],
                scope=TokenScope(row["scope"]),
                enabled=bool(row["enabled"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        )
    return tokens


def delete_token(conn: sqlite3.Connection, name: str) -> bool:
    """Delete a token by name.

    Args:
        conn: Database connection.
        name: Name of the token to delete.

    Returns:
        True if a token was deleted, False if no token with that name existed.
    """
    cursor = conn.execute(
        """
        DELETE FROM tokens WHERE name = ?
        """,
        (name,),
    )
    conn.commit()
    return cursor.rowcount > 0
