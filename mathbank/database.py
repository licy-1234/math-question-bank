import datetime
import sqlite3
import json
from pathlib import Path
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, object_session, relationship, sessionmaker
from mathbank.paths import DATABASE_FILE, sqlite_url
from mathbank.question_types import normalize_section_order
from mathbank.question_assets import without_review_only_assets
from mathbank.sub_knowledge import normalize_sub_knowledge, resolve_active_version
from mathbank.tags import chapter_display_label


FIGURE_ALIGN_VALUES = frozenset({"right", "center", "bottom_left", "bottom_right"})
FIGURE_SIZE_VALUES = frozenset({"auto", "small", "medium", "large"})


def normalize_figure_size(value) -> str:
    """Return the persisted figure-size enum or its safe default."""

    normalized = str(value or "").strip()
    return normalized if normalized in FIGURE_SIZE_VALUES else "auto"


# SQLite Database URL
SQLALCHEMY_DATABASE_URL = sqlite_url(DATABASE_FILE)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):
    """Apply relational safety settings to every SQLite connection."""

    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _utcnow_naive():
    """Return UTC without tzinfo for the existing SQLite DateTime columns."""

    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def configure_sqlite_wal(database_engine: Engine) -> str | None:
    """Prefer WAL, but keep a local single-user database usable with DELETE."""

    database_name = database_engine.url.database
    if not database_name or database_name == ":memory:":
        return None
    wal_error = None
    try:
        with database_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            mode = str(
                connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
            ).lower()
            if mode == "wal":
                connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
                connection.exec_driver_sql("PRAGMA wal_autocheckpoint=1000")
            else:
                wal_error = f"journal_mode returned {mode}"
    except SQLAlchemyError as exc:
        mode = ""
        wal_error = f"{type(exc).__name__}: {exc}"

    if mode != "wal":
        try:
            with database_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                mode = str(
                    connection.exec_driver_sql("PRAGMA journal_mode=DELETE").scalar_one()
                ).lower()
                if mode != "delete":
                    raise RuntimeError(
                        f"SQLite 无法启用 WAL 或 DELETE 日志模式，当前模式: {mode}"
                    )
                connection.exec_driver_sql("PRAGMA synchronous=FULL")
        except SQLAlchemyError as exc:
            raise RuntimeError("SQLite WAL 降级至 DELETE 模式失败") from exc
        print(
            "[Database Warning] WAL mode is unavailable; using the safer "
            f"single-user DELETE journal fallback ({wal_error}).",
            flush=True,
        )
    try:
        Path(database_name).resolve().chmod(0o600)
    except OSError:
        pass
    return mode

class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)  # 题干 (LaTeX + markdown)
    question_type = Column(String(50), default="single_choice", index=True)  # single_choice, multi_choice, fill_in_blank, detailed_answer
    category_compulsory = Column(String(100), default="", index=True)  # 必修/选修/选择性必修
    category_chapter = Column(String(100), default="", index=True)  # 章节
    category_knowledge = Column(String(100), default="", index=True)  # 知识点
    difficulty = Column(String(50), default="medium", index=True)  # easy, medium, hard
    source = Column(String(200), default="")  # 来源
    answer_markdown = Column(Text, default="")  # 答案与解析 (LaTeX + markdown)
    review = Column(Text, default="")  # 评述 (允许空白)
    association_group_id = Column(String(100), default="", index=True)  # 关联题目分组ID (支持传递关系)
    _image_paths = Column(Text, default="[]", name="image_paths")  # 以JSON字符串形式存储相对路径列表
    tikz_code = Column(Text, default="")  # TikZ 几何绘图源代码
    tikz_reference_image_path = Column(Text, default="")  # 题干 TikZ 自动重绘时的原题参考图
    _content_tikz_assets = Column(Text, default="[]", name="content_tikz_assets")  # 题干多图 TikZ 源码与渲染图映射
    _answer_tikz_assets = Column(Text, default="[]", name="answer_tikz_assets")  # 解答多图 TikZ 源码与渲染图映射
    figure_align = Column(String(50), default="right")  # 插图排版位置: right (题干右侧), center (下方居中), bottom_left (下方居左), bottom_right (下方居右)
    figure_align_custom = Column(
        Boolean, nullable=False, default=False, server_default="0"
    )  # 是否由用户明确选择过插图位置
    figure_size = Column(
        String(20), nullable=False, default="auto", server_default="auto"
    )  # 题末可分离插图尺寸: auto, small, medium, large
    _image_layouts = Column(Text, nullable=False, default="{}", server_default="{}", name="image_layouts")
    tags = Column(Text, default="")  # 自定义标签 (逗号分隔或字符串)
    usage_count = Column(Integer, default=0, index=True)  # 组卷引用次数
    created_at = Column(DateTime, default=_utcnow_naive)

    curriculum_mappings = relationship("QuestionCurriculum", back_populates="question", cascade="all, delete-orphan")

    def sub_knowledge_for_version(self, version_code: str | None = None):
        version_code = version_code or resolve_active_version()
        mapping = next((row for row in self.curriculum_mappings if row.version_code == version_code), None)
        return mapping.sub_knowledge if mapping is not None else []

    @property
    def sub_knowledge(self):
        return self.sub_knowledge_for_version()

    @property
    def image_layouts(self):
        from .image_layout import normalize_image_layouts
        try:
            return normalize_image_layouts(self._image_layouts or "{}", self.content)
        except ValueError:
            return {}

    @image_layouts.setter
    def image_layouts(self, value):
        from .image_layout import normalize_image_layouts
        self._image_layouts = json.dumps(normalize_image_layouts(value, self.content), ensure_ascii=False)

    @property
    def image_paths(self):
        try:
            value = json.loads(self._image_paths)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @image_paths.setter
    def image_paths(self, value):
        if isinstance(value, list):
            self._image_paths = json.dumps(value)
        else:
            self._image_paths = "[]"

    @property
    def answer_tikz_assets(self):
        try:
            value = json.loads(self._answer_tikz_assets or "[]")
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @answer_tikz_assets.setter
    def answer_tikz_assets(self, value):
        if isinstance(value, list):
            self._answer_tikz_assets = json.dumps(value, ensure_ascii=False)
        else:
            self._answer_tikz_assets = "[]"

    @property
    def content_tikz_assets(self):
        try:
            value = json.loads(self._content_tikz_assets or "[]")
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @content_tikz_assets.setter
    def content_tikz_assets(self, value):
        if isinstance(value, list):
            self._content_tikz_assets = json.dumps(value, ensure_ascii=False)
        else:
            self._content_tikz_assets = "[]"

    @property
    def display_image_paths(self):
        """Return ordinary figures, hiding AI references and review-only images."""

        hidden_references = {
            str(self.tikz_reference_image_path or "").strip()
        }
        for asset in [*self.content_tikz_assets, *self.answer_tikz_assets]:
            if not isinstance(asset, dict):
                continue
            reference = str(asset.get("reference_image_path") or "").strip()
            if reference:
                hidden_references.add(reference)
        hidden_references.discard("")
        return without_review_only_assets(
            [path for path in self.image_paths if path not in hidden_references],
            self.content or "", self.answer_markdown or "", self.review or "",
        )

    def tag_codes(self) -> dict:
        """Group this question's multi-value tags by dimension.

        Ordered by primary key so the returned order matches the order the
        teacher entered them.  Without an explicit ORDER BY SQLite walks the
        (question_id, dim, code) unique index and returns codes in lexicographic
        order, which silently reshuffles what the user typed.
        """

        grouped = {"chapter": [], "thought": [], "function": [], "custom": []}
        session = object_session(self)
        if session is None or self.id is None:
            return grouped
        rows = (
            session.query(QuestionTag.dim, QuestionTag.code)
            .filter(QuestionTag.question_id == self.id)
            .order_by(QuestionTag.dim, QuestionTag.id)
            .all()
        )
        for dim, code in rows:
            grouped.setdefault(dim, []).append(code)
        return grouped

    def to_dict(self, version_code: str | None = None):
        tag_codes = self.tag_codes()
        return {
            "id": self.id,
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "tag_codes": tag_codes,
            "chapter_label": chapter_display_label(tag_codes.get("chapter") or []),
            "sub_knowledge": self.sub_knowledge_for_version(version_code),
            "difficulty": self.difficulty,
            "source": self.source,
            "answer_markdown": self.answer_markdown,
            "has_answer": bool((self.answer_markdown or "").strip()),
            "review": self.review,
            "association_group_id": self.association_group_id,
            "image_paths": self.display_image_paths,
            "tikz_code": self.tikz_code,
            "tikz_reference_image_path": self.tikz_reference_image_path or "",
            "content_tikz_assets": self.content_tikz_assets,
            "answer_tikz_assets": self.answer_tikz_assets,
            "figure_align": self.figure_align or "right",
            "figure_align_custom": bool(self.figure_align_custom),
            "figure_size": normalize_figure_size(self.figure_size),
            "image_layouts": self.image_layouts,
            "tags": self.tags,
            "usage_count": self.usage_count or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

    def to_summary_dict(self, version_code: str | None = None):
        tag_codes = self.tag_codes()
        return {
            "id": self.id,
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "tag_codes": tag_codes,
            "chapter_label": chapter_display_label(tag_codes.get("chapter") or []),
            "sub_knowledge": self.sub_knowledge_for_version(version_code),
            "difficulty": self.difficulty,
            "source": self.source,
            "has_answer": bool((self.answer_markdown or "").strip()),
            "association_group_id": self.association_group_id,
            "image_paths": self.display_image_paths,
            "tikz_code": self.tikz_code,
            "figure_align": self.figure_align or "right",
            "figure_align_custom": bool(self.figure_align_custom),
            "figure_size": normalize_figure_size(self.figure_size),
            "image_layouts": self.image_layouts,
            "tags": self.tags,
            "usage_count": self.usage_count or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

class QuestionCurriculum(Base):
    __tablename__ = "question_curriculums"

    __table_args__ = (
        UniqueConstraint(
            "question_id",
            "version_code",
            name="uq_question_curriculum_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_code = Column(String(50), index=True, nullable=False)  # 'A', 'B', 'S'
    compulsory = Column(String(100), default="", index=True)
    chapter = Column(String(100), default="", index=True)
    knowledge = Column(String(100), default="", index=True)
    question = relationship("Question", back_populates="curriculum_mappings")
    sub_knowledge_rows = relationship("QuestionSubKnowledge", back_populates="curriculum", cascade="all, delete-orphan", order_by="QuestionSubKnowledge.position")

    @property
    def sub_knowledge(self):
        return [row.to_dict() for row in sorted(self.sub_knowledge_rows, key=lambda row: row.position)]

    @sub_knowledge.setter
    def sub_knowledge(self, value):
        paths = normalize_sub_knowledge(value)
        existing = {(row.compulsory, row.chapter, row.knowledge): row for row in self.sub_knowledge_rows}
        rows = []
        for position, path in enumerate(paths):
            key = (path["compulsory"], path["chapter"], path["knowledge"])
            row = existing.get(key) or QuestionSubKnowledge(**path)
            row.position = position
            rows.append(row)
        self.sub_knowledge_rows = rows

    def to_dict(self):
        return {
            "id": self.id,
            "question_id": self.question_id,
            "version_code": self.version_code,
            "compulsory": self.compulsory,
            "chapter": self.chapter,
            "knowledge": self.knowledge,
            "sub_knowledge": self.sub_knowledge,
        }


class QuestionSubKnowledge(Base):
    __tablename__ = "question_sub_knowledge"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "compulsory", "chapter", "knowledge", name="uq_question_sub_knowledge_path"),
        CheckConstraint("position >= 0", name="ck_question_sub_knowledge_position"),
    )

    id = Column(Integer, primary_key=True)
    curriculum_id = Column(Integer, ForeignKey("question_curriculums.id", ondelete="CASCADE"), nullable=False, index=True)
    compulsory = Column(String(100), nullable=False)
    chapter = Column(String(100), nullable=False)
    knowledge = Column(String(100), nullable=False)
    position = Column(Integer, nullable=False, default=0, server_default="0")
    curriculum = relationship("QuestionCurriculum", back_populates="sub_knowledge_rows")

    def to_dict(self):
        return {"compulsory": self.compulsory, "chapter": self.chapter, "knowledge": self.knowledge}


class QuestionFingerprint(Base):
    """Rebuildable duplicate-detection indexes derived from question content."""

    __tablename__ = "question_fingerprints"

    __table_args__ = (
        Index(
            "idx_question_fingerprints_exact",
            "fingerprint_version",
            "exact_hash",
        ),
        Index(
            "idx_question_fingerprints_band0",
            "fingerprint_version",
            "band0",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band1",
            "fingerprint_version",
            "band1",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band2",
            "fingerprint_version",
            "band2",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band3",
            "fingerprint_version",
            "band3",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band4",
            "fingerprint_version",
            "band4",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band5",
            "fingerprint_version",
            "band5",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band6",
            "fingerprint_version",
            "band6",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band7",
            "fingerprint_version",
            "band7",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band0",
            "fingerprint_version",
            "text_band0",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band1",
            "fingerprint_version",
            "text_band1",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band2",
            "fingerprint_version",
            "text_band2",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band3",
            "fingerprint_version",
            "text_band3",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band4",
            "fingerprint_version",
            "text_band4",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band5",
            "fingerprint_version",
            "text_band5",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band6",
            "fingerprint_version",
            "text_band6",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band7",
            "fingerprint_version",
            "text_band7",
            "token_count",
        ),
    )

    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    fingerprint_version = Column(Integer, primary_key=True, nullable=False)
    content_revision_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    exact_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    critical_math_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    answer_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    simhash_hex = Column(
        String(32), default="", server_default=text("''"), nullable=False
    )
    token_count = Column(Integer, default=0, server_default=text("0"), nullable=False)
    choice_count = Column(Integer, default=0, server_default=text("0"), nullable=False)
    figure_count = Column(Integer, default=0, server_default=text("0"), nullable=False)
    visible_image_hashes = Column(
        Text, default="[]", server_default=text("'[]'"), nullable=False
    )
    tikz_hashes = Column(
        Text, default="[]", server_default=text("'[]'"), nullable=False
    )
    text_band0 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band1 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band2 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band3 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band4 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band5 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band6 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band7 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    band0 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band1 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band2 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band3 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band4 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band5 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band6 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band7 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    status = Column(
        String(20),
        default="pending",
        server_default=text("'pending'"),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

class QuestionTag(Base):
    """One row per multi-valued tag: 数学思想方法 / 教材章节 / 功能 / 自定义标签."""

    __tablename__ = "question_tags"
    __table_args__ = (
        UniqueConstraint("question_id", "dim", "code", name="uq_question_tag"),
        Index("idx_question_tags_dim_code", "dim", "code"),
        Index("idx_question_tags_question", "question_id"),
    )

    id = Column(Integer, primary_key=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dim = Column(String(20), nullable=False, index=True)  # thought|chapter|function|custom
    code = Column(String(120), nullable=False, index=True)
    created_at = Column(
        DateTime,
        default=_utcnow_naive,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class QuestionRelation(Base):
    """One row per typed, undirected question-to-question relation."""

    __tablename__ = "question_relations"
    __table_args__ = (
        UniqueConstraint(
            "question_id", "related_id", "relation_code",
            name="uq_question_relation",
        ),
        CheckConstraint(
            "question_id < related_id", name="ck_question_relation_order"
        ),
        Index("idx_qrel_question", "question_id"),
        Index("idx_qrel_related", "related_id"),
        Index("idx_qrel_code", "relation_code"),
    )

    id = Column(Integer, primary_key=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    related_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_code = Column(String(60), nullable=False, index=True)
    relation_label = Column(
        String(120),
        nullable=False,
        default="",
        server_default="",
    )
    note = Column(Text, default="")
    created_at = Column(
        DateTime,
        default=_utcnow_naive,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    subtitle = Column(String(200), default="")
    paper_type = Column(String(50), default="exam")  # exam, quiz, handout
    total_score = Column(Integer, default=150)
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        meta = {}
        try:
            parsed_meta = json.loads(self.metadata_json or "{}")
            meta = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            meta = {}
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "paper_type": self.paper_type,
            "total_score": self.total_score,
            "show_secret": meta.get("show_secret", True),
            "show_notice": meta.get("show_notice", True),
            "section_order": normalize_section_order(meta.get("section_order")),
            "metadata_json": self.metadata_json,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

class PaperQuestion(Base):
    __tablename__ = "paper_questions"

    __table_args__ = (
        UniqueConstraint("paper_id", "order_index", name="uq_paper_question_order"),
        CheckConstraint("score >= 0", name="ck_paper_question_score_nonnegative"),
    )

    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(
        Integer,
        ForeignKey("papers.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    order_index = Column(Integer, default=0)
    score = Column(Integer, default=5)

    def to_dict(self):
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "question_id": self.question_id,
            "order_index": self.order_index,
            "score": self.score
        }


# ----------------- 专题工坊（schema v13） -----------------

class Topic(Base):
    """教学专题：题目的教学化重组单元（长期沉淀、可嵌套、可关联、可版本化）。"""

    __tablename__ = "topics"
    __table_args__ = (
        Index("idx_topics_parent", "parent_topic_id"),
        Index("idx_topics_type", "topic_type"),
        Index("idx_topics_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    topic_type = Column(String(30), nullable=False, default="knowledge", server_default=text("'knowledge'"))
    description = Column(Text, nullable=False, default="", server_default=text("''"))
    view_mode = Column(String(20), nullable=False, default="linear", server_default=text("'linear'"))
    parent_topic_id = Column(Integer, ForeignKey("topics.id", ondelete="RESTRICT"), nullable=True)
    is_template = Column(Boolean, nullable=False, default=False, server_default="0")
    status = Column(String(20), nullable=False, default="active", server_default=text("'active'"))
    usage_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive,
                        server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    # passive_deletes 必须：否则 ORM 删父行前会先把子专题 FK 清 NULL，绕过 DB RESTRICT。
    parent_topic = relationship("Topic", remote_side=[id], passive_deletes=True)
    # 依赖 DB 级 ON DELETE CASCADE / SET NULL，避免 ORM 预先加载子行改写 FK。
    groups = relationship("TopicGroup", passive_deletes=True)
    items = relationship("TopicItem", passive_deletes=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "topic_type": self.topic_type,
            "description": self.description or "", "view_mode": self.view_mode or "linear",
            "parent_topic_id": self.parent_topic_id, "is_template": bool(self.is_template),
            "status": self.status or "active", "usage_count": self.usage_count or 0,
            "last_used_at": (self.last_used_at.isoformat() + "Z") if self.last_used_at else None,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
            "updated_at": (self.updated_at.isoformat() + "Z") if self.updated_at else None,
        }


class TopicGroup(Base):
    """专题内的题目分组（有序；删除分组只会把题目置为未分组）。"""

    __tablename__ = "topic_groups"
    __table_args__ = (
        UniqueConstraint("topic_id", "position", name="uq_topic_group_order"),
        CheckConstraint("position >= 0", name="ck_topic_group_position"),
        Index("idx_topic_groups_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(120), nullable=False, default="", server_default=text("''"))
    position = Column(Integer, nullable=False, default=0, server_default=text("0"))
    note = Column(Text, nullable=False, default="", server_default=text("''"))
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "topic_id": self.topic_id, "title": self.title or "",
                "position": self.position, "note": self.note or ""}


class TopicItem(Base):
    """专题-题目有序多对多行：position 全局有序 + 题目级教学批注 note。"""

    __tablename__ = "topic_items"
    __table_args__ = (
        UniqueConstraint("topic_id", "question_id", name="uq_topic_item_question"),
        UniqueConstraint("topic_id", "position", name="uq_topic_item_order"),
        CheckConstraint("position >= 0", name="ck_topic_item_position"),
        Index("idx_topic_items_topic", "topic_id"),
        Index("idx_topic_items_question", "question_id"),
        Index("idx_topic_items_group", "group_id"),
    )

    id = Column(Integer, primary_key=True)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(Integer, ForeignKey("topic_groups.id", ondelete="SET NULL"), nullable=True)
    position = Column(Integer, nullable=False, default=0, server_default=text("0"))
    note = Column(Text, nullable=False, default="", server_default=text("''"))
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    # DB 级 CASCADE 负责删题离组；ORM 侧不得抢先 UPDATE 非空 FK。
    question = relationship("Question", passive_deletes=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "topic_id": self.topic_id, "question_id": self.question_id,
                "group_id": self.group_id, "position": self.position, "note": self.note or ""}


class TopicVersion(Base):
    """专题版本快照：append-only，snapshot_json 自包含（主题 + 分组 + 题目项）。"""

    __tablename__ = "topic_versions"
    __table_args__ = (
        UniqueConstraint("topic_id", "version_no", name="uq_topic_version_no"),
        CheckConstraint("version_no >= 1", name="ck_topic_version_no_positive"),
        CheckConstraint("item_count >= 0", name="ck_topic_version_item_count"),
        Index("idx_topic_versions_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    version_no = Column(Integer, nullable=False)
    snapshot_json = Column(Text, nullable=False)
    note = Column(Text, nullable=False, default="", server_default=text("''"))
    item_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    def to_dict(self) -> dict:
        return {"version_no": self.version_no, "note": self.note or "",
                "item_count": self.item_count,
                "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None}


class TopicLink(Base):
    """专题间有类型关联：前置/进阶/同类对比/母题变式/派生来源（照 question_relations 范式）。"""

    __tablename__ = "topic_links"
    __table_args__ = (
        UniqueConstraint("source_topic_id", "target_topic_id", "link_code", name="uq_topic_link"),
        CheckConstraint("source_topic_id <> target_topic_id", name="ck_topic_link_order"),
        Index("idx_topic_links_source", "source_topic_id"),
        Index("idx_topic_links_target", "target_topic_id"),
        Index("idx_topic_links_code", "link_code"),
    )

    id = Column(Integer, primary_key=True)
    source_topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    target_topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    link_code = Column(String(60), nullable=False)
    link_label = Column(String(120), nullable=False, default="", server_default=text("''"))
    note = Column(Text, nullable=False, default="", server_default=text("''"))
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    source_topic = relationship("Topic", foreign_keys=[source_topic_id], passive_deletes=True)
    target_topic = relationship("Topic", foreign_keys=[target_topic_id], passive_deletes=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "source_topic_id": self.source_topic_id,
                "target_topic_id": self.target_topic_id, "link_code": self.link_code,
                "link_label": self.link_label or "", "note": self.note or ""}


class QuestionUsageStat(Base):
    """题目级学情记录（本地单机务实版）：手工录入追加 + 组卷讲评回写。"""

    __tablename__ = "question_usage_stats"
    __table_args__ = (
        CheckConstraint("correct_rate IS NULL OR (correct_rate BETWEEN 0 AND 1)", name="ck_qus_rate"),
        CheckConstraint("avg_seconds IS NULL OR avg_seconds >= 0", name="ck_qus_seconds"),
        CheckConstraint("sample_size >= 0", name="ck_qus_sample"),
        Index("idx_qus_question_time", "question_id", "recorded_at"),
        Index("idx_qus_topic", "topic_id"),
        Index("uq_qus_paper_question", "paper_id", "question_id",
              unique=True, sqlite_where=text("paper_id IS NOT NULL AND record_kind = 'paper_review'")),
    )

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="SET NULL"), nullable=True)
    record_kind = Column(String(20), nullable=False, default="manual", server_default=text("'manual'"))
    correct_rate = Column(Float, nullable=True)
    avg_seconds = Column(Integer, nullable=True)
    sample_size = Column(Integer, nullable=False, default=0, server_default=text("0"))
    note = Column(Text, nullable=False, default="", server_default=text("''"))
    recorded_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    question = relationship("Question", passive_deletes=True)
    topic = relationship("Topic", passive_deletes=True)
    paper = relationship("Paper", passive_deletes=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "question_id": self.question_id, "topic_id": self.topic_id,
                "paper_id": self.paper_id, "record_kind": self.record_kind,
                "correct_rate": self.correct_rate, "avg_seconds": self.avg_seconds,
                "sample_size": self.sample_size, "note": self.note or "",
                "recorded_at": (self.recorded_at.isoformat() + "Z") if self.recorded_at else None}


# ----------------- GGB 动态演示资源库（schema v14） -----------------

class GgbAsset(Base):
    """GeoGebra 动态演示资源：教师自制的 .ggb 课件（单机资源库主表）。

    单机单教师场景下刻意不建 scope / creator 等"无判定主体"的列；也不建操作
    记录表（不做 V3/V4）。详见 GGB 实施方案文档。
    """

    __tablename__ = "ggb_assets"
    __table_args__ = (
        CheckConstraint("file_size >= 0", name="ck_ggb_asset_file_size"),
        CheckConstraint("current_version_no >= 1", name="ck_ggb_asset_version_no"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_ggb_asset_status"),
        Index("idx_ggb_assets_status", "status"),
        Index("idx_ggb_assets_sha256", "file_sha256"),
        Index("idx_ggb_assets_updated", "updated_at"),
    )

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="", server_default=text("''"))
    file_path = Column(String(500), nullable=False, default="", server_default=text("''"))
    file_size = Column(Integer, nullable=False, default=0, server_default=text("0"))
    file_sha256 = Column(String(64), nullable=False, default="", server_default=text("''"))
    preview_image_path = Column(String(500), nullable=False, default="", server_default=text("''"))
    status = Column(String(20), nullable=False, default="active", server_default=text("'active'"))
    current_version_no = Column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive,
                        server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    # 依赖 DB 级 ON DELETE CASCADE，避免 ORM 删父行前预先加载子行。
    tags = relationship("GgbTag", passive_deletes=True)
    versions = relationship("GgbVersion", passive_deletes=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "description": self.description or "",
            "file_path": self.file_path or "", "file_size": self.file_size or 0,
            "file_sha256": self.file_sha256 or "",
            "preview_image_path": self.preview_image_path or "",
            "status": self.status or "active",
            "current_version_no": self.current_version_no or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
            "updated_at": (self.updated_at.isoformat() + "Z") if self.updated_at else None,
        }


class GgbTag(Base):
    """GGB 分类标签窄表（照 question_tags）：dim ∈ knowledge/dynamic_type/difficulty/custom."""

    __tablename__ = "ggb_tags"
    __table_args__ = (
        UniqueConstraint("ggb_id", "dim", "code", name="uq_ggb_tag"),
        Index("idx_ggb_tags_dim_code", "dim", "code"),
        Index("idx_ggb_tags_ggb", "ggb_id"),
    )

    id = Column(Integer, primary_key=True)
    ggb_id = Column(Integer, ForeignKey("ggb_assets.id", ondelete="CASCADE"), nullable=False)
    dim = Column(String(20), nullable=False)
    code = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "ggb_id": self.ggb_id, "dim": self.dim, "code": self.code}


class QuestionGgbLink(Base):
    """题目↔GGB 有类型关联（照 question_relations）。

    与 question_relations 的两点刻意差异：跨类型主键无规范序，故不设 CHECK 序；
    单题关联通常 1–5 条，故不设 UNIQUE(question_id, position)（也就不需要
    topic_items 那套 position 压缩逻辑）。
    """

    __tablename__ = "question_ggb_links"
    __table_args__ = (
        UniqueConstraint("question_id", "ggb_id", "link_code", name="uq_question_ggb_link"),
        CheckConstraint("position >= 0", name="ck_qggb_position"),
        Index("idx_qggb_question", "question_id"),
        Index("idx_qggb_ggb", "ggb_id"),
        Index("idx_qggb_code", "link_code"),
    )

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    ggb_id = Column(Integer, ForeignKey("ggb_assets.id", ondelete="CASCADE"), nullable=False)
    link_code = Column(String(60), nullable=False)
    link_label = Column(String(120), nullable=False, default="", server_default=text("''"))
    note = Column(Text, nullable=False, default="", server_default=text("''"))
    is_default = Column(Boolean, nullable=False, default=False, server_default="0")
    position = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    question = relationship("Question", passive_deletes=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "question_id": self.question_id, "ggb_id": self.ggb_id,
            "link_code": self.link_code, "link_label": self.link_label or "",
            "note": self.note or "", "is_default": bool(self.is_default),
            "position": self.position,
        }


class GgbVersion(Base):
    """GGB 版本快照：append-only；大文件外置到版本目录，snapshot_json 只存小摘要。"""

    __tablename__ = "ggb_versions"
    __table_args__ = (
        UniqueConstraint("ggb_id", "version_no", name="uq_ggb_version_no"),
        CheckConstraint("version_no >= 1", name="ck_ggb_version_no_positive"),
        CheckConstraint("file_size >= 0", name="ck_ggb_version_file_size"),
        Index("idx_ggb_versions_ggb", "ggb_id"),
    )

    id = Column(Integer, primary_key=True)
    ggb_id = Column(Integer, ForeignKey("ggb_assets.id", ondelete="CASCADE"), nullable=False)
    version_no = Column(Integer, nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False, default=0, server_default=text("0"))
    file_sha256 = Column(String(64), nullable=False, default="", server_default=text("''"))
    snapshot_json = Column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    note = Column(Text, nullable=False, default="", server_default=text("''"))
    created_at = Column(DateTime, default=_utcnow_naive, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    def to_dict(self) -> dict:
        return {
            "version_no": self.version_no, "file_size": self.file_size or 0,
            "file_sha256": self.file_sha256 or "", "note": self.note or "",
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
        }


# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables
def init_db():
    from mathbank.db_migrations import (
        LEGACY_REQUIRED_TABLES,
        LATEST_SCHEMA_VERSION,
        REQUIRED_TABLES,
        SELF_HEALING_TABLES,
        create_pre_migration_backup,
        migrate_database,
        schema_version,
    )

    # Refuse a future schema before create_all or any legacy ALTER can mutate it.
    current_version = schema_version(engine)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current_version} 高于程序支持版本 "
            f"{LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    with engine.connect() as connection:
        existing_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        core_tables = existing_tables & LEGACY_REQUIRED_TABLES
        if existing_tables:
            upgradeable_layouts = (
                {"questions"},
                {"questions", "question_curriculums"},
                LEGACY_REQUIRED_TABLES,
            )
            if current_version != 0:
                upgradeable_layouts = (LEGACY_REQUIRED_TABLES,)
            if core_tables not in upgradeable_layouts:
                missing = ", ".join(sorted(LEGACY_REQUIRED_TABLES - core_tables))
                raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")
            if current_version >= LATEST_SCHEMA_VERSION:
                # Tables in SELF_HEALING_TABLES are (re)created downstream by
                # create_all and by migrate_database's ensure pass.  Rejecting
                # them here would fire before that repair can run, which would
                # turn a recoverable gap into a hard startup failure.
                missing = (REQUIRED_TABLES - SELF_HEALING_TABLES) - existing_tables
                if missing:
                    topic_missing = sorted(
                        set(missing) & {
                            "topics", "topic_groups", "topic_items",
                            "topic_versions", "topic_links", "question_usage_stats",
                        }
                    )
                    if topic_missing:
                        raise RuntimeError(
                            "检测到专题表缺失: " + ", ".join(topic_missing)
                            + "。专题数据为用户原创教学内容，不会自动重建；"
                            "请从 data_backup 恢复备份后重启。"
                        )
                    ggb_missing = sorted(
                        set(missing) & {
                            "ggb_assets", "ggb_tags", "question_ggb_links", "ggb_versions",
                        }
                    )
                    if ggb_missing:
                        raise RuntimeError(
                            "检测到 GGB 资源表缺失: " + ", ".join(ggb_missing)
                            + "。教师自制的 .ggb 课件与题目关联为用户原创内容，不会自动重建；"
                            "请从 data_backup 恢复备份后重启。"
                        )
                    raise RuntimeError(
                        "数据库结构不完整，缺少必要数据表: "
                        + ", ".join(sorted(missing))
                    )

            # Old releases legitimately had only the question tables.  Accept
            # those known layouts, but reject a similarly named damaged table
            # before create_all can disguise the missing core columns.
            required_columns = {
                "questions": {
                    "id", "content", "question_type", "category_compulsory",
                    "category_chapter", "category_knowledge", "difficulty",
                    "source", "answer_markdown", "image_paths", "created_at",
                },
                "question_curriculums": {
                    "id", "question_id", "version_code", "compulsory",
                    "chapter", "knowledge",
                },
                "papers": {
                    "id", "title", "subtitle", "paper_type", "total_score",
                    "metadata_json", "created_at",
                },
                "paper_questions": {
                    "id", "paper_id", "question_id", "order_index", "score",
                },
            }
            if current_version >= 3:
                required_columns["questions"].add("answer_tikz_assets")
            if current_version >= 4:
                required_columns["questions"].add("tikz_reference_image_path")
            if current_version >= 5:
                required_columns["questions"].add("content_tikz_assets")
            if current_version >= 10:
                required_columns["questions"].add("image_layouts")
            if current_version >= 9:
                required_columns["questions"].add("figure_size")
                required_columns["questions"].add("figure_align_custom")
            for table_name in core_tables:
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        f'PRAGMA table_info("{table_name}")'
                    ).fetchall()
                }
                missing_columns = required_columns[table_name] - columns
                if missing_columns:
                    missing = ", ".join(sorted(missing_columns))
                    raise RuntimeError(
                        f"数据库表 {table_name} 缺少核心字段: {missing}"
                    )
    pre_migration_backup = None
    if current_version < LATEST_SCHEMA_VERSION and existing_tables:
        pre_migration_backup = create_pre_migration_backup(
            engine,
            from_version=current_version,
            to_version=LATEST_SCHEMA_VERSION,
        )

    if existing_tables:
        # Existing databases must leave fingerprint table/index creation or
        # replacement to the explicit migration transaction below.  create_all
        # remains responsible only for filling the known legacy core layout
        # when upgrading very old question-only databases.
        Base.metadata.create_all(
            bind=engine,
            tables=[
                Question.__table__,
                QuestionCurriculum.__table__,
                QuestionTag.__table__,
                QuestionRelation.__table__,
                Paper.__table__,
                PaperQuestion.__table__,
            ],
        )
    else:
        Base.metadata.create_all(bind=engine)
    # Create indexes manually and execute automatic migrations for SQLite databases to ensure maximum performance at scale
    try:
        with engine.begin() as conn:
            # Check column existence
            cursor = conn.execute(text("PRAGMA table_info(questions)"))
            columns = [row[1] for row in cursor.fetchall()]
            
            if "review" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN review TEXT DEFAULT ''"))
                print("Added column 'review' to questions table successfully.")
                
            if "association_group_id" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN association_group_id VARCHAR(100) DEFAULT ''"))
                print("Added column 'association_group_id' to questions table successfully.")
                
            if "tikz_code" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tikz_code TEXT DEFAULT ''"))
                print("Added column 'tikz_code' to questions table successfully.")

            if "figure_align" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN figure_align VARCHAR(50) DEFAULT 'right'"))
                print("Added column 'figure_align' to questions table successfully.")
                
            if "tags" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tags TEXT DEFAULT ''"))
                print("Added column 'tags' to questions table successfully.")
                
            if "usage_count" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN usage_count INTEGER DEFAULT 0"))
                print("Added column 'usage_count' to questions table successfully.")
                
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_compulsory ON questions (category_compulsory)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_chapter ON questions (category_chapter)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_knowledge ON questions (category_knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_question_type ON questions (question_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions (difficulty)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_association_group_id ON questions (association_group_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions (tags)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_usage_count ON questions (usage_count)"))

            # Create indexes on question_curriculums
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_lookup ON question_curriculums (version_code, compulsory, chapter, knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_qid ON question_curriculums (question_id)"))

            # Additive fallback for the v12 typed relation table.  Same idempotent
            # "create if missing" style as the columns above so the ORM can always
            # query it even before the explicit migration transaction runs.
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS question_relations (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER NOT NULL,
                    related_id INTEGER NOT NULL,
                    relation_code VARCHAR(60) NOT NULL,
                    relation_label VARCHAR(120) NOT NULL DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_question_relation UNIQUE (question_id, related_id, relation_code),
                    CONSTRAINT ck_question_relation_order CHECK (question_id < related_id),
                    CONSTRAINT fk_qrel_question FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
                    CONSTRAINT fk_qrel_related FOREIGN KEY(related_id) REFERENCES questions(id) ON DELETE CASCADE
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_qrel_question ON question_relations (question_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_qrel_related ON question_relations (related_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_qrel_code ON question_relations (relation_code)"))

            # Auto-migrate legacy data to A-version question_curriculums
            cursor = conn.execute(text("SELECT COUNT(*) FROM question_curriculums"))
            count = cursor.fetchone()[0]
            if count == 0:
                conn.execute(text("""
                    INSERT INTO question_curriculums (question_id, version_code, compulsory, chapter, knowledge)
                    SELECT id, 'A', category_compulsory, category_chapter, category_knowledge
                    FROM questions
                """))
                print("Successfully auto-migrated legacy question categories to A-version question_curriculums mapping.")
    except Exception as e:
        raise RuntimeError("数据库旧字段或索引迁移失败，服务已停止启动") from e

    migration_result = migrate_database(
        engine, pre_migration_backup=pre_migration_backup
    )
    configure_sqlite_wal(engine)
    if migration_result.get("from_version") != migration_result.get("to_version"):
        print(f"[Database] Schema migration complete: {migration_result}")
