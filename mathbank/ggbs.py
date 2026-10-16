"""GGB 动态演示资源库领域层：白名单校验、版本快照与回滚、题目关联排序。

纯领域逻辑，不 import main（避免循环依赖）；也**不做任何文件 I/O**——磁盘上的
.ggb 读写由 HTTP 路由层负责，本层只维护数据库真值（与 mathbank/topics.py 同构）。
路由层负责把 ValueError 翻译成 400、把 RuntimeError 翻译成 500。

版本语义（append-only）：
- 每个版本行**独占**一个磁盘文件（文件名含 uuid、内容不可变）；
- ``ggb_assets.file_path/file_size/file_sha256`` 是"当前版本"的**指针镜像**，
  必须与 ``current_version_no`` 指向的版本行保持同步；
- 回滚 = 复制目标版本的文件字节为新文件 + 追加一条新版本行 + 移动指针，
  历史版本行与其文件永不被改写或复用。
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from mathbank.asset_security import (
    SAFE_IMAGE_EXTENSIONS,
    AssetSecurityError,
    resolve_upload_asset,
)
from mathbank.database import GgbAsset, GgbTag, GgbVersion, Question, QuestionGgbLink
from mathbank.ggb_security import GGB_EXTENSIONS

#: 题目↔GGB 的关联类型：5 类 + custom: 逃生舱。
#: 注意：``task`` **只是分类标签**，不承载任何"任务单机制"（那属 V3，已砍）。
GGB_LINK_CODES = ("demo", "steps", "variant", "diagnosis", "task")
GGB_LINK_LABELS = {
    "demo": "直观演示",
    "steps": "解题步骤",
    "variant": "变式探究",
    "diagnosis": "错题诊断",
    "task": "课堂任务",
}
GGB_LINK_CODE_MAX_LENGTH = 60
RELATION_CUSTOM_PREFIX = "custom:"

#: 标签四维：knowledge 复用现有四级课程树码（如 B1-C1-S1-P2）；
#: 刻意不设 question_type（同一 GGB 常跨题型复用，且无聚合消费者）。
GGB_TAG_DIMS = ("knowledge", "dynamic_type", "difficulty", "custom")
GGB_TAG_DIM_LABELS = {
    "knowledge": "知识点",
    "dynamic_type": "动态类型",
    "difficulty": "难度",
    "custom": "自定义",
}
GGB_TAG_CODE_MAX_LENGTH = 120
GGB_MAX_TAGS_PER_DIM = 50

GGB_DYNAMIC_TYPES = ("param_slider", "trace", "transform", "probability", "rotation3d")
GGB_DYNAMIC_TYPE_LABELS = {
    "param_slider": "参数滑动",
    "trace": "轨迹追踪",
    "transform": "几何变换",
    "probability": "概率模拟",
    "rotation3d": "3D 旋转",
}

GGB_LEVELS = ("easy", "medium", "hard")
GGB_LEVEL_LABELS = {"easy": "基础", "medium": "中档", "hard": "难题"}

#: status 只是本机归档标识；V1.0 不提供筛选入口（延后到 V1.5）。
GGB_STATUSES = ("active", "archived")

GGB_TITLE_MAX_LENGTH = 200
GGB_MAX_LINKS_PER_QUESTION = 20
GGB_SORTS = ("updated", "created", "title")
GGB_PAGE_SIZE_MAX = 200
GGB_SNAPSHOT_APP_NAME = "classic"


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % _ 变成通配（照 main.py 同名辅助）。"""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def validate_ggb_status(value: str) -> str:
    code = str(value or "").strip()
    if code not in GGB_STATUSES:
        raise ValueError(f"资源状态必须是：{' / '.join(GGB_STATUSES)}")
    return code


def validate_ggb_link_code(value: str) -> str:
    """白名单 + custom: 逃生舱（照 question_relations / topic_links 惯例）。"""

    code = str(value or "").strip()
    if code in GGB_LINK_CODES:
        return code
    if code.startswith(RELATION_CUSTOM_PREFIX) and len(code) > len(RELATION_CUSTOM_PREFIX):
        return code[:GGB_LINK_CODE_MAX_LENGTH]
    raise ValueError(
        f"关联类型必须是：{' / '.join(GGB_LINK_CODES)}（或 custom: 开头自定义）"
    )


def _coerce_tag_payload(value) -> dict:
    """把三种常见标签载荷形状统一成 {dim: [code, ...]}。

    兼容形状（前端不同入口历史上用过不同形状，后端一律容忍，避免契约脆弱）：
    * 对象 ``{"custom": ["阿氏圆"]}``（规范形状）
    * 字符串数组 ``["阿氏圆", "切线"]`` → 全部归入 ``custom``
    * 对象数组 ``[{"tag_code": "阿氏圆", "tag_dim": "custom"}]``
    """

    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)):
        bucket: dict[str, list[str]] = {}
        for entry in value:
            if isinstance(entry, dict):
                code = entry.get("tag_code") or entry.get("code")
                dim = entry.get("tag_dim") or entry.get("dim") or "custom"
            else:
                code, dim = entry, "custom"
            code = str(code or "").strip()
            if not code:
                continue
            bucket.setdefault(str(dim or "custom").strip() or "custom", []).append(code)
        return bucket
    return {}


def normalize_ggb_tags(value) -> dict[str, list[str]]:
    """归一化四维标签：去空、去重、限长、限数量、维度白名单。"""

    raw = _coerce_tag_payload(value)
    unknown = sorted(key for key, codes in raw.items() if key not in GGB_TAG_DIMS and codes)
    if unknown:
        raise ValueError(
            f"不支持的标签维度：{' / '.join(unknown)}"
            f"（可用：{' / '.join(GGB_TAG_DIMS)}）"
        )
    normalized: dict[str, list[str]] = {}
    for dim in GGB_TAG_DIMS:
        codes = raw.get(dim) or []
        if isinstance(codes, str):
            codes = [codes]
        cleaned: list[str] = []
        seen: set[str] = set()
        for entry in codes:
            code = str(entry or "").strip()
            if not code:
                continue
            code = code[:GGB_TAG_CODE_MAX_LENGTH]
            if code in seen:
                continue
            seen.add(code)
            cleaned.append(code)
        if len(cleaned) > GGB_MAX_TAGS_PER_DIM:
            raise ValueError(f"「{GGB_TAG_DIM_LABELS[dim]}」维度最多 {GGB_MAX_TAGS_PER_DIM} 个标签")
        if dim == "dynamic_type":
            invalid = [code for code in cleaned if code not in GGB_DYNAMIC_TYPES]
            if invalid:
                raise ValueError(
                    f"动态类型必须是：{' / '.join(GGB_DYNAMIC_TYPES)}（收到 {invalid}）"
                )
        if dim == "difficulty":
            invalid = [code for code in cleaned if code not in GGB_LEVELS]
            if invalid:
                raise ValueError(f"难度必须是：{' / '.join(GGB_LEVELS)}（收到 {invalid}）")
        normalized[dim] = cleaned
    return normalized


def get_ggb_tags(db: Session, ggb_id: int) -> dict[str, list[str]]:
    """读取单个资源的四维标签（缺失维度返回空列表，保证前端结构稳定）。"""

    rows = (
        db.query(GgbTag)
        .filter(GgbTag.ggb_id == int(ggb_id))
        .order_by(GgbTag.dim.asc(), GgbTag.code.asc())
        .all()
    )
    result: dict[str, list[str]] = {dim: [] for dim in GGB_TAG_DIMS}
    for row in rows:
        result.setdefault(row.dim, []).append(row.code)
    return result


def _tags_by_asset_ids(db: Session, asset_ids) -> dict[int, dict[str, list[str]]]:
    """一次查询取回多个资源的标签，避免列表页 N+1。"""

    ids = [int(value) for value in asset_ids]
    result: dict[int, dict[str, list[str]]] = {
        asset_id: {dim: [] for dim in GGB_TAG_DIMS} for asset_id in ids
    }
    if not ids:
        return result
    rows = (
        db.query(GgbTag)
        .filter(GgbTag.ggb_id.in_(ids))
        .order_by(GgbTag.dim.asc(), GgbTag.code.asc())
        .all()
    )
    for row in rows:
        bucket = result.setdefault(row.ggb_id, {dim: [] for dim in GGB_TAG_DIMS})
        bucket.setdefault(row.dim, []).append(row.code)
    return result


def sync_ggb_tags(db: Session, ggb_id: int, tags) -> None:
    """整体替换某资源的标签（先删后增；删除先 flush 以避开唯一约束瞬态冲突）。"""

    normalized = normalize_ggb_tags(tags)
    desired = {
        (dim, code) for dim, codes in normalized.items() for code in codes
    }
    existing = {
        (row.dim, row.code): row
        for row in db.query(GgbTag).filter(GgbTag.ggb_id == int(ggb_id)).all()
    }
    removed = False
    for key, row in existing.items():
        if key not in desired:
            db.delete(row)
            removed = True
    if removed:
        db.flush()
    for dim, code in sorted(desired - set(existing)):
        db.add(GgbTag(ggb_id=int(ggb_id), dim=dim, code=code))
    db.flush()


def build_ggb_snapshot(
    asset: GgbAsset,
    tags: dict[str, list[str]],
    *,
    file_size: int = 0,
    file_sha256: str = "",
    entry_count: int | None = None,
) -> dict:
    """版本快照只存**小摘要**（.ggb 完整 XML 可达数百 KB，绝不入库）。"""

    return {
        "title": asset.title,
        "description": asset.description or "",
        "app_name": GGB_SNAPSHOT_APP_NAME,
        "file_size": int(file_size or 0),
        "file_sha256": file_sha256 or "",
        "entry_count": entry_count,
        "tags": {dim: list(tags.get(dim) or []) for dim in GGB_TAG_DIMS},
    }


def ggb_snapshot_diff_summary(previous: dict | None, current: dict) -> str:
    """与上一版快照对比，生成人读摘要（如「改标题 / 改标签 / 文件更新」）。"""

    if not previous:
        return "创建资源"
    parts: list[str] = []
    if previous.get("title") != current.get("title"):
        parts.append("改标题")
    if previous.get("description") != current.get("description"):
        parts.append("改说明")
    if previous.get("file_sha256") != current.get("file_sha256"):
        parts.append("文件更新")
    if previous.get("tags") != current.get("tags"):
        parts.append("改标签")
    return " / ".join(parts) if parts else "无实质变化"


def _load_asset(db: Session, ggb_id) -> GgbAsset:
    asset = db.get(GgbAsset, int(ggb_id))
    if asset is None:
        raise ValueError("GGB 资源不存在")
    return asset


def _normalize_title(title) -> str:
    text = str(title or "").strip()
    if not text:
        raise ValueError("资源标题不能为空")
    if len(text) > GGB_TITLE_MAX_LENGTH:
        raise ValueError(f"资源标题不能超过 {GGB_TITLE_MAX_LENGTH} 字")
    return text


def _latest_version(db: Session, ggb_id: int) -> GgbVersion | None:
    return (
        db.query(GgbVersion)
        .filter(GgbVersion.ggb_id == int(ggb_id))
        .order_by(GgbVersion.version_no.desc())
        .first()
    )


def create_ggb_asset(
    db: Session,
    *,
    title: str,
    file_path: str,
    file_size: int = 0,
    file_sha256: str = "",
    description: str = "",
    preview_image_path: str = "",
    tags=None,
    entry_count: int | None = None,
    note: str = "上传初始版本",
) -> int:
    """创建资源 + 版本 1（版本 1 直接拥有刚写入的那个文件，不复制字节）+ 标签。"""

    clean_title = _normalize_title(title)
    stored_path = str(file_path or "").strip()
    if not stored_path:
        raise ValueError("缺少 .ggb 文件路径")
    asset = GgbAsset(
        title=clean_title,
        description=str(description or ""),
        file_path=stored_path,
        file_size=int(file_size or 0),
        file_sha256=str(file_sha256 or ""),
        preview_image_path=str(preview_image_path or ""),
        status="active",
        current_version_no=1,
    )
    db.add(asset)
    db.flush()
    sync_ggb_tags(db, asset.id, tags)
    snapshot = build_ggb_snapshot(
        asset, get_ggb_tags(db, asset.id),
        file_size=file_size, file_sha256=file_sha256, entry_count=entry_count,
    )
    db.add(
        GgbVersion(
            ggb_id=asset.id,
            version_no=1,
            file_path=stored_path,
            file_size=int(file_size or 0),
            file_sha256=str(file_sha256 or ""),
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            note=str(note or ""),
        )
    )
    db.flush()
    return asset.id


def add_ggb_version(
    db: Session,
    ggb_id,
    *,
    file_path: str,
    file_size: int = 0,
    file_sha256: str = "",
    note: str = "",
    entry_count: int | None = None,
) -> dict:
    """追加一个新版本并把"当前版本"指针移到它（新版本行独占该文件）。"""

    asset = _load_asset(db, ggb_id)
    stored_path = str(file_path or "").strip()
    if not stored_path:
        raise ValueError("缺少 .ggb 文件路径")
    latest = _latest_version(db, asset.id)
    version_no = (latest.version_no + 1) if latest else 1
    snapshot = build_ggb_snapshot(
        asset, get_ggb_tags(db, asset.id),
        file_size=file_size, file_sha256=file_sha256, entry_count=entry_count,
    )
    version = GgbVersion(
        ggb_id=asset.id,
        version_no=version_no,
        file_path=stored_path,
        file_size=int(file_size or 0),
        file_sha256=str(file_sha256 or ""),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        note=str(note or ""),
    )
    db.add(version)
    asset.file_path = stored_path
    asset.file_size = int(file_size or 0)
    asset.file_sha256 = str(file_sha256 or "")
    asset.current_version_no = version_no
    db.flush()
    return version.to_dict()


def get_ggb_version(db: Session, ggb_id, version_no) -> GgbVersion:
    version = (
        db.query(GgbVersion)
        .filter(
            GgbVersion.ggb_id == int(ggb_id),
            GgbVersion.version_no == int(version_no),
        )
        .first()
    )
    if version is None:
        raise ValueError("版本不存在")
    return version


def restore_ggb_version(
    db: Session,
    ggb_id,
    version_no,
    *,
    file_path: str,
    file_size: int = 0,
    file_sha256: str = "",
    entry_count: int | None = None,
) -> dict:
    """回滚 = 追加：路由层已把目标版本字节复制为新文件，这里只追加行并移指针。"""

    get_ggb_version(db, ggb_id, version_no)
    return add_ggb_version(
        db, ggb_id,
        file_path=file_path, file_size=file_size, file_sha256=file_sha256,
        note=f"恢复自版本 {int(version_no)}", entry_count=entry_count,
    )


def delete_ggb_version(db: Session, ggb_id, version_no) -> dict:
    """删单个版本（至少保留一个）。返回被释放的文件路径，供路由层清理磁盘。"""

    asset = _load_asset(db, ggb_id)
    total = db.query(GgbVersion).filter(GgbVersion.ggb_id == asset.id).count()
    if total <= 1:
        raise ValueError("至少保留一个版本，无法删除")
    version = get_ggb_version(db, asset.id, version_no)
    released_path = version.file_path
    db.delete(version)
    db.flush()
    latest = _latest_version(db, asset.id)
    asset.current_version_no = latest.version_no
    asset.file_path = latest.file_path
    asset.file_size = latest.file_size
    asset.file_sha256 = latest.file_sha256
    db.flush()
    return {
        "version_no": int(version_no),
        "released_file_path": released_path,
        "current_version_no": latest.version_no,
    }


def list_ggb_versions(db: Session, ggb_id) -> list[dict]:
    _load_asset(db, ggb_id)
    rows = (
        db.query(GgbVersion)
        .filter(GgbVersion.ggb_id == int(ggb_id))
        .order_by(GgbVersion.version_no.desc())
        .all()
    )
    return [row.to_dict() for row in rows]


def list_ggb_assets(
    db: Session,
    *,
    q=None,
    status=None,
    dim=None,
    code=None,
    sort: str = "updated",
    page: int = 1,
    page_size: int = 60,
) -> dict:
    """资源列表（关键字 / 状态 / 标签维度筛选 + 分页）。"""

    query = db.query(GgbAsset)
    if status:
        query = query.filter(GgbAsset.status == validate_ggb_status(status))
    keyword = str(q or "").strip()
    if keyword:
        like = f"%{_escape_like(keyword)}%"
        query = query.filter(
            or_(
                GgbAsset.title.like(like, escape="\\"),
                GgbAsset.description.like(like, escape="\\"),
            )
        )
    dim_value = str(dim or "").strip()
    code_value = str(code or "").strip()
    if dim_value and dim_value not in GGB_TAG_DIMS:
        raise ValueError(f"标签维度必须是：{' / '.join(GGB_TAG_DIMS)}")
    if dim_value or code_value:
        tag_query = db.query(GgbTag.ggb_id)
        if dim_value:
            tag_query = tag_query.filter(GgbTag.dim == dim_value)
        if code_value:
            tag_query = tag_query.filter(GgbTag.code == code_value)
        query = query.filter(GgbAsset.id.in_(tag_query))

    sort_key = str(sort or "updated").strip()
    if sort_key not in GGB_SORTS:
        raise ValueError(f"排序方式必须是：{' / '.join(GGB_SORTS)}")
    if sort_key == "created":
        query = query.order_by(GgbAsset.created_at.desc(), GgbAsset.id.desc())
    elif sort_key == "title":
        query = query.order_by(GgbAsset.title.asc(), GgbAsset.id.asc())
    else:
        query = query.order_by(GgbAsset.updated_at.desc(), GgbAsset.id.desc())

    page = max(1, int(page or 1))
    page_size = max(1, min(GGB_PAGE_SIZE_MAX, int(page_size or 60)))
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    tags_map = _tags_by_asset_ids(db, [row.id for row in rows])
    return {
        "items": [
            {**row.to_dict(), "tags": tags_map.get(row.id, {dim: [] for dim in GGB_TAG_DIMS})}
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_ggb_detail(db: Session, ggb_id) -> dict:
    """资源详情：资产 + 标签 + 版本列表 + 被关联的题目（含题干摘要）。"""

    asset = _load_asset(db, ggb_id)
    versions = list_ggb_versions(db, asset.id)
    link_rows = (
        db.query(QuestionGgbLink, Question.content)
        .outerjoin(Question, Question.id == QuestionGgbLink.question_id)
        .filter(QuestionGgbLink.ggb_id == asset.id)
        .order_by(QuestionGgbLink.question_id.asc(), QuestionGgbLink.position.asc())
        .all()
    )
    links = []
    for link, question_content in link_rows:
        snippet = str(question_content or "").strip().replace("\n", " ")
        links.append({
            **link.to_dict(),
            "question_snippet": snippet[:60],
        })
    return {
        "asset": asset.to_dict(),
        "tags": get_ggb_tags(db, asset.id),
        "versions": versions,
        "version_count": len(versions),
        "links": links,
        "link_count": len(links),
    }


def update_ggb_asset(db: Session, ggb_id, payload: dict) -> dict:
    """改元信息（只改 payload 里出现的字段）；标签走整体替换语义。"""

    asset = _load_asset(db, ggb_id)
    payload = payload if isinstance(payload, dict) else {}
    if "title" in payload:
        asset.title = _normalize_title(payload.get("title"))
    if "description" in payload:
        asset.description = str(payload.get("description") or "")
    if "status" in payload:
        asset.status = validate_ggb_status(payload.get("status"))
    if "tags" in payload:
        sync_ggb_tags(db, asset.id, payload.get("tags"))
        latest = _latest_version(db, asset.id)
        if latest is not None:
            try:
                previous = json.loads(latest.snapshot_json)
            except (TypeError, ValueError):
                previous = None
            current = build_ggb_snapshot(
                asset, get_ggb_tags(db, asset.id),
                file_size=asset.file_size, file_sha256=asset.file_sha256,
                entry_count=(previous or {}).get("entry_count"),
            )
            if ggb_snapshot_diff_summary(previous, current) != "无实质变化":
                add_ggb_version(
                    db, asset.id,
                    file_path=asset.file_path, file_size=asset.file_size,
                    file_sha256=asset.file_sha256, note=ggb_snapshot_diff_summary(previous, current),
                    entry_count=(previous or {}).get("entry_count"),
                )
                return {"previous_snapshot": previous, **(db.get(GgbAsset, asset.id)).to_dict()}
    db.flush()
    return asset.to_dict()


def replace_ggb_file(
    db: Session,
    ggb_id,
    *,
    file_path: str,
    file_size: int = 0,
    file_sha256: str = "",
    note: str = "更新文件",
    entry_count: int | None = None,
) -> dict:
    """替换当前 .ggb 文件（在线编辑保存）：本质就是追加一个新版本。"""

    return add_ggb_version(
        db, ggb_id,
        file_path=file_path, file_size=file_size, file_sha256=file_sha256,
        note=note, entry_count=entry_count,
    )


def delete_ggb_asset(db: Session, ggb_id, *, force: bool = False) -> dict:
    """删资源。有关联题目且未 force 时抛 ValueError（路由映射 409）。

    返回所有需要由路由层从磁盘删除的文件路径（资产当前文件 + 全部版本文件
    + 预览图），因为行删除后路径就查不到了。
    """

    asset = _load_asset(db, ggb_id)
    link_count = (
        db.query(QuestionGgbLink).filter(QuestionGgbLink.ggb_id == asset.id).count()
    )
    if link_count and not force:
        raise ValueError(f"该资源已被 {link_count} 道题关联；如需强制删除请加 force=true")
    file_paths = {asset.file_path} if asset.file_path else set()
    file_paths.update(
        row[0] for row in
        db.query(GgbVersion.file_path).filter(GgbVersion.ggb_id == asset.id).all()
        if row[0]
    )
    preview_path = asset.preview_image_path or ""
    db.query(GgbVersion).filter(GgbVersion.ggb_id == asset.id).delete(
        synchronize_session=False
    )
    db.query(GgbTag).filter(GgbTag.ggb_id == asset.id).delete(
        synchronize_session=False
    )
    db.query(QuestionGgbLink).filter(QuestionGgbLink.ggb_id == asset.id).delete(
        synchronize_session=False
    )
    db.flush()
    db.delete(asset)
    db.flush()
    return {
        "id": int(ggb_id),
        "links_removed": link_count,
        "file_paths": sorted(file_paths),
        "preview_image_path": preview_path,
    }


def collect_referenced_ggb_paths(db: Session, *, uploads_dir, url_prefix) -> set[Path]:
    """收集全部受管 .ggb 文件与预览图的绝对路径，供启动期孤儿清理登记。

    .ggb 落在 uploads 的**子目录**里，现有的 clean_orphaned_images 只枚举
    uploads 根与 tmp/（非递归），本函数是"万一将来改成递归"的纵深防御。
    """

    references: set[str] = set()
    for file_path, preview_path in db.query(
        GgbAsset.file_path, GgbAsset.preview_image_path
    ).all():
        if file_path:
            references.add(str(file_path))
        if preview_path:
            references.add(str(preview_path))
    for (file_path,) in db.query(GgbVersion.file_path).all():
        if file_path:
            references.add(str(file_path))
    allowed = set(SAFE_IMAGE_EXTENSIONS) | set(GGB_EXTENSIONS)
    resolved: set[Path] = set()
    for reference in references:
        try:
            resolved.add(
                resolve_upload_asset(
                    reference,
                    uploads_dir=uploads_dir,
                    url_prefix=url_prefix,
                    require_file=False,
                    allowed_extensions=allowed,
                )
            )
        except AssetSecurityError:
            continue
    return resolved


# ----------------- 题目 ↔ GGB 关联 -----------------

def list_question_ggb_links(db: Session, question_id) -> list[dict]:
    rows = (
        db.query(QuestionGgbLink, GgbAsset)
        .outerjoin(GgbAsset, GgbAsset.id == QuestionGgbLink.ggb_id)
        .filter(QuestionGgbLink.question_id == int(question_id))
        .order_by(QuestionGgbLink.position.asc(), QuestionGgbLink.id.asc())
        .all()
    )
    result = []
    for link, asset in rows:
        payload = link.to_dict()
        payload["asset"] = asset.to_dict() if asset is not None else None
        result.append(payload)
    return result


def _compact_link_positions(db: Session, question_id: int) -> None:
    """把某题关联的 position 重写为 0 基连续。

    注意：question_ggb_links **没有** UNIQUE(question_id, position)，所以不需要
    topic_items 那套 10000 偏移两段式写入，直接赋值即可（见 GGB 方案 §2 表 3）。
    """

    rows = (
        db.query(QuestionGgbLink)
        .filter(QuestionGgbLink.question_id == int(question_id))
        .order_by(QuestionGgbLink.position.asc(), QuestionGgbLink.id.asc())
        .all()
    )
    for index, row in enumerate(rows):
        row.position = index
    db.flush()


def add_ggb_link(
    db: Session,
    *,
    question_id,
    ggb_id,
    link_code,
    link_label: str = "",
    note: str = "",
    is_default: bool = False,
) -> dict:
    """建立题目↔GGB 关联（先查后插，重复时抛 ValueError 供路由映射 409）。"""

    question_id = int(question_id)
    ggb_id = int(ggb_id)
    if db.get(Question, question_id) is None:
        raise ValueError("题目不存在")
    _load_asset(db, ggb_id)
    code = validate_ggb_link_code(link_code)
    duplicate = (
        db.query(QuestionGgbLink)
        .filter(
            QuestionGgbLink.question_id == question_id,
            QuestionGgbLink.ggb_id == ggb_id,
            QuestionGgbLink.link_code == code,
        )
        .first()
    )
    if duplicate is not None:
        raise ValueError("该题已存在同类型关联")
    current_count = (
        db.query(QuestionGgbLink)
        .filter(QuestionGgbLink.question_id == question_id)
        .count()
    )
    if current_count >= GGB_MAX_LINKS_PER_QUESTION:
        raise ValueError(f"单题关联的 GGB 数量上限 {GGB_MAX_LINKS_PER_QUESTION}")
    default_flag = bool(is_default)
    link = QuestionGgbLink(
        question_id=question_id,
        ggb_id=ggb_id,
        link_code=code,
        link_label=str(link_label or "")[:120],
        note=str(note or ""),
        is_default=default_flag,
        position=current_count,
    )
    db.add(link)
    db.flush()
    if default_flag:
        _set_single_default(db, question_id, link.id)
    return link.to_dict()


def _set_single_default(db: Session, question_id: int, keep_link_id: int) -> None:
    """同一题最多一个默认演示。"""

    rows = (
        db.query(QuestionGgbLink)
        .filter(
            QuestionGgbLink.question_id == int(question_id),
            QuestionGgbLink.id != int(keep_link_id),
            QuestionGgbLink.is_default.is_(True),
        )
        .all()
    )
    for row in rows:
        row.is_default = False
    db.flush()


def update_ggb_link(db: Session, link_id, payload: dict) -> dict:
    link = db.get(QuestionGgbLink, int(link_id))
    if link is None:
        raise ValueError("关联不存在")
    payload = payload if isinstance(payload, dict) else {}
    if "link_code" in payload:
        link.link_code = validate_ggb_link_code(payload.get("link_code"))
    if "link_label" in payload:
        link.link_label = str(payload.get("link_label") or "")[:120]
    if "note" in payload:
        link.note = str(payload.get("note") or "")
    if "position" in payload:
        try:
            position = int(payload.get("position"))
        except (TypeError, ValueError) as exc:
            raise ValueError("position 必须为整数") from exc
        if position < 0:
            raise ValueError("position 不能为负数")
        link.position = position
    if "is_default" in payload:
        link.is_default = bool(payload.get("is_default"))
    db.flush()
    if link.is_default:
        _set_single_default(db, link.question_id, link.id)
    return link.to_dict()


def remove_ggb_link(db: Session, link_id) -> dict:
    link = db.get(QuestionGgbLink, int(link_id))
    if link is None:
        raise ValueError("关联不存在")
    question_id = link.question_id
    payload = link.to_dict()
    db.delete(link)
    db.flush()
    _compact_link_positions(db, question_id)
    return payload


def reorder_ggb_links(db: Session, question_id, ordered_ids) -> None:
    """按给定顺序重排某题的关联（集合必须完全一致）。"""

    question_id = int(question_id)
    rows = (
        db.query(QuestionGgbLink)
        .filter(QuestionGgbLink.question_id == question_id)
        .all()
    )
    by_id = {row.id: row for row in rows}
    try:
        wanted = [int(value) for value in (ordered_ids or [])]
    except (TypeError, ValueError) as exc:
        raise ValueError("ordered_ids 必须为整数列表") from exc
    if len(wanted) != len(by_id) or set(wanted) != set(by_id):
        raise ValueError("重排列表与本题关联不一致")
    for position, link_id in enumerate(wanted):
        by_id[link_id].position = position
    db.flush()
