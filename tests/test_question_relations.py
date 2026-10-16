"""Schema v12 typed question relations: migration, API and filtering coverage.

These tests pin the "fold" migration contract: the migration creates the
``question_relations`` table, moves every legacy ``association_group_id`` group
into derived ``legacy_group`` rows and then blanks the source column, so
``question_relations`` is the single source of truth.  Nothing else in
``questions`` may be touched, and a second run must be a no-op.
"""

import sqlite3
from contextlib import closing, contextmanager

import pytest
from sqlalchemy import create_engine

from main import LOCAL_TOKEN, _relation_counts_for_questions
from mathbank import database as database_module
from mathbank import db_migrations
from mathbank.database import Question, QuestionRelation


@contextmanager
def _sqlite_connection(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


def _create_grouped_database(path):
    """A v11-era database with a 3-question legacy group plus one loner."""

    engine = create_engine(f"sqlite:///{path}")
    database_module.Base.metadata.create_all(bind=engine)
    engine.dispose()
    with _sqlite_connection(path) as connection:
        connection.execute("PRAGMA user_version=11")
        connection.executescript(
            """
            INSERT INTO questions (id, content, image_paths, association_group_id)
            VALUES (1, '题一', '[]', 'grp-A'),
                   (2, '题二', '[]', 'grp-A'),
                   (3, '题三', '[]', 'grp-A'),
                   (4, '题四', '[]', '');
            """
        )


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #

def test_migration_backfills_legacy_groups_and_is_idempotent(tmp_path, monkeypatch):
    database_path = tmp_path / "grouped.db"
    _create_grouped_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")

    first = db_migrations.migrate_database(engine)
    assert first["from_version"] == 11
    assert first["to_version"] == db_migrations.LATEST_SCHEMA_VERSION == 14
    # C(3, 2) pairs from the single legacy group.
    assert first["backfilled_question_relations"] == 3
    # ...and the source column is folded away in the same pass.
    assert first["cleared_association_groups"] == 3

    # A second run is a no-op that must not raise or duplicate rows.
    second = db_migrations.migrate_database(engine)
    assert second["from_version"] == 14
    assert second["to_version"] == 14
    # Idempotence is proven by the row counts below: the fold already happened,
    # so nothing new is derived and nothing is re-blanked.

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='question_relations'"
        ).fetchone() == ("question_relations",)
        rows = connection.execute(
            "SELECT question_id, related_id, relation_code "
            "FROM question_relations ORDER BY question_id, related_id"
        ).fetchall()
        assert rows == [
            (1, 2, "legacy_group"),
            (1, 3, "legacy_group"),
            (2, 3, "legacy_group"),
        ]
        # Hard constraint: the legacy column is blanked once the rows are
        # derived, so nothing can re-derive a deleted legacy relation later.
        groups = dict(
            connection.execute(
                "SELECT id, association_group_id FROM questions"
            ).fetchall()
        )
        assert groups == {1: "", 2: "", 3: "", 4: ""}
        # Every other user-confirmed question column is left alone.
        assert dict(
            connection.execute("SELECT id, content FROM questions").fetchall()
        ) == {1: "题一", 2: "题二", 3: "题三", 4: "题四"}
    engine.dispose()


def test_migration_creates_missing_relation_table(tmp_path, monkeypatch):
    database_path = tmp_path / "missing-relations.db"
    _create_grouped_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute("DROP TABLE question_relations")
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["added_question_relations"] == 1
    assert result["backfilled_question_relations"] == 3
    with _sqlite_connection(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM question_relations"
        ).fetchone()[0] == 3
    engine.dispose()


def test_current_version_branch_recovers_missing_relation_table(tmp_path, monkeypatch):
    database_path = tmp_path / "v12-missing.db"
    _create_grouped_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute("DROP TABLE question_relations")
        connection.execute("PRAGMA user_version=12")
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 12
    with _sqlite_connection(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='question_relations'"
        ).fetchone() == ("question_relations",)
        assert connection.execute(
            "SELECT COUNT(*) FROM question_relations"
        ).fetchone()[0] == 3
    engine.dispose()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@pytest.fixture
def seeded(db_session):
    for index in range(4):
        db_session.add(
            Question(content=f"题目 {index + 1}", question_type="detailed_answer")
        )
    db_session.commit()
    return [
        q.id for q in db_session.query(Question).order_by(Question.id.asc()).all()
    ]


def _token():
    return {"X-Local-Token": LOCAL_TOKEN}


def test_create_relation_normalises_pair_and_is_idempotent(client, db_session, seeded):
    a_id, b_id = seeded[0], seeded[1]

    resp = client.post(
        f"/api/questions/{b_id}/relations",
        data={
            "target_id": a_id,
            "relation_code": "one_q_variants",
            "relation_label": "一题多变",
        },
        headers=_token(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["created"] is True
    relation_id = body["relation_id"]

    row = db_session.query(QuestionRelation).one()
    # Reverse submission is normalised so question_id < related_id always holds.
    assert row.question_id == min(a_id, b_id)
    assert row.related_id == max(a_id, b_id)
    assert row.relation_code == "one_q_variants"

    # Re-submitting the same pair (opposite direction) does not add a second row.
    again = client.post(
        f"/api/questions/{a_id}/relations",
        data={
            "target_id": b_id,
            "relation_code": "one_q_variants",
            "relation_label": "一题多变",
        },
        headers=_token(),
    )
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["relation_id"] == relation_id
    assert db_session.query(QuestionRelation).count() == 1


def test_create_relation_rejects_self_and_bad_input(client, seeded):
    qid = seeded[0]

    self_resp = client.post(
        f"/api/questions/{qid}/relations",
        data={"target_id": qid, "relation_code": "same_topic"},
        headers=_token(),
    )
    assert self_resp.status_code == 400
    assert self_resp.json()["detail"] == "不能与自身建立关联。"

    missing = client.post(
        f"/api/questions/{qid}/relations",
        data={"target_id": 999999, "relation_code": "same_topic"},
        headers=_token(),
    )
    assert missing.status_code == 404

    blank = client.post(
        f"/api/questions/{qid}/relations",
        data={"target_id": seeded[1], "relation_code": "   "},
        headers=_token(),
    )
    assert blank.status_code == 400


def test_create_relation_whitelists_relation_code(client, seeded):
    a_id, b_id = seeded[0], seeded[1]

    # Built-ins pass; so does the custom: escape hatch the frontend always mints.
    for code in ["same_topic", "one_q_variants", "custom:拓展延伸"]:
        resp = client.post(
            f"/api/questions/{a_id}/relations",
            data={"target_id": b_id, "relation_code": code},
            headers=_token(),
        )
        assert resp.status_code == 200, code
        client.delete(f"/api/relations/{resp.json()['relation_id']}", headers=_token())

    # Anything outside the whitelist is rejected, including a bare prefix.
    for bad in ["totally-made-up", "custom:", "  ", "custom:   ", "x" * 61]:
        resp = client.post(
            f"/api/questions/{a_id}/relations",
            data={"target_id": b_id, "relation_code": bad},
            headers=_token(),
        )
        assert resp.status_code == 400, bad


def test_get_relations_is_bidirectional_and_delete_removes_one_row(
    client, db_session, seeded
):
    a_id, b_id, c_id = seeded[0], seeded[1], seeded[2]
    r1 = client.post(
        f"/api/questions/{a_id}/relations",
        data={
            "target_id": b_id,
            "relation_code": "same_topic",
            "relation_label": "同知识点掌握",
        },
        headers=_token(),
    ).json()
    r2 = client.post(
        f"/api/questions/{a_id}/relations",
        data={
            "target_id": c_id,
            "relation_code": "one_method_multi_use",
            "relation_label": "一法多用",
        },
        headers=_token(),
    ).json()

    items_a = client.get(f"/api/questions/{a_id}/relations").json()["items"]
    assert {item["relation_id"] for item in items_a} == {
        r1["relation_id"],
        r2["relation_id"],
    }
    # Only the single opposite question is returned, never the whole bank.
    by_id = {item["relation_id"]: item for item in items_a}
    assert by_id[r1["relation_id"]]["other"]["id"] == b_id
    assert by_id[r1["relation_id"]]["other"]["question_type"] == "detailed_answer"
    assert "content" in by_id[r1["relation_id"]]["other"]

    # Reverse direction finds the same relation.
    items_b = client.get(f"/api/questions/{b_id}/relations").json()["items"]
    assert [item["relation_id"] for item in items_b] == [r1["relation_id"]]
    assert items_b[0]["other"]["id"] == a_id

    # A missing question is a 404.
    assert client.get("/api/questions/999999/relations").status_code == 404

    # DELETE removes exactly one relation row and never touches questions.
    questions_before = db_session.query(Question).count()
    deleted = client.delete(f"/api/relations/{r1['relation_id']}", headers=_token())
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "success"}
    assert db_session.query(QuestionRelation).count() == 1
    assert db_session.query(Question).count() == questions_before
    assert client.delete(
        f"/api/relations/{r1['relation_id']}", headers=_token()
    ).status_code == 404


def test_relation_types_lists_builtins_then_custom(client, seeded):
    # 内置关联类型已收敛为两类（多题归一 / 一题多变），其余由自定义类型承接。
    expected = [
        "multi_q_unify",
        "one_q_variants",
    ]
    builtin = client.get("/api/relation-types").json()["items"]
    assert [item["code"] for item in builtin] == expected
    assert all(item["builtin"] for item in builtin)

    client.post(
        f"/api/questions/{seeded[0]}/relations",
        data={
            "target_id": seeded[1],
            "relation_code": "custom:拓展",
            "relation_label": "拓展延伸",
        },
        headers=_token(),
    )
    items = client.get("/api/relation-types").json()["items"]
    assert [item["code"] for item in items[:2]] == expected
    custom = next(item for item in items if item["code"] == "custom:拓展")
    assert custom["builtin"] is False
    assert custom["label"] == "拓展延伸"


def test_relation_filters_and_counts(client, db_session, seeded):
    a_id, b_id, c_id, d_id = seeded
    client.post(
        f"/api/questions/{a_id}/relations",
        data={"target_id": b_id, "relation_code": "same_topic"},
        headers=_token(),
    )

    # relation_code matches a question on either side of the relation.
    hit = client.get("/api/questions", params={"relation_code": "same_topic"}).json()
    assert {item["id"] for item in hit} == {a_id, b_id}

    any_items = client.get("/api/questions", params={"relation_group": "any"}).json()
    assert {item["id"] for item in any_items} == {a_id, b_id}

    none_items = client.get("/api/questions", params={"relation_group": "none"}).json()
    assert {item["id"] for item in none_items} == {c_id, d_id}

    # Unknown relation_group values are ignored (all questions returned).
    ignored = client.get("/api/questions", params={"relation_group": "bogus"}).json()
    assert {item["id"] for item in ignored} == {a_id, b_id, c_id, d_id}

    # relation_count is injected on both the bare-array and paginated paths.
    bare = {
        item["id"]: item["relation_count"]
        for item in client.get("/api/questions").json()
    }
    paged = client.get(
        "/api/questions", params={"page": 1, "page_size": 100}
    ).json()
    paged_counts = {item["id"]: item["relation_count"] for item in paged["items"]}
    assert bare == paged_counts
    assert bare == {a_id: 1, b_id: 1, c_id: 0, d_id: 0}

    # The single aggregate helper counts both directions correctly.  It only
    # returns questions that actually have rows, so absent ids default to 0.
    helper_counts = _relation_counts_for_questions(db_session, seeded)
    assert {qid: helper_counts.get(qid, 0) for qid in seeded} == bare
