import json
from pathlib import Path

import pytest

from main import LOCAL_TOKEN, METADATA_FILE
from mathbank.database import QuestionCurriculum, init_db

# 单教材版本定案后的共存契约（get_active_version_code() 固定返回 "A"，
# 保存元数据时的版本猜启发式已废弃，源与目标版本码都恒为 A）：
#   * 题目新增/更新只写入**启用版本 A** 的映射；
#   * 保存元数据（哪怕大纲长得像 B 版）也只按 A 版落地：不会创建 B/H/S
#     映射，questions 主表分类字段由 A 版映射回填；
#   * 重新保存 A 版元数据时，主表分类字段同样由启用版本 A 的映射回填。
# 历史上本用例曾断言"保存 B 版大纲会补建 B 映射并按 B 回填主表"，该能力
# 随多版本方案废弃已不再存在（见 main.py save_metadata_config 内注释：
# 章节名启发式会误判 B 版并触发全量 UPDATE 覆盖用户已确认的分类），现按
# 上述单版本口径校验。

@pytest.fixture(autouse=True)
def isolate_metadata_state():
    """快照并恢复共享的元数据文件与内存缓存。

    本用例通过 /api/config/metadata 会把测试用大纲写进
    data_backup/custom_metadata_test.json（IS_TESTING 下的 METADATA_FILE），
    并原地替换 main.METADATA_CACHE。历史上收尾回退只在用例全绿时才执行，
    一旦中途失败，B 版风格的大纲就留在磁盘和内存里，把同会话后续用例
    （乃至下一个 pytest 进程）的课程树全部带偏。因此无论成败都必须还原。
    """
    import main

    path = Path(METADATA_FILE)
    original_file = path.read_text(encoding="utf-8") if path.exists() else None
    original_cache = main.METADATA_CACHE
    try:
        yield
    finally:
        main.METADATA_CACHE = original_cache
        if original_file is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original_file, encoding="utf-8")

def test_curriculum_coexistence_and_migration(client, db_session):
    # Initialize the test DB index and initial migrations
    init_db()

    # Reset metadata cache to A-version default to ensure test independence from local configuration
    import main
    main.METADATA_CACHE = {
        "question_types": [],
        "difficulties": [],
        "curriculum": {
            "必修一": {
                "1. 集合与常用逻辑用语": ["1.1"]
            }
        }
    }

    headers = {"X-Local-Token": LOCAL_TOKEN}

    # 1. Create a question under Renjiao A (the only supported version)
    payload = {
        "content": "测试向量题干 $a+b$",
        "question_type": "single_choice",
        "category_compulsory": "必修二",
        "category_chapter": "6. 平面向量及其应用",
        "category_knowledge": "平面向量的数量积",
        "difficulty": "medium",
        "source": "单元测试",
        "answer_markdown": "答案解析",
        "review": "评述",
        "tikz_code": "",
        "tags": "",
        "related_question_id": "",
        "image_paths": "[]"
    }
    
    response = client.post("/api/questions", data=payload, headers=headers)
    assert response.status_code == 200
    question_id = response.json()["question"]["id"]

    # Verify that it exists in QuestionCurriculum for version 'A'
    mapping_a = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="A"
    ).first()
    assert mapping_a is not None
    assert mapping_a.compulsory == "必修二"
    assert mapping_a.chapter == "6. 平面向量及其应用"
    assert mapping_a.knowledge == "平面向量的数量积"

    # 2. Test changing metadata config version to B (which automatically triggers migration from A to B)
    # Save a metadata payload configured with curriculumB (Renjiao B)
    from main import METADATA_CACHE
    new_metadata_payload = {
        "question_types": METADATA_CACHE["question_types"],
        "difficulties": METADATA_CACHE["difficulties"],
        "curriculum": {
            "必修一": {
                "第一章 集合与常用逻辑用语": ["1.1 集合"]
            },
            "必修二": {
                "第六章 平面向量初步": ["6.1"]
            },
            "必修三": {
                "第八章 向量的数量积与三角恒等变换": ["8.1"]
            }
        }
    }

    response = client.post("/api/config/metadata", json=new_metadata_payload, headers=headers)
    assert response.status_code == 200

    # 单版本定案：保存时不再按章节名猜测版本，源与目标恒为 A，因此不会
    # 创建任何 B 版映射（历史多版本行为已废弃）。
    mapping_b = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="B"
    ).first()
    assert mapping_b is None

    # Query the question and verify its main category fields still follow the
    # active A-version mapping (the B-looking payload must not rewrite them).
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200
    fetched_q = response.json()
    assert fetched_q["category_compulsory"] == "必修二"
    assert fetched_q["category_chapter"] == "6. 平面向量及其应用"
    assert fetched_q["category_knowledge"] == "平面向量的数量积"

    # 3. Update the question under the only active version (A).
    # The update must land in the A mapping only; the B mapping stays as a
    # read-only historical record of the previous metadata switch.
    update_payload = payload.copy()
    update_payload["content"] = "更新向量题干"
    update_payload["category_compulsory"] = "必修二"
    update_payload["category_chapter"] = "6. 平面向量及其应用"
    update_payload["category_knowledge"] = "6.1"

    response = client.put(f"/api/questions/{question_id}", data=update_payload, headers=headers)
    assert response.status_code == 200

    # Verify the active A-version mapping is updated
    mapping_a_updated = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="A"
    ).first()
    assert mapping_a_updated.compulsory == "必修二"
    assert mapping_a_updated.chapter == "6. 平面向量及其应用"
    assert mapping_a_updated.knowledge == "6.1"

    # 单版本定案：不存在也不创建非启用版本映射
    mapping_b_preserved = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="B"
    ).first()
    assert mapping_b_preserved is None

    # Main category fields follow the submitted (active-version) values
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200
    updated_q = response.json()
    assert updated_q["category_compulsory"] == "必修二"
    assert updated_q["category_chapter"] == "6. 平面向量及其应用"
    assert updated_q["category_knowledge"] == "6.1"

    # 4. Switch metadata back to A-version
    revert_metadata_payload = {
        "question_types": METADATA_CACHE["question_types"],
        "difficulties": METADATA_CACHE["difficulties"],
        "curriculum": {
            "必修一": {
                "1. 集合与常用逻辑用语": ["1.1"]
            },
            "必修二": {
                "6. 平面向量及其应用": ["6.1"]
            }
        }
    }
    response = client.post("/api/config/metadata", json=revert_metadata_payload, headers=headers)
    assert response.status_code == 200

    # Query the question and verify its main category fields are re-filled
    # from the active A-version mapping (the only version that exists).
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200
    reverted_q = response.json()
    assert reverted_q["category_compulsory"] == "必修二"
    assert reverted_q["category_chapter"] == "6. 平面向量及其应用"
    assert reverted_q["category_knowledge"] == "6.1"

    mapping_a_final = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="A"
    ).first()
    assert mapping_a_final.knowledge == "6.1"
    mapping_b_final = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="B"
    ).first()
    assert mapping_b_final is None
