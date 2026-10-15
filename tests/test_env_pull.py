"""Deterministic tests for tools/dev/pull_env_from_copy.py (no network, no copy writes)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dev import pull_env_from_copy as pull


def _now():
    return datetime(2026, 10, 12, 3, 4, 5, tzinfo=timezone.utc)


def _source_text(**overrides):
    values = {key: f"value-{key}" for key in pull.EXPECTED_KEYS}
    values.update(overrides)
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _source(tmp_path, text):
    path = tmp_path / "copy.env"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_keys_are_rejected_without_writing(tmp_path):
    source = _source(tmp_path, "DEEPSEEK_API_KEY=abc123\n")
    dest = tmp_path / "repo.env"

    rc = pull.run(source, dest, backup_dir=tmp_path / "bk", now=_now())

    assert rc == 2
    assert not dest.exists()


def test_write_adds_single_header_and_backs_up_previous(tmp_path):
    dest = tmp_path / "repo.env"
    dest.write_text(_source_text(), encoding="utf-8")
    source = _source(tmp_path, _source_text(DEEPSEEK_API_KEY="sk-copyvalid9001"))

    rc = pull.run(source, dest, backup_dir=tmp_path / "bk", now=_now())

    assert rc == 0
    text = dest.read_text(encoding="utf-8")
    assert text.count(pull.HEADER_PREFIX) == 1
    assert f"# GENERATED-FROM: {source.as_posix()} @" in text
    assert "sk-copyvalid9001" in text
    backups = list((tmp_path / "bk").glob(".env.bak-*"))
    assert len(backups) == 1
    assert "sk-copyvalid9001" not in backups[0].read_text(encoding="utf-8")


def test_rerun_is_idempotent_and_does_not_stack_headers(tmp_path):
    source = _source(tmp_path, _source_text())
    dest = tmp_path / "repo.env"

    assert pull.run(source, dest, backup_dir=tmp_path / "bk", now=_now()) == 0
    first = dest.read_text(encoding="utf-8")
    # Second run: content already matches -> no rewrite.
    assert pull.run(source, dest, backup_dir=tmp_path / "bk", now=_now()) == 0
    second = dest.read_text(encoding="utf-8")

    assert first == second
    assert second.count(pull.HEADER_PREFIX) == 1


def test_old_stacked_headers_are_replaced_not_appended(tmp_path):
    dest = tmp_path / "repo.env"
    dest.write_text(
        f"{pull.HEADER_PREFIX} old-a @ 2020\n"
        f"{pull.HEADER_PREFIX} old-b @ 2021\n"
        + _source_text(),
        encoding="utf-8",
    )
    source = _source(tmp_path, _source_text(PREFER_DRAW_MODEL="BAILIAN/qwen3.7-plus"))

    assert pull.run(source, dest, backup_dir=tmp_path / "bk", now=_now()) == 0
    text = dest.read_text(encoding="utf-8")

    assert text.count(pull.HEADER_PREFIX) == 1
    assert "old-a" not in text and "old-b" not in text


def test_check_mode_exit_codes_and_read_only(tmp_path):
    source_text = _source_text()
    source = _source(tmp_path, source_text)

    # Inconsistent: dest missing.
    missing_dest = tmp_path / "missing.env"
    assert pull.run(source, missing_dest, backup_dir=tmp_path / "bk", check=True) == 1
    assert not missing_dest.exists()

    # Consistent.
    dest = tmp_path / "repo.env"
    assert pull.run(source, dest, backup_dir=tmp_path / "bk", now=_now()) == 0
    assert pull.run(source, dest, backup_dir=tmp_path / "bk", check=True) == 0


def test_dry_run_does_not_write(tmp_path):
    source = _source(tmp_path, _source_text())
    dest = tmp_path / "repo.env"

    assert pull.run(source, dest, backup_dir=tmp_path / "bk", dry_run=True) == 0
    assert not dest.exists()


def test_refuses_to_write_back_to_source(tmp_path):
    source = _source(tmp_path, _source_text())

    rc = pull.run(source, source, backup_dir=tmp_path / "bk", now=_now())

    assert rc == 2


def test_refuses_to_write_into_the_user_copy(tmp_path):
    source = _source(tmp_path, _source_text())
    # A path inside the read-only copy that does not exist yet, so the guard is
    # exercised without ever touching the real .env of the user's copy.
    inside_copy = pull.COPY_ROOT / "should-never-be-created.env"

    rc = pull.run(source, inside_copy, backup_dir=tmp_path / "bk", now=_now())

    assert rc == 2
    assert not inside_copy.exists()


def test_mask_value_hides_secret_and_keeps_model_strings():
    secret = pull.mask_value("DEEPSEEK_API_KEY", "sk-topsecret9001")
    model = pull.mask_value("PREFER_SOLVE_MODEL", "DEEPSEEK/deepseek-flash")

    assert "topsecret" not in secret
    assert "9001" in secret
    assert model == "DEEPSEEK/deepseek-flash"


def test_build_comparison_ignores_generated_header(tmp_path):
    body = _source_text()
    with_header = f"{pull.HEADER_PREFIX} x @ 2026\n{body}"

    comparison = pull.build_comparison(body, with_header)

    assert comparison["identical"] is True
    assert comparison["header_present"] is True
