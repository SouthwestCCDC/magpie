"""Unit tests for validation module (token and tag name validation)."""

from __future__ import annotations

import pytest

from magpie.validation import (
    TAG_NAME_MAX_LENGTH,
    TAG_NAME_PATTERN,
    TOKEN_NAME_MAX_LENGTH,
    TOKEN_NAME_PATTERN,
    ValidationError,
    validate_tag_name,
    validate_token_name,
)


class TestValidateTokenName:
    """Tests for validate_token_name function."""

    def test_valid_alphanumeric_name(self) -> None:
        """Valid alphanumeric token name is accepted."""
        result = validate_token_name("myservice123")
        assert result == "myservice123"

    def test_valid_name_with_dots(self) -> None:
        """Token name with dots is accepted."""
        result = validate_token_name("my.service.name")
        assert result == "my.service.name"

    def test_valid_name_with_underscores(self) -> None:
        """Token name with underscores is accepted."""
        result = validate_token_name("my_service_name")
        assert result == "my_service_name"

    def test_valid_name_with_hyphens(self) -> None:
        """Token name with hyphens is accepted."""
        result = validate_token_name("my-service-name")
        assert result == "my-service-name"

    def test_valid_single_character_name(self) -> None:
        """Single alphanumeric character token name is accepted."""
        result = validate_token_name("a")
        assert result == "a"

    def test_valid_max_length_name(self) -> None:
        """Token name at max length (64 chars) is accepted."""
        max_name = "a" * TOKEN_NAME_MAX_LENGTH
        result = validate_token_name(max_name)
        assert result == max_name

    def test_invalid_empty_name_raises(self) -> None:
        """Empty token name raises ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_token_name("")

    def test_invalid_name_too_long_raises(self) -> None:
        """Token name exceeding max length raises ValidationError."""
        long_name = "a" * (TOKEN_NAME_MAX_LENGTH + 1)
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            validate_token_name(long_name)

    def test_invalid_name_starting_with_hyphen_raises(self) -> None:
        """Token name starting with hyphen raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_token_name("-invalid")

    def test_invalid_name_starting_with_dot_raises(self) -> None:
        """Token name starting with dot raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_token_name(".invalid")

    def test_invalid_name_starting_with_underscore_raises(self) -> None:
        """Token name starting with underscore raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_token_name("_invalid")

    def test_invalid_name_with_spaces_raises(self) -> None:
        """Token name with spaces raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_token_name("invalid name")

    def test_invalid_name_with_special_chars_raises(self) -> None:
        """Token name with special characters raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_token_name("invalid@name!")


class TestValidateTagName:
    """Tests for validate_tag_name function."""

    def test_valid_alphanumeric_name(self) -> None:
        """Valid alphanumeric tag name is accepted."""
        result = validate_tag_name("v1release123")
        assert result == "v1release123"

    def test_valid_name_with_dots(self) -> None:
        """Tag name with dots is accepted."""
        result = validate_tag_name("v1.0.0")
        assert result == "v1.0.0"

    def test_valid_name_with_underscores(self) -> None:
        """Tag name with underscores is accepted."""
        result = validate_tag_name("release_stable")
        assert result == "release_stable"

    def test_valid_name_with_hyphens(self) -> None:
        """Tag name with hyphens is accepted."""
        result = validate_tag_name("release-v1-beta")
        assert result == "release-v1-beta"

    def test_valid_common_tags(self) -> None:
        """Common tag names like 'latest', 'stable' are accepted."""
        assert validate_tag_name("latest") == "latest"
        assert validate_tag_name("stable") == "stable"
        assert validate_tag_name("production") == "production"
        assert validate_tag_name("v1") == "v1"

    def test_valid_max_length_name(self) -> None:
        """Tag name at max length (128 chars) is accepted."""
        max_name = "a" * TAG_NAME_MAX_LENGTH
        result = validate_tag_name(max_name)
        assert result == max_name

    def test_invalid_empty_name_raises(self) -> None:
        """Empty tag name raises ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_tag_name("")

    def test_invalid_name_too_long_raises(self) -> None:
        """Tag name exceeding max length raises ValidationError."""
        long_name = "a" * (TAG_NAME_MAX_LENGTH + 1)
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            validate_tag_name(long_name)

    def test_invalid_name_starting_with_hyphen_raises(self) -> None:
        """Tag name starting with hyphen raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_tag_name("-invalid")

    def test_invalid_name_starting_with_dot_raises(self) -> None:
        """Tag name starting with dot raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_tag_name(".invalid")

    def test_invalid_name_starting_with_underscore_raises(self) -> None:
        """Tag name starting with underscore raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_tag_name("_invalid")

    def test_invalid_name_with_spaces_raises(self) -> None:
        """Tag name with spaces raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_tag_name("invalid name")

    def test_invalid_name_with_special_chars_raises(self) -> None:
        """Tag name with special characters raises ValidationError."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            validate_tag_name("v1@beta!")


class TestValidationConstants:
    """Tests for validation constants."""

    def test_token_name_max_length_is_64(self) -> None:
        """TOKEN_NAME_MAX_LENGTH should be 64."""
        assert TOKEN_NAME_MAX_LENGTH == 64

    def test_tag_name_max_length_is_128(self) -> None:
        """TAG_NAME_MAX_LENGTH should be 128."""
        assert TAG_NAME_MAX_LENGTH == 128

    def test_token_pattern_matches_expected(self) -> None:
        """TOKEN_NAME_PATTERN should match expected regex."""
        assert TOKEN_NAME_PATTERN == r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"

    def test_tag_pattern_matches_expected(self) -> None:
        """TAG_NAME_PATTERN should match expected regex."""
        assert TAG_NAME_PATTERN == r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"


class TestValidationErrorIsValueError:
    """Tests to verify ValidationError is a subclass of ValueError."""

    def test_validation_error_is_value_error(self) -> None:
        """ValidationError should be a subclass of ValueError."""
        assert issubclass(ValidationError, ValueError)

    def test_validation_error_can_be_caught_as_value_error(self) -> None:
        """ValidationError should be catchable as ValueError."""
        try:
            validate_token_name("")
            assert False, "Should have raised ValidationError"
        except ValueError:
            pass  # Expected
