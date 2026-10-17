"""GGB 资源库 API 契约：上传 / 下载 / 关联 / 版本 / 导出。

钉住 V1.0 的对外行为：

* 上传走 zip 安全通道，**返回 413 的是超限、400 的是非法归档**；
* ``GET .../file`` **不需要** X-Local-Token（GeoGebra applet 无法附加自定义头，
  强制校验会同时打断"嵌入加载"与"点链接下载"），写路由则必须带 token；
* 中文标题在 Content-Disposition 里走 RFC 5987 ``filename*=utf-8''``；
* 下载内容与上传内容 sha256 一致（往返一致性硬断言）；
* 导出 HTML 内嵌 base64 且引**官方 CDN**引擎（不得引本地 /static/lib/ 路径，
  否则导出文件脱离本机服务后必坏）。
"""

import hashlib
import io
import os
import zipfile
from pathlib import Path

import pytest

from main import LOCAL_TOKEN

GGB_MEDIA_TYPE = "application/octet-stream"

GEOGEBRA_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<geogebra format="5.0"><construction><element type="point">'
    '<label>A</label><coords x="1" y="2" z="1"/></element></construction></geogebra>\n'
)


def _token():
    return {"X-Local-Token": LOCAL_TOKEN}


def build_ggb(members=None) -> bytes:
    """在内存里造一个最小但合法的 .ggb（zip + geogebra.xml）。"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        payload = members if members is not None else {"geogebra.xml": GEOGEBRA_XML}
        for name, content in payload.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def ggb_upload_root():
    """隔离 static/test_uploads/ggb 子树，用完把新增文件清干净。"""

    from main import GGB_UPLOAD_DIR

    root = Path(GGB_UPLOAD_DIR)
    before = {path for path in root.rglob("*")} if root.exists() else set()
    yield root
    if root.exists():
        for path in sorted(root.rglob("*"), reverse=True):
            if path in before:
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass


@pytest.fixture
def uploaded(client, ggb_upload_root):
    """上传一个带中文标题的 .ggb，返回 (asset_id, raw_bytes)。"""

    raw = build_ggb()
    response = client.post(
        "/api/ggb-assets/upload",
        files={"file": ("2.5.1例3教材P99T13.ggb", raw, GGB_MEDIA_TYPE)},
        data={"title": "2.5.1例3教材P99T13", "description": "直线与圆的位置关系"},
        headers=_token(),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["asset"]["id"], raw


@pytest.fixture
def a_question(client, db_session):
    from mathbank.database import Question

    question = Question(
        content="已知圆 $x^2+y^2=4$，求过点 $P(2,3)$ 的切线方程。",
        question_type="detailed_answer",
        difficulty="medium",
        answer_markdown="略",
        category_compulsory="必修第二册",
        category_chapter="第2章",
        category_knowledge="圆与方程",
    )
    db_session.add(question)
    db_session.commit()
    return question.id


# --------------------------------------------------------------------------- #
# 1. 上传
# --------------------------------------------------------------------------- #

def test_upload_then_download_roundtrip_is_byte_identical(client, uploaded):
    asset_id, raw = uploaded
    response = client.get(f"/api/ggb-assets/{asset_id}/file")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(GGB_MEDIA_TYPE)
    assert _sha256(response.content) == _sha256(raw)


def test_download_uses_rfc5987_for_chinese_title(client, uploaded):
    asset_id, _ = uploaded
    disposition = client.get(f"/api/ggb-assets/{asset_id}/file").headers[
        "content-disposition"
    ]
    assert "filename*=utf-8''" in disposition
    assert "%E4%BE%8B" in disposition  # "例" 的 UTF-8 百分号编码


def test_upload_rejects_non_zip_400(client, ggb_upload_root):
    response = client.post(
        "/api/ggb-assets/upload",
        files={"file": ("fake.ggb", b"not a zip at all", GGB_MEDIA_TYPE)},
        data={"title": "伪装的 .ggb"},
        headers=_token(),
    )
    assert response.status_code == 400, response.text
    assert response.json()["status"] == "error"


def test_upload_rejects_archive_without_geogebra_xml_400(client, ggb_upload_root):
    response = client.post(
        "/api/ggb-assets/upload",
        files={"file": ("x.ggb", build_ggb({"readme.txt": "hi"}), GGB_MEDIA_TYPE)},
        data={"title": "缺 geogebra.xml"},
        headers=_token(),
    )
    assert response.status_code == 400


def test_upload_rejects_active_content_member_400(client, ggb_upload_root):
    response = client.post(
        "/api/ggb-assets/upload",
        files={
            "file": (
                "x.ggb",
                build_ggb({"geogebra.xml": GEOGEBRA_XML, "payload.html": "<script/>"}),
                GGB_MEDIA_TYPE,
            )
        },
        data={"title": "夹带主动内容"},
        headers=_token(),
    )
    assert response.status_code == 400
    assert "payload.html" in response.text


def test_upload_rejects_oversize_with_413(client, ggb_upload_root):
    """超限走 413。

    必须用**不可压缩**的随机字节：全零内容会被压缩比守卫先一步拦下（400），
    那测试的是另一条规则（两者都对，但要各自命中）。
    """

    from mathbank.ggb_security import MAX_GGB_BYTES

    oversized = build_ggb(
        {"geogebra.xml": GEOGEBRA_XML, "padding.bin": os.urandom(MAX_GGB_BYTES + 1024)}
    )
    assert len(oversized) > MAX_GGB_BYTES
    response = client.post(
        "/api/ggb-assets/upload",
        files={"file": ("big.ggb", oversized, GGB_MEDIA_TYPE)},
        data={"title": "超大文件"},
        headers=_token(),
    )
    assert response.status_code == 413, response.text


def test_upload_rejects_compression_bomb_with_400(client, ggb_upload_root):
    """超压缩比走 400（与 413 是两条不同规则）。"""

    response = client.post(
        "/api/ggb-assets/upload",
        files={
            "file": (
                "bomb.ggb",
                build_ggb({"geogebra.xml": GEOGEBRA_XML, "z.bin": b"\x00" * (4 * 1024 * 1024)}),
                GGB_MEDIA_TYPE,
            )
        },
        data={"title": "高压缩比"},
        headers=_token(),
    )
    assert response.status_code == 400, response.text
    assert "压缩比" in response.text


def test_upload_without_local_token_is_refused(client, ggb_upload_root):
    response = client.post(
        "/api/ggb-assets/upload",
        files={"file": ("x.ggb", build_ggb(), GGB_MEDIA_TYPE)},
        data={"title": "无 token"},
    )
    assert response.status_code in (401, 403)


def test_download_needs_no_local_token(client, uploaded):
    asset_id, raw = uploaded
    response = client.get(f"/api/ggb-assets/{asset_id}/file")
    assert response.status_code == 200
    assert _sha256(response.content) == _sha256(raw)


def test_upload_falls_back_to_filename_as_title(client, ggb_upload_root):
    response = client.post(
        "/api/ggb-assets/upload",
        files={"file": ("2.4.1教材P89阿氏圆.ggb", build_ggb(), GGB_MEDIA_TYPE)},
        data={"title": ""},
        headers=_token(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["asset"]["title"] == "2.4.1教材P89阿氏圆"


def test_upload_with_preview_image_is_registered_as_referenced(client, ggb_upload_root):
    from mathbank import ggbs

    png = _tiny_png()
    response = client.post(
        "/api/ggb-assets/upload",
        files={
            "file": ("带预览.ggb", build_ggb(), GGB_MEDIA_TYPE),
            "preview_image": ("shot.png", png, "image/png"),
        },
        data={"title": "带预览图"},
        headers=_token(),
    )
    assert response.status_code == 200, response.text
    asset = response.json()["data"]["asset"]
    assert asset["preview_image_path"].startswith("/static/test_uploads/ggb/previews/")
    assert asset["preview_image_path"].endswith(".png")
    Path(_absolute(asset["preview_image_path"])).exists()

    from main import UPLOAD_DIR, UPLOAD_DIR_REL

    from mathbank.database import SessionLocal

    session = SessionLocal()
    try:
        resolved = ggbs.collect_referenced_ggb_paths(
            session, uploads_dir=UPLOAD_DIR, url_prefix=UPLOAD_DIR_REL
        )
    finally:
        session.close()
    assert any(path.name.endswith(".png") for path in resolved)
    assert any(path.name.endswith(".ggb") for path in resolved)


def _absolute(reference: str) -> str:
    from main import UPLOAD_DIR, UPLOAD_DIR_REL

    relative = reference[len(f"/{UPLOAD_DIR_REL}/"):]
    return str(Path(UPLOAD_DIR) / relative)


def _tiny_png() -> bytes:
    """生成一张真实可解码的小 PNG（既有图片管线会重编码并校验）。"""

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (48, 32), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# 1.1 上传后编辑：更换 / 清除预览图
# --------------------------------------------------------------------------- #

def test_replace_preview_updates_asset(client, uploaded, ggb_upload_root):
    asset_id, _ = uploaded
    response = client.put(
        f"/api/ggb-assets/{asset_id}/preview",
        files={"preview_image": ("new.png", _tiny_png(), "image/png")},
        headers=_token(),
    )
    assert response.status_code == 200, response.text
    reference = response.json()["data"]["preview_image_path"]
    assert reference.startswith("/static/test_uploads/ggb/previews/")
    detail = client.get(f"/api/ggb-assets/{asset_id}")
    assert detail.json()["data"]["asset"]["preview_image_path"] == reference


def test_replace_preview_without_file_clears_it(client, uploaded, ggb_upload_root):
    asset_id, _ = uploaded
    first = client.put(
        f"/api/ggb-assets/{asset_id}/preview",
        files={"preview_image": ("a.png", _tiny_png(), "image/png")},
        headers=_token(),
    )
    assert first.status_code == 200, first.text
    second = client.put(f"/api/ggb-assets/{asset_id}/preview", headers=_token())
    assert second.status_code == 200, second.text
    assert second.json()["data"]["preview_image_path"] == ""
    detail = client.get(f"/api/ggb-assets/{asset_id}")
    assert detail.json()["data"]["asset"]["preview_image_path"] == ""


def test_replace_preview_rejects_non_image_400(client, uploaded, ggb_upload_root):
    asset_id, _ = uploaded
    response = client.put(
        f"/api/ggb-assets/{asset_id}/preview",
        files={"preview_image": ("fake.png", b"definitely not an image", "image/png")},
        headers=_token(),
    )
    assert response.status_code == 400, response.text


def test_replace_preview_requires_local_token(client, uploaded):
    asset_id, _ = uploaded
    response = client.put(f"/api/ggb-assets/{asset_id}/preview")
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# 2. 列表 / 详情 / 标签
# --------------------------------------------------------------------------- #

def test_list_and_detail_expose_tags_and_counts(client, uploaded):
    asset_id, _ = uploaded
    listing = client.get("/api/ggb-assets")
    assert listing.status_code == 200
    payload = listing.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == asset_id
    assert set(payload["items"][0]["tags"]) == {
        "knowledge", "dynamic_type", "difficulty", "custom",
    }

    detail = client.get(f"/api/ggb-assets/{asset_id}")
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["version_count"] == 1
    assert data["link_count"] == 0
    assert data["asset"]["file_size"] > 0
    assert len(data["asset"]["file_sha256"]) == 64


def test_detail_and_download_return_404_for_unknown_asset(client):
    assert client.get("/api/ggb-assets/999999").status_code == 404
    assert client.get("/api/ggb-assets/999999/file").status_code == 404


def test_replace_tags_endpoint(client, uploaded):
    asset_id, _ = uploaded
    response = client.put(
        f"/api/ggb-assets/{asset_id}/tags",
        json={"tags": {"dynamic_type": ["trace"], "difficulty": ["hard"], "custom": ["阿氏圆"]}},
        headers=_token(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["dynamic_type"] == ["trace"]
    assert response.json()["data"]["custom"] == ["阿氏圆"]

    bad = client.put(
        f"/api/ggb-assets/{asset_id}/tags",
        json={"tags": {"question_type": ["single_choice"]}},
        headers=_token(),
    )
    assert bad.status_code == 400


def test_upload_accepts_frontend_tag_payload_shapes(client, ggb_upload_root):
    """前端上传表单用「字符串数组 + 独立 dynamic_type」，后端三种形状都要吃。"""

    response = client.post(
        "/api/ggb-assets/upload",
        files={"file": ("形状.ggb", build_ggb(), GGB_MEDIA_TYPE)},
        data={
            "title": "标签形状",
            "tags_json": '["阿氏圆", "切线"]',
            "dynamic_type": "trace",
        },
        headers=_token(),
    )
    assert response.status_code == 200, response.text
    tags = response.json()["data"]["tags"]
    # 读取端按 code 排序（确定性），故与传入顺序无关，比较集合。
    assert sorted(tags["custom"]) == sorted(["阿氏圆", "切线"])
    assert tags["dynamic_type"] == ["trace"]


def test_replace_tags_accepts_object_array_shape(client, uploaded):
    asset_id, _ = uploaded
    response = client.put(
        f"/api/ggb-assets/{asset_id}/tags",
        json={"tags": [{"tag_code": "圆的方程", "tag_dim": "custom"}]},
        headers=_token(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["custom"] == ["圆的方程"]


def test_update_asset_and_archive(client, uploaded):
    asset_id, _ = uploaded
    response = client.put(
        f"/api/ggb-assets/{asset_id}",
        json={"title": "改过的标题", "status": "archived"},
        headers=_token(),
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "archived"


# --------------------------------------------------------------------------- #
# 3. 题目关联
# --------------------------------------------------------------------------- #

def test_link_flow_conflict_and_delete(client, uploaded, a_question):
    asset_id, _ = uploaded
    created = client.post(
        f"/api/questions/{a_question}/ggb-links",
        json={"ggb_id": asset_id, "link_code": "demo", "link_label": "直观演示"},
        headers=_token(),
    )
    assert created.status_code == 200, created.text
    link_id = created.json()["data"]["id"]

    conflict = client.post(
        f"/api/questions/{a_question}/ggb-links",
        json={"ggb_id": asset_id, "link_code": "demo"},
        headers=_token(),
    )
    assert conflict.status_code == 409, conflict.text

    listed = client.get(f"/api/questions/{a_question}/ggb-links")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["asset"]["id"] == asset_id

    updated = client.put(
        f"/api/ggb-links/{link_id}",
        json={"link_code": "steps", "note": "先联立再判别式"},
        headers=_token(),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["link_code"] == "steps"

    removed = client.delete(f"/api/ggb-links/{link_id}", headers=_token())
    assert removed.status_code == 200
    assert client.get(f"/api/questions/{a_question}/ggb-links").json()["data"] == []


def test_link_missing_asset_returns_404(client, a_question):
    response = client.post(
        f"/api/questions/{a_question}/ggb-links",
        json={"ggb_id": 999999, "link_code": "demo"},
        headers=_token(),
    )
    assert response.status_code == 404, response.text


def test_reorder_links_endpoint(client, uploaded, a_question):
    from mathbank.database import GgbAsset, SessionLocal

    asset_id, _ = uploaded
    session = SessionLocal()
    try:
        second = GgbAsset(
            title="第二个演示", description="", file_path="",
            file_size=0, file_sha256="", preview_image_path="",
            status="active", current_version_no=1,
        )
        session.add(second)
        session.commit()
        second_id = second.id
    finally:
        session.close()

    link_ids = []
    for target in (asset_id, second_id):
        response = client.post(
            f"/api/questions/{a_question}/ggb-links",
            json={"ggb_id": target, "link_code": "demo"},
            headers=_token(),
        )
        assert response.status_code == 200, response.text
        link_ids.append(response.json()["data"]["id"])

    reordered = client.put(
        f"/api/questions/{a_question}/ggb-links/order",
        json={"ordered_ids": list(reversed(link_ids))},
        headers=_token(),
    )
    assert reordered.status_code == 200, reordered.text
    assert [row["id"] for row in reordered.json()["data"]] == list(reversed(link_ids))

    mismatched = client.put(
        f"/api/questions/{a_question}/ggb-links/order",
        json={"ordered_ids": [link_ids[0]]},
        headers=_token(),
    )
    assert mismatched.status_code == 400


# --------------------------------------------------------------------------- #
# 4. 版本：替换 / 下载旧版 / 回滚 / 删除
# --------------------------------------------------------------------------- #

def test_replace_file_adds_version_and_old_version_stays_downloadable(client, uploaded):
    asset_id, first_raw = uploaded
    second_raw = build_ggb(
        {
            "geogebra.xml": GEOGEBRA_XML.replace('x="1"', 'x="9"'),
            "geogebra_javascript.js": "// 改了一条辅助线",
        }
    )
    replaced = client.post(
        f"/api/ggb-assets/{asset_id}/file",
        files={"file": ("v2.ggb", second_raw, GGB_MEDIA_TYPE)},
        data={"note": "改了两条辅助线"},
        headers=_token(),
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()["data"]
    assert body["version"]["version_no"] == 2
    assert body["asset"]["current_version_no"] == 2

    # 当前文件 = 新文件
    assert _sha256(client.get(f"/api/ggb-assets/{asset_id}/file").content) == _sha256(second_raw)
    # 旧版本文件仍然可下载，且内容未被改写（append-only）
    old = client.get(f"/api/ggb-assets/{asset_id}/versions/1/file")
    assert old.status_code == 200
    assert _sha256(old.content) == _sha256(first_raw)
    assert "_v1.ggb" in old.headers["content-disposition"]

    versions = client.get(f"/api/ggb-assets/{asset_id}/versions").json()["data"]
    assert [row["version_no"] for row in versions] == [2, 1]


def test_restore_version_is_append_only(client, uploaded):
    asset_id, first_raw = uploaded
    client.post(
        f"/api/ggb-assets/{asset_id}/file",
        files={"file": ("v2.ggb", build_ggb({"geogebra.xml": GEOGEBRA_XML.replace('x="1"', 'x="7"')}), GGB_MEDIA_TYPE)},
        headers=_token(),
    )
    restored = client.post(
        f"/api/ggb-assets/{asset_id}/versions/1/restore", headers=_token()
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["version"]["version_no"] == 3
    # 内容回到版本 1
    assert _sha256(client.get(f"/api/ggb-assets/{asset_id}/file").content) == _sha256(first_raw)
    # 历史三版都还在
    versions = client.get(f"/api/ggb-assets/{asset_id}/versions").json()["data"]
    assert [row["version_no"] for row in versions] == [3, 2, 1]


def test_delete_last_version_is_refused(client, uploaded):
    asset_id, _ = uploaded
    response = client.delete(
        f"/api/ggb-assets/{asset_id}/versions/1", headers=_token()
    )
    assert response.status_code == 400
    assert "至少保留一个版本" in response.text


def test_delete_version_releases_its_file(client, uploaded):
    asset_id, _ = uploaded
    client.post(
        f"/api/ggb-assets/{asset_id}/file",
        files={"file": ("v2.ggb", build_ggb(), GGB_MEDIA_TYPE)},
        headers=_token(),
    )
    from mathbank.database import GgbVersion, SessionLocal

    session = SessionLocal()
    try:
        stored = (
            session.query(GgbVersion)
            .filter(GgbVersion.ggb_id == asset_id, GgbVersion.version_no == 2)
            .one()
        )
        released = _absolute(stored.file_path)
    finally:
        session.close()

    assert Path(released).exists()
    response = client.delete(
        f"/api/ggb-assets/{asset_id}/versions/2", headers=_token()
    )
    assert response.status_code == 200, response.text
    assert not Path(released).exists(), "被删版本的独占文件没有从磁盘清理"
    detail = client.get(f"/api/ggb-assets/{asset_id}").json()["data"]
    assert detail["asset"]["current_version_no"] == 1


# --------------------------------------------------------------------------- #
# 5. 导出 HTML
# --------------------------------------------------------------------------- #

def test_export_html_embeds_base64_and_uses_official_cdn(client, uploaded):
    asset_id, raw = uploaded
    response = client.get(f"/api/ggb-assets/{asset_id}/export.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "filename*=utf-8''" in response.headers["content-disposition"]
    html = response.text
    assert "ggbBase64" in html
    assert "https://www.geogebra.org/apps/deployggb.js" in html
    assert "Made with GeoGebra" in html
    # 导出文件脱离本机服务后必须仍可用 → 不得引本地相对路径
    assert "/static/lib/" not in html
    assert _sha256(raw) not in html  # 内容以 base64 内嵌，不是明文
    assert '"PK"' not in html


def test_export_html_pins_the_modern_engine_build(client, uploaded):
    asset_id, _ = uploaded
    html = client.get(f"/api/ggb-assets/{asset_id}/export.html").text
    assert "setHTML5Codebase" in html
    assert "/apps/5.4.932.1/web3d" in html, "未钉住 Classic 6 现代版对应构建 5.4.932.1"


# --------------------------------------------------------------------------- #
# 6. 删除资源
# --------------------------------------------------------------------------- #

def test_delete_asset_requires_force_when_linked(client, uploaded, a_question):
    asset_id, _ = uploaded
    client.post(
        f"/api/questions/{a_question}/ggb-links",
        json={"ggb_id": asset_id, "link_code": "demo"},
        headers=_token(),
    )
    refused = client.delete(f"/api/ggb-assets/{asset_id}", headers=_token())
    assert refused.status_code == 409, refused.text

    forced = client.delete(
        f"/api/ggb-assets/{asset_id}", params={"force": "true"}, headers=_token()
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["data"]["links_removed"] == 1
    assert client.get(f"/api/ggb-assets/{asset_id}").status_code == 404


def test_delete_asset_removes_files_from_disk(client, uploaded):
    asset_id, _ = uploaded
    from mathbank.database import GgbAsset, SessionLocal

    session = SessionLocal()
    try:
        stored = _absolute(session.get(GgbAsset, asset_id).file_path)
    finally:
        session.close()
    assert Path(stored).exists()

    assert client.delete(f"/api/ggb-assets/{asset_id}", headers=_token()).status_code == 200
    assert not Path(stored).exists()
    assert not (ggb_versions_root() / str(asset_id)).exists()


def ggb_versions_root() -> Path:
    from main import GGB_VERSIONS_DIR

    return Path(GGB_VERSIONS_DIR)


# --------------------------------------------------------------------------- #
# 7. 词表
# --------------------------------------------------------------------------- #

def test_config_endpoint_lists_link_codes_and_dims(client):
    response = client.get("/api/config/ggb-link-types")
    assert response.status_code == 200
    data = response.json()["data"]
    codes = [row["code"] for row in data["link_codes"]]
    for code in ("demo", "steps", "variant", "diagnosis", "task"):
        assert code in codes
    assert [row["code"] for row in data["tag_dims"]] == [
        "knowledge", "dynamic_type", "difficulty", "custom",
    ]
    assert "question_type" not in [row["code"] for row in data["tag_dims"]]

