"""Unit tests for token storage operations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from magpie.auth.database import (
    delete_token,
    get_connection,
    get_token_by_hash,
    init_database,
    list_tokens,
    save_token,
)
from magpie.auth.models import Token, TokenScope


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def initialized_db(db_path: Path) -> Path:
    """Initialize database and return path."""
    init_database(db_path)
    return db_path


@pytest.fixture
def db_conn(initialized_db: Path) -> sqlite3.Connection:
    """Get connection to initialized database."""
    conn = get_connection(initialized_db)
    yield conn
    conn.close()


def make_token(
    name: str = "test-token",
    token_hash: str = "abc123hash",
    scope: TokenScope = TokenScope.READ,
    enabled: bool = True,
) -> Token:
    """Helper to create a test token."""
    return Token(
        name=name,
        token_hash=token_hash,
        scope=scope,
        enabled=enabled,
        created_at=datetime.now(timezone.utc),
    )


class TestInitDatabase:
    """Tests for init_database function."""

    def test_init_creates_database_file(self, db_path: Path) -> None:
        """init_database should create the database file."""
        assert not db_path.exists()

        init_database(db_path)

        assert db_path.exists()

    def test_init_creates_tokens_table(self, db_path: Path) -> None:
        """init_database should create the tokens table."""
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tokens'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "tokens"

    def test_init_enables_wal_mode(self, db_path: Path) -> None:
        """init_database should enable WAL journal mode."""
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"

    def test_init_creates_parent_directories(self, tmp_path: Path) -> None:
        """init_database should create parent directories if needed."""
        nested_path = tmp_path / "nested" / "dirs" / "db.sqlite"

        init_database(nested_path)

        assert nested_path.exists()

    def test_init_is_idempotent(self, db_path: Path) -> None:
        """init_database can be called multiple times safely."""
        init_database(db_path)
        init_database(db_path)  # Should not raise

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tokens'"
        )
        assert cursor.fetchone() is not None
        conn.close()


class TestGetConnection:
    """Tests for get_connection function."""

    def test_get_connection_returns_connection(self, initialized_db: Path) -> None:
        """get_connection should return a valid connection."""
        conn = get_connection(initialized_db)

        assert conn is not None
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_get_connection_enables_wal_mode(self, initialized_db: Path) -> None:
        """get_connection should enable WAL mode."""
        conn = get_connection(initialized_db)
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"

    def test_get_connection_enables_row_factory(self, initialized_db: Path) -> None:
        """get_connection should enable Row factory for dict-like access."""
        conn = get_connection(initialized_db)

        assert conn.row_factory == sqlite3.Row
        conn.close()


class TestSaveToken:
    """Tests for save_token function."""

    def test_save_token_persists_token(self, db_conn: sqlite3.Connection) -> None:
        """save_token should persist token to database."""
        token = make_token(name="my-token", token_hash="hash123")

        save_token(db_conn, token)

        cursor = db_conn.execute("SELECT * FROM tokens WHERE name = ?", ("my-token",))
        row = cursor.fetchone()
        assert row is not None
        assert row["name"] == "my-token"
        assert row["token_hash"] == "hash123"

    def test_save_token_stores_all_fields(self, db_conn: sqlite3.Connection) -> None:
        """save_token should store all token fields correctly."""
        token = make_token(
            name="full-token",
            token_hash="fullhash",
            scope=TokenScope.ADMIN,
            enabled=False,
        )

        save_token(db_conn, token)

        cursor = db_conn.execute(
            "SELECT * FROM tokens WHERE name = ?", ("full-token",)
        )
        row = cursor.fetchone()
        assert row["scope"] == "admin"
        assert row["enabled"] == 0
        assert row["created_at"] is not None

    def test_save_token_duplicate_name_raises(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """save_token should raise error for duplicate name."""
        token1 = make_token(name="duplicate", token_hash="hash1")
        token2 = make_token(name="duplicate", token_hash="hash2")

        save_token(db_conn, token1)

        with pytest.raises(sqlite3.IntegrityError):
            save_token(db_conn, token2)

    def test_save_token_duplicate_hash_raises(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """save_token should raise error for duplicate hash."""
        token1 = make_token(name="token1", token_hash="same-hash")
        token2 = make_token(name="token2", token_hash="same-hash")

        save_token(db_conn, token1)

        with pytest.raises(sqlite3.IntegrityError):
            save_token(db_conn, token2)


class TestGetTokenByHash:
    """Tests for get_token_by_hash function."""

    def test_get_token_by_hash_returns_token(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """get_token_by_hash should return matching token."""
        token = make_token(name="find-me", token_hash="findable-hash")
        save_token(db_conn, token)

        result = get_token_by_hash(db_conn, "findable-hash")

        assert result is not None
        assert result.name == "find-me"
        assert result.token_hash == "findable-hash"

    def test_get_token_by_hash_returns_none_for_unknown(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """get_token_by_hash should return None for unknown hash."""
        result = get_token_by_hash(db_conn, "nonexistent-hash")

        assert result is None

    def test_get_token_by_hash_preserves_all_fields(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """get_token_by_hash should return token with all fields intact."""
        original = make_token(
            name="complete",
            token_hash="complete-hash",
            scope=TokenScope.WRITE,
            enabled=True,
        )
        save_token(db_conn, original)

        result = get_token_by_hash(db_conn, "complete-hash")

        assert result is not None
        assert result.name == original.name
        assert result.token_hash == original.token_hash
        assert result.scope == original.scope
        assert result.enabled == original.enabled
        assert isinstance(result.created_at, datetime)


class TestListTokens:
    """Tests for list_tokens function."""

    def test_list_tokens_returns_all_tokens(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """list_tokens should return all stored tokens."""
        tokens = [
            make_token(name="token1", token_hash="hash1"),
            make_token(name="token2", token_hash="hash2"),
            make_token(name="token3", token_hash="hash3"),
        ]
        for token in tokens:
            save_token(db_conn, token)

        result = list_tokens(db_conn)

        assert len(result) == 3
        names = {t.name for t in result}
        assert names == {"token1", "token2", "token3"}

    def test_list_tokens_returns_empty_for_no_tokens(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """list_tokens should return empty list when no tokens exist."""
        result = list_tokens(db_conn)

        assert result == []

    def test_list_tokens_includes_disabled_tokens(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """list_tokens should include disabled tokens."""
        enabled_token = make_token(name="enabled", token_hash="hash1", enabled=True)
        disabled_token = make_token(name="disabled", token_hash="hash2", enabled=False)
        save_token(db_conn, enabled_token)
        save_token(db_conn, disabled_token)

        result = list_tokens(db_conn)

        assert len(result) == 2
        disabled = next(t for t in result if t.name == "disabled")
        assert disabled.enabled is False


class TestDeleteToken:
    """Tests for delete_token function."""

    def test_delete_token_removes_token(self, db_conn: sqlite3.Connection) -> None:
        """delete_token should remove the token from database."""
        token = make_token(name="to-delete", token_hash="delete-hash")
        save_token(db_conn, token)

        result = delete_token(db_conn, "to-delete")

        assert result is True
        assert get_token_by_hash(db_conn, "delete-hash") is None

    def test_delete_token_returns_true_on_success(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """delete_token should return True when token is deleted."""
        token = make_token(name="deletable", token_hash="hash")
        save_token(db_conn, token)

        result = delete_token(db_conn, "deletable")

        assert result is True

    def test_delete_token_returns_false_for_nonexistent(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """delete_token should return False for non-existent token."""
        result = delete_token(db_conn, "nonexistent")

        assert result is False

    def test_delete_token_does_not_affect_other_tokens(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """delete_token should not affect other tokens."""
        token1 = make_token(name="keep", token_hash="hash1")
        token2 = make_token(name="delete", token_hash="hash2")
        save_token(db_conn, token1)
        save_token(db_conn, token2)

        delete_token(db_conn, "delete")

        assert get_token_by_hash(db_conn, "hash1") is not None
        assert get_token_by_hash(db_conn, "hash2") is None


class TestTokenModel:
    """Tests for Token model."""

    def test_token_scope_values(self) -> None:
        """TokenScope should have correct string values."""
        assert TokenScope.READ.value == "read"
        assert TokenScope.WRITE.value == "write"
        assert TokenScope.ADMIN.value == "admin"

    def test_token_default_enabled(self) -> None:
        """Token should default to enabled=True."""
        token = Token(
            name="test",
            token_hash="hash",
            scope=TokenScope.READ,
            created_at=datetime.now(timezone.utc),
        )

        assert token.enabled is True

    def test_token_scope_from_string(self) -> None:
        """TokenScope should be constructible from string values."""
        assert TokenScope("read") == TokenScope.READ
        assert TokenScope("write") == TokenScope.WRITE
        assert TokenScope("admin") == TokenScope.ADMIN
