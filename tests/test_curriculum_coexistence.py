import pytest
from main import LOCAL_TOKEN
from mathbank.database import Question, QuestionCurriculum, init_db

# 单教材版本定案后的共存契约（get_active_version_code() 固定返回 "A"）：
#   * 题目新增/更新只写入**启用版本 A** 的映射；
#   * 保存元数据时若检测到非 A 版大纲，会为非启用版本补建历史映射，
#     并按该版本回填 questions 主表分类字段；
#   * 非启用版本（B/H/S）的映射是**只读历史归档**，题目更新不会改写它；
#   * 重新保存 A 版元数据时，主表分类字段由启用版本 A 的映射回填。
# 历史上本用例曾断言"B 版下更新会写入 B 映射"，该能力随多版本方案废弃
# 已不再存在，现按上述口径校验映射共存、版本切换回填与更新只写启用版本。

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

    # Verify that a 'B' version mapping was automatically created and aligned during metadata save
    mapping_b = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="B"
    ).first()
    assert mapping_b is not None
    assert mapping_b.compulsory == "必修三"
    assert mapping_b.chapter == "第八章 向量的数量积与三角恒等变换"
    assert mapping_b.knowledge == "" # Leaf knowledge is reset to empty for B

    # Query the question and verify its main category fields are now updated to B-version!
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200
    fetched_q = response.json()
    assert fetched_q["category_compulsory"] == "必修三"
    assert fetched_q["category_chapter"] == "第八章 向量的数量积与三角恒等变换"
    assert fetched_q["category_knowledge"] == ""

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

    # Verify the non-active B-version mapping is preserved and untouched!
    mapping_b_preserved = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="B"
    ).first()
    assert mapping_b_preserved.compulsory == "必修三"
    assert mapping_b_preserved.chapter == "第八章 向量的数量积与三角恒等变换"
    assert mapping_b_preserved.knowledge == ""

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
    # from the active A-version mapping (both mappings still coexist).
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
    assert mapping_b_final is not None
    assert mapping_b_final.chapter == "第八章 向量的数量积与三角恒等变换"
