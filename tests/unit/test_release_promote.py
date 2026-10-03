"""release_promote.sh against stubbed git and docker (#634)."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_promote.sh"
IMAGE = "ghcr.io/southwestccdc/magpie"

STUB_GIT = """#!/bin/bash
printf '%s\\n' $FAKE_TAGS
"""

# inspect: FAKE_MISSING tags report "not found", FAKE_BROKEN tags fail
# some other way; create calls are appended to FAKE_LOG.
STUB_DOCKER = """#!/bin/bash
ref="${@: -1}"
v="${ref##*:}"
if [[ "$3" == inspect ]]; then
    for m in $FAKE_MISSING; do [[ "$v" == "$m" ]] && { echo "ERROR: $ref: not found" >&2; exit 1; }; done
    for m in $FAKE_BROKEN; do [[ "$v" == "$m" ]] && { echo "ERROR: unexpected status 503" >&2; exit 1; }; done
    exit 0
fi
echo "$*" >>"$FAKE_LOG"
"""


def _run(tmp_path, tags, missing="", broken=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("git", STUB_GIT), ("docker", STUB_DOCKER)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "creates.log"
    log.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_TAGS": " ".join(tags),
        "FAKE_MISSING": missing,
        "FAKE_BROKEN": broken,
        "FAKE_LOG": str(log),
    }
    result = subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), IMAGE], env=env, capture_output=True, text=True, check=False
    )
    creates = {}
    for line in log.read_text().splitlines():
        # buildx imagetools create -t <image>:<alias> <image>:<version>
        parts = line.split()
        creates[parts[4].rsplit(":", 1)[1]] = parts[5].rsplit(":", 1)[1]
    return result, creates


TAGS = ["v0.1.6", "v0.1.7", "v0.2.0-rc3", "v0.2.0", "v0.2.1"]


def test_moves_every_alias_to_highest_published(tmp_path):
    result, creates = _run(tmp_path, TAGS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert creates == {"latest": "0.2.1", "0": "0.2.1", "0.1": "0.1.7", "0.2": "0.2.1"}


def test_tag_without_image_is_skipped(tmp_path):
    result, creates = _run(tmp_path, TAGS, missing="0.2.1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipping 0.2.1" in result.stdout
    assert creates == {"latest": "0.2.0", "0": "0.2.0", "0.1": "0.1.7", "0.2": "0.2.0"}


@pytest.mark.parametrize("broken", ["0.2.1", "0.1.6"])
def test_registry_error_moves_nothing(tmp_path, broken):
    result, creates = _run(tmp_path, TAGS, broken=broken)
    assert result.returncode == 1
    assert f"Could not inspect {IMAGE}:{broken}" in result.stdout
    assert creates == {}


def test_no_published_release_moves_nothing(tmp_path):
    result, creates = _run(tmp_path, ["v0.2.0-rc1"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert creates == {}
