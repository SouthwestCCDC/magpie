"""Rolling image tags only move forward (scripts/release_tag_flags.sh, #634)."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_tag_flags.sh"


def _flags(version: str, tags: list[str]) -> dict[str, str]:
    result = subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), version],
        input="\n".join(tags) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


HISTORY = ["v0.1.5", "v0.1.6", "v0.1.6-rc1", "v0.2.0-rc3"]


@pytest.mark.parametrize(
    ("version", "tags", "latest", "major", "minor"),
    [
        # First stable 0.2.0: everything moves.
        ("0.2.0", [*HISTORY, "v0.2.0"], "true", "true", "true"),
        # A 0.1.x hotfix after 0.2.0 only moves :0.1.
        ("0.1.7", [*HISTORY, "v0.2.0", "v0.1.7"], "false", "false", "true"),
        # Prereleases never move rolling tags.
        ("0.2.1-rc1", [*HISTORY, "v0.2.0"], "false", "false", "false"),
        # Re-running an older release's workflow doesn't move :0.2 back.
        ("0.2.0", [*HISTORY, "v0.2.0", "v0.2.1"], "false", "false", "false"),
        # Version order, not string order: 0.2.10 > 0.2.9.
        ("0.2.10", ["v0.2.9", "v0.2.10"], "true", "true", "true"),
        # A 0.x patch after 1.0.0 moves :0 and :0.3, not :latest.
        ("0.3.1", ["v0.3.0", "v1.0.0", "v0.3.1"], "false", "true", "true"),
        # The version being released counts even if its tag isn't listed yet.
        ("0.2.0", HISTORY, "true", "true", "true"),
    ],
)
def test_rolling_tag_flags(version, tags, latest, major, minor):
    flags = _flags(version, tags)
    assert (flags["move_latest"], flags["move_major"], flags["move_minor"]) == (
        latest,
        major,
        minor,
    )


def test_major_minor_values():
    flags = _flags("v0.2.10", [])
    assert (flags["major"], flags["minor"]) == ("0", "0.2")
