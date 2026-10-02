"""Rolling image tags point at the highest release in scope (#634)."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_rolling_tags.sh"


def _aliases(versions: list[str]) -> dict[str, str]:
    result = subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT)],
        input="".join(f"{v}\n" for v in versions),
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(line.split() for line in result.stdout.splitlines())


@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        (
            ["v0.1.5", "v0.1.6", "v0.2.0"],
            {"latest": "0.2.0", "0": "0.2.0", "0.1": "0.1.6", "0.2": "0.2.0"},
        ),
        # A hotfix on an older line only owns its own minor.
        (
            ["0.2.0", "0.1.7", "0.1.6"],
            {"latest": "0.2.0", "0": "0.2.0", "0.1": "0.1.7", "0.2": "0.2.0"},
        ),
        # Version order, not string order.
        (["0.2.9", "0.2.10"], {"latest": "0.2.10", "0": "0.2.10", "0.2": "0.2.10"}),
        # Each major keeps its own highest.
        (
            ["0.3.0", "1.0.0", "0.3.1"],
            {"latest": "1.0.0", "0": "0.3.1", "1": "1.0.0", "0.3": "0.3.1", "1.0": "1.0.0"},
        ),
        # Prereleases and junk never own an alias.
        (["0.2.0-rc3", "v0.1.6", "garbage", ""], {"latest": "0.1.6", "0": "0.1.6", "0.1": "0.1.6"}),
        ([], {}),
    ],
)
def test_rolling_aliases(versions, expected):
    assert _aliases(versions) == expected
