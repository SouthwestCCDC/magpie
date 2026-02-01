"""E2E tests for Caddyfile configuration validation.

This test validates that the consolidated Caddyfile parses correctly with
various environment variable configurations, preventing regressions in the
multiline environment variable syntax and ensuring production configurations work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Project root directory (where Caddyfile is located)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()


def validate_caddyfile_with_env(env_vars: dict[str, str]) -> tuple[bool, str]:
    """Validate Caddyfile with specific environment variables.

    Args:
        env_vars: Environment variables to set for validation.

    Returns:
        Tuple of (success: bool, stderr: str).
    """
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{PROJECT_ROOT / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
            *[f"-e{k}={v}" for k, v in env_vars.items()],
            "caddy:2-alpine",
            "caddy",
            "validate",
            "--config",
            "/etc/caddy/Caddyfile",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stderr


class TestCaddyfileValidation:
    """Test Caddyfile configuration validation."""

    def test_development_config_validates(self):
        """Verify development configuration (minimal env vars) parses correctly."""
        env_vars = {
            "MAGPIE_SITE_ADDRESS": ":80",
            # Development defaults: no logging, admin enabled, no security headers
        }
        success, stderr = validate_caddyfile_with_env(env_vars)
        assert success, f"Development Caddyfile validation failed:\n{stderr}"

    def test_production_config_validates(self):
        """Verify production configuration with all env vars parses correctly."""
        env_vars = {
            "MAGPIE_SITE_ADDRESS": "magpie.example.com",
            "MAGPIE_ENABLE_LOGGING": """log {
    output stdout
    format json
    level INFO
}""",
            "MAGPIE_DISABLE_ADMIN": "admin off",
            "MAGPIE_PROD_SECURITY_HEADERS": """Strict-Transport-Security "max-age=2592000; includeSubDomains"
X-Content-Type-Options "nosniff"
X-Frame-Options "DENY"
X-XSS-Protection "1; mode=block"
Referrer-Policy "strict-origin-when-cross-origin"
-Server""",
            "MAGPIE_ALLOWED_CIDRS": "10.0.0.0/8 192.168.1.0/24",  # Space-separated, not comma
        }
        success, stderr = validate_caddyfile_with_env(env_vars)
        assert success, f"Production Caddyfile validation failed:\n{stderr}"

    def test_production_with_manual_tls_validates(self):
        """Verify production with manual TLS configuration parses correctly."""
        env_vars = {
            "MAGPIE_SITE_ADDRESS": "magpie.example.com",
            "MAGPIE_TLS_CONFIG": "tls internal",
            "MAGPIE_ENABLE_LOGGING": """log {
    output stdout
    format json
}""",
            "MAGPIE_DISABLE_ADMIN": "admin off",
        }
        success, stderr = validate_caddyfile_with_env(env_vars)
        assert success, f"Production Caddyfile with TLS config validation failed:\n{stderr}"

    def test_production_with_authentik_validates(self):
        """Verify production with Authentik SSO enabled parses correctly."""
        env_vars = {
            "MAGPIE_SITE_ADDRESS": "magpie.example.com",
            "AUTHENTIK_HOST": "authentik.example.com",
            "MAGPIE_ENABLE_LOGGING": """log {
    output stdout
    format json
}""",
            "MAGPIE_DISABLE_ADMIN": "admin off",
            "MAGPIE_PROD_SECURITY_HEADERS": """Strict-Transport-Security "max-age=2592000"
-Server""",
        }
        success, stderr = validate_caddyfile_with_env(env_vars)
        assert success, f"Production Caddyfile with Authentik validation failed:\n{stderr}"

    def test_minimal_config_validates(self):
        """Verify Caddyfile parses with no environment variables (uses defaults)."""
        env_vars = {}
        success, stderr = validate_caddyfile_with_env(env_vars)
        assert success, f"Minimal Caddyfile validation failed:\n{stderr}"

    @pytest.mark.parametrize(
        "cidr_value",
        [
            "10.0.0.0/8",
            "192.168.1.0/24 172.16.0.0/12",  # Space-separated, not comma
            "255.255.255.255/32",  # Default value used in docker-compose.prod.yml
        ],
    )
    def test_various_cidr_formats_validate(self, cidr_value: str):
        """Verify various CIDR format values parse correctly.

        Note: Caddy's client_ip matcher expects space-separated CIDR ranges,
        not comma-separated.
        """
        env_vars = {
            "MAGPIE_SITE_ADDRESS": ":80",
            "MAGPIE_ALLOWED_CIDRS": cidr_value,
        }
        success, stderr = validate_caddyfile_with_env(env_vars)
        assert success, f"Caddyfile validation failed with CIDR {cidr_value}:\n{stderr}"
