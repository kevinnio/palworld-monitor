import subprocess
from unittest.mock import patch

import pytest

from src.version import VERSION_FILE, get_monitor_version, resolve_git_version


def test_release_tag_has_priority(tmp_path):
    with patch("src.version.subprocess.run") as run:
        run.return_value.stdout = "v1.20\n"
        assert resolve_git_version(tmp_path) == "v1.20"
    run.assert_called_once()
    assert "--exact-match" in run.call_args.args[0]


def test_untagged_checkout_uses_commit(tmp_path):
    tag_missing = subprocess.CalledProcessError(128, "git describe")
    sha = "abc123d"
    with patch(
        "src.version.subprocess.run",
        side_effect=[tag_missing, subprocess.CompletedProcess("git", 0, stdout=sha)],
    ) as run:
        assert resolve_git_version(tmp_path) == sha
    assert run.call_args.args[0] == ["git", "rev-parse", "--short=7", "HEAD"]


def test_version_is_unknown_without_git(tmp_path):
    with patch("src.version.subprocess.run", side_effect=FileNotFoundError):
        assert resolve_git_version(tmp_path) is None


@pytest.mark.parametrize("version", ["v1.20", "abc123d", None])
def test_frozen_build_reads_packaged_version_only(tmp_path, monkeypatch, version):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    if version:
        (tmp_path / VERSION_FILE).write_text(version, encoding="utf-8")
    get_monitor_version.cache_clear()
    try:
        with patch("src.version.subprocess.run") as run:
            assert get_monitor_version() == version
        run.assert_not_called()
    finally:
        get_monitor_version.cache_clear()
