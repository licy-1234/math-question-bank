"""Attack-surface tests for ``mathbank.ggb_security``.

All archives are built in memory with ``io.BytesIO`` + ``zipfile`` so the suite
needs no network, no credentials and no real symlinks on disk.
"""

import io
import stat
import zipfile

import pytest

from mathbank.asset_security import AssetSecurityError
from mathbank.ggb_security import (
    ACTIVE_CONTENT_EXTENSIONS,
    GGB_MAGIC_PREFIXES,
    MAX_GGB_BYTES,
    MAX_GGB_ENTRIES,
    MAX_GGB_MEMBER_BYTES,
    GgbEntryRejectedError,
    GgbSecurityError,
    GgbTooLargeError,
    assert_ggb_extension,
    build_ggb_storage_filename,
    hash_bytes,
    hash_file,
    read_ggb_stream_limited,
    resolve_ggb_file,
    safe_member_name,
    validate_ggb_archive,
)


MINIMAL_XML = b'<?xml version="1.0"?><geogebra app="classic"/>'


def _zip_bytes(entries, *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _zip_with_raw_member_name(member_name: str) -> bytes:
    """Build an archive whose member keeps ``member_name`` byte-for-byte.

    ``zipfile`` rewrites backslashes to slashes on write, so the name is first
    written as an equal-length placeholder and then patched in place.
    """

    placeholder = "p" * len(member_name)
    raw = _zip_bytes([("geogebra.xml", MINIMAL_XML), (placeholder, b"x")])
    return raw.replace(placeholder.encode("ascii"), member_name.encode("ascii"))


def _zip_with_symlink() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("geogebra.xml", MINIMAL_XML)
        info = zipfile.ZipInfo("link.xml")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "geogebra.xml")
    return buffer.getvalue()


def _patch_central_uncompressed_size(raw: bytes, filename: bytes, size: int) -> bytes:
    """Forge the declared uncompressed size in the central directory only."""

    signature = b"PK\x01\x02"
    start = 0
    while True:
        index = raw.index(signature, start)
        name_length = int.from_bytes(raw[index + 28 : index + 30], "little")
        name = raw[index + 46 : index + 46 + name_length]
        if name == filename:
            patched = bytearray(raw)
            patched[index + 24 : index + 28] = size.to_bytes(4, "little")
            return bytes(patched)
        start = index + 1


def test_valid_minimal_archive_is_accepted_and_hashed():
    raw = _zip_bytes([("geogebra.xml", MINIMAL_XML)])

    result = validate_ggb_archive(raw)

    assert raw[:4] in GGB_MAGIC_PREFIXES
    assert result == {
        "size": len(raw),
        "sha256": hash_bytes(raw),
        "entry_count": 1,
        "has_geogebra_xml": True,
        "ignored_members": [],
    }


def test_archive_without_geogebra_xml_is_rejected():
    raw = _zip_bytes([("readme.txt", b"no geometry here")])

    with pytest.raises(GgbSecurityError, match="geogebra.xml"):
        validate_ggb_archive(raw)


def test_non_zip_and_magic_spoof_are_rejected():
    with pytest.raises(GgbSecurityError):
        validate_ggb_archive(b"definitely not a zip container")
    with pytest.raises(GgbSecurityError):
        validate_ggb_archive(b"PK\x03\x04" + b"payload-without-a-real-archive")


@pytest.mark.parametrize(
    # A literal backslash never reaches ``safe_member_name`` through an archive:
    # ``zipfile`` rewrites ``\`` to ``/`` on both write and read, so that case is
    # covered at the unit level in ``test_safe_member_name_normalizes_and_rejects``.
    "member_name",
    ["../evil.xml", "/abs.xml", "C:/x.xml", "nested/../../evil.xml"],
)
def test_traversal_member_names_are_rejected(member_name):
    with pytest.raises(GgbSecurityError):
        validate_ggb_archive(_zip_with_raw_member_name(member_name))


def test_symlink_member_is_rejected():
    with pytest.raises(GgbEntryRejectedError, match="符号链接"):
        validate_ggb_archive(_zip_with_symlink())


def test_entry_count_over_the_limit_is_rejected():
    entries = [(f"member_{index}.dat", b"x") for index in range(MAX_GGB_ENTRIES + 1)]
    entries.append(("geogebra.xml", MINIMAL_XML))

    with pytest.raises(GgbEntryRejectedError, match="条目数"):
        validate_ggb_archive(_zip_bytes(entries))


def test_single_member_over_the_byte_cap_is_rejected():
    raw = _zip_bytes([("geogebra.xml", b"A" * (MAX_GGB_MEMBER_BYTES + 4096))])
    assert len(raw) < MAX_GGB_BYTES

    with pytest.raises(GgbEntryRejectedError, match="解压后超过"):
        validate_ggb_archive(raw)


def test_forged_declared_size_is_still_measured():
    large = _zip_bytes([("geogebra.xml", b"A" * (MAX_GGB_MEMBER_BYTES + 4096))])

    underreported = _patch_central_uncompressed_size(large, b"geogebra.xml", 1)
    with zipfile.ZipFile(io.BytesIO(underreported)) as archive:
        assert archive.getinfo("geogebra.xml").file_size == 1
    with pytest.raises(GgbSecurityError):
        validate_ggb_archive(underreported)

    overreported = _patch_central_uncompressed_size(
        _zip_bytes([("geogebra.xml", b"A" * 4096)]), b"geogebra.xml", 1 << 20
    )
    with pytest.raises(GgbSecurityError):
        validate_ggb_archive(overreported)


def test_high_compression_ratio_is_rejected():
    raw = _zip_bytes([("geogebra.xml", b"A" * (1024 * 1024))])

    with pytest.raises(GgbEntryRejectedError, match="压缩比"):
        validate_ggb_archive(raw)


def test_uncompressed_total_limit_is_enforced():
    raw = _zip_bytes(
        [("geogebra.xml", MINIMAL_XML), ("a.dat", b"x" * 800), ("b.dat", b"y" * 800)]
    )

    with pytest.raises(GgbEntryRejectedError, match="解压总量"):
        validate_ggb_archive(raw, max_total_bytes=1024)


@pytest.mark.parametrize("member_name", ["payload.html", "evil.svg", "run.exe", "code.mjs"])
def test_active_content_members_are_rejected_with_the_entry_name(member_name):
    assert ("." + member_name.rsplit(".", 1)[1]) in ACTIVE_CONTENT_EXTENSIONS
    raw = _zip_bytes([("geogebra.xml", MINIMAL_XML), (member_name, b"<script>x</script>")])

    with pytest.raises(GgbEntryRejectedError) as excinfo:
        validate_ggb_archive(raw)

    assert member_name in str(excinfo.value)


def test_unknown_companion_member_is_allowed_but_recorded():
    raw = _zip_bytes([("geogebra.xml", MINIMAL_XML), ("doc.cache", b"binary-blob")])

    result = validate_ggb_archive(raw)

    assert result["ignored_members"] == ["doc.cache"]
    assert result["has_geogebra_xml"] is True


def test_passive_image_member_is_allowed_and_not_ignored():
    raw = _zip_bytes([("geogebra.xml", MINIMAL_XML), ("images/fig.png", b"\x89PNG....")])

    result = validate_ggb_archive(raw)

    assert result["ignored_members"] == []


def test_oversized_payload_is_rejected_before_parsing():
    raw = b"PK\x03\x04" + b"\x00" * MAX_GGB_BYTES
    assert len(raw) > MAX_GGB_BYTES

    with pytest.raises(GgbTooLargeError):
        validate_ggb_archive(raw)


def test_stream_reader_stops_at_the_cap():
    assert read_ggb_stream_limited(io.BytesIO(b"abc"), max_bytes=10) == b"abc"

    with pytest.raises(GgbTooLargeError):
        read_ggb_stream_limited(io.BytesIO(b"x" * 64), max_bytes=16)


def test_safe_member_name_normalizes_and_rejects():
    assert safe_member_name("dir/geogebra.xml") == "dir/geogebra.xml"

    for unsafe in ("", "/a.xml", "../a.xml", "a\\b.xml", "C:/a.xml"):
        with pytest.raises(GgbSecurityError):
            safe_member_name(unsafe)


def test_extension_guard_and_storage_filename():
    assert assert_ggb_extension("Model.GGB") == ".ggb"
    with pytest.raises(GgbSecurityError):
        assert_ggb_extension("model.zip")

    storage_name = build_ggb_storage_filename()
    assert storage_name.endswith(".ggb")
    assert storage_name != build_ggb_storage_filename()


def test_hash_file_matches_hash_bytes(tmp_path):
    target = tmp_path / "payload.ggb"
    target.write_bytes(b"payload-bytes")

    assert hash_file(target) == hash_bytes(b"payload-bytes")


def test_resolve_ggb_file_blocks_traversal_and_wrong_type(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "model.ggb").write_bytes(_zip_bytes([("geogebra.xml", MINIMAL_XML)]))

    resolved = resolve_ggb_file(
        "/static/uploads/model.ggb", uploads_dir=uploads, url_prefix="static/uploads"
    )
    assert resolved == (uploads / "model.ggb").resolve()

    missing = resolve_ggb_file(
        "/static/uploads/missing.ggb",
        uploads_dir=uploads,
        url_prefix="static/uploads",
        require_file=False,
    )
    assert missing == (uploads / "missing.ggb").resolve()

    with pytest.raises(AssetSecurityError):
        resolve_ggb_file(
            "/static/uploads/../escape.ggb",
            uploads_dir=uploads,
            url_prefix="static/uploads",
        )
    with pytest.raises(AssetSecurityError):
        resolve_ggb_file(
            "/static/uploads/model.zip", uploads_dir=uploads, url_prefix="static/uploads"
        )
