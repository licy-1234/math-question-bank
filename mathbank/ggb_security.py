"""Security validation for uploaded GeoGebra ``.ggb`` archives.

A ``.ggb`` file is a ZIP container that only the GeoGebra engine ever opens.
This project stores the bytes and hands them back to that engine; it never
unpacks the archive to disk and never delivers it to a browser as HTML.  The
bytes still cross a trust boundary though, so a hostile archive must not be
able to smuggle path-traversal names, symlink members, active content or a
decompression bomb into storage.  This module is the single place that decides
which archives are allowed.

Member handling is deliberately *graded* rather than "reject everything":
GeoGebra versions ship assorted companion members, and wrongly rejecting a
teacher's genuine course file is a worse outcome than tolerating an unforeseen
but inert extra entry.  Only exactly three classes exist -- known-safe members,
passive raster images, and rejected active content -- everything else is
allowed and merely recorded in ``ignored_members``.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
import zipfile

from mathbank.asset_security import resolve_upload_asset


GGB_EXTENSIONS = frozenset({".ggb"})
GGB_MAGIC_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
MAX_GGB_BYTES = 2 * 1024 * 1024
MAX_GGB_ENTRIES = 128
MAX_GGB_MEMBER_BYTES = 8 * 1024 * 1024
MAX_GGB_UNCOMPRESSED_TOTAL_BYTES = 16 * 1024 * 1024
MAX_GGB_COMPRESSION_RATIO = 200
ALLOWED_GGB_MEMBER_NAMES = frozenset(
    {"geogebra.xml", "geogebra_javascript.js", "geogebra_defaults.xml"}
)
ALLOWED_GGB_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
ACTIVE_CONTENT_EXTENSIONS = frozenset(
    {
        ".html",
        ".htm",
        ".svg",
        ".js",
        ".mjs",
        ".exe",
        ".dll",
        ".bat",
        ".cmd",
        ".ps1",
        ".sh",
        ".com",
        ".scr",
        ".msi",
    }
)
REQUIRED_GGB_MEMBER_NAME = "geogebra.xml"

# Mirrors ``mathbank.asset_security._SAFE_ASSET_COMPONENT`` so the two modules
# agree on what an acceptable relative path segment looks like.
_SAFE_GGB_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

_READ_CHUNK = 1 << 20


class GgbSecurityError(ValueError):
    """Raised when a ``.ggb`` payload is not a safe GeoGebra archive."""


class GgbTooLargeError(ValueError):
    """Raised as soon as a ``.ggb`` upload exceeds its byte cap (HTTP 413)."""


class GgbEntryRejectedError(GgbSecurityError):
    """Raised when one member of a ``.ggb`` archive is not allowed."""


def read_ggb_stream_limited(
    stream, max_bytes: int = MAX_GGB_BYTES, *, chunk_size: int = _READ_CHUNK
) -> bytes:
    """Read a synchronous stream without ever buffering past the cap."""

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(chunk_size, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise GgbTooLargeError(f"GeoGebra 文件超过 {max_bytes} 字节安全上限。")
        chunks.append(chunk)
    return b"".join(chunks)


def hash_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of ``data``."""

    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    """Return the lowercase SHA-256 hex digest of the file at ``path``."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_ggb_extension(name: str) -> str:
    """Return the normalized (lowercase, dotted) suffix of a ``.ggb`` name."""

    if not isinstance(name, str) or not name.strip():
        raise GgbSecurityError("GeoGebra 文件名不能为空。")
    extension = PurePosixPath(name.strip()).suffix.lower()
    if extension not in GGB_EXTENSIONS:
        raise GgbSecurityError("仅支持 .ggb 格式的 GeoGebra 文件。")
    return extension


def safe_member_name(name: str) -> str:
    """Return a normalized archive member name, or reject it as unsafe."""

    if not isinstance(name, str) or not name:
        raise GgbSecurityError("GeoGebra 归档条目名不能为空。")
    if "\\" in name or ":" in name:
        raise GgbSecurityError(f"GeoGebra 归档条目名包含不允许的字符：{name}")
    if name.startswith("/"):
        raise GgbSecurityError(f"GeoGebra 归档条目名不能是绝对路径：{name}")
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise GgbSecurityError(f"GeoGebra 归档条目名包含目录穿越片段：{name}")
    if any(not _SAFE_GGB_COMPONENT.fullmatch(part) for part in parts):
        raise GgbSecurityError(f"GeoGebra 归档条目名包含不安全的文件名：{name}")
    return "/".join(parts)


def _measure_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, max_member_bytes: int
) -> int:
    """Decompress one member, counting real bytes and rejecting overruns.

    ``info.file_size`` is attacker-controlled, so the declared value is never
    trusted for the size decision: the member is streamed and measured, and the
    read stops as soon as it would pass the cap.  A member whose real stream
    disagrees with its declared size surfaces as ``BadZipFile`` (CRC mismatch),
    which is itself a rejection.
    """

    consumed = 0
    try:
        with archive.open(info) as member:
            while True:
                chunk = member.read(_READ_CHUNK)
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > max_member_bytes:
                    raise GgbEntryRejectedError(
                        f"GeoGebra 条目解压后超过 {max_member_bytes} 字节安全上限："
                        f"{info.filename}"
                    )
    except zipfile.BadZipFile as exc:
        raise GgbSecurityError(
            f"GeoGebra 条目内容与其声明不一致，已拒绝：{info.filename}"
        ) from exc
    return consumed


def validate_ggb_archive(
    raw: bytes,
    *,
    max_bytes: int = MAX_GGB_BYTES,
    max_entries: int = MAX_GGB_ENTRIES,
    max_member_bytes: int = MAX_GGB_MEMBER_BYTES,
    max_total_bytes: int = MAX_GGB_UNCOMPRESSED_TOTAL_BYTES,
    max_ratio: float = MAX_GGB_COMPRESSION_RATIO,
) -> dict:
    """Validate a ``.ggb`` payload and describe it for storage.

    Returns ``{"size", "sha256", "entry_count", "has_geogebra_xml",
    "ignored_members"}`` on success and raises ``GgbTooLargeError``,
    ``GgbEntryRejectedError`` or ``GgbSecurityError`` otherwise.
    """

    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise GgbSecurityError("GeoGebra 文件内容为空。")
    if len(raw) > max_bytes:
        raise GgbTooLargeError(f"GeoGebra 文件超过 {max_bytes} 字节安全上限。")
    if raw[:4] not in GGB_MAGIC_PREFIXES or not zipfile.is_zipfile(io.BytesIO(raw)):
        raise GgbSecurityError("文件不是有效的 GeoGebra(.ggb) 压缩容器。")

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise GgbSecurityError("GeoGebra 文件结构损坏，无法解析。") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise GgbEntryRejectedError(
                f"GeoGebra 文件条目数 {len(infos)} 超过 {max_entries} 个安全上限。"
            )

        total_uncompressed = 0
        ignored_members: list[str] = []
        has_geogebra_xml = False

        for info in infos:
            name = safe_member_name(info.filename)
            if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                raise GgbEntryRejectedError(
                    f"GeoGebra 文件不允许包含符号链接条目：{info.filename}"
                )

            total_uncompressed += _measure_member(archive, info, max_member_bytes)
            if total_uncompressed > max_total_bytes:
                raise GgbEntryRejectedError(
                    f"GeoGebra 文件解压总量超过 {max_total_bytes} 字节安全上限。"
                )

            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > max_ratio:
                raise GgbEntryRejectedError(
                    f"GeoGebra 条目压缩比异常（{ratio:.0f}:1）：{info.filename}"
                )

            basename = PurePosixPath(name).name
            extension = PurePosixPath(name).suffix.lower()
            if basename in ALLOWED_GGB_MEMBER_NAMES:
                if basename == REQUIRED_GGB_MEMBER_NAME:
                    has_geogebra_xml = True
            elif extension in ALLOWED_GGB_IMAGE_EXTENSIONS:
                pass
            elif extension in ACTIVE_CONTENT_EXTENSIONS:
                raise GgbEntryRejectedError(
                    f"GeoGebra 文件不允许包含主动内容条目：{info.filename}"
                )
            else:
                ignored_members.append(name)

        if not has_geogebra_xml:
            raise GgbSecurityError(
                "缺少 geogebra.xml，可能不是有效的 GeoGebra 文件。"
            )

    return {
        "size": len(raw),
        "sha256": hash_bytes(raw),
        "entry_count": len(infos),
        "has_geogebra_xml": True,
        "ignored_members": ignored_members,
    }


def resolve_ggb_file(
    reference: str,
    *,
    uploads_dir,
    url_prefix: str,
    require_file: bool = True,
) -> Path:
    """Resolve a browser ``.ggb`` reference to a contained local file.

    Delegates traversal, containment and symlink checks to
    ``asset_security.resolve_upload_asset`` so there is a single implementation.
    """

    return resolve_upload_asset(
        reference,
        uploads_dir=uploads_dir,
        url_prefix=url_prefix,
        require_file=require_file,
        allowed_extensions=GGB_EXTENSIONS,
    )


def build_ggb_storage_filename() -> str:
    """Return an opaque, collision-free ``.ggb`` storage basename."""

    return f"{uuid.uuid4().hex}.ggb"
