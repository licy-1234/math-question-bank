"""Deterministic tests for the metadata self-heal write (no network, no browser).

Regression guard for a Windows-only silent failure: the self-heal write used to
run *inside* the ``with open(METADATA_FILE)`` block.  Windows ``open()`` does not
grant ``FILE_SHARE_DELETE``, so ``write_private_text_atomic``'s ``os.replace()``
raised ``PermissionError [WinError 5]`` on every startup.  The write is wrapped
in ``try/except`` and only printed, so the upgrade silently never reached disk:
legacy book names stayed on disk forever and were re-mapped in memory on every
boot.
"""

import json
import sys

import pytest

from mathbank.curriculums import build_default_metadata


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_payload():
    default = build_default_metadata("A")
    return {
        "question_types": default["question_types"],
        "difficulties": default["difficulties"],
        "curriculum": {
            "选择性必修一": {"1. 集合与常用逻辑用语": ["1.1 集合的概念"]},
            "必修第一册": {"2. 一元二次函数、方程和不等式": []},
        },
    }


def test_self_heal_rewrites_legacy_book_names_on_disk(monkeypatch, tmp_path, capfd):
    """The upgrade must land on disk, not just in METADATA_CACHE."""

    import main

    metadata_file = tmp_path / "custom_metadata.json"
    _write(metadata_file, _legacy_payload())

    monkeypatch.setattr(main, "METADATA_FILE", str(metadata_file))
    saved_cache = main.METADATA_CACHE
    try:
        main.load_or_init_metadata()
    finally:
        main.METADATA_CACHE = saved_cache

    captured = capfd.readouterr()
    stream = captured.out + captured.err
    assert "[Metadata Self-Heal Error]" not in stream, stream
    assert "[Metadata Self-Heal]" in stream, stream

    on_disk = _read(metadata_file)
    assert set(on_disk["curriculum"]) == {"选修一", "必修一"}, on_disk["curriculum"]
    # Chapter payload must survive the rename untouched.
    assert on_disk["curriculum"]["选修一"] == {"1. 集合与常用逻辑用语": ["1.1 集合的概念"]}
    assert on_disk["curriculum"]["必修一"] == {"2. 一元二次函数、方程和不等式": []}


def test_self_heal_is_idempotent_on_a_second_run(monkeypatch, tmp_path, capfd):
    """A healed file must not be rewritten again (no needless disk churn)."""

    import main

    metadata_file = tmp_path / "custom_metadata.json"
    _write(metadata_file, _legacy_payload())

    monkeypatch.setattr(main, "METADATA_FILE", str(metadata_file))
    saved_cache = main.METADATA_CACHE
    try:
        main.load_or_init_metadata()
        capfd.readouterr()
        before = metadata_file.stat().st_mtime_ns
        main.load_or_init_metadata()
    finally:
        main.METADATA_CACHE = saved_cache

    stream = capfd.readouterr()
    assert "[Metadata Self-Heal Error]" not in stream.out + stream.err
    assert metadata_file.stat().st_mtime_ns == before
    assert set(_read(metadata_file)["curriculum"]) == {"选修一", "必修一"}


def test_atomic_replace_cannot_overwrite_a_file_that_is_still_open(tmp_path):
    """Why the read handle must be closed first (Windows-only)."""

    if sys.platform != "win32":
        pytest.skip("Windows-only: POSIX permits replacing an open file")

    from mathbank.asset_security import write_private_text_atomic

    target = tmp_path / "custom_metadata.json"
    _write(target, {"curriculum": {}})

    with pytest.raises(OSError):
        with open(target, "r", encoding="utf-8"):
            write_private_text_atomic(target, json.dumps({"curriculum": {"选修一": {}}}))

    assert _read(target) == {"curriculum": {}}
