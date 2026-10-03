"""release_require_ci.sh and release_verify.sh previous, against a stubbed gh."""

import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

REQUIRED = [
    "lint",
    "test",
    "security",
    "e2e",
    "Tier 1 -- static checks",
    "Tier 2 -- real install + purge",
    "full-test-suite",
]

# Prints the next line-group of FAKE_RESPONSES (separated by "---") on each
# call, repeating the last one.
STUB_GH = """#!/bin/bash
n=$(cat "$FAKE_COUNT" 2>/dev/null || echo 0)
echo $((n + 1)) >"$FAKE_COUNT"
awk -v want="$n" 'BEGIN{i=0} /^---$/{i++; next} i==want' "$FAKE_RESPONSES" >"$FAKE_COUNT.out"
if [[ ! -s "$FAKE_COUNT.out" ]]; then
    awk 'BEGIN{RS="---\\n"} {last=$0} END{printf "%s", last}' "$FAKE_RESPONSES"
else
    cat "$FAKE_COUNT.out"
fi
"""


def _run(tmp_path, script, args, responses, extra_env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(STUB_GH)
    (bin_dir / "gh").chmod(0o755)
    (tmp_path / "responses").write_text("\n---\n".join(responses) + "\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_RESPONSES": str(tmp_path / "responses"),
        "FAKE_COUNT": str(tmp_path / "count"),
        "GITHUB_REPOSITORY": "SouthwestCCDC/magpie",
        "RELEASE_CI_POLL": "0",
        **(extra_env or {}),
    }
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPTS / script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _checks(**overrides):
    rows = []
    for name in REQUIRED:
        status, conclusion = overrides.get(name, ("completed", "success"))
        if status is not None:
            rows.append(f"{name}\t{status}\t{conclusion}")
    return "\n".join(rows)


def test_passes_when_every_required_check_succeeded(tmp_path):
    result = _run(tmp_path, "release_require_ci.sh", ["abc123"], [_checks()])
    assert result.returncode == 0, result.stderr
    assert "CI passed on abc123" in result.stdout


def test_waits_for_running_and_missing_checks(tmp_path):
    responses = [
        _checks(**{"e2e": ("in_progress", ""), "full-test-suite": (None, None)}),
        _checks(),
    ]
    result = _run(tmp_path, "release_require_ci.sh", ["abc123"], responses)
    assert result.returncode == 0, result.stderr
    assert "Waiting for: e2e, full-test-suite" in result.stdout


def test_fails_on_a_failed_check(tmp_path):
    responses = [_checks(**{"Tier 2 -- real install + purge": ("completed", "failure")})]
    result = _run(tmp_path, "release_require_ci.sh", ["abc123"], responses)
    assert result.returncode == 1
    assert "CI did not pass on abc123: Tier 2 -- real install + purge" in result.stdout


def test_skipped_is_not_success(tmp_path):
    responses = [_checks(**{"security": ("completed", "skipped")})]
    result = _run(tmp_path, "release_require_ci.sh", ["abc123"], responses)
    assert result.returncode == 1


def test_times_out_on_a_check_that_never_runs(tmp_path):
    responses = [_checks(**{"lint": (None, None)})]
    result = _run(
        tmp_path, "release_require_ci.sh", ["abc123"], responses, {"RELEASE_CI_TIMEOUT": "0"}
    )
    assert result.returncode == 1
    assert "Timed out waiting for CI on abc123: lint" in result.stdout


TAGS = "\n".join(["v0.1.6", "v0.2.0-rc7", "v0.2.0-rc10", "v0.2.0-rc8", "v0.2.0", "v0.2.1"])


def _previous(tmp_path, version):
    result = _run(tmp_path, "release_verify.sh", ["previous"], [TAGS], {"VERSION": version})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_previous_is_the_highest_older_release(tmp_path):
    assert _previous(tmp_path, "0.2.0-rc11") == "0.2.0-rc10"


def test_previous_of_a_final_is_its_last_rc(tmp_path):
    assert _previous(tmp_path, "0.2.0") == "0.2.0-rc10"


def test_previous_of_a_hotfix_ignores_newer_lines(tmp_path):
    assert _previous(tmp_path, "0.1.7") == "0.1.6"


def test_no_previous_release(tmp_path):
    assert _previous(tmp_path, "0.1.0") == ""
