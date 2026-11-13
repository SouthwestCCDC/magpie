"""Release assets pin the image by digest (scripts/render_release_assets.sh, #633)."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "render_release_assets.sh"
DIGEST = "sha256:" + "ab" * 32
PINNED = f"ghcr.io/southwestccdc/magpie:0.2.0@{DIGEST}"


def _render(
    out: Path, digest: str = DIGEST, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), "0.2.0", digest, str(out)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    result = _render(tmp_path)
    assert result.returncode == 0, result.stderr
    return tmp_path


def test_compose_image_pinned_by_digest(assets: Path):
    compose = (assets / "docker-compose.yml").read_text()
    assert f"image: ${{MAGPIE_IMAGE:-{PINNED}}}" in compose
    assert ":latest" not in compose


def test_compose_otherwise_unchanged(assets: Path):
    src = (ROOT / "docker-compose.yml").read_text().splitlines()
    out = (assets / "docker-compose.yml").read_text().splitlines()
    changed = [(a, b) for a, b in zip(src, out, strict=True) if a != b]
    assert len(changed) == 2  # the image line and its doc comment


def test_env_example_names_pinned_image(assets: Path):
    env = (assets / "env.example").read_text()
    assert f"# MAGPIE_IMAGE={PINNED}" in env
    assert "magpie:latest" not in env


def test_sha256sums_cover_every_asset(assets: Path):
    sums = {}
    for line in (assets / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name] = digest
    assert set(sums) == {"docker-compose.yml", "env.example", "magpie-deploy.sh"}
    for name, digest in sums.items():
        assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("digest", ["", "sha256:abc", "latest", "md5:" + "0" * 32])
def test_rejects_bad_digest(tmp_path: Path, digest: str):
    assert _render(tmp_path, digest).returncode != 0


def test_fork_image_repo_pins_fork_image(tmp_path: Path):
    env = {**os.environ, "IMAGE_REPO": "ghcr.io/alice/magpie"}
    result = _render(tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    fork_pinned = f"ghcr.io/alice/magpie:0.2.0@{DIGEST}"
    assert f"${{MAGPIE_IMAGE:-{fork_pinned}}}" in (tmp_path / "docker-compose.yml").read_text()
    assert f"# MAGPIE_IMAGE={fork_pinned}" in (tmp_path / "env.example").read_text()


def test_no_asset_name_starts_with_a_dot(assets: Path):
    # GitHub renames uploaded assets with a leading dot (".env.example"
    # becomes "default.env.example"), which breaks SHA256SUMS and the
    # documented download URLs.
    names = {p.name for p in assets.iterdir()}
    assert not [n for n in names if n.startswith(".")], names
