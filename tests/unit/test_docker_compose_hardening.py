"""Drift guard for docker-compose.yml's container hardening (#309, #591).

Static assertions against the canonical operator compose file -- no Docker
required -- so a future edit that silently drops `cap_drop: ALL`,
`read_only: true`, or the fail-closed admin-token-sink default doesn't slip
through review unnoticed. The actual writable-path set (this test's
`EXPECTED_TMPFS`) was derived by running a real container under this exact
cap/read_only combination, not by reading the Dockerfile -- see
docker-compose.yml's own comments for what each path is for.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
OVERRIDE_FILE = PROJECT_ROOT / "docker-compose.override.yml"

# The minimal capability set verified sufficient for both first boot
# (chowning a foreign-owned bind mount) and steady state (issue #591).
# NET_BIND_SERVICE, KILL, and FOWNER are deliberately absent -- see
# docker-compose.yml's comment for why each is unnecessary.
EXPECTED_CAP_ADD = {"SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE"}
EXPECTED_TMPFS = {"/run", "/tmp", "/var/lib/caddy"}


def _load_compose(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def _env_dict(service: dict) -> dict[str, str]:
    """Parse a service's `environment:` block into a dict.

    Compose accepts two equivalent YAML shapes for `environment:` -- the
    list form (`[KEY=value, ...]`, what docker-compose.yml actually uses)
    and the mapping form (`{KEY: value, ...}`). Handling only the list form
    would silently drop every value (iterating a mapping yields its keys)
    if a file ever switched shapes, blinding every assertion below that
    reads this dict without raising an error.

    Raw ${VAR:-default}/${VAR:?msg} substitution syntax is preserved
    as-is (not interpolated) -- these assertions check the *shape* of the
    directive (has a default vs. requires a value), not a resolved value.
    """
    raw = service.get("environment", [])
    env: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            env[key] = "" if value is None else str(value)
    else:
        for entry in raw:
            key, _, value = entry.partition("=")
            env[key] = value
    return env


def test_env_dict_handles_both_list_and_mapping_forms():
    """Prove `_env_dict()` -- what every fail-closed/hardening assertion
    below relies on -- handles both YAML shapes Compose accepts for
    `environment:`, so a compose file switching shapes wouldn't silently
    blind this whole drift guard (a `:-` default missed rather than
    caught).
    """
    list_form = {"environment": ["FOO=${FOO:-bar}", "BAZ=qux"]}
    mapping_form = {"environment": {"FOO": "${FOO:-bar}", "BAZ": "qux"}}
    expected = {"FOO": "${FOO:-bar}", "BAZ": "qux"}
    assert _env_dict(list_form) == expected
    assert _env_dict(mapping_form) == expected


def test_operator_compose_has_single_magpie_service():
    compose = _load_compose(COMPOSE_FILE)
    assert set(compose["services"]) == {"magpie"}, (
        "docker-compose.yml must define exactly one service (`magpie`, the bundled "
        "single-container image) -- no separate `caddy` service. See issue #309's "
        "2026-07-18 decision."
    )


def test_cap_drop_all():
    service = _load_compose(COMPOSE_FILE)["services"]["magpie"]
    assert service.get("cap_drop") == ["ALL"], (
        "the operator compose file must drop all capabilities before adding back "
        "the minimal set -- found: " + repr(service.get("cap_drop"))
    )


def test_cap_add_is_exactly_the_verified_minimal_set():
    service = _load_compose(COMPOSE_FILE)["services"]["magpie"]
    cap_add = set(service.get("cap_add", []))
    assert cap_add == EXPECTED_CAP_ADD, (
        f"cap_add drifted from the verified-minimal set: expected {EXPECTED_CAP_ADD}, "
        f"got {cap_add}. If you're adding a capability, verify it's actually load-"
        f"bearing against a real container (not just reasoning from the Dockerfile) "
        f"before updating this test -- see #591 for how the current set was derived."
    )


def test_read_only_filesystem():
    service = _load_compose(COMPOSE_FILE)["services"]["magpie"]
    assert service.get("read_only") is True, (
        "the operator compose file must run with a read-only root filesystem"
    )


def test_tmpfs_covers_the_verified_writable_paths():
    service = _load_compose(COMPOSE_FILE)["services"]["magpie"]
    tmpfs = set(service.get("tmpfs", []))
    assert tmpfs == EXPECTED_TMPFS, (
        f"tmpfs mounts drifted from the verified writable-path set: expected "
        f"{EXPECTED_TMPFS}, got {tmpfs}. A missing entry here means the container "
        f"fails to start under read_only: true; verify any change against a real "
        f"container, not just the Dockerfile."
    )


def test_admin_token_sink_has_no_compose_level_default():
    """MAGPIE_ADMIN_TOKEN_SINK must pass through with no `:-default` in the
    operator file -- fail-closed enforcement is left to the application
    (MagpieSettings' env_ignore_empty + deliver_admin_token's own error),
    not Compose's `:?`. `:?` is deliberately NOT used here: it aborts
    during that file's own interpolation, before docker-compose.override.yml
    (which supplies a dev default for the same key) is ever merged in --
    see docker-compose.yml's comment for the verified repro.
    """
    service = _load_compose(COMPOSE_FILE)["services"]["magpie"]
    env = _env_dict(service)
    assert "MAGPIE_ADMIN_TOKEN_SINK" in env, (
        "MAGPIE_ADMIN_TOKEN_SINK must be defined in the operator compose file (so "
        "operators can see and configure it), just with no Compose-level default"
    )
    sink = env["MAGPIE_ADMIN_TOKEN_SINK"]
    assert ":-" not in sink and ":?" not in sink, (
        "MAGPIE_ADMIN_TOKEN_SINK must have no Compose-level default or required-var "
        f"guard in the operator compose file -- got: {sink!r}"
    )


def test_debug_mode_is_hardcoded_off():
    service = _load_compose(COMPOSE_FILE)["services"]["magpie"]
    env = _env_dict(service)
    assert env.get("MAGPIE_DEBUG") == "false", (
        "MAGPIE_DEBUG must be hardcoded to false (not a defaultable env passthrough) "
        f"in the operator compose file -- got: {env.get('MAGPIE_DEBUG')!r}"
    )


def test_test_endpoints_not_wired_in_operator_file():
    service = _load_compose(COMPOSE_FILE)["services"]["magpie"]
    env = _env_dict(service)
    assert "MAGPIE_ENABLE_TEST_ENDPOINTS" not in env, (
        "MAGPIE_ENABLE_TEST_ENDPOINTS is dev-only -- it belongs in "
        "docker-compose.override.yml, not the operator compose file"
    )


def test_override_file_does_not_relax_hardening():
    """docker-compose.override.yml (auto-merged for local dev) must be purely
    additive: it must not touch any of the hardening keys, so dev never runs
    under weaker isolation than an operator deployment would.
    """
    override = _load_compose(OVERRIDE_FILE)
    service = override["services"]["magpie"]
    for key in ("cap_drop", "cap_add", "read_only", "tmpfs"):
        assert key not in service, (
            f"docker-compose.override.yml must not set `{key}` -- hardening is "
            f"unconditional and must only be defined in docker-compose.yml"
        )
