"""Deterministic tests for the read-only .env drift check in check_drift.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.deploy import check_drift


def _write_env(path, mapping):
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in mapping.items()) + "\n",
        encoding="utf-8",
    )


def _pair(tmp_path, repo_env, dest_env):
    repo = tmp_path / "repo"
    dest = tmp_path / "dest"
    repo.mkdir()
    dest.mkdir()
    if repo_env is not None:
        _write_env(repo / ".env", repo_env)
    if dest_env is not None:
        _write_env(dest / ".env", dest_env)
    return repo, dest


def test_env_drift_reports_masked_value_and_key_differences(tmp_path):
    repo, dest = _pair(
        tmp_path,
        {"DEEPSEEK_API_KEY": "sk-old-0001", "OCR_PREFER_ENGINE": "ali_bailian"},
        {
            "DEEPSEEK_API_KEY": "sk-new-9999",
            "OCR_PREFER_ENGINE": "ali_bailian",
            "EXTRA_KEY": "1",
        },
    )

    report = check_drift.env_drift(repo, dest)

    assert report["changed_values"][0]["key"] == "DEEPSEEK_API_KEY"
    assert report["keys_only_in_dest"] == ["EXTRA_KEY"]
    joined = "\n".join(report["warnings"])
    assert "0001" in joined and "9999" in joined
    assert "sk-old-0001" not in joined  # full secret never printed
    assert "很可能已失效" in joined
    assert "pull_env_from_copy.py" in joined  # actionable advice


def test_env_drift_identical_has_no_warnings(tmp_path):
    mapping = {
        "PREFER_SOLVE_MODEL": "DEEPSEEK/deepseek-flash",
        "DEEPSEEK_API_KEY": "sk-same-1234",
    }
    repo, dest = _pair(tmp_path, mapping, mapping)

    report = check_drift.env_drift(repo, dest)

    assert report["warnings"] == []
    assert report["changed_values"] == []


def test_env_drift_flags_missing_copy_env(tmp_path):
    repo, dest = _pair(tmp_path, {"A_KEY": "1"}, None)

    report = check_drift.env_drift(repo, dest)

    assert report["dest_env_present"] is False
    assert report["warnings"]


def test_env_drift_flags_missing_repo_env(tmp_path):
    repo, dest = _pair(tmp_path, None, {"A_KEY": "1"})

    report = check_drift.env_drift(repo, dest)

    assert report["repo_env_present"] is False
    assert report["warnings"]


def test_exit_code_env_warning_is_non_fatal_by_default():
    # check_drift 退出码 0 是既定交付门禁，不能被 .env 差异打破。
    assert check_drift.resolve_exit_code([], ["repo .env differs"]) == 0
    assert check_drift.resolve_exit_code([], ["repo .env differs"], strict_env=True) == 1
    assert check_drift.resolve_exit_code(["real drift"], []) == 1
    assert check_drift.resolve_exit_code([], []) == 0
