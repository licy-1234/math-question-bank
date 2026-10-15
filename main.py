import os
import atexit
import io
import sys
import uuid
import json
import copy
import hashlib
import time
import re
import signal
import datetime
import threading
import requests
from contextlib import asynccontextmanager
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import secrets
from typing import List, Optional
from PIL import Image
from fastapi import FastAPI, Depends, HTTPException, Query, UploadFile, File, Form, BackgroundTasks, Request, Response, Header
from sqlalchemy import bindparam, func, or_, text
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import and_
from dotenv import load_dotenv

from mathbank.database import (
    FIGURE_ALIGN_VALUES,
    FIGURE_SIZE_VALUES,
    Question,
    QuestionCurriculum,
    QuestionTag,
    QuestionRelation,
    QuestionSubKnowledge,
    QuestionFingerprint as StoredQuestionFingerprint,
    Paper,
    PaperQuestion,
    Topic,
    TopicGroup,
    TopicItem,
    TopicLink,
    TopicVersion,
    QuestionUsageStat,
    engine,
    get_db,
    init_db,
    normalize_figure_size,
    _utcnow_naive,
)
from mathbank.sub_knowledge import normalize_sub_knowledge
from mathbank.question_duplicates import (
    QuestionDuplicateInput,
    build_question_fingerprint,
)
from mathbank.import_review import (
    make_source_review_advisory, prepare_word_extraction_reviews, finalize_word_extraction_reviews,
)
from mathbank.question_duplicate_service import (
    batch_local_matches,
    exact_duplicate_ids,
    find_indexed_candidates,
    fingerprint_for_question,
    index_status as duplicate_index_status,
    rebuild_all_missing_fingerprints,
    select_answer_images,
    select_visible_question_images,
    tikz_signatures as build_tikz_signatures,
    upsert_question_fingerprint,
    visible_image_signatures as build_visible_image_signatures,
)
from mathbank.paper_helper import build_latex_document, build_answer_sheet_latex, compile_tex_to_pdf, create_tex_zip_package, create_full_bundle_zip_package, collect_referenced_images, build_restricted_tex_environment
from mathbank.word_export_helper import build_word_document, create_word_bundle_zip
from mathbank.runtime_components import (
    PANDOC_INSTALL_MANAGER,
    pandoc_status,
)
from mathbank.sync_helper import export_database_to_files
from mathbank.backup import acquire_runtime_lock, create_full_backup_if_due
from mathbank.health import readiness_report
from mathbank.task_manager import (
    TaskCancelled,
    TaskManager,
    TaskQueueFull,
)
from mathbank.docx_helper import extract_docx_markdown
from mathbank.content_locks import lock_visible_math, reconcile_visible_math
from mathbank.source_metadata import prepare_word_source_metadata, source_metadata_diagnostics, normalize_source_fillin
from mathbank.source_metadata_request import request_word_source_metadata
from mathbank.paper_parse import (
    PAPER_SPLIT_TIMEOUT_SECONDS, PAPER_SPLIT_CACHE_MAX_CHARACTERS,
    parse_paper_completion, finalize_source_answers, request_pdf_paper_completion,
)
from mathbank.math_markdown import normalize_question_math_markdown, normalize_table_math_wrappers
from mathbank.fraction_style import normalize_fraction_style
from mathbank.tex_helper import (
    MAX_TEX_BYTES,
    decode_and_prepare_tex,
    prepare_tex_source,
    tex_asset_basename,
    tex_asset_references_match,
)
from mathbank.markdown_helper import (
    MAX_MARKDOWN_BYTES,
    decode_and_prepare_markdown,
    prepare_markdown_source,
    markdown_image_references,
    map_markdown_question_images,
    prepare_markdown_question_for_storage,
)
from mathbank.latex_diagnostics import (
    build_local_latex_diagnostic,
    merge_ai_latex_diagnostic,
)
from mathbank.ai_json import parse_ai_json
from mathbank.ai_http import (
    post_chat_completion,
)
from mathbank.ai_providers import (
    MultimodalProviderConfig,
    apply_model_thinking_policy,
    apply_structured_output_policy,
    resolve_draw_provider,
    resolve_ocr_provider,
    resolve_text_provider,
)
from mathbank.tags import (
    chapter_prefixes,
    codes_matching_text,
    curriculum_index,
    dimension_values,
    load_curriculum_tree,
    load_tag_schema,
    normalize_codes,
    normalize_tag_codes,
    resolve_legacy_code,
    split_custom_tags,
)
from mathbank.curriculums import (
    build_default_metadata,
    canonical_category_labels,
    get_curriculum_preset,
    load_curriculum,
)
from mathbank.prompts import (
    COMMON_OCR_PROMPT,
    PDF_VISUAL_FIDELITY_RULE,
    ILLUSTRATION_BOX_PROMPT,
    SINGLE_OCR_STRUCTURED_INSTRUCTIONS,
    build_single_ocr_vision_prompt,
    build_ai_solve_prompts,
    build_classification_system_prompt,
    build_import_parse_system_prompt,
    build_latex_error_explanation_prompts,
    build_paper_selection_prompts,
    build_pdf_parse_system_prompt,
    build_tikz_correction_prompt,
    build_tikz_draw_prompt,
)
from mathbank.ocr_illustrations import (
    MAX_OCR_RESPONSE_CHARS,
    materialize_ocr_illustrations,
    parse_single_ocr_response,
)
from mathbank.ocr_figure_review import review_single_ocr_figures, review_tikz_previews
from mathbank.question_types import (
    normalize_section_order,
)
from mathbank.classify_rules import (
    classify_with_rules,
    estimate_difficulty_prior,
    suggest_chapter_candidates,
    suggest_method_tags,
)
from mathbank import tag_memory
import shutil
from mathbank.pdf_inspector_helper import (
    is_pdf_inspector_available,
    get_pdf_inspector_version,
    inspect_and_extract_pdf,
    merge_pdf_page_texts,
)
from mathbank.pdf_figures import (
    PDF_STRATEGIES,
    apply_pdf_layout_reviews,
    refresh_pdf_review_items,
    enrich_pdf_with_figures,
    isolate_shared_pdf_figures,
)
from mathbank.pdf_layout import inspect_pdf_page
from mathbank.document_requests import DOCUMENT_AI_TIMEOUT_SECONDS
from mathbank.pdf_vision_request import completion_content, request_pdf_vision, collected_usage
from mathbank.paths import (
    DATABASE_PATH,
    DATA_BACKUP_DIR,
    ENV_FILE,
    PROJECT_ROOT,
    STATIC_CSS_DIR,
    STATIC_DIR,
    STATIC_JS_DIR,
    SYSTEM_GENERATED_DIR,
    TEST_UPLOADS_DIR,
    UPLOADS_DIR,
)
from mathbank.asset_security import (
    AssetSecurityError,
    InvalidImageError,
    MAX_OCR_IMAGE_BYTES,
    MAX_PDF_BYTES,
    MAX_SINGLE_IMAGE_BYTES,
    UploadTooLargeError,
    harden_private_path,
    normalize_answer_tikz_assets,
    normalize_content_tikz_assets,
    normalize_optional_upload_asset_reference,
    normalize_raster_image,
    normalize_upload_asset_reference,
    normalize_upload_asset_references,
    read_stream_limited,
    resolve_upload_asset,
    write_private_text_atomic,
)
from mathbank.asset_lifecycle import (
    AssetLifecycleError,
    RetainedUploadStaticFiles,
    quarantine_asset,
    register_asset_store,
    serialize_asset_lifecycle,
)
from mathbank.paths import RETAINED_UPLOADS_DIR
from mathbank.question_assets import (
    embedded_question_assets,
    markdown_literal_ranges,
    rewrite_image_layout_paths,
    rewrite_question_asset_paths,
    rewrite_structured_asset_paths,
    structured_question_assets,
)

# Load environment variables
load_dotenv(ENV_FILE)
harden_private_path(ENV_FILE)

# The Windows launcher injects an unguessable UUID hex value and binds its
# health/shutdown checks to the exact child it created.  Reject arbitrary
# environment text because this value is also embedded into the local HTML.
_ENV_LAUNCH_ID = os.environ.get("MATHBANK_LAUNCH_ID", "").strip().lower()
SERVER_INSTANCE_ID = (
    _ENV_LAUNCH_ID
    if re.fullmatch(r"[0-9a-f]{32}", _ENV_LAUNCH_ID)
    else uuid.uuid4().hex
)
_SHUTDOWN_SCHEDULED = threading.Event()
_SHUTDOWN_SCHEDULE_LOCK = threading.Lock()

# Hold an OS-backed project lock before the first database access.  The restore
# CLI takes the same lock, so a manually started uvicorn process is protected
# even when no launcher PID file exists.  Unit tests use isolated databases and
# exercise the lock helper directly instead of holding the production lock.
IS_TESTING = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv)
_RUNTIME_LOCK = None if IS_TESTING else acquire_runtime_lock()
if _RUNTIME_LOCK is not None:
    atexit.register(_RUNTIME_LOCK.close)

# Initialize DB
init_db()


def schedule_database_export(
    background_tasks: BackgroundTasks, *, operation: str
) -> None:
    """Best-effort export scheduling after a database transaction commits."""

    def run_export_safely() -> None:
        try:
            export_database_to_files()
        except Exception as exc:
            print(
                f"[Database Export] Post-commit export failed for {operation} "
                f"(type={type(exc).__name__}); the next write/startup export can retry."
            )

    try:
        background_tasks.add_task(run_export_safely)
    except Exception as exc:
        print(
            f"[Database Export] Post-commit scheduling failed for {operation} "
            f"(type={type(exc).__name__}); the next write/startup export can retry."
        )


def schedule_question_fingerprint_retry(
    background_tasks: BackgroundTasks,
    *,
    question_id: int,
    operation: str,
) -> None:
    def retry_safely() -> None:
        from mathbank.database import SessionLocal

        retry_db = SessionLocal()
        try:
            question = retry_db.query(Question).filter(Question.id == question_id).first()
            if question is None:
                return
            fingerprint = fingerprint_for_question(
                question,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            upsert_question_fingerprint(retry_db, question, fingerprint)
            retry_db.commit()
        except Exception as exc:
            retry_db.rollback()
            print(
                f"[Duplicate Index] Post-commit retry failed for {operation} "
                f"(question_id={question_id}, type={type(exc).__name__}); "
                "startup backfill will retry."
            )
        finally:
            retry_db.close()

    try:
        background_tasks.add_task(retry_safely)
    except Exception as exc:
        print(
            f"[Duplicate Index] Retry scheduling failed for {operation} "
            f"(question_id={question_id}, type={type(exc).__name__}); "
            "startup backfill will retry."
        )


def print_startup_diagnostics():
    """打印不扫描 PATH、不阻塞就绪的基础启动诊断。"""
    is_venv = sys.prefix != sys.base_prefix
    env_type = f"虚拟环境 ({os.path.basename(sys.prefix)})" if is_venv else "全局/系统环境"
    pdf_insp_ok = is_pdf_inspector_available()

    print("=" * 64, flush=True)
    print("      本地数学题库教研系统 (MathBank) 启动自检与诊断面板", flush=True)
    print("=" * 64, flush=True)
    print(f"  • Python 运行环境   : {sys.version.split()[0]} [{env_type}]", flush=True)
    print(f"  • Python 可执行路径 : {sys.executable}", flush=True)
    if is_venv:
        print(f"  • 虚拟环境根目录   : {sys.prefix}", flush=True)
    print(f"  • PDF Inspector 引擎: {'🚀 已就绪 (原生矢量试卷毫秒级直提)' if pdf_insp_ok else '⚠️ 未安装 (已自动平滑降级至 VLM 多模态 OCR)'}", flush=True)
    print("  • 可选排版工具   : 服务就绪后后台检测", flush=True)
    print(f"  • SQLite 本地数据库 : {DATABASE_PATH}", flush=True)
    print(f"  • 项目静态与根路径 : {PROJECT_ROOT}", flush=True)
    print("=" * 64, flush=True)


print_startup_diagnostics()


def print_optional_tool_diagnostics():
    """服务就绪后再扫描可选工具，避免慢 PATH 阻断启动。"""

    latex_engine = shutil.which("xelatex") or shutil.which("pdflatex")
    pandoc_path = os.getenv("MATHBANK_PANDOC_PATH", "").strip() or shutil.which("pandoc")
    try:
        import pymupdf  # noqa: F401

        pymupdf_status = "ready"
    except ImportError:
        pymupdf_status = "missing"
    print(
        "[Optional Tools] "
        f"latex={latex_engine or 'missing'}, "
        f"pandoc={pandoc_path or 'missing'}, "
        f"pymupdf={pymupdf_status}",
        flush=True,
    )

def heal_database_curriculum_names():
    """Normalize proven book aliases without deleting stored knowledge labels.

    Historical imports may use chapter-level or custom labels, and a mirror can
    belong to another textbook version.  Absence from the current preset is not
    evidence that a stored label is invalid.  Startup must preserve those values;
    classification changes belong to an explicit user edit or reviewed migration.
    """
    from mathbank.database import SessionLocal
    db = SessionLocal()
    try:
        mappings = {
            "选择性必修一": "选修一",
            "选择性必修二": "选修二",
            "选择性必修三": "选修三",
            "必修第一册": "必修一",
            "必修第二册": "必修二",
            "必修第三册": "必修三",
            "必修第四册": "必修四",
        }
        updated_questions = 0
        for old, new in mappings.items():
            res = db.query(Question).filter(Question.category_compulsory == old).update(
                {Question.category_compulsory: new}, synchronize_session=False
            )
            updated_questions += res
            
        updated_mappings = 0
        for old, new in mappings.items():
            res = db.query(QuestionCurriculum).filter(QuestionCurriculum.compulsory == old).update(
                {QuestionCurriculum.compulsory: new}, synchronize_session=False
            )
            updated_mappings += res

        if updated_questions > 0 or updated_mappings > 0:
            db.commit()
            print(f"[Self-Healing DB] Migrated book aliases for {updated_questions} questions and {updated_mappings} mappings; preserved all knowledge labels.")
    except Exception as e:
        db.rollback()
        print(f"[Self-Healing DB Error] Failed to run database book names migration: {e}")
    finally:
        db.close()

UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"
UPLOAD_DIR = str(TEST_UPLOADS_DIR if IS_TESTING else UPLOADS_DIR)
register_asset_store(UPLOAD_DIR, RETAINED_UPLOADS_DIR / ("test" if IS_TESTING else "uploads"))

def load_or_create_local_token() -> str:
    token_dir = str(SYSTEM_GENERATED_DIR)
    os.makedirs(token_dir, exist_ok=True)
    harden_private_path(token_dir, directory=True)
    token_file = os.path.join(token_dir, "local_token")
    if os.path.exists(token_file):
        try:
            harden_private_path(token_file)
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token and len(token) >= 16:
                    return token
        except Exception as e:
            print(f"[Security] Failed to read persistent token: {e}")
            
    # Generate new token
    token = secrets.token_hex(16)
    try:
        write_private_text_atomic(token_file, token)
    except Exception as e:
        print(f"[Security] Failed to write persistent token: {e}")
    return token

LOCAL_TOKEN = load_or_create_local_token()


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    """在模块完整导入后再启动低优先级维护任务。"""

    if IS_TESTING:
        yield
        return

    # 历史标签记忆回填：只读题库 + 只写 data_backup/tag_memory.json，
    # 幂等且自带异常兜底，失败也不会拖住启动。
    _backfill_free_text_tag_memory()

    DOCUMENT_TASKS.start_maintenance(interval_seconds=60.0)
    threading.Thread(
        target=start_startup_cleanup,
        name="mathbank-post-startup-maintenance",
        daemon=True,
    ).start()
    try:
        yield
    finally:
        DOCUMENT_TASKS.shutdown(wait=False)


app = FastAPI(title="本地化数学题库管理系统 API", lifespan=app_lifespan)

# Enable CORS for local development (restrict allowed origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Heartbeat & Security Middleware -----------------
LAST_ACTIVE_TIME = time.time()

@app.middleware("http")
async def security_and_heartbeat_middleware(request: Request, call_next):
    global LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    
    # Verify local security token for modifying operations
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.url.path != "/api/heartbeat":
            token = request.headers.get("X-Local-Token")
            if not token or not secrets.compare_digest(token, LOCAL_TOKEN):
                print(f"[Security Alert] Blocked {request.method} {request.url.path} - invalid local token")
                return JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "Forbidden: Invalid or missing local token."}
                )
                
    response = await call_next(request)
    return response

@app.post("/api/heartbeat")
def api_heartbeat():
    global LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    return {"status": "success", "timestamp": LAST_ACTIVE_TIME}

def watchdog_loop():
    global LAST_ACTIVE_TIME
    # 1小时闲置超时 (3600秒)
    TIMEOUT_LIMIT = 3600
    while True:
        time.sleep(15) # 每 15 秒轻量巡检一次
        elapsed = time.time() - LAST_ACTIVE_TIME
        if elapsed > TIMEOUT_LIMIT:
            print(f"[Watchdog] 检测到网页已关闭且超过 1 小时无任何动作 (已静默 {int(elapsed)} 秒)，正在自动安全关闭题库程序...")
            # 进程内触发 SIGINT，让 uvicorn 执行正常 lifespan 关闭。
            signal.raise_signal(signal.SIGINT)
            break

# 启动看门狗后台守护线程 (daemon=True 确保主线程消亡时其也随之释放)
# threading.Thread(target=watchdog_loop, daemon=True).start()

# ----------------- 启动自愈：后台静默清理孤儿临时图片 -----------------
@serialize_asset_lifecycle
def clean_orphaned_images():
    """Retain unregistered images recoverably; old browser drafts remain usable."""
    try:
        from mathbank.database import SessionLocal, Question
        db = SessionLocal()
        try:
            referenced_images = _referenced_question_assets(db)
                        
            # 2. 遍历本地图片目录及 tmp 子目录
            upload_dir = UPLOAD_DIR
            if not os.path.exists(upload_dir):
                return
                
            cleaned_count = 0
            now = time.time()
            one_hour_seconds = 3600
            
            # 清理 static/uploads/ 根目录下未引用的孤儿图片
            for filename in os.listdir(upload_dir):
                full_path = os.path.join(upload_dir, filename)
                if os.path.isfile(full_path) and not filename.startswith("."):
                    if Path(full_path).resolve() not in referenced_images:
                        try:
                            mtime = os.path.getmtime(full_path)
                            if now - mtime > one_hour_seconds:
                                cleaned_count += int(quarantine_asset(
                                    f"/{UPLOAD_DIR_REL}/{filename}", uploads_dir=UPLOAD_DIR,
                                    url_prefix=UPLOAD_DIR_REL, reason="startup-unregistered",
                                ))
                        except Exception:
                            pass

            # 清理 static/uploads/tmp/ 子目录下残留的所有旧拆卷/OCR临时切片图
            tmp_dir = os.path.join(upload_dir, "tmp")
            if os.path.exists(tmp_dir):
                for filename in os.listdir(tmp_dir):
                    full_path = os.path.join(tmp_dir, filename)
                    if os.path.isfile(full_path) and not filename.startswith("."):
                        if Path(full_path).resolve() not in referenced_images:
                            try:
                                mtime = os.path.getmtime(full_path)
                                if now - mtime > 600:  # 超过 10 分钟未被使用的 tmp 切片立即清理
                                    cleaned_count += int(quarantine_asset(
                                        f"/{UPLOAD_DIR_REL}/tmp/{filename}", uploads_dir=UPLOAD_DIR,
                                        url_prefix=UPLOAD_DIR_REL, reason="startup-temporary",
                                    ))
                            except Exception:
                                pass
                        
            if cleaned_count > 0:
                print(f"[Storage Retention] 已将 {cleaned_count} 个暂未登记图片移入可恢复保留区；原地址访问时自动恢复，未永久删除。")
        finally:
            db.close()
    except Exception as e:
        print(f"[Storage Cleanup Error] 执行静默图片净化时发生异常: {str(e)}")

def recalibrate_usage_counts():
    """自动校准全库题目的引用频次 usage_count，修正由于历史删除试卷遗留的计数差异"""
    try:
        from mathbank.database import SessionLocal, Question, PaperQuestion
        from sqlalchemy import func
        db = SessionLocal()
        try:
            counts = db.query(PaperQuestion.question_id, func.count(PaperQuestion.id)).group_by(PaperQuestion.question_id).all()
            ref_map = dict(counts)
            questions = db.query(Question).all()
            changed = False
            for q in questions:
                actual_ref = ref_map.get(q.id, 0)
                if (q.usage_count or 0) != actual_ref:
                    q.usage_count = actual_ref
                    changed = True
            if changed:
                db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[Usage Calibration Error] {e}")

def start_startup_cleanup():
    # Lifespan 已确保模块完整导入；再让出短暂时间给首屏请求。
    time.sleep(2.5)
    try:
        backup_path = create_full_backup_if_due()
        if backup_path:
            print(f"[Backup] 已创建并验证每日完整备份: {backup_path.name}")
    except Exception as exc:
        print(f"[Backup Error] 每日完整备份失败: {type(exc).__name__}: {exc}")
        print_optional_tool_diagnostics()
        return
    heal_database_curriculum_names()
    clean_orphaned_images()
    recalibrate_usage_counts()
    try:
        from mathbank.database import SessionLocal

        fingerprint_result = rebuild_all_missing_fingerprints(
            SessionLocal,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
            batch_size=250,
        )
        if fingerprint_result.get("backfilled"):
            print(
                "[Duplicate Index] Backfill complete: "
                f"{fingerprint_result['indexed']}/{fingerprint_result['total']}"
            )
    except Exception as exc:
        print(
            "[Duplicate Index] Background backfill failed "
            f"(type={type(exc).__name__}); duplicate checks will report partial coverage."
        )
    print_optional_tool_diagnostics()


# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
TMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "tmp")
os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)

# Bounded document task manager shared by PDF and Word imports.
DOCUMENT_TASKS = TaskManager(
    max_workers=2,
    max_queue=4,
    terminal_ttl_seconds=3600,
    temp_asset_cleanup=lambda paths: _delete_task_temp_assets(paths),
)
PDF_OCR_SEMAPHORE = threading.BoundedSemaphore(4)
MAX_PDF_TASK_PAGES = 80

# Typed question-to-question relations (schema v12).  The contract is frozen:
# the frontend consumes these codes/labels verbatim, so do not rename them.
BUILTIN_RELATION_TYPES = (
    ("multi_q_unify", "多题归一"),
    ("one_q_variants", "一题多变"),
)
# 旧版五类（v12 早期口径）。当前库中已无此类关系行，但保留校验兼容与展示
# 映射：万一历史数据带出旧码，详情/列表仍能译出可读 label 而不是裸代码。
LEGACY_RELATION_TYPES = (
    ("same_topic", "同知识点掌握"),
    ("multi_q_one_solution", "多题一解"),
    ("one_method_multi_use", "一法多用"),
    ("multi_method_one", "多法归一"),
)
LEGACY_RELATION_LABELS = dict(LEGACY_RELATION_TYPES)
RELATION_CUSTOM_PREFIX = "custom:"
BUILTIN_RELATION_CODES = frozenset(code for code, _ in BUILTIN_RELATION_TYPES)
#: Slug charset the frontend mints for ``custom:<slug>`` codes.  Kept permissive
#: (any non-empty slug) so a custom topic typed today still validates tomorrow.
RELATION_CODE_MAX_LENGTH = 60


def validate_relation_code(code: str) -> str:
    """Whitelist a submitted ``relation_code``.

    Only the current built-in codes, a ``custom:`` prefixed code, or a legacy
    built-in code (kept readable for any pre-migration history) are accepted.
    The custom escape hatch is deliberate: users invent their own comparison
    topics ("一题多解对比"), and the frontend always mints those as
    ``custom:<slug>``, so they must keep flowing through.
    """

    code = str(code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="关联类型不能为空。")
    if len(code) > RELATION_CODE_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"关联类型过长（最多 {RELATION_CODE_MAX_LENGTH} 个字符）。",
        )
    if code in BUILTIN_RELATION_CODES or code in LEGACY_RELATION_LABELS:
        return code
    if code.startswith(RELATION_CUSTOM_PREFIX):
        slug = code[len(RELATION_CUSTOM_PREFIX):].strip()
        if slug:
            return code
        raise HTTPException(
            status_code=400,
            detail="自定义关联类型的名称不能为空。",
        )
    raise HTTPException(
        status_code=400,
        detail="无效的关联类型，请从列表中选择或使用自定义类型。",
    )


def get_seq_mapping(db: Session, question_ids=None):
    """Map physical ID order to the user-facing contiguous sequence number."""

    if question_ids is None:
        all_q = db.query(Question.id).order_by(Question.id.asc()).all()
        return {q_id: idx + 1 for idx, (q_id,) in enumerate(all_q)}

    normalized_ids = {int(question_id) for question_id in question_ids}
    if not normalized_ids:
        return {}
    from sqlalchemy import func

    ranked = db.query(
        Question.id.label("question_id"),
        func.row_number().over(order_by=Question.id.asc()).label("seq_num"),
    ).subquery()
    rows = db.query(ranked.c.question_id, ranked.c.seq_num).filter(
        ranked.c.question_id.in_(normalized_ids)
    ).all()
    return {question_id: int(seq_num) for question_id, seq_num in rows}


def _relation_counts_for_questions(
    db: Session, question_ids: list[int]
) -> dict[int, int]:
    """Count each question's relation rows with one aggregate query (no N+1).

    Relations are undirected: a question can appear on either the question_id or
    the related_id side, so both sides are grouped and merged.
    """

    from sqlalchemy import func

    ids = {int(qid) for qid in question_ids if qid is not None}
    if not ids:
        return {}
    id_list = list(ids)
    left_side = db.query(
        QuestionRelation.question_id.label("question_id"),
        func.count().label("relation_count"),
    ).filter(QuestionRelation.question_id.in_(id_list)).group_by(
        QuestionRelation.question_id
    )
    right_side = db.query(
        QuestionRelation.related_id.label("question_id"),
        func.count().label("relation_count"),
    ).filter(QuestionRelation.related_id.in_(id_list)).group_by(
        QuestionRelation.related_id
    )
    combined = left_side.union_all(right_side).subquery()
    rows = (
        db.query(combined.c.question_id, func.sum(combined.c.relation_count))
        .group_by(combined.c.question_id)
        .all()
    )
    return {int(question_id): int(total or 0) for question_id, total in rows}

# ----------------- Static Files & Index -----------------


@app.get("/healthz", include_in_schema=False)
def healthz():
    report = readiness_report(engine)
    report["server_instance_id"] = SERVER_INSTANCE_ID
    report["pdf_inspector_version"] = get_pdf_inspector_version()
    return JSONResponse(report, status_code=200 if report["ready"] else 503)

@app.get("/")
def read_index():
    from mathbank.ui_settings import community_qa_enabled, render_qa_visibility

    index_path = str(STATIC_DIR / "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        qa_enabled = community_qa_enabled()
        html_content = render_qa_visibility(html_content, enabled=qa_enabled)
        
        # Inject dynamic cache-busting version parameter based on file mtime
        js_files = ["api.js", "editor.js", "ocr.js", "import.js", "paper.js", "dashboard.js", "qa-data.js", "qa.js", "topic.js"]
        for js in js_files:
            js_path = str(STATIC_JS_DIR / js)
            mtime = int(os.path.getmtime(js_path)) if os.path.exists(js_path) else 0
            # Replace template version parameter
            html_content = html_content.replace(f"/static/js/{js}?v=1.0.1", f"/static/js/{js}?v={mtime}")
            # Also handle plain scripts references if they exist
            html_content = html_content.replace(f'src="/static/js/{js}"', f'src="/static/js/{js}?v={mtime}"')
            
        # Inject dynamic cache-busting version parameter for app.css and favicon assets
        css_path = str(STATIC_CSS_DIR / "app.css")
        css_mtime = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 0
        html_content = html_content.replace('/static/css/app.css', f'/static/css/app.css?v={css_mtime}')

        fav_path = str(STATIC_DIR / "favicon.png")
        fav_mtime = int(os.path.getmtime(fav_path)) if os.path.exists(fav_path) else 0
        html_content = html_content.replace('/static/favicon.png', f'/static/favicon.png?v={fav_mtime}')
            
        # Inject the token and server_instance_id directly into index.html to bypass any cookie blocking policies
        token_script = f'<script>window.__localToken = "{LOCAL_TOKEN}"; window.__serverInstanceId = "{SERVER_INSTANCE_ID}"; window.__qaEnabled = {json.dumps(qa_enabled)};</script>'
        html_content = html_content.replace('<head>', f'<head>\n    {token_script}')
            
        res = HTMLResponse(content=html_content)
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        
        res.set_cookie(
            key="local_token",
            value=LOCAL_TOKEN,
            httponly=False,  # JavaScript must be able to read this cookie to send it back via headers
            samesite="lax",
            secure=False
        )
        return res
    return JSONResponse(
        content={"status": "error", "message": "static/index.html not found. Please create it."},
        status_code=404
    )

@app.get("/favicon.ico", include_in_schema=False)
def read_favicon():
    favicon_path = str(STATIC_DIR / "favicon.ico")
    if os.path.exists(favicon_path):
        res = FileResponse(favicon_path, media_type="image/x-icon")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    favicon_png_path = str(STATIC_DIR / "favicon.png")
    if os.path.exists(favicon_png_path):
        res = FileResponse(favicon_png_path, media_type="image/png")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon not found."},
        status_code=404
    )

@app.get("/favicon.svg", include_in_schema=False)
def read_favicon_svg():
    svg_path = str(STATIC_DIR / "favicon.svg")
    if os.path.exists(svg_path):
        res = FileResponse(svg_path, media_type="image/svg+xml")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon.svg not found."},
        status_code=404
    )

@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def read_apple_touch_icon():
    for name in ["apple-touch-icon.png", "favicon.png"]:
        icon_path = str(STATIC_DIR / name)
        if os.path.exists(icon_path):
            res = FileResponse(icon_path, media_type="image/png")
            res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            res.headers["Pragma"] = "no-cache"
            res.headers["Expires"] = "0"
            return res
    return JSONResponse(
        content={"status": "error", "message": "apple touch icon not found."},
        status_code=404
    )

@app.post("/api/format/fractions")
def format_fraction_style(text: str = Form("", max_length=200000)):
    """Format an editor snapshot without reading or writing stored questions."""
    normalized = normalize_fraction_style(text)
    return {"text": normalized, "changed": normalized != text}


# ----------------- Upload API -----------------

@app.post("/api/upload")
def upload_image(file: UploadFile = File(...)):
    try:
        raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
        normalized = normalize_raster_image(raw)

        # Never trust the client suffix.  The server-generated extension and
        # re-encoded bytes prevent HTML/SVG/polyglot files being served same-origin.
        filename = f"{uuid.uuid4().hex}{normalized.extension}"
        filepath = os.path.join(UPLOAD_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(normalized.data)

        relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
        return {
            "status": "success",
            "file_path": relative_path,
            "filename": file.filename
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "图片过大，请上传 10MB 以内的文件。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"图片上传失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        print(f"[Upload Error] {type(e).__name__}: {e}")
        return JSONResponse(
            content={"status": "error", "message": "文件上传失败，请检查文件后重试。"},
            status_code=500
        )

# ----------------- OCR API -----------------

def auto_crop_image(image):
    try:
        from PIL import ImageOps, ImageStat
        # 估算灰度均值，判断主色调（暗色背景还是亮色背景）
        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        mean_val = stat.mean[0]
        
        if mean_val < 100:  # 偏暗，可能含有大面积黑边背景
            bbox = image.getbbox()
            if bbox:
                # 留出 8 像素的边距以防文字贴边影响识别
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
        elif mean_val > 220:  # 偏亮，可能含有大面积白边背景
            inverted = ImageOps.invert(image.convert("RGB"))
            bbox = inverted.getbbox()
            if bbox:
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
    except Exception as e:
        print(f"[Auto Crop] 裁剪失败，返回原图. Error: {str(e)}")
    return image


def ocr_via_provider(
    image_path: str,
    provider: MultimodalProviderConfig,
    include_illustration_box: bool = False,
    *, pdf_page: bool = False, single_question_layout: bool = False, check_cancelled=lambda: None,
    report_attempt=lambda _event: None, page_index: int = None,
) -> str:
    """Use one resolved multimodal provider for formula and text OCR."""
    import base64

    if not provider.supports_image_input:
        raise ValueError(
            f"{provider.provider_label} 模型 {provider.model_name} 不支持图像输入，"
            "请在默认公式识图模型中选择支持图片的模型。"
        )

    print(
        f"[OCR Flow] 正在向 {provider.provider_label} 提交多模态识别任务: "
        f"{image_path} (模型: {provider.model_name})..."
    )
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对图片进行 Base64 编码失败: {str(e)}")

    if pdf_page and single_question_layout:
        raise ValueError("PDF 页面识别不能使用单题配图协议。")
    prompt = build_single_ocr_vision_prompt() if single_question_layout else COMMON_OCR_PROMPT
    if pdf_page:
        prompt += PDF_VISUAL_FIDELITY_RULE
    if include_illustration_box and not single_question_layout:
        prompt += ILLUSTRATION_BOX_PROMPT

    payload = {
        "model": provider.model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded_string}"
                        }
                    }
                ]
            }
        ],
        "stream": False
    }

    if single_question_layout:
        payload["max_tokens"] = 16384
    payload = apply_model_thinking_policy(
        payload,
        provider=provider,
        task="ocr",
    )
    if single_question_layout:
        payload = apply_structured_output_policy(
            payload, provider=provider,
            system_instruction=SINGLE_OCR_STRUCTURED_INSTRUCTIONS,
        )

    if pdf_page:
        def validate(body):
            return {"markdown": completion_content(body, "PDF 页面识别", max_chars=200_000,
                                                   allow_missing_finish=True).strip()}
        result = request_pdf_vision(
            provider, payload, post=post_chat_completion, validate=validate,
            label="PDF 页面识别", stage="page_ocr", page_index=page_index,
            check_cancelled=check_cancelled, report_attempt=report_attempt,
        )
        return result["markdown"]

    timeout = 240
    # Chat-completion POSTs are not idempotent: a read timeout can happen after
    # the provider has accepted (and billed) the request.  Do not automatically
    # send the same image two or three times.  The shared transport still
    # retries a connection-establishment failure where no response was read.
    response = post_chat_completion(
        provider,
        payload,
        timeout=timeout,
        check_status=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} API 识别失败: HTTP {response.status_code}"
        )

    res_json = response.json()
    if single_question_layout:
        return completion_content(res_json, "单题图文识别", max_chars=MAX_OCR_RESPONSE_CHARS,
                                  allow_missing_finish=True).strip()
    try:
        choices = res_json.get("choices", [])
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()
        else:
            raise RuntimeError(
                f"{provider.provider_label} 返回的数据中未包含 Choices 结果。"
            )
    except Exception as e:
        raise RuntimeError(
            f"解析 {provider.provider_label} 响应数据失败: {str(e)}"
        )


def extract_tikz_source(ai_message: str) -> str:
    """Extract one complete TikZ environment from a model response."""

    message = str(ai_message or "").strip()
    match = re.search(
        r"\\begin\s*\{\s*tikzpicture\s*\}.*?\\end\s*\{\s*tikzpicture\s*\}",
        message,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(0).strip()

    match_block = re.search(
        r"```(?:latex|tex)?\s*(.*?)```",
        message,
        re.DOTALL | re.IGNORECASE,
    )
    if match_block:
        code = match_block.group(1).strip()
        if code and "tikzpicture" not in code.lower():
            return f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"

    raise RuntimeError("绘图模型未返回完整的 tikzpicture 源码。")


def request_tikz_completion(provider, content_payload, *, timeout: int = 120) -> str:
    """Send one TikZ model request with the shared reasoning and parser policy."""

    payload = {
        "model": provider.model_name,
        "messages": [{"role": "user", "content": content_payload}],
        "stream": False,
    }
    payload = apply_model_thinking_policy(
        payload,
        provider=provider,
        task="draw",
    )
    response = post_chat_completion(
        provider,
        payload,
        timeout=timeout,
        check_status=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} 绘图接口返回 HTTP {response.status_code}"
        )
    choices = response.json().get("choices", [])
    if not choices:
        raise RuntimeError(f"{provider.provider_label} 绘图接口未返回 choices。")
    ai_message = choices[0].get("message", {}).get("content", "")
    return extract_tikz_source(ai_message)


def draw_tikz_via_high_model(
    image_path: Optional[str],
    prefer_draw: str,
    latex_content: Optional[str] = None,
    *,
    instruction: str = "",
    existing_tikz: str = "",
    require_image_support: bool = False,
) -> Optional[str]:
    """使用指定的高级绘图模型（多模态或纯文本自适应）生成 TikZ 代码。"""
    import base64
    provider = resolve_draw_provider(prefer_draw)
    if not provider.api_key:
        print(
            f"[High Model Draw] 未配置 {provider.credential_label}，降级跳过。"
        )
        return None

    has_reference_image = bool(image_path)
    if has_reference_image and require_image_support and not provider.supports_image_input:
        raise RuntimeError(
            f"当前绘图模型 {provider.model_name} 不支持参考图输入，"
            "请在 API 设置中选择支持图像的 TikZ 绘图模型。"
        )
    use_image_input = has_reference_image and provider.supports_image_input

    if use_image_input:
        # 多模态图文输入模式
        try:
            with open(image_path, "rb") as f:
                encoded_image = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            print(f"[High Model Draw] 读取裁剪小图 Base64 失败: {str(e)}")
            return None
            
        suffix = Path(image_path).suffix.lower()
        image_media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        prompt = build_tikz_draw_prompt(
            latex_content,
            multimodal=True,
            instruction=instruction,
            existing_tikz=existing_tikz,
        )
        
        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_media_type};base64,{encoded_image}"
                }
            }
        ]
    else:
        # 纯文本推理模式。带有视觉能力的模型也可以在没有参考图时走此分支。
        if not any((latex_content, instruction, existing_tikz)):
            print("[High Model Draw] 绘图模型未获得任何可用输入，跳过。")
            return None
            
        prompt = build_tikz_draw_prompt(
            latex_content,
            multimodal=False,
            instruction=instruction,
            existing_tikz=existing_tikz,
        )
        content_payload = prompt

    try:
        return request_tikz_completion(
            provider,
            content_payload,
            timeout=120,
        )
    except Exception as e:
        print(f"[High Model Draw Error] 大模型请求发生异常: {str(e)}")
    return None


@app.post("/api/ocr")
def ocr_formula(
    file: UploadFile = File(...),
    engine: str = Form(None),
    skip_tikz: bool = Form(False)
):
    import re
    temp_filepath = None
    try:
        # OCR route is synchronous and runs in FastAPI's worker pool.  Stream
        # only up to the endpoint cap, then fully decode/re-encode the image.
        file_bytes = read_stream_limited(file.file, MAX_OCR_IMAGE_BYTES)
        normalized = normalize_raster_image(file_bytes)
        image = Image.open(io.BytesIO(normalized.data)).convert("RGB")
        
        # 1. 运行自适应图像去噪/自动切边预处理
        image = auto_crop_image(image)
        
        # 将裁剪后的图片保存为持久化 OCR 文件，未来可作为题目配图
        filename = f"ocr_original_{uuid.uuid4().hex[:12]}.png"
        temp_filepath = os.path.join(UPLOAD_DIR, filename)
        image.save(temp_filepath, format="PNG")
        
        # 确定调用的具体引擎。
        # 临时传参 engine 取值: default, siliconflow, simpletex, ali_bailian
        if not engine or engine == "default":
            engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
            
        print(f"[OCR Flow] 当前决策分配识图引擎: {engine}")
        
        latex_content = None
        confidence = 0.95
        provider = ""

        known_ocr_engines = {
            "deepseek",
            "siliconflow",
            "ali_bailian",
            "bailian",
            "zhongzhan",
            "zhongzhan_gpt",
            "zhongzhan_claude",
        }
        if engine in known_ocr_engines:
            ocr_provider = resolve_ocr_provider(engine)
            if ocr_provider.api_key and ocr_provider.api_key.strip():
                try:
                    with PDF_OCR_SEMAPHORE:
                        latex_content = ocr_via_provider(
                            temp_filepath,
                            ocr_provider,
                            single_question_layout=True,
                        )
                    confidence = 0.99
                    provider = (
                        f"{ocr_provider.provider_label} "
                        f"({ocr_provider.model_name})"
                    )
                except Exception as e:
                    print(
                        f"[{ocr_provider.provider_label} 识别失败] "
                        f"发生异常: {str(e)}"
                    )
            else:
                print(
                    f"[OCR Flow Warning] 未配置 {ocr_provider.credential_label}，"
                    "当前识图引擎无法启动！"
                )

        if not latex_content:
            raise RuntimeError("当前分配的识图引擎无法启动或识别失败。请检查系统设置中所选识图平台的 API Key、模型名称和接口地址。")

        recognized = parse_single_ocr_response(latex_content)
        recognized = review_single_ocr_figures(recognized, temp_filepath, ocr_provider,
                                               post=post_chat_completion, semaphore=PDF_OCR_SEMAPHORE)
        prefer_draw = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")

        def draw_one(crop_path, text_context, instruction):
            return draw_tikz_via_high_model(
                crop_path, prefer_draw, latex_content=text_context,
                instruction=instruction, require_image_support=True,
            )

        figures = materialize_ocr_illustrations(
            recognized, image, skip_tikz=skip_tikz, upload_root=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL, draw=draw_one, compile_tikz=compile_tikz_to_png,
            require_review=True, verified_crops=recognized["_verified_crops"],
        )
        figures = review_tikz_previews(figures, ocr_provider, upload_root=UPLOAD_DIR,
                                       url_prefix=UPLOAD_DIR_REL, summary=recognized["illustration_review"],
                                       post=post_chat_completion, semaphore=PDF_OCR_SEMAPHORE)
        # Source slots are filled before math repair; neither protocol IDs nor
        # JSON syntax can be reinterpreted as naked mathematical expressions.
        latex_content = figures["markdown"].replace("\\,", "").replace("\\!", "")
        latex_content = normalize_fillin_macro(latex_content)
        latex_content = normalize_table_math_wrappers(latex_content)
        latex_content = normalize_question_math_markdown(latex_content)
        first_asset = figures["tikz_assets"][0] if figures["tikz_assets"] else {}

        # 将 temp_filepath 置为 None，避免在 finally 块中被删除
        saved_filepath = temp_filepath
        temp_filepath = None

        return {
            "status": "success",
            "latex": latex_content,
            "confidence": confidence,
            "provider": provider,
            "image_path": f"/{UPLOAD_DIR_REL}/{os.path.basename(saved_filepath)}",
            "tikz_code": first_asset.get("tikz_code"),
            "tikz_image_path": first_asset.get("image_path"),
            "illustrations": figures["illustrations"],
            "tikz_assets": figures["tikz_assets"],
            "image_layouts": figures["image_layouts"],
            "warnings": figures["warnings"],
            "illustration_review": figures["illustration_review"],
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "公式识图失败: 图片不能超过 10MB。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=500
        )
    finally:
        # 确保清理临时文件
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception as e_cleanup:
                print(f"[OCR Flow Cleanup Error] 无法删除临时文件 {temp_filepath}: {str(e_cleanup)}")

# ----------------- DeepSeek AI Solve API -----------------

@app.post("/api/ai/solve")
def ai_solve(
    content: str = Form(...),
    question_type: str = Form("detailed_answer"),
    ocr_result: str = Form(""),
    custom_prompt: str = Form(""),
    thinking: str = Form("enabled"),
    model: str = Form(""),
    stream: str = Form("false")
):
    provider = resolve_text_provider(model or os.getenv("PREFER_SOLVE_MODEL") or "deepseek-v4-pro")
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法智能解答！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        system_instructions, user_prompt = build_ai_solve_prompts(
            question_type=question_type,
            content=content,
            ocr_result=ocr_result,
            custom_prompt=custom_prompt,
        )

        # Keep the legacy fallback cap for older Bailian models. Current
        # Qwen3.7/3.8 requests are converted below to max_completion_tokens.
        max_output_tokens = 8192 if provider.provider_code == "bailian" else 16384

        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_output_tokens
        }
        
        data["temperature"] = 0.2
        data = apply_model_thinking_policy(
            data,
            provider=provider,
            task="solve",
            thinking_enabled=thinking == "enabled",
        )

        if stream == "true":
            def event_generator():
                data["stream"] = True
                try:
                    response = post_chat_completion(
                        provider,
                        data,
                        timeout=300,
                        stream=True,
                        check_status=False,
                    )
                    if response.status_code != 200:
                        error_msg = f"{provider_name} 接口错误: HTTP {response.status_code}"
                        yield f"data: {json.dumps({'status': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
                        return
                    
                    reasoning_count = 0
                    content_count = 0
                    
                    for line in response.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8").strip()
                        if line_str.startswith("data:"):
                            data_content = line_str[5:].strip()
                            if data_content == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_content)
                                
                                # 优先读取接口可能返回的官方 usage 统计
                                usage = chunk_json.get("usage")
                                if usage and isinstance(usage, dict):
                                    c_tok = usage.get("completion_tokens")
                                    r_tok = usage.get("completion_tokens_details", {}).get("reasoning_tokens") if isinstance(usage.get("completion_tokens_details"), dict) else None
                                    if c_tok is not None:
                                        content_count = max(content_count, c_tok)
                                    if r_tok is not None:
                                        reasoning_count = max(reasoning_count, r_tok)

                                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                content_piece = delta.get("content") or ""
                                
                                # 针对不同模型的流式数据块进行动态 Token 数量估算（兼容大 Chunk 输出模型如 Gemini Flash）
                                if reasoning:
                                    cjk_c = sum(1 for c in reasoning if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(reasoning) - cjk_c
                                    reasoning_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                if content_piece:
                                    cjk_c = sum(1 for c in content_piece if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(content_piece) - cjk_c
                                    content_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                    
                                if reasoning or content_piece:
                                    yield f"data: {json.dumps({'status': 'processing', 'reasoning': reasoning, 'content': content_piece, 'reasoning_count': reasoning_count, 'content_count': content_count}, ensure_ascii=False)}\n\n"
                            except Exception:
                                continue
                    yield f"data: {json.dumps({'status': 'done'}, ensure_ascii=False)}\n\n"
                except requests.exceptions.Timeout:
                    friendly_msg = (
                        f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
                        f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
                    )
                    yield f"data: {json.dumps({'status': 'error', 'message': friendly_msg}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'AI 解析生成出错: {str(e)}'}, ensure_ascii=False)}\n\n"
            
            return StreamingResponse(event_generator(), media_type="text/event-stream")

        # Generous 300 seconds timeout (5 minutes) for high-school math reasoning and network proxies
        response = post_chat_completion(
            provider,
            data,
            timeout=300,
            provider_name=provider_name,
        )
            
        res_json = response.json()
        
        msg_obj = res_json.get("choices", [{}])[0].get("message", {})
        ai_message = msg_obj.get("content") or ""
        reasoning_content = msg_obj.get("reasoning_content") or ""
        
        # Robust fallback: if content is empty but reasoning is present, use reasoning as explanation
        if not ai_message and reasoning_content:
            ai_message = f"【深度思考推理过程】\n{reasoning_content}\n\n【参考解析】已成功生成推理步骤。如果需要标准的三板块排版，请尝试在控制面板中关闭「AI 深度思考推理」再次生成。"
            
        if not ai_message:
            print(
                f"[Solve API] Provider returned an empty message "
                f"(provider={provider.provider_code}, status={response.status_code})."
            )
            raise Exception(f"{provider_name} 返回了空消息，请检查 API 或账户余额。")
            
        return {
            "status": "success",
            "solution": ai_message
        }
    except requests.exceptions.Timeout:
        friendly_msg = (
            f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
            f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
        )
        return JSONResponse(
            content={"status": "error", "message": friendly_msg},
            status_code=500
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 解析生成出错: {str(e)}"},
            status_code=500
        )

# ----------------- Save ENV Settings from UI -----------------

@app.get("/api/settings")
def get_settings():
    ds_key = os.getenv("DEEPSEEK_API_KEY", "")
    sf_key = os.getenv("SILICONFLOW_API_KEY", "")
    ali_key = os.getenv("ALI_BAILIAN_API_KEY", "")
    
    # 兼容老版 ZHONGZHAN 环境变量
    zz_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
    zz_gpt_base = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "")
    zz_gpt_ocr_model = os.getenv("ZHONGZHAN_GPT_OCR_MODEL") or os.getenv("ZHONGZHAN_OCR_MODEL", "gpt-4o")
    
    zz_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")
    zz_claude_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "")
    zz_claude_ocr_model = os.getenv("ZHONGZHAN_CLAUDE_OCR_MODEL", "claude-3-5-sonnet")
    
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    ds_model = os.getenv("DEEPSEEK_OCR_MODEL") or "deepseek-flash"
    sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
    ali_model = os.getenv("ALI_BAILIAN_OCR_MODEL", "qwen3.7-flash")
    prefer_solve_model = os.getenv("PREFER_SOLVE_MODEL", "deepseek-v4-pro")
    prefer_parse_model = os.getenv("PREFER_PARSE_MODEL", "deepseek-flash")
    prefer_classify_model = os.getenv("PREFER_CLASSIFY_MODEL") or os.getenv("DEEPSEEK_CLASSIFY_MODEL", "deepseek-flash")
    prefer_draw_model = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    
    masked_ds = ""
    if ds_key:
        masked_ds = ds_key[:4] + "••••" + ds_key[-4:] if len(ds_key) > 8 else "••••••••"
        
    masked_sf = ""
    if sf_key:
        masked_sf = sf_key[:4] + "••••" + sf_key[-4:] if len(sf_key) > 8 else "••••••••"
        
    masked_ali = ""
    if ali_key:
        masked_ali = ali_key[:4] + "••••" + ali_key[-4:] if len(ali_key) > 8 else "••••••••"

    masked_zz_gpt = ""
    if zz_gpt_key:
        masked_zz_gpt = zz_gpt_key[:4] + "••••" + zz_gpt_key[-4:] if len(zz_gpt_key) > 8 else "••••••••"
        
    masked_zz_claude = ""
    if zz_claude_key:
        masked_zz_claude = zz_claude_key[:4] + "••••" + zz_claude_key[-4:] if len(zz_claude_key) > 8 else "••••••••"
        
    return {
        "deepseek_key": masked_ds,
        "siliconflow_key": masked_sf,
        "ali_bailian_key": masked_ali,
        "zhongzhan_gpt_key": masked_zz_gpt,
        "zhongzhan_gpt_base_url": zz_gpt_base,
        "zhongzhan_gpt_ocr_model": zz_gpt_ocr_model,
        "zhongzhan_claude_key": masked_zz_claude,
        "zhongzhan_claude_base_url": zz_claude_base,
        "zhongzhan_claude_ocr_model": zz_claude_ocr_model,
        "prefer_engine": prefer_engine,
        "deepseek_model": ds_model,
        "siliconflow_model": sf_model,
        "ali_bailian_model": ali_model,
        "prefer_solve_model": prefer_solve_model,
        "prefer_parse_model": prefer_parse_model,
        "prefer_classify_model": prefer_classify_model,
        "prefer_draw_model": prefer_draw_model
    }

@app.post("/api/settings/save")
def save_settings(
    deepseek_key: str = Form(""),
    siliconflow_key: str = Form(""),
    ali_bailian_key: str = Form(""),
    zhongzhan_gpt_key: str = Form(""),
    zhongzhan_gpt_base_url: str = Form(""),
    zhongzhan_gpt_ocr_model: str = Form(""),
    zhongzhan_claude_key: str = Form(""),
    zhongzhan_claude_base_url: str = Form(""),
    zhongzhan_claude_ocr_model: str = Form(""),
    prefer_engine: str = Form("siliconflow"),
    deepseek_model: str | None = Form(None),
    siliconflow_model: str = Form("Qwen/Qwen3-VL-8B-Instruct"),
    ali_bailian_model: str = Form("qwen3.7-flash"),
    prefer_solve_model: str = Form("deepseek-v4-pro"),
    prefer_parse_model: str = Form("deepseek-flash"),
    prefer_classify_model: str = Form("deepseek-flash"),
    prefer_draw_model: str = Form("Qwen/Qwen3-VL-32B-Instruct")
):
    try:
        # Older clients and other OCR providers may omit this new field.
        deepseek_model = deepseek_model or os.getenv("DEEPSEEK_OCR_MODEL") or "deepseek-flash"
        settings_values = {
            "deepseek_key": deepseek_key,
            "siliconflow_key": siliconflow_key,
            "ali_bailian_key": ali_bailian_key,
            "zhongzhan_gpt_key": zhongzhan_gpt_key,
            "zhongzhan_gpt_base_url": zhongzhan_gpt_base_url,
            "zhongzhan_gpt_ocr_model": zhongzhan_gpt_ocr_model,
            "zhongzhan_claude_key": zhongzhan_claude_key,
            "zhongzhan_claude_base_url": zhongzhan_claude_base_url,
            "zhongzhan_claude_ocr_model": zhongzhan_claude_ocr_model,
            "prefer_engine": prefer_engine,
            "deepseek_model": deepseek_model,
            "siliconflow_model": siliconflow_model,
            "ali_bailian_model": ali_bailian_model,
            "prefer_solve_model": prefer_solve_model,
            "prefer_parse_model": prefer_parse_model,
            "prefer_classify_model": prefer_classify_model,
            "prefer_draw_model": prefer_draw_model,
        }
        if any("\r" in value or "\n" in value for value in settings_values.values()):
            raise ValueError("配置值不能包含换行符。")

        # If masked, preserve current key
        if "••••" in deepseek_key:
            deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if "••••" in siliconflow_key:
            siliconflow_key = os.getenv("SILICONFLOW_API_KEY", "")
        if "••••" in ali_bailian_key:
            ali_bailian_key = os.getenv("ALI_BAILIAN_API_KEY", "")
        if "••••" in zhongzhan_gpt_key:
            zhongzhan_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
        if "••••" in zhongzhan_claude_key:
            zhongzhan_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")
            
        # Read current .env
        env_lines = []
        if ENV_FILE.exists():
            with ENV_FILE.open("r", encoding="utf-8") as f:
                env_lines = f.readlines()
        
        keys_replaced = {
            "DEEPSEEK_API_KEY": False,
            "DEEPSEEK_OCR_MODEL": False,
            "SILICONFLOW_API_KEY": False,
            "ALI_BAILIAN_API_KEY": False,
            "ZHONGZHAN_GPT_API_KEY": False,
            "ZHONGZHAN_GPT_BASE_URL": False,
            "ZHONGZHAN_GPT_OCR_MODEL": False,
            "ZHONGZHAN_CLAUDE_API_KEY": False,
            "ZHONGZHAN_CLAUDE_BASE_URL": False,
            "ZHONGZHAN_CLAUDE_OCR_MODEL": False,
            "OCR_PREFER_ENGINE": False,
            "SILICONFLOW_OCR_MODEL": False,
            "ALI_BAILIAN_OCR_MODEL": False,
            "PREFER_SOLVE_MODEL": False,
            "PREFER_PARSE_MODEL": False,
            "PREFER_CLASSIFY_MODEL": False,
            "PREFER_DRAW_MODEL": False
        }
        new_lines = []
        
        for line in env_lines:
            line_strip = line.strip()
            # Skip old Pix2Text settings to clean .env
            if line_strip.startswith("PIX2TEXT_API_KEY=") or line_strip.startswith("PIX2TEXT_SERVER_TYPE="):
                continue
                
            if line_strip.startswith("DEEPSEEK_API_KEY="):
                new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
                keys_replaced["DEEPSEEK_API_KEY"] = True
            elif line_strip.startswith("DEEPSEEK_OCR_MODEL="):
                new_lines.append(f"DEEPSEEK_OCR_MODEL={deepseek_model}\n")
                keys_replaced["DEEPSEEK_OCR_MODEL"] = True
            elif line_strip.startswith("SILICONFLOW_API_KEY="):
                new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
                keys_replaced["SILICONFLOW_API_KEY"] = True
            elif line_strip.startswith("ALI_BAILIAN_API_KEY="):
                new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
                keys_replaced["ALI_BAILIAN_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_API_KEY="):
                new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
                keys_replaced["ZHONGZHAN_GPT_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
                keys_replaced["ZHONGZHAN_GPT_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
                keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_API_KEY="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"] = True
            elif line_strip.startswith("OCR_PREFER_ENGINE="):
                new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
                keys_replaced["OCR_PREFER_ENGINE"] = True
            elif line_strip.startswith("SILICONFLOW_OCR_MODEL="):
                new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
                keys_replaced["SILICONFLOW_OCR_MODEL"] = True
            elif line_strip.startswith("ALI_BAILIAN_OCR_MODEL="):
                new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
                keys_replaced["ALI_BAILIAN_OCR_MODEL"] = True
            elif line_strip.startswith("PREFER_SOLVE_MODEL="):
                new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
                keys_replaced["PREFER_SOLVE_MODEL"] = True
            elif line_strip.startswith("PREFER_PARSE_MODEL="):
                new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
                keys_replaced["PREFER_PARSE_MODEL"] = True
            elif line_strip.startswith("PREFER_CLASSIFY_MODEL=") or line_strip.startswith("DEEPSEEK_CLASSIFY_MODEL="):
                new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
                keys_replaced["PREFER_CLASSIFY_MODEL"] = True
            elif line_strip.startswith("PREFER_DRAW_MODEL="):
                new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
                keys_replaced["PREFER_DRAW_MODEL"] = True
            else:
                new_lines.append(line)
                
        # Append keys if not replaced
        if not keys_replaced["DEEPSEEK_API_KEY"]:
            new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
        if not keys_replaced["DEEPSEEK_OCR_MODEL"]:
            new_lines.append(f"DEEPSEEK_OCR_MODEL={deepseek_model}\n")
        if not keys_replaced["SILICONFLOW_API_KEY"]:
            new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
        if not keys_replaced["ALI_BAILIAN_API_KEY"]:
            new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
        if not keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
        if not keys_replaced["OCR_PREFER_ENGINE"]:
            new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
        if not keys_replaced["SILICONFLOW_OCR_MODEL"]:
            new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
        if not keys_replaced["ALI_BAILIAN_OCR_MODEL"]:
            new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
        if not keys_replaced["PREFER_SOLVE_MODEL"]:
            new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
        if not keys_replaced["PREFER_PARSE_MODEL"]:
            new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
        if not keys_replaced["PREFER_CLASSIFY_MODEL"]:
            new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
        if not keys_replaced["PREFER_DRAW_MODEL"]:
            new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
            
        write_private_text_atomic(ENV_FILE, "".join(new_lines))
            
        # Clean current process env
        os.environ.pop("PIX2TEXT_API_KEY", None)
        os.environ.pop("PIX2TEXT_SERVER_TYPE", None)
        
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key
        os.environ["DEEPSEEK_OCR_MODEL"] = deepseek_model
        os.environ["SILICONFLOW_API_KEY"] = siliconflow_key
        os.environ["ALI_BAILIAN_API_KEY"] = ali_bailian_key
        os.environ["ZHONGZHAN_GPT_API_KEY"] = zhongzhan_gpt_key
        os.environ["ZHONGZHAN_GPT_BASE_URL"] = zhongzhan_gpt_base_url
        os.environ["ZHONGZHAN_GPT_OCR_MODEL"] = zhongzhan_gpt_ocr_model
        os.environ["ZHONGZHAN_CLAUDE_API_KEY"] = zhongzhan_claude_key
        os.environ["ZHONGZHAN_CLAUDE_BASE_URL"] = zhongzhan_claude_base_url
        os.environ["ZHONGZHAN_CLAUDE_OCR_MODEL"] = zhongzhan_claude_ocr_model
        
        os.environ["OCR_PREFER_ENGINE"] = prefer_engine
        os.environ["SILICONFLOW_OCR_MODEL"] = siliconflow_model
        os.environ["ALI_BAILIAN_OCR_MODEL"] = ali_bailian_model
        os.environ["PREFER_SOLVE_MODEL"] = prefer_solve_model
        os.environ["PREFER_PARSE_MODEL"] = prefer_parse_model
        os.environ["PREFER_CLASSIFY_MODEL"] = prefer_classify_model
        os.environ["PREFER_DRAW_MODEL"] = prefer_draw_model
        
        return {"status": "success", "message": "API 与首选大模型配置已成功保存并即时生效！"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"保存配置失败: {str(e)}"},
            status_code=500
        )

# ----------------- Version & Update Check API -----------------

def parse_version_tuple(v_str: str):
    """Parse version string like 'v2.0.1' or '2.0.1' into integer tuple for comparison."""
    if not v_str:
        return (0, 0, 0)
    cleaned = v_str.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in cleaned.split("."):
        try:
            parts.append(int(re.sub(r"\D", "", p) or "0"))
        except Exception:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

@app.get("/api/version")
def get_version_info():
    """Return local version info."""
    from mathbank import __version__, GITHUB_REPO
    is_git_repo = (PROJECT_ROOT / ".git").exists()
    return {
        "current_version": __version__,
        "repo": GITHUB_REPO,
        "is_git_repo": is_git_repo,
        "server_instance_id": SERVER_INSTANCE_ID,
    }

@app.get("/api/version/check-update")
def check_version_update():
    """Check for latest release on GitHub."""
    from mathbank import __version__, GITHUB_REPO
    from mathbank.ai_http import robust_request_get
    
    is_git_repo = (PROJECT_ROOT / ".git").exists()
    current_ver = __version__
    
    result = {
        "status": "success",
        "current_version": current_ver,
        "latest_version": current_ver,
        "has_update": False,
        "release_title": "",
        "release_body": "",
        "release_url": f"https://github.com/{GITHUB_REPO}/releases/latest",
        "published_at": "",
        "assets": {},
        "is_git_repo": is_git_repo
    }
    
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MathBank-Question-Bank-App"
        }
        resp = robust_request_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            latest_tag = data.get("tag_name", "").strip()
            latest_ver = latest_tag.lstrip("vV")
            
            # Compare versions
            current_tuple = parse_version_tuple(current_ver)
            latest_tuple = parse_version_tuple(latest_ver)
            
            has_update = latest_tuple > current_tuple
            
            assets_map = {}
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                download_url = asset.get("browser_download_url", "")
                size_mb = round(asset.get("size", 0) / (1024 * 1024), 1)
                download_count = asset.get("download_count", 0)
                if "macOS" in name or "mac" in name.lower() or "darwin" in name.lower():
                    assets_map["macOS"] = {"name": name, "url": download_url, "size_mb": size_mb, "downloads": download_count}
                elif "Windows" in name or "win" in name.lower():
                    assets_map["Windows"] = {"name": name, "url": download_url, "size_mb": size_mb, "downloads": download_count}
            
            result.update({
                "latest_version": latest_tag,
                "has_update": has_update,
                "release_title": data.get("name", "") or latest_tag,
                "release_body": data.get("body", ""),
                "release_url": data.get("html_url", result["release_url"]),
                "published_at": data.get("published_at", ""),
                "assets": assets_map
            })
        else:
            result["status"] = "warning"
            result["message"] = f"GitHub API 返回状态码: {resp.status_code}"
    except Exception as e:
        result["status"] = "warning"
        result["message"] = f"检查更新超时或失败: {str(e)}"
        
    return result

# ----------------- TikZ Render & AI Correction API -----------------

def compile_tikz_to_png(tikz_code: str) -> str:
    """
    编译 TikZ 代码为 PNG 并存放在静态资源目录中。
    如果编译成功，返回相对路径（如 /static/uploads/tikz_xxx.png）。
    如果编译失败，抛出 Exception 详细说明原因。
    """
    import shutil
    import uuid
    import subprocess
    import os
    import platform

    # 1. 检查 xelatex
    # macOS 特有处理：如果系统是 macOS 且标准 MacTeX 路径存在，确保其在 PATH 中，防止 GUI/后台进程环境变量丢失
    if platform.system() == "Darwin":
        mactex_bin = "/Library/TeX/texbin"
        if os.path.exists(mactex_bin) and mactex_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.path.pathsep + mactex_bin

    if not shutil.which("xelatex"):
        raise RuntimeError("系统未检测到 'xelatex' 编译器。请确保您的系统已安装 MacTeX/TeX Live 并将其加入 PATH。")

    # 2. 检查 PyMuPDF
    try:
        import pymupdf as fitz
    except ImportError:
        raise RuntimeError("Python 环境中未安装 'pymupdf'，无法将 PDF 转换为图像，请运行 'pip install pymupdf' 安装。")

    # 3. 创建临时文件夹
    temp_dir = os.path.join(UPLOAD_DIR, ".tikz_temp")
    os.makedirs(temp_dir, exist_ok=True)

    unique_id = uuid.uuid4().hex
    tex_path = os.path.join(temp_dir, f"{unique_id}.tex")
    pdf_path = os.path.join(temp_dir, f"{unique_id}.pdf")
    png_path = os.path.join(temp_dir, f"{unique_id}.png")
    aux_path = os.path.join(temp_dir, f"{unique_id}.aux")
    log_path = os.path.join(temp_dir, f"{unique_id}.log")

    # 拼装完整的 TeX 模板
    tex_content = f"""\\documentclass[tikz, border=2mm]{{standalone}}
\\usepackage{{ctex}}
\\usepackage{{amsmath}}
\\usepackage{{amssymb}}
\\usepackage{{tikz}}
\\usepackage{{pgfplots}}
\\pgfplotsset{{compat=1.16}}
\\usetikzlibrary{{patterns}}
\\usetikzlibrary{{calc,positioning,intersections,arrows}}
\\usetikzlibrary{{shapes.geometric,through,decorations.pathmorphing,arrows.meta,quotes,mindmap,shapes.symbols,shapes.arrows,automata,angles,3d,trees,shadows,shapes.callouts,decorations.pathreplacing,decorations.markings}}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""

    try:
        # 写入临时 tex 文件
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        # 调用 xelatex 编译
        result = subprocess.run(
            [
                "xelatex",
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "-output-directory=.",
                os.path.basename(tex_path),
            ],
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env=build_restricted_tex_environment(temp_dir),
        )

        if result.returncode != 0:
            # 尝试提取编译错误原因
            log_content = ""
            if os.path.exists(log_path):
                try:
                    with open(log_path, "rb") as lf:
                        raw_log = lf.read()
                        try:
                            log_text = raw_log.decode("utf-8")
                        except UnicodeDecodeError:
                            log_text = raw_log.decode("gbk", errors="replace")
                        lines = log_text.splitlines()
                        # 找到包含 ! 的报错行
                        error_lines = [line.strip() for line in lines if line.startswith("!")]
                        if error_lines:
                            log_content = "\n".join(error_lines[:3])
                except Exception:
                    pass
            error_msg = log_content if log_content else "LaTeX 语法错误，编译失败。"
            raise RuntimeError(f"编译错误: {error_msg}")

        if not os.path.exists(pdf_path):
            raise RuntimeError("编译未生成 PDF 文件。")

        # 使用 PyMuPDF 将 PDF 转换成 PNG，并确保异常路径也会关闭文档。
        with fitz.open(pdf_path) as doc:
            if len(doc) == 0:
                raise RuntimeError("生成的 PDF 文件为空。")
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=150)
            pix.save(png_path)

        if not os.path.exists(png_path):
            raise RuntimeError("PDF 转换 PNG 失败。")

        # 将最终生成的图片拷贝到 uploads 目录下
        final_filename = f"tikz_{unique_id}.png"
        final_dest = os.path.join(UPLOAD_DIR, final_filename)
        shutil.copy2(png_path, final_dest)

        # 返回相对路径
        return f"/{UPLOAD_DIR_REL}/{final_filename}"

    except subprocess.TimeoutExpired:
        raise RuntimeError("编译超时 (15秒)，可能是您的 TikZ 绘图循环出现了死循环。")
    except Exception as e:
        raise RuntimeError(str(e))
    finally:
        # 清理临时文件
        for temp_file in [tex_path, pdf_path, png_path, aux_path, log_path]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

@app.post("/api/render_tikz")
def render_tikz_endpoint(tikz_code: str = Form(...)):
    """接收 TikZ 代码并编译成静态 PNG，返回其相对路径"""
    try:
        image_path = compile_tikz_to_png(tikz_code)
        return {"status": "success", "image_path": image_path}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/correct_tikz")
def correct_tikz_endpoint(
    tikz_code: str = Form(...),
    original_image_path: str = Form(...),
    user_prompt: str = Form(None)
):
    """利用用户指定的高级绘图模型进行 TikZ 纠错，支持人工指导意见注入"""
    import base64

    # 动态读取高级绘图模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    draw_provider = resolve_draw_provider(prefer_draw)
    if not draw_provider.api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                f"未配置 {draw_provider.credential_label}！"
                "请在设置面板中配置后重试。"
            ),
        )
    print(
        f"[TikZ Correction] 启用 {draw_provider.provider_label} 高级模型进行纠错: "
        f"{draw_provider.model_name}, Base URL: {draw_provider.chat_completions_url}"
    )

    # 对原始截图进行 Base64 编码
    try:
        clean_original_path = resolve_upload_asset(
            original_image_path,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    except AssetSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with open(clean_original_path, "rb") as f:
            encoded_original = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"读取原始图片失败: {str(e)}")

    # 尝试编译当前的 TikZ 代码
    rendered_image_path = None
    compile_error_log = None
    try:
        rendered_image_path = compile_tikz_to_png(tikz_code)
    except Exception as e:
        compile_error_log = str(e)

    # 视觉比对模式（编译成功，获取到两张图）
    if rendered_image_path:
        try:
            clean_rendered_path = resolve_upload_asset(
                rendered_image_path,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
        except AssetSecurityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            with open(clean_rendered_path, "rb") as f:
                encoded_rendered = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"读取渲染出的 TikZ 图片失败: {str(e)}")

        prompt = build_tikz_correction_prompt(
            tikz_code,
            user_guidance=user_prompt,
            rendered_comparison=True,
        )

        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_original}"
                }
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_rendered}"
                }
            }
        ]
        
        # 临时创建的渲染图在使用后也可以删除，以节省磁盘
        try:
            clean_rendered_path.unlink()
        except Exception:
            pass

    # 报错自愈模式（编译失败，只有原始图 + 报错日志）
    else:
        prompt = build_tikz_correction_prompt(
            tikz_code,
            user_guidance=user_prompt,
            compile_error_log=compile_error_log,
            rendered_comparison=False,
        )

        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_original}"
                }
            }
        ]

    try:
        corrected_code = request_tikz_completion(
            draw_provider,
            content_payload,
            timeout=90,
        )
        return {
            "status": "success",
            "corrected_code": corrected_code,
            "mode": "visual_diff" if rendered_image_path else "error_recovery"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI 纠错请求失败: {str(e)}")

@app.post("/api/ai/draw_tikz")
def draw_tikz_workbench_endpoint(
    instruction: str = Form(""),
    context: str = Form(""),
    existing_tikz: str = Form(""),
    reference_image_path: str = Form(""),
    reference_image: Optional[UploadFile] = File(None),
):
    """从文字、参考图或已有源码生成/修改 TikZ，供手动录题工作台使用。"""

    instruction = (instruction or "").strip()
    context = (context or "").strip()
    existing_tikz = (existing_tikz or "").strip()
    reference_image_path = (reference_image_path or "").strip()
    has_uploaded_reference = bool(reference_image and reference_image.filename)
    has_reference = has_uploaded_reference or bool(reference_image_path)
    if not instruction and not existing_tikz and not has_reference:
        raise HTTPException(status_code=400, detail="请输入绘图要求、上传参考图或提供已有 TikZ 源码。")
    if len(instruction) > 4000:
        raise HTTPException(status_code=400, detail="绘图要求不能超过 4000 个字符。")
    if len(context) > 30000:
        raise HTTPException(status_code=400, detail="绘图上下文不能超过 30000 个字符。")
    if len(existing_tikz) > 200000:
        raise HTTPException(status_code=400, detail="TikZ 源码不能超过 200000 个字符。")

    reference_path: Optional[Path] = None
    temporary_reference_path: Optional[Path] = None
    persisted_reference_url = ""
    try:
        if has_uploaded_reference:
            raw = read_stream_limited(reference_image.file, MAX_SINGLE_IMAGE_BYTES)
            normalized = normalize_raster_image(raw)
            temporary_reference_path = Path(TMP_UPLOAD_DIR) / (
                f"tikz_reference_{uuid.uuid4().hex}{normalized.extension}"
            )
            temporary_reference_path.write_bytes(normalized.data)
            reference_path = temporary_reference_path
        elif reference_image_path:
            normalized_reference = normalize_upload_asset_reference(
                reference_image_path,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            reference_path = resolve_upload_asset(
                normalized_reference,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            persisted_reference_url = normalized_reference

        prefer_draw = (
            os.getenv("PREFER_DRAW_MODEL")
            or os.getenv("PREFER_PARSE_MODEL")
            or "Qwen/Qwen3-VL-32B-Instruct"
        )
        tikz_code = draw_tikz_via_high_model(
            str(reference_path) if reference_path else None,
            prefer_draw,
            latex_content=context,
            instruction=instruction,
            existing_tikz=existing_tikz,
            require_image_support=has_reference,
        )
        if not tikz_code:
            raise RuntimeError(
                "TikZ 绘图模型未返回可用源码，请检查绘图模型与 API 密钥设置。"
            )
        if temporary_reference_path is not None:
            persisted_name = (
                f"tikz_reference_{uuid.uuid4().hex}"
                f"{temporary_reference_path.suffix.lower()}"
            )
            persisted_path = Path(UPLOAD_DIR) / persisted_name
            temporary_reference_path.replace(persisted_path)
            temporary_reference_path = None
            persisted_reference_url = f"/{UPLOAD_DIR_REL}/{persisted_name}"
        return {
            "status": "success",
            "tikz_code": tikz_code,
            "used_reference_image": has_reference,
            "reference_image_path": persisted_reference_url,
        }
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail="参考图不能超过 10MB。") from exc
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=f"参考图无效: {str(exc)}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"AI TikZ 绘图失败: {str(exc)}") from exc
    finally:
        if temporary_reference_path is not None:
            try:
                temporary_reference_path.unlink(missing_ok=True)
            except OSError:
                pass


@app.post("/api/ai/draw_tikz_from_image")
def draw_tikz_from_image_endpoint(
    image_path: str = Form(...),
    latex_content: str = Form(None),
    x_local_token: str = Header(None, alias="X-Local-Token")
):
    """根据指定的题目图片，调用高级多模态模型生成对应的 LaTeX TikZ 代码"""
    # Middleware already enforces this header for HTTP calls.  Keep the direct
    # function guard tied to the same single token source for test/internal use.
    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        physical_path = resolve_upload_asset(
            image_path,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    except AssetSecurityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
        
    # 动态读取绘图高级模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL") or os.getenv("PREFER_PARSE_MODEL") or "Qwen/Qwen3-VL-32B-Instruct"
    
    try:
        print(f"[API Draw TikZ] 正在调用高级模型 {prefer_draw} 对插图 {image_path} 进行多模态 TikZ 绘图分析...")
        tikz_code = draw_tikz_via_high_model(
            physical_path,
            prefer_draw,
            latex_content=latex_content
        )
        
        if not tikz_code:
            raise RuntimeError(f"多模态高级模型 {prefer_draw} 未能生成有效的 TikZ 代码")
            
        return {
            "status": "success",
            "tikz_code": tikz_code
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"AI 识图绘图失败: {str(e)}")

# ----------------- Questions Management API -----------------

def _escape_like(term: str) -> str:
    """转义 LIKE 通配符，使搜索词按字面匹配（% / _ / \\）。"""
    return str(term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def curriculum_filter_expression(
    compulsory=None, chapter=None, knowledge=None, *,
    include_sub_knowledge=False, version_code=None,
):
    """Filter only the stored single-value classification, preserving relations."""

    if include_sub_knowledge:
        raise HTTPException(status_code=400, detail="细分关系仅作为历史证据保留，请使用原单值小节筛选")
    fields = {"compulsory": compulsory, "chapter": chapter, "knowledge": knowledge}
    fields = {field: value for field, value in fields.items() if value}
    if not fields:
        return None
    return and_(*(getattr(Question, f"category_{field}") == value for field, value in fields.items()))


def apply_curriculum_filters(query, compulsory=None, chapter=None, knowledge=None, *,
                             include_sub_knowledge=False, sub_knowledge=None,
                             sub_knowledge_mode="any", version_code=None):
    expression = curriculum_filter_expression(
        compulsory, chapter, knowledge, include_sub_knowledge=include_sub_knowledge,
        version_code=version_code,
    )
    if expression is not None:
        query = query.filter(expression)
    if sub_knowledge_mode not in {"any", "all"}:
        raise HTTPException(status_code=400, detail="细分知识点筛选模式必须是 any 或 all")
    paths = normalize_sub_knowledge(sub_knowledge if sub_knowledge is not None else [])
    if paths:
        raise HTTPException(status_code=400, detail="细分关系仅作为历史证据保留，请使用原单值小节筛选")
    return query


def require_expected_curriculum_version(expected_version_code):
    if expected_version_code is not None:
        expected = str(expected_version_code).strip().upper()
        if expected != get_active_version_code():
            raise HTTPException(status_code=409, detail="教材版本已切换，请重新载入分类后保存")


@app.get("/api/questions")
def list_questions(
    q: str = None,
    search: str = None,
    compulsory: str = None,
    category_compulsory: str = None,
    chapter: str = None,
    category_chapter: str = None,
    knowledge: str = None,
    category_knowledge: str = None,
    include_sub_knowledge: bool = False,
    sub_knowledge: Optional[List[str]] = Query(None),
    sub_knowledge_mode: str = "any",
    qtype: str = None,
    question_type: str = None,
    difficulty: str = None,
    source: str = None,
    chapter_code: Optional[List[str]] = Query(None),
    thought: Optional[List[str]] = Query(None),
    strategy: Optional[List[str]] = Query(None),
    micro_method: Optional[str] = None,
    function_code: Optional[str] = None,
    tag: Optional[str] = None,
    relation_code: Optional[str] = None,
    relation_group: Optional[str] = None,
    page: Optional[int] = None,
    page_size: int = 20,
    sort: str = "desc",
    db: Session = Depends(get_db)
):
    search_q = q or search
    comp_val = compulsory or category_compulsory
    chap_val = chapter or category_chapter
    know_val = knowledge or category_knowledge
    type_val = qtype or question_type

    query = db.query(Question).options(
        selectinload(Question.curriculum_mappings).selectinload(QuestionCurriculum.sub_knowledge_rows)
    )
    
    # Check if searching for a specific display sequence number
    target_id_by_seq = None
    if search_q:
        clean_q = search_q.strip()
        if clean_q.startswith("#"):
            clean_q = clean_q[1:]
        if clean_q.isdigit():
            seq_val = int(clean_q)
            if seq_val >= 1:
                row = (
                    db.query(Question.id)
                    .order_by(Question.id.asc())
                    .offset(seq_val - 1)
                    .limit(1)
                    .first()
                )
                if row:
                    target_id_by_seq = row[0]

    if search_q:
        escaped_q = _escape_like(search_q)
        # 顶部全局搜索要覆盖全部维度：题干 / 解析 / 评注 / 来源 / 标签（自定义+功能）
        # / 方法（宏观思想、中观策略、微观技法，含中文名→机器码）/ 关联（关联类型与其说明）。
        like_q = f"%{escaped_q}%"
        method_codes: list[str] = []
        for dim in ("thought", "strategy", "function"):
            method_codes.extend(codes_matching_text(dim, search_q))
        tag_conditions = [QuestionTag.code.like(like_q, escape="\\")]
        if method_codes:
            tag_conditions.append(QuestionTag.code.in_(method_codes))
        tag_match = (
            db.query(QuestionTag.question_id)
            .filter(
                QuestionTag.dim.in_(("custom", "thought", "strategy", "micro", "function")),
                or_(*tag_conditions),
            )
        )
        relation_match = (
            db.query(QuestionRelation.id)
            .filter(
                or_(
                    QuestionRelation.relation_code.like(like_q, escape="\\"),
                    QuestionRelation.relation_label.like(like_q, escape="\\"),
                ),
                or_(
                    QuestionRelation.question_id == Question.id,
                    QuestionRelation.related_id == Question.id,
                ),
            )
            .exists()
        )
        search_conditions = [
            (Question.content.like(like_q, escape="\\")),
            (Question.source.like(like_q, escape="\\")),
            (Question.answer_markdown.like(like_q, escape="\\")),
            (Question.review.like(like_q, escape="\\")),
            (Question.tags.like(like_q, escape="\\")),
            Question.id.in_(tag_match),
            relation_match,
        ]
        if target_id_by_seq is not None:
            query = query.filter(or_(Question.id == target_id_by_seq, *search_conditions))
        else:
            query = query.filter(or_(*search_conditions))
    try:
        paths = [json.loads(path) for path in (sub_knowledge or [])]
        query = apply_curriculum_filters(
            query, comp_val, chap_val, know_val,
            include_sub_knowledge=include_sub_knowledge,
            sub_knowledge=paths, sub_knowledge_mode=sub_knowledge_mode,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if type_val:
        query = query.filter(Question.question_type == type_val)
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)
    if source:
        query = query.filter(Question.source.like(f"%{_escape_like(source)}%", escape="\\"))

    # Multi-value tag filters.  Chapter codes match the node and every
    # descendant because a parent code covers its whole subtree.
    for code in chapter_code or []:
        patterns = chapter_prefixes(code)
        if not patterns:
            continue
        query = query.filter(
            Question.id.in_(
                db.query(QuestionTag.question_id).filter(
                    QuestionTag.dim == "chapter",
                    or_(*[QuestionTag.code.like(pattern) for pattern in patterns]),
                )
            )
        )
    for code in thought or []:
        query = query.filter(
            Question.id.in_(
                db.query(QuestionTag.question_id).filter(
                    QuestionTag.dim == "thought",
                    QuestionTag.code == str(code).strip(),
                )
            )
        )
    for code in strategy or []:
        query = query.filter(
            Question.id.in_(
                db.query(QuestionTag.question_id).filter(
                    QuestionTag.dim == "strategy",
                    QuestionTag.code == str(code).strip(),
                )
            )
        )
    if micro_method:
        query = query.filter(
            Question.id.in_(
                db.query(QuestionTag.question_id).filter(
                    QuestionTag.dim == "micro",
                    QuestionTag.code.like(f"%{_escape_like(micro_method)}%", escape="\\"),
                )
            )
        )
    if function_code:
        query = query.filter(
            Question.id.in_(
                db.query(QuestionTag.question_id).filter(
                    QuestionTag.dim == "function",
                    QuestionTag.code == function_code,
                )
            )
        )
    if tag:
        query = query.filter(
            Question.id.in_(
                db.query(QuestionTag.question_id).filter(
                    QuestionTag.dim == "custom",
                    QuestionTag.code.like(f"%{_escape_like(tag)}%", escape="\\"),
                )
            )
        )

    # Typed relation filters.  Relations are undirected, so a question matches
    # whether it sits on the question_id or the related_id side.
    if relation_code:
        query = query.filter(
            db.query(QuestionRelation.id)
            .filter(
                QuestionRelation.relation_code == relation_code,
                or_(
                    QuestionRelation.question_id == Question.id,
                    QuestionRelation.related_id == Question.id,
                ),
            )
            .exists()
        )
    relation_group_val = str(relation_group or "").strip().lower()
    if relation_group_val in {"any", "none"}:
        has_relation = (
            db.query(QuestionRelation.id)
            .filter(
                or_(
                    QuestionRelation.question_id == Question.id,
                    QuestionRelation.related_id == Question.id,
                )
            )
            .exists()
        )
        query = query.filter(
            has_relation if relation_group_val == "any" else ~has_relation
        )

    order_columns = (
        (Question.created_at.asc(), Question.id.asc())
        if str(sort).lower() == "asc"
        else (Question.created_at.desc(), Question.id.desc())
    )
    if page is not None:
        safe_page_size = max(1, min(int(page_size), 100))
        total = query.count()
        total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
        safe_page = max(1, min(int(page), total_pages))
        questions = (
            query.order_by(*order_columns)
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
            .all()
        )
        seq_map = get_seq_mapping(db, [item.id for item in questions])
        relation_counts = _relation_counts_for_questions(
            db, [item.id for item in questions]
        )
        return {
            "items": [
                {
                    **item.to_summary_dict(get_active_version_code()),
                    "seq_num": seq_map.get(item.id),
                    "relation_count": relation_counts.get(item.id, 0),
                    "version_code": get_active_version_code(),
                    "active_version_code": get_active_version_code(),
                }
                for item in questions
            ],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
            "total_pages": total_pages,
        }

    questions = query.order_by(*order_columns).all()
    seq_map = get_seq_mapping(db, [item.id for item in questions])
    relation_counts = _relation_counts_for_questions(db, [item.id for item in questions])
    return [
        {
            **item.to_summary_dict(get_active_version_code()),
            "seq_num": seq_map.get(item.id),
            "relation_count": relation_counts.get(item.id, 0),
            "version_code": get_active_version_code(),
            "active_version_code": get_active_version_code(),
        }
        for item in questions
    ]

def _duplicate_payload_list(value, *, field_name: str, max_items: int = 50):
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} 格式无效") from exc
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是数组")
    if len(value) > max_items:
        raise ValueError(f"{field_name} 数量超过 {max_items} 项")
    return value


def _duplicate_input_from_payload(item: dict) -> QuestionDuplicateInput:
    content = normalize_fillin_macro(str(item.get("content") or ""))
    if not content.strip():
        raise ValueError("题干内容不能为空")
    if len(content) > 200_000:
        raise ValueError("单题题干过长")
    answer_markdown = str(item.get("answer_markdown") or "")
    if len(answer_markdown) > 500_000:
        raise ValueError("单题解析过长")
    image_paths = [
        str(path or "").strip()
        for path in _duplicate_payload_list(
            item.get("image_paths"), field_name="image_paths"
        )
        if str(path or "").strip()
    ]
    content_assets = _duplicate_payload_list(
        item.get("content_tikz_assets"),
        field_name="content_tikz_assets",
    )
    answer_assets = _duplicate_payload_list(
        item.get("answer_tikz_assets"),
        field_name="answer_tikz_assets",
    )
    legacy_reference = str(item.get("tikz_reference_image_path") or "").strip()
    hidden_references = {legacy_reference}
    for asset in [*content_assets, *answer_assets]:
        if isinstance(asset, dict):
            hidden_references.add(str(asset.get("reference_image_path") or "").strip())
    hidden_references.discard("")
    evidence_image_paths = [
        path for path in image_paths if path not in hidden_references
    ]
    visible_paths = select_visible_question_images(
        content,
        answer_markdown,
        evidence_image_paths,
        content_assets,
        review=str(item.get("review") or ""),
    )
    return QuestionDuplicateInput(
        content=content,
        answer_markdown=answer_markdown,
        question_type=str(item.get("question_type") or ""),
        visible_image_signatures=build_visible_image_signatures(
            visible_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        ),
        tikz_signatures=build_tikz_signatures(
            content_assets,
            str(item.get("tikz_code") or ""),
        ),
        answer_asset_signatures=(
            build_visible_image_signatures(
                select_answer_images(
                    answer_markdown,
                    evidence_image_paths,
                    answer_assets,
                ),
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            + build_tikz_signatures(answer_assets)
        ),
    )


def _prompt_visible_image_paths(question: Question) -> list[str]:
    return select_visible_question_images(
        question.content or "",
        question.answer_markdown or "",
        question.display_image_paths,
        question.content_tikz_assets,
        review=question.review or "",
    )


@app.post("/api/questions/check-duplicates")
def check_question_duplicates(payload: dict, db: Session = Depends(get_db)):
    """Check only when a teacher initiates a save/import; never during parsing."""

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="查重请求格式无效")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items 必须是非空题目数组")
    if len(items) > 500:
        raise HTTPException(status_code=400, detail="单次最多查重 500 道题")
    max_candidates = max(1, min(int(payload.get("max_candidates") or 5), 10))

    try:
        prepared = []
        total_content_size = 0
        for index, raw_item in enumerate(items):
            if not isinstance(raw_item, dict):
                raise ValueError(f"第 {index + 1} 道题格式无效")
            duplicate_input = _duplicate_input_from_payload(raw_item)
            total_content_size += len(duplicate_input.content) + len(
                duplicate_input.answer_markdown
            )
            if total_content_size > 5_000_000:
                raise ValueError("本次查重内容总量过大")
            exclude_id = raw_item.get("exclude_id")
            if exclude_id not in (None, ""):
                exclude_id = int(exclude_id)
                if exclude_id <= 0:
                    raise ValueError("exclude_id 必须是正整数")
            else:
                exclude_id = None
            client_key = str(raw_item.get("client_key") or index)
            if len(client_key) > 128:
                raise ValueError("client_key 过长")
            prepared.append(
                {
                    "client_key": client_key,
                    "exclude_id": exclude_id,
                    "input": duplicate_input,
                    "fingerprint": build_question_fingerprint(duplicate_input),
                }
            )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        fingerprints = [item["fingerprint"] for item in prepared]
        batch_matches, batch_diagnostics = batch_local_matches(fingerprints)
        candidate_cache = {}
        raw_results = []
        all_candidate_ids = set()
        truncated_recall_band_count = 0
        for index, item in enumerate(prepared):
            recall_diagnostics = {}
            candidates = find_indexed_candidates(
                db,
                item["fingerprint"],
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
                exclude_id=item["exclude_id"],
                limit=max_candidates,
                fingerprint_cache=candidate_cache,
                diagnostics=recall_diagnostics,
            )
            truncated_recall_band_count += int(
                recall_diagnostics.get("truncated_band_count") or 0
            )
            all_candidate_ids.update(candidate.question.id for candidate in candidates)
            raw_results.append((index, item, candidates))
        seq_map = get_seq_mapping(db, all_candidate_ids)
        rank = {"exact": 3, "probable": 2, "possible_variant": 1, "none": 0}
        response_items = []
        for index, item, candidates in raw_results:
            candidate_payloads = []
            levels = []
            needs_visual_review = False
            for candidate in candidates:
                comparison = candidate.comparison.to_dict()
                levels.append(comparison["level"])
                needs_visual_review = (
                    needs_visual_review or comparison["needs_visual_review"]
                )
                question = candidate.question
                content_preview = str(question.content or "")[:500]
                candidate_images = _prompt_visible_image_paths(question)
                candidate_payloads.append(
                    {
                        "id": question.id,
                        "seq_num": seq_map.get(question.id),
                        "content": content_preview,
                        "content_truncated": len(str(question.content or "")) > 500,
                        "source": question.source or "",
                        "question_type": question.question_type or "",
                        "has_answer": bool((question.answer_markdown or "").strip()),
                        "image_paths": candidate_images[:4],
                        "image_count": len(candidate_images),
                        "snapshot_hash": candidate.fingerprint.content_revision_hash,
                        **comparison,
                    }
                )
            local_payloads = []
            for local_match in batch_matches.get(index, []):
                other_index = int(local_match["other_index"])
                local_payload = {
                    **local_match,
                    "other_client_key": prepared[other_index]["client_key"],
                }
                local_payloads.append(local_payload)
                levels.append(str(local_match["level"]))
                needs_visual_review = (
                    needs_visual_review
                    or bool(local_match.get("needs_visual_review"))
                )
            level = max(levels, key=lambda value: rank.get(value, 0)) if levels else "none"
            response_items.append(
                {
                    "client_key": item["client_key"],
                    "snapshot_hash": item["fingerprint"].content_revision_hash,
                    "level": level,
                    "candidates": candidate_payloads,
                    "batch_matches": local_payloads,
                    "needs_visual_review": needs_visual_review,
                }
            )
        status = duplicate_index_status(db)
        if truncated_recall_band_count:
            status = {
                **status,
                "ready": False,
                "warning": (
                    "近似候选分桶过宽，已为保持速度停止扩展；"
                    "本次结果可能不完整。"
                ),
            }
        if not batch_diagnostics.get("index_complete", True):
            status = {
                **status,
                "ready": False,
                "warning": "批内近似候选过多，结果可能不完整",
            }
        return {
            "status": "success",
            "index": status,
            "batch_diagnostics": batch_diagnostics,
            "items": response_items,
        }
    except Exception as exc:
        db.rollback()
        print(
            "[Duplicate Check] Failed "
            f"(type={type(exc).__name__}); saving remains available."
        )
        raise HTTPException(status_code=503, detail="查重暂不可用，可选择继续保存") from exc


@app.get("/api/questions/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    seq_map = get_seq_mapping(db, [q.id])
    q_dict = q.to_dict(get_active_version_code())
    q_dict["version_code"] = get_active_version_code()
    q_dict["active_version_code"] = get_active_version_code()
    q_dict["curriculum_mappings"] = [mapping.to_dict() for mapping in q.curriculum_mappings]
    q_dict["seq_num"] = seq_map.get(q.id)
    return q_dict

def normalize_fillin_macro(text: str) -> str:
    """Normalize proven blank lines; preserve literal TeX, subscripts and macros.

    Self-contained: the private AST importer still needs only its existing re.
    This proposal does not mutate source files or historical questions.
    """
    if not text or not isinstance(text, str):
        return text or ""
    spans = []

    def escaped(position):
        previous = position - 1
        while previous >= 0 and text[previous] == "\\":
            previous -= 1
        return (position - previous - 1) % 2 == 1

    def covered(position):
        return any(start <= position < end for start, end, _ in spans)

    def group_end(position):
        stack = ["]" if text[position] == "[" else "}"]
        for index in range(position + 1, len(text)):
            if escaped(index):
                continue
            char = text[index]
            if char in "[{":
                stack.append("]" if char == "[" else "}")
            elif char in "]}":
                if char != stack[-1]:
                    return len(text)
                stack.pop()
                if not stack:
                    return index + 1
        return len(text)

    # Code/HTML literals, links, URLs and asset paths cannot supply blank lines.
    literal = re.compile(
        r"<(?P<literal_tag>code|pre|mathbank-math)\b[^>]*>[\s\S]*?</(?P=literal_tag)\s*>|<!--[\s\S]*?(?:-->|\Z)|"
        r"!?\[[^\]\n]*\]\([^\n)]*\)|(?:https?|ftp)://[^\s<>\"']+|"
        r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]*|/?)>", re.IGNORECASE)
    spans.extend((m.start(), m.end(), None) for m in literal.finditer(text))
    cursor = 0
    while match := re.search(r"`+|(?m:^[ \t]{0,3}~{3,})", text[cursor:]):
        start, end = cursor + match.start(), cursor + match.end()
        token = match.group().lstrip(" \t")
        if covered(start) or escaped(start):
            cursor = end
            continue
        line_start = text.rfind("\n", 0, start) + 1
        fence = len(token) >= 3 and not text[line_start:start].strip()
        if fence:
            newline = text.find("\n", end)
            body = newline + 1 if newline >= 0 else len(text)
            closing = re.search(r"(?m)^[ \t]{0,3}" + re.escape(token[0]) + "{" + str(len(token)) + r",}[ \t]*(?:\n|\Z)", text[body:])
            end = body + closing.end() if closing else len(text)
        else:
            closing = next((m for m in re.finditer(r"`+", text[end:]) if len(m.group()) == len(token)), None)
            end = end + closing.end() if closing else len(text)
        spans.append((start, end, None))
        cursor = end
    # Conservatively retain Markdown indented code. Lists' own content stays
    # active; four additional columns establish an indented literal block.
    offset, previous_blank, code_start, list_indent = 0, True, None, None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        leading = re.match(r"[ \t]*", body).group()
        indent = len(leading.expandtabs(4))
        marker = re.match(r"(?:[-+*]|[0-9]{1,9}[.)])[ \t]+", body[len(leading):])
        threshold = list_indent + 4 if list_indent is not None else 4
        if not body.strip():
            previous_blank = True
        elif indent >= threshold and (previous_blank or code_start is not None):
            if code_start is None:
                code_start = offset
            previous_blank = False
        else:
            if code_start is not None:
                spans.append((code_start, offset, None))
                code_start = None
            if marker:
                list_indent = indent + len(marker.group().expandtabs(4))
            elif indent < 4:
                list_indent = None
            previous_blank = False
        offset += len(line)
    if code_start is not None:
        spans.append((code_start, len(text), None))

    environments = re.compile(r"\\begin\s*\{(tikzpicture|verbatim\*?|Verbatim\*?|lstlisting|minted)\}")
    for match in environments.finditer(text):
        if covered(match.start()) or escaped(match.start()):
            continue
        closing = re.search(r"\\end\s*\{" + re.escape(match.group(1)) + r"\}", text[match.end():])
        spans.append((match.start(), match.end() + closing.end() if closing else len(text), None))
    commands = re.compile(r"\\(verb|Verb|lstinline|mintinline|detokenize|texttt|url|path|includegraphics|fillin|underline)(?![A-Za-z])\*?")
    for match in commands.finditer(text):
        if covered(match.start()):
            continue
        name, cursor = match.group(1), match.end()
        while cursor < len(text) and text[cursor] in " \t":
            cursor += 1
        if name in {"verb", "Verb", "lstinline", "mintinline"}:
            if cursor < len(text) and text[cursor] == "[":
                cursor = group_end(cursor)
            if name == "mintinline" and text[cursor:cursor + 1] == "{":
                cursor = group_end(cursor)
            while cursor < len(text) and text[cursor] in " \t":
                cursor += 1
            if cursor < len(text) and not text[cursor].isspace():
                if text[cursor] == "{":
                    end = group_end(cursor)
                else:
                    closing = text.find(text[cursor], cursor + 1)
                    end = closing + 1 if closing >= 0 else len(text)
                spans.append((match.start(), end, None))
            continue
        arguments, end = [], match.end()
        while cursor < len(text) and text[cursor] in "[{":
            stop = group_end(cursor)
            arguments.append((cursor, stop))
            end = stop
            cursor = stop
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
        replacement = None
        if name == "underline" and not escaped(match.start()) and len(arguments) == 1:
            left, right = arguments[0]
            if text[left] == "{" and text[right - 1:right] == "}":
                inner = text[left + 1:right - 1]
                blank = re.fullmatch(r"(?:\s|_|\\_|~|\\(?:quad|qquad|strut)|\\hspace\{[0-9.]+(?:cm|mm|pt|em|ex|in)\})*", inner)
                if blank:
                    replacement = r"\fillin"
        # Preserve explicit fillin parameters/braces and nonblank underline.
        spans.append((match.start(), end, replacement))

    # An underscore inside a real subscript group is not an inferred blank.
    stack = []
    for index, char in enumerate(text):
        if char not in "{}" or escaped(index) or covered(index):
            continue
        if char == "{":
            stack.append((index, index > 0 and text[index - 1] == "_" and not escaped(index - 1)))
        elif stack:
            start, is_subscript = stack.pop()
            if is_subscript:
                spans.append((start + 1, index, None))
    spans.extend((start + 1, len(text), None) for start, is_subscript in stack if is_subscript)

    def normalize_segment(segment, base):
        def blank(match):
            start, end = base + match.start(), base + match.end()
            if escaped(start):
                return match.group()
            left = text[start - 1:start]
            right = text[end:end + 1]
            if re.fullmatch(r"[A-Za-z0-9_\u0370-\u03ff]", left) or re.fullmatch(r"[A-Za-z0-9_\u0370-\u03ff]", right):
                return match.group()
            if match.group().startswith("\\") and len(match.group()) < 6:
                # Single/double escaped glyphs need a declared value slot. This
                # covers the real textbook S_n = \_ without guessing literal _.
                before = text[max(0, start - 24):start].rstrip()
                after = text[end:]
                if not re.search(r"(?:=|为|是|等于|填写)\s*$", before) or not re.match(r"\s*(?:\$|\\[)\]]|[。，,；;.!?！？]|\Z)", after):
                    return match.group()
            return r"\fillin"
        return re.sub(r"(?:\\_)+|_{3,}", blank, segment)

    result, cursor = [], 0
    for start, end, replacement in sorted(spans, key=lambda row: (row[0], -row[1])):
        if start < cursor:
            continue
        result.append(normalize_segment(text[cursor:start], cursor))
        result.append(text[start:end] if replacement is None else replacement)
        cursor = end
    result.append(normalize_segment(text[cursor:], cursor))
    return "".join(result)


def committed_question_response(
    db: Session,
    db_question: Question,
    question_id: int,
    *,
    operation: str,
) -> dict:
    """Serialize a committed write without ever misreporting it as failed."""

    try:
        db.refresh(db_question)
        seq_map = get_seq_mapping(db, [question_id])
        question = db_question.to_dict(get_active_version_code())
        question["version_code"] = get_active_version_code()
        question["active_version_code"] = get_active_version_code()
        question["seq_num"] = seq_map.get(question_id)
        return {"status": "success", "question": question}
    except Exception as exc:
        # The durable transaction is already complete.  End any failed read
        # transaction and return enough identity for the client to continue;
        # a later list/detail refresh can obtain the full representation.
        try:
            db.rollback()
        except Exception:
            pass
        print(
            f"[Question Write] Post-commit {operation} response degraded "
            f"(type={type(exc).__name__})."
        )
        return {"status": "success", "question": {"id": question_id}}


def prepare_question_assets(
    content: str,
    answer_markdown: str,
    image_paths: str,
    content_tikz_assets: Optional[str],
    tikz_reference_image_path: str,
    answer_tikz_assets: str,
    tikz_code: str = "",
    review: str = "",
    *,
    promotion_log: list[tuple[Path, Path]],
    path_map: dict[str, str] | None = None,
) -> tuple[
    str,
    str,
    list[str],
    str,
    str,
    list[dict[str, str]],
    list[dict[str, str]],
    str,
]:
    """Promote and validate all visible and AI-only assets in one policy path.

    ``review`` (评述) is treated exactly like the 题干/答案 as an asset source so
    images referenced only there are promoted, rewritten and registered too.
    """

    parsed_img_paths = json.loads(image_paths) if image_paths else []
    if not isinstance(parsed_img_paths, list):
        raise AssetSecurityError("image_paths 必须是插图路径数组。")
    parsed_img_paths.extend(embedded_question_assets(review))
    parsed_img_paths.extend(structured_question_assets(
        content_tikz_assets, answer_tikz_assets,
        legacy_reference=tikz_reference_image_path,
    ))
    replacements: dict[str, str] = {}
    content, answer_markdown, review, parsed_img_paths = promote_question_temp_assets(
        content,
        answer_markdown,
        parsed_img_paths,
        review=review,
        promotion_log=promotion_log,
        path_map=replacements,
    )
    if path_map is not None:
        path_map.update(replacements)
    content_tikz_assets = rewrite_structured_asset_paths(content_tikz_assets, replacements)
    answer_tikz_assets = rewrite_structured_asset_paths(answer_tikz_assets, replacements)
    tikz_reference_image_path = replacements.get(tikz_reference_image_path, tikz_reference_image_path)
    parsed_img_paths = normalize_upload_asset_references(
        parsed_img_paths,
        uploads_dir=UPLOAD_DIR,
        url_prefix=UPLOAD_DIR_REL,
    )
    parsed_answer_tikz_assets = normalize_answer_tikz_assets(
        answer_tikz_assets,
        allowed_image_paths=parsed_img_paths,
        uploads_dir=UPLOAD_DIR,
        url_prefix=UPLOAD_DIR_REL,
    )
    if content_tikz_assets is None:
        # Compatibility for clients and existing drafts created before v5.
        parsed_content_tikz_assets: list[dict[str, str]] = []
        parsed_tikz_code = str(tikz_code or "").strip()
        parsed_tikz_reference_image_path = normalize_optional_upload_asset_reference(
            tikz_reference_image_path,
            allowed_image_paths=parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    else:
        parsed_content_tikz_assets = normalize_content_tikz_assets(
            content_tikz_assets,
            allowed_image_paths=parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        first_content_asset = (
            parsed_content_tikz_assets[0] if parsed_content_tikz_assets else {}
        )
        parsed_tikz_code = str(first_content_asset.get("tikz_code") or "")
        parsed_tikz_reference_image_path = str(
            first_content_asset.get("reference_image_path") or ""
        )
    return (
        content,
        answer_markdown,
        parsed_img_paths,
        parsed_tikz_code,
        parsed_tikz_reference_image_path,
        parsed_content_tikz_assets,
        parsed_answer_tikz_assets,
        review,
    )


def build_prepared_question_fingerprint(
    *,
    content: str,
    answer_markdown: str,
    question_type: str,
    image_paths: list[str],
    content_tikz_assets: list[dict[str, str]],
    answer_tikz_assets: list[dict[str, str]],
    tikz_code: str,
    tikz_reference_image_path: str,
    review: str = "",
):
    hidden_references = {str(tikz_reference_image_path or "").strip()}
    for asset in [*content_tikz_assets, *answer_tikz_assets]:
        if isinstance(asset, dict):
            hidden_references.add(str(asset.get("reference_image_path") or "").strip())
    hidden_references.discard("")
    registered_visible_paths = [
        path for path in image_paths if path not in hidden_references
    ]
    visible_paths = select_visible_question_images(
        content,
        answer_markdown,
        registered_visible_paths,
        content_tikz_assets,
        review=review,
    )
    return build_question_fingerprint(
        QuestionDuplicateInput(
            content=content,
            answer_markdown=answer_markdown,
            question_type=question_type,
            visible_image_signatures=build_visible_image_signatures(
                visible_paths,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            ),
            tikz_signatures=build_tikz_signatures(
                content_tikz_assets,
                tikz_code,
            ),
            answer_asset_signatures=(
                build_visible_image_signatures(
                    select_answer_images(
                        answer_markdown,
                        image_paths,
                        answer_tikz_assets,
                    ),
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                )
                + build_tikz_signatures(answer_tikz_assets)
            ),
        )
    )


def duplicate_review_required_response(
    db: Session,
    *,
    fingerprint,
    question_ids: list[int],
    message: str,
    code: str = "duplicate_review_required",
):
    questions = db.query(Question).filter(Question.id.in_(question_ids)).all()
    seq_map = get_seq_mapping(db, question_ids)
    return JSONResponse(
        status_code=409,
        content={
            "status": "error",
            "code": code,
            "message": message,
            "snapshot_hash": fingerprint.content_revision_hash,
            "candidates": [
                {
                    "id": question.id,
                    "seq_num": seq_map.get(question.id),
                    "content": str(question.content or "")[:500],
                    "content_truncated": len(str(question.content or "")) > 500,
                    "source": question.source or "",
                    "question_type": question.question_type or "",
                    "image_paths": _prompt_visible_image_paths(question)[:4],
                }
                for question in questions
            ],
        },
    )


def _parse_code_list(raw: Optional[str]) -> Optional[list[str]]:
    """Parse a JSON/comma code list; None means the field was not submitted."""

    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return [part.strip() for part in re.split(r"[,，]", text) if part.strip()]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        return [parsed.strip()] if parsed.strip() else []
    return []


def sync_question_tags(
    db: Session,
    question: Question,
    *,
    chapter_raw: Optional[str] = None,
    thought_raw: Optional[str] = None,
    function_raw: Optional[str] = None,
    custom_raw: Optional[str] = None,
    strategy_raw: Optional[str] = None,
    micro_raw: Optional[str] = None,
    legacy: Optional[tuple] = None,
    legacy_prev: Optional[tuple] = None,
) -> dict:
    """Replace the multi-value tags of one question dimension by dimension.

    A dimension is only rewritten when its field was submitted, so partial
    updates (for example OCR saving content only) never drop existing tags.

    ``legacy`` is the legacy (册/章/节) triple used by clients that predate the
    multi-value tag fields.  Because those form fields default to ``""`` rather
    than ``None``, the triple is *always* present, so it may only drive the
    chapter dimension when it actually carries information **and** differs from
    ``legacy_prev`` (the triple stored before this request).  Otherwise the
    chapter dimension is treated as "not submitted" and left untouched.
    """

    pending: dict[str, list[str]] = {}

    chapter_codes = _parse_code_list(chapter_raw)
    if chapter_codes is None and legacy is not None:
        submitted = tuple(str(value or "").strip() for value in legacy)
        previous = (
            tuple(str(value or "").strip() for value in legacy_prev)
            if legacy_prev is not None
            else None
        )
        # 旧字段只在"确实带了册/章/节信息"且"这次真的改动了"时才派生章节标签；
        # 否则视为该维度未提交，保留已有标签。
        if any(submitted) and (previous is None or submitted != previous):
            derived = resolve_legacy_code(*legacy)
            chapter_codes = [derived] if derived else []
    if chapter_codes is not None:
        pending["chapter"] = normalize_codes(chapter_codes)

    thought_codes = _parse_code_list(thought_raw)
    if thought_codes is not None:
        pending["thought"] = normalize_tag_codes("thought", thought_codes)

    if function_raw is not None:
        function_code = str(function_raw or "").strip()
        pending["function"] = normalize_tag_codes(
            "function", [function_code] if function_code else []
        )

    if custom_raw is not None:
        pending["custom"] = split_custom_tags(custom_raw)

    strategy_codes = _parse_code_list(strategy_raw)
    if strategy_codes is not None:
        pending["strategy"] = normalize_tag_codes("strategy", strategy_codes)

    if micro_raw is not None:
        # 微观解题方法：自由文本多值。前端以 JSON 数组提交；也兼容纯逗号分隔
        # 文本（与自定义标签同口径），两种格式都正确拆分为多行。
        pending["micro"] = _parse_free_text_list(micro_raw)

    for dim, codes in pending.items():
        db.query(QuestionTag).filter(
            QuestionTag.question_id == question.id,
            QuestionTag.dim == dim,
        ).delete(synchronize_session=False)
        for code in codes:
            db.add(QuestionTag(question_id=question.id, dim=dim, code=code))
    return {dim: list(codes) for dim, codes in pending.items()}


def _record_free_text_tag_memory(custom_raw: Optional[str], micro_raw: Optional[str]) -> None:
    """Record free-text tag usage for the reuse dropdown; never raises.

    自定义标签与微观解题方法的记忆是「锦上添花」能力：任何失败都不能影响
    题目保存主流程，因此整体吞异常只留日志。
    """

    try:
        if custom_raw is not None:
            tag_memory.record_usage("custom", split_custom_tags(custom_raw))
        if micro_raw is not None:
            tag_memory.record_usage("micro", _parse_free_text_list(micro_raw))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[tag-memory] 记录标签使用记忆失败（已忽略）: {exc}")


def _parse_free_text_list(raw: Optional[str]) -> list[str]:
    """Parse a free-text multi-value field into a clean list of labels.

    Accepts either a JSON array of strings (the frontend's payload shape) or
    plain comma / Chinese-comma separated text, so both submission styles land
    as individual rows in ``question_tags`` instead of one literal string.
    """

    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return split_custom_tags("，".join(
                    str(item or "").strip() for item in parsed if str(item or "").strip()
                ))
        except (ValueError, TypeError):
            pass
    return split_custom_tags(text)


def _harvest_free_text_tag_memory(db) -> dict[str, list[tuple[str, int]]]:
    """Read-only harvest of historical free-text tag values from the bank.

    题库里绝大多数题目是在「标签记忆」上线**之前**录入的：它们的自定义标签
    真实存在于库里，却从没被 ``record_usage`` 记过，面板因此一直显示
    「暂无记忆」。这里把这段历史值捞出来供回填使用。

    取值来源两处，全部只读 SELECT：
      * ``question_tags`` 中 dim ∈ {custom, micro} 的 code（新标签体系）；
      * ``questions.tags``（legacy 逗号分隔字符串，归入 custom）。

    同一个 tag 若在同一道题的两个来源里都出现，**按题去重只算一次**，
    避免 use_count 被重复计数虚高。返回 ``{dim: [(tag, 出现题数), ...]}``，
    顺序确定：按出现题数**升序**（同次数按名称）。backfill_from_bank 按传入
    顺序递增时间戳，所以升序传入 == 用得最多的那条拿到最新时间戳，
    正好排在「最近使用优先」面板的第一位。
    """

    owners: dict[str, dict[str, set[int]]] = {
        dim: {} for dim in tag_memory.MEMORY_DIMENSIONS
    }
    rows = (
        db.query(QuestionTag.question_id, QuestionTag.dim, QuestionTag.code)
        .filter(QuestionTag.dim.in_(tuple(tag_memory.MEMORY_DIMENSIONS)))
        .all()
    )
    for question_id, dim, code in rows:
        bucket = owners.get(str(dim or "").strip())
        if bucket is None:
            continue
        for tag in split_custom_tags(code):
            bucket.setdefault(tag, set()).add(int(question_id or 0))

    legacy = owners.get("custom")
    if legacy is not None:
        legacy_rows = db.query(Question.id, Question.tags).filter(
            Question.tags.isnot(None), Question.tags != ""
        ).all()
        for question_id, raw_tags in legacy_rows:
            if not raw_tags:
                continue
            for tag in split_custom_tags(raw_tags):
                legacy.setdefault(tag, set()).add(int(question_id or 0))

    harvested: dict[str, list[tuple[str, int]]] = {}
    for dim, bucket in owners.items():
        ordered = sorted(bucket, key=lambda tag: (len(bucket[tag]), tag))
        harvested[dim] = [(tag, len(bucket[tag])) for tag in ordered]
    return harvested


def _backfill_free_text_tag_memory() -> None:
    """Seed the free-text tag memory from the bank; never blocks startup.

    只写 ``data_backup/tag_memory.json``（经 ``tag_memory.backfill_from_bank``），
    绝不写任何数据库表；任何一步失败都只留日志，应用照常启动。
    """

    try:
        from mathbank.database import SessionLocal

        with SessionLocal() as db:
            harvested = _harvest_free_text_tag_memory(db)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[tag-memory] 读取题库历史标签失败（已跳过回填）: {type(exc).__name__}: {exc}")
        return
    for dim, entries in harvested.items():
        try:
            added = tag_memory.backfill_from_bank(dim, entries)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[tag-memory] 回填 {dim} 标签记忆失败（已忽略）: {type(exc).__name__}: {exc}")
            continue
        if added:
            print(f"[tag-memory] 已从题库回填 {added} 条 {dim} 历史标签记忆。")


@app.get("/api/tag-memory")
def get_tag_memory(dim: str = "custom"):
    """Return remembered free-text tags (自定义标签 / 微观解题方法), MRU first."""

    entries = tag_memory.list_memory(dim)
    return {"dim": dim, "items": entries}


@app.post("/api/tag-memory/use")
def use_tag_memory(dim: str = Form(...), tags: str = Form("")):
    """Record a manual usage of free-text tags (e.g. picked from the dropdown)."""

    updated = tag_memory.record_usage(dim, split_custom_tags(tags))
    return {"status": "success", "dim": dim, "items": updated}


@app.delete("/api/tag-memory")
def delete_tag_memory(dim: str = "custom", tag: str = "", clear: bool = False):
    """Delete one remembered tag, or every tag of the dimension with ``clear``."""

    if clear:
        removed = tag_memory.clear_memory(dim)
        return {"status": "success", "dim": dim, "cleared": removed}
    removed = tag_memory.delete_memory(dim, tag)
    if not removed:
        raise HTTPException(status_code=404, detail="未找到该条标签记忆。")
    return {"status": "success", "dim": dim, "deleted": tag}


@app.get("/api/config/micro-method-vocab")
def get_micro_method_vocab():
    """Return the three-level 微观解题方法 reference tree (L1/L2/L3)."""

    from mathbank.tags import load_micro_method_vocab

    return load_micro_method_vocab()


@app.get("/api/tag-values")
def list_tag_values(dim: str = "micro", db: Session = Depends(get_db)):
    """Distinct in-use codes/labels of one narrow-table dimension.

    用于筛选下拉：只列题库里真实存在的取值（按使用次数降序），
    前端再与个人记忆合并展示。
    """

    if dim not in {"thought", "strategy", "micro", "custom", "function"}:
        raise HTTPException(status_code=400, detail="不支持的标签维度。")
    rows = (
        db.query(QuestionTag.code, func.count(QuestionTag.id))
        .filter(QuestionTag.dim == dim)
        .group_by(QuestionTag.code)
        .order_by(func.count(QuestionTag.id).desc(), QuestionTag.code.asc())
        .all()
    )
    return {"dim": dim, "items": [{"value": code, "count": int(count or 0)} for code, count in rows]}


@app.get("/api/config/tag-schema")
def get_tag_schema():
    """Return the dimension definitions of the classification system."""

    return load_tag_schema()


@app.get("/api/config/curriculum-tree/{version}")
def get_curriculum_tree(version: str):
    """Return the 册/章/节/小节 tree of one textbook version."""

    try:
        return load_curriculum_tree(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/questions")
@serialize_asset_lifecycle
def create_question(
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    sub_knowledge: str = Form("[]"),
    expected_version_code: Optional[str] = Form(None),
    difficulty: str = Form(...),
    source: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    content_tikz_assets: Optional[str] = Form(None),
    tikz_reference_image_path: str = Form(""),
    answer_tikz_assets: str = Form("[]"),
    figure_align: str = Form("right"),
    figure_align_custom: bool = Form(False),
    figure_size: str = Form("auto"),
    image_layouts: str = Form("{}"),
    tags: str = Form(""),
    image_paths: str = Form("[]"),  # JSON array string
    duplicate_snapshot_hash: str = Form(""),
    duplicate_override: str = Form(""),
    tag_chapter_codes: Optional[str] = Form(None),
    tag_thought_codes: Optional[str] = Form(None),
    tag_function_code: Optional[str] = Form(None),
    tag_custom_tags: Optional[str] = Form(None),
    tag_strategy_codes: Optional[str] = Form(None),
    tag_micro_methods: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    require_expected_curriculum_version(expected_version_code)
    asset_promotions: list[tuple[Path, Path]] = []
    asset_path_map: dict[str, str] = {}
    duplicate_warning = ""
    try:
        parsed_sub_knowledge = normalize_sub_knowledge(sub_knowledge)
        duplicate_snapshot_hash = str(duplicate_snapshot_hash or "").strip()
        duplicate_override = str(duplicate_override or "").strip()
        if duplicate_override not in {"", "independent"}:
            raise ValueError("无效的查重处理方式")
        if duplicate_override and not duplicate_snapshot_hash:
            raise ValueError("独立保存确认缺少题目快照")
        if duplicate_snapshot_hash and not re.fullmatch(
            r"[0-9a-f]{64}", duplicate_snapshot_hash
        ):
            raise ValueError("查重题目快照格式无效")
        figure_size = str(figure_size or "").strip()
        if figure_size not in FIGURE_SIZE_VALUES:
            raise ValueError("无效的插图尺寸")
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)

        (
            content,
            answer_markdown,
            parsed_img_paths,
            parsed_tikz_code,
            parsed_tikz_reference_image_path,
            parsed_content_tikz_assets,
            parsed_answer_tikz_assets,
            review,
        ) = prepare_question_assets(
            content,
            answer_markdown,
            image_paths,
            content_tikz_assets,
            tikz_reference_image_path,
            answer_tikz_assets,
            tikz_code,
            review=review,
            promotion_log=asset_promotions,
            path_map=asset_path_map,
        )
        review = rewrite_question_asset_paths(review, asset_path_map)
        
        # 1. Fallback if third level is empty, default to chapter
            
        db_question = Question(
            content=content,
            question_type=question_type,
            category_compulsory=category_compulsory,
            category_chapter=category_chapter,
            category_knowledge=category_knowledge,
            difficulty=difficulty,
            source=source,
            answer_markdown=answer_markdown,
            review=review,
            tikz_code=parsed_tikz_code,
            tikz_reference_image_path=parsed_tikz_reference_image_path,
            figure_align=figure_align if figure_align in FIGURE_ALIGN_VALUES else "right",
            figure_align_custom=bool(figure_align_custom),
            figure_size=figure_size,
            tags=tags
        )
        db_question.image_layouts = rewrite_image_layout_paths(image_layouts, asset_path_map)
        db_question.image_paths = parsed_img_paths
        db_question.content_tikz_assets = parsed_content_tikz_assets
        db_question.answer_tikz_assets = parsed_answer_tikz_assets

        # 关联关系只走 question_relations 单轨：保存题目本身不再碰
        # association_group_id，该列已由迁移折叠为 legacy_group 关系行后清空。
        db.add(db_question)
        db.flush()

        try:
            question_fingerprint = build_prepared_question_fingerprint(
                content=content,
                answer_markdown=answer_markdown,
                question_type=question_type,
                image_paths=parsed_img_paths,
                content_tikz_assets=parsed_content_tikz_assets,
                answer_tikz_assets=parsed_answer_tikz_assets,
                tikz_code=parsed_tikz_code,
                tikz_reference_image_path=parsed_tikz_reference_image_path,
                review=review,
            )
        except Exception as fingerprint_exc:
            question_fingerprint = None
            duplicate_warning = "题目已保存，但本次查重指纹未生成；后台将尝试补建，失败时下次启动继续。"
            print(
                "[Duplicate Index] Create fingerprint failed open "
                f"(type={type(fingerprint_exc).__name__}); background backfill will retry."
            )
        if duplicate_snapshot_hash and question_fingerprint is not None:
            if duplicate_snapshot_hash != question_fingerprint.content_revision_hash:
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=[],
                    message="题目在查重后已发生变化，请重新查重后保存。",
                    code="duplicate_snapshot_stale",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
            exact_ids = exact_duplicate_ids(
                db,
                question_fingerprint,
                exclude_id=db_question.id,
            )
            if exact_ids and duplicate_override != "independent":
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=exact_ids,
                    message="入库前发现新的疑似已收录题，请核对后再决定。",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
        if question_fingerprint is not None:
            upsert_question_fingerprint(db, db_question, question_fingerprint)

        # Save the question and its active curriculum mirror atomically.
        active_version = get_active_version_code()
        curriculum_map = QuestionCurriculum(
            question_id=db_question.id,
            version_code=active_version,
            compulsory=category_compulsory,
            chapter=category_chapter,
            knowledge=category_knowledge
        )
        curriculum_map.sub_knowledge = parsed_sub_knowledge
        db.add(curriculum_map)
        sync_question_tags(
            db,
            db_question,
            chapter_raw=tag_chapter_codes,
            thought_raw=tag_thought_codes,
            function_raw=tag_function_code,
            custom_raw=tag_custom_tags,
            strategy_raw=tag_strategy_codes,
            micro_raw=tag_micro_methods,
            legacy=(category_compulsory, category_chapter, category_knowledge),
            legacy_prev=None,
        )
        _record_free_text_tag_memory(tag_custom_tags, tag_micro_methods)
        committed_question_id = db_question.id
        db.commit()
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"保存题目失败: {str(e)}")

    # Everything below is compensating or response work after the durable
    # success boundary; none of it may turn the write into a misleading 400.
    schedule_database_export(background_tasks, operation="create_question")
    if duplicate_warning:
        schedule_question_fingerprint_retry(
            background_tasks,
            question_id=committed_question_id,
            operation="create_question",
        )
    response = committed_question_response(
        db,
        db_question,
        committed_question_id,
        operation="create_question",
    )
    if duplicate_warning:
        response["warning"] = duplicate_warning
    if asset_path_map:
        response["asset_path_map"] = asset_path_map
    return response

@app.put("/api/questions/{question_id}")
@serialize_asset_lifecycle
def update_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    sub_knowledge: Optional[str] = Form(None),
    expected_version_code: Optional[str] = Form(None),
    difficulty: str = Form(...),
    source: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    content_tikz_assets: Optional[str] = Form(None),
    tikz_reference_image_path: str = Form(""),
    answer_tikz_assets: str = Form("[]"),
    figure_align: str = Form("right"),
    figure_align_custom: Optional[bool] = Form(None),
    figure_size: Optional[str] = Form(None),
    image_layouts: Optional[str] = Form(None),
    tags: str = Form(""),
    image_paths: str = Form("[]"),
    duplicate_snapshot_hash: str = Form(""),
    duplicate_override: str = Form(""),
    tag_chapter_codes: Optional[str] = Form(None),
    tag_thought_codes: Optional[str] = Form(None),
    tag_function_code: Optional[str] = Form(None),
    tag_custom_tags: Optional[str] = Form(None),
    tag_strategy_codes: Optional[str] = Form(None),
    tag_micro_methods: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    require_expected_curriculum_version(expected_version_code)
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    asset_promotions: list[tuple[Path, Path]] = []
    asset_path_map: dict[str, str] = {}
    duplicate_warning = ""
    old_images = list(db_question.image_paths)
    try:
        parsed_sub_knowledge = normalize_sub_knowledge(sub_knowledge) if sub_knowledge is not None else None
        duplicate_snapshot_hash = str(duplicate_snapshot_hash or "").strip()
        duplicate_override = str(duplicate_override or "").strip()
        if duplicate_override not in {"", "independent"}:
            raise ValueError("无效的查重处理方式")
        if duplicate_override and not duplicate_snapshot_hash:
            raise ValueError("独立保存确认缺少题目快照")
        if duplicate_snapshot_hash and not re.fullmatch(
            r"[0-9a-f]{64}", duplicate_snapshot_hash
        ):
            raise ValueError("查重题目快照格式无效")
        if figure_size is not None:
            figure_size = str(figure_size).strip()
            if figure_size not in FIGURE_SIZE_VALUES:
                raise ValueError("无效的插图尺寸")
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)

        (
            content,
            answer_markdown,
            parsed_img_paths,
            parsed_tikz_code,
            parsed_tikz_reference_image_path,
            parsed_content_tikz_assets,
            parsed_answer_tikz_assets,
            review,
        ) = prepare_question_assets(
            content,
            answer_markdown,
            image_paths,
            content_tikz_assets,
            tikz_reference_image_path,
            answer_tikz_assets,
            tikz_code,
            review=review,
            promotion_log=asset_promotions,
            path_map=asset_path_map,
        )
        review = rewrite_question_asset_paths(review, asset_path_map)
        
        # 1. Fallback if third level is empty, default to chapter
        if not category_knowledge and category_chapter:
            category_knowledge = category_chapter

        # 记下改动前的"册/章/节"三元组。sync_question_tags 用它判断这次是否真的
        # 动了旧分类字段 —— 必须在这批赋值之前抓，否则读到的是新值，判断恒为"未变化"。
        legacy_category_prev = (
            db_question.category_compulsory,
            db_question.category_chapter,
            db_question.category_knowledge,
        )

        db_question.content = content
        db_question.question_type = question_type
        db_question.category_compulsory = category_compulsory
        db_question.category_chapter = category_chapter
        db_question.category_knowledge = category_knowledge
        db_question.difficulty = difficulty
        db_question.source = source
        db_question.answer_markdown = answer_markdown
        db_question.review = review
        db_question.tikz_code = parsed_tikz_code
        db_question.tikz_reference_image_path = parsed_tikz_reference_image_path
        if figure_align in FIGURE_ALIGN_VALUES:
            db_question.figure_align = figure_align
        if figure_align_custom is not None:
            db_question.figure_align_custom = bool(figure_align_custom)
        if figure_size is not None:
            db_question.figure_size = figure_size
        db_question.tags = tags
        # Physical cleanup happens only after the database commit succeeds.
        # Images still referenced by the (rewritten) 评述 must never be treated
        # as removed, so union the review references back in.
        removed_images = set(old_images) - set(parsed_img_paths)
        removed_images -= set(embedded_question_assets(review))

        db_question.image_layouts = rewrite_image_layout_paths(
            image_layouts if image_layouts is not None else db_question.image_layouts,
            asset_path_map,
        )
        db_question.image_paths = parsed_img_paths
        db_question.content_tikz_assets = parsed_content_tikz_assets
        db_question.answer_tikz_assets = parsed_answer_tikz_assets

        # 关联关系只走 question_relations 单轨：更新题目本身不再碰
        # association_group_id，该列已由迁移折叠为 legacy_group 关系行后清空。

        # Update or create active QuestionCurriculum mapping
        active_version = get_active_version_code()
        curriculum_map = db.query(QuestionCurriculum).filter(
            QuestionCurriculum.question_id == db_question.id,
            QuestionCurriculum.version_code == active_version
        ).first()
        if not curriculum_map:
            curriculum_map = QuestionCurriculum(
                question_id=db_question.id,
                version_code=active_version
            )
            db.add(curriculum_map)
        curriculum_map.compulsory = category_compulsory
        curriculum_map.chapter = category_chapter
        curriculum_map.knowledge = category_knowledge
        if parsed_sub_knowledge is not None:
            curriculum_map.sub_knowledge = parsed_sub_knowledge

        db.flush()
        try:
            question_fingerprint = build_prepared_question_fingerprint(
                content=content,
                answer_markdown=answer_markdown,
                question_type=question_type,
                image_paths=parsed_img_paths,
                content_tikz_assets=parsed_content_tikz_assets,
                answer_tikz_assets=parsed_answer_tikz_assets,
                tikz_code=parsed_tikz_code,
                tikz_reference_image_path=parsed_tikz_reference_image_path,
                review=review,
            )
        except Exception as fingerprint_exc:
            question_fingerprint = None
            duplicate_warning = "题目已更新，但本次查重指纹未生成；后台将尝试补建，失败时下次启动继续。"
            db.query(StoredQuestionFingerprint).filter(
                StoredQuestionFingerprint.question_id == db_question.id
            ).delete(synchronize_session=False)
            print(
                "[Duplicate Index] Update fingerprint failed open "
                f"(type={type(fingerprint_exc).__name__}); background backfill will retry."
            )
        if duplicate_snapshot_hash and question_fingerprint is not None:
            if duplicate_snapshot_hash != question_fingerprint.content_revision_hash:
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=[],
                    message="题目在查重后已发生变化，请重新查重后保存。",
                    code="duplicate_snapshot_stale",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
            exact_ids = exact_duplicate_ids(
                db,
                question_fingerprint,
                exclude_id=db_question.id,
            )
            if exact_ids and duplicate_override != "independent":
                response = duplicate_review_required_response(
                    db,
                    fingerprint=question_fingerprint,
                    question_ids=exact_ids,
                    message="更新后的题目与题库已有题相同，请核对后再决定。",
                )
                db.rollback()
                rollback_question_asset_promotions(asset_promotions)
                return response
        if question_fingerprint is not None:
            upsert_question_fingerprint(db, db_question, question_fingerprint)

        sync_question_tags(
            db,
            db_question,
            chapter_raw=tag_chapter_codes,
            thought_raw=tag_thought_codes,
            function_raw=tag_function_code,
            custom_raw=tag_custom_tags,
            strategy_raw=tag_strategy_codes,
            micro_raw=tag_micro_methods,
            legacy=(category_compulsory, category_chapter, category_knowledge),
            legacy_prev=legacy_category_prev,
        )
        _record_free_text_tag_memory(tag_custom_tags, tag_micro_methods)
        db.commit()
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"更新题目失败: {str(e)}")

    # The question is already durably updated at this point.  Best-effort
    # cleanup and response assembly must not turn success into a false failure.
    try:
        delete_unreferenced_question_assets(db, removed_images)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit update cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )
    schedule_database_export(background_tasks, operation="update_question")
    if duplicate_warning:
        schedule_question_fingerprint_retry(
            background_tasks,
            question_id=question_id,
            operation="update_question",
        )
    response = committed_question_response(
        db,
        db_question,
        question_id,
        operation="update_question",
    )
    if duplicate_warning:
        response["warning"] = duplicate_warning
    if asset_path_map:
        response["asset_path_map"] = asset_path_map
    return response

@app.post("/api/questions/{question_id}/figure_align")
def update_question_figure_align(
    question_id: int,
    background_tasks: BackgroundTasks,
    figure_align: str = Form("right"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    if figure_align not in FIGURE_ALIGN_VALUES:
        figure_align = "right"
    db_question.figure_align = figure_align
    db_question.figure_align_custom = True
    db.commit()
    db.refresh(db_question)
    schedule_database_export(background_tasks, operation="update_figure_align")
    return {
        "status": "success",
        "question_id": question_id,
        "figure_align": figure_align,
        "figure_align_custom": True,
    }


@app.post("/api/questions/{question_id}/figure_layout")
def update_question_figure_layout(
    question_id: int,
    background_tasks: BackgroundTasks,
    figure_align: str = Form(...),
    figure_size: str = Form(...),
    db: Session = Depends(get_db),
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    if figure_align not in FIGURE_ALIGN_VALUES:
        raise HTTPException(status_code=400, detail="无效的插图排版位置")
    figure_size = str(figure_size or "").strip()
    if figure_size not in FIGURE_SIZE_VALUES:
        raise HTTPException(status_code=400, detail="无效的插图尺寸")
    db_question.figure_align = figure_align
    db_question.figure_align_custom = True
    db_question.figure_size = figure_size
    db.commit()
    db.refresh(db_question)
    schedule_database_export(background_tasks, operation="update_figure_layout")
    return {
        "status": "success",
        "question_id": question_id,
        "figure_align": db_question.figure_align,
        "figure_align_custom": True,
        "figure_size": normalize_figure_size(db_question.figure_size),
    }

# 历史说明：此处曾有基于 questions.association_group_id 的旧题组三件套路由
# （GET/POST/DELETE /api/questions/{id}/associated 与 POST /associate）。
# 旧题组已在 v12 迁移中折叠为 question_relations 的 legacy_group 行并清空源列，
# 继续保留读写口子会导致双轨不同步，已整体删除；读写统一走
# GET/POST /api/questions/{id}/relations 与 DELETE /api/relations/{id}。


def _relation_type_items(db: Session) -> list[dict]:
    """Built-in relation types followed by any custom ``custom:`` codes in use."""

    items = [
        {"code": code, "label": label, "builtin": True}
        for code, label in BUILTIN_RELATION_TYPES
    ]
    builtin_codes = {code for code, _ in BUILTIN_RELATION_TYPES}
    custom_rows = (
        db.query(
            QuestionRelation.relation_code,
            QuestionRelation.relation_label,
        )
        .filter(QuestionRelation.relation_code.like(f"{RELATION_CUSTOM_PREFIX}%"))
        .distinct()
        .all()
    )
    resolved: dict[str, str] = {}
    for code, label in custom_rows:
        code = str(code or "")
        if not code or code in builtin_codes:
            continue
        clean_label = str(label or "").strip()
        if code not in resolved or (clean_label and not resolved[code]):
            resolved[code] = clean_label or code
    for code, label in resolved.items():
        items.append({"code": code, "label": label, "builtin": False})
    return items


@app.get("/api/relation-types")
def list_relation_types(db: Session = Depends(get_db)):
    """List selectable relation types (built-ins first, then custom codes)."""

    return {"items": _relation_type_items(db)}


@app.get("/api/questions/{question_id}/relations")
def get_question_relations(question_id: int, db: Session = Depends(get_db)):
    """Return every typed relation touching this question (both directions)."""

    question = db.query(Question).filter(Question.id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="未找到题目")

    rows = (
        db.query(QuestionRelation)
        .filter(
            or_(
                QuestionRelation.question_id == question_id,
                QuestionRelation.related_id == question_id,
            )
        )
        .order_by(QuestionRelation.created_at.asc(), QuestionRelation.id.asc())
        .all()
    )
    other_ids = [
        row.related_id if row.question_id == question_id else row.question_id
        for row in rows
    ]
    others = (
        {
            item.id: item
            for item in db.query(Question).filter(Question.id.in_(other_ids)).all()
        }
        if other_ids
        else {}
    )
    seq_map = get_seq_mapping(db, other_ids)

    items = []
    for row in rows:
        other_id = row.related_id if row.question_id == question_id else row.question_id
        other = others.get(other_id)
        if other is None:
            # A dangling relation (question removed elsewhere) is skipped rather
            # than failing the whole listing.
            continue
        items.append(
            {
                "relation_id": row.id,
                "relation_code": row.relation_code,
                "relation_label": row.relation_label or "",
                "note": row.note or "",
                "created_at": (
                    row.created_at.isoformat() if row.created_at else None
                ),
                "other": {
                    "id": other.id,
                    "seq_num": seq_map.get(other.id),
                    "question_type": other.question_type or "",
                    "content": other.content or "",
                },
            }
        )
    return {"items": items}


@app.post("/api/questions/{question_id}/relations")
def create_question_relation(
    background_tasks: BackgroundTasks,
    question_id: int,
    target_id: int = Form(...),
    relation_code: str = Form(...),
    relation_label: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create (or idempotently update) one typed, undirected relation row."""

    from sqlalchemy.exc import IntegrityError

    # Whitelist first: built-ins only, plus the custom: escape hatch that the
    # frontend always uses for user-invented topics.
    code = validate_relation_code(relation_code)
    if int(question_id) == int(target_id):
        raise HTTPException(status_code=400, detail="不能与自身建立关联。")

    source = db.query(Question).filter(Question.id == question_id).first()
    target = db.query(Question).filter(Question.id == target_id).first()
    if not source or not target:
        raise HTTPException(status_code=404, detail="未找到对应题目")

    # Normalise the pair so the CHECK (question_id < related_id) always holds.
    a, b = sorted((int(question_id), int(target_id)))
    label = str(relation_label or "").strip()
    note_val = str(note or "")

    def _apply_update(row: QuestionRelation) -> None:
        # Apply the same "only overwrite when supplied" rule to both fields so a
        # partial edit cannot silently erase the note that is already stored.
        if label:
            row.relation_label = label
        if note_val:
            row.note = note_val

    try:
        existing = (
            db.query(QuestionRelation)
            .filter(
                QuestionRelation.question_id == a,
                QuestionRelation.related_id == b,
                QuestionRelation.relation_code == code,
            )
            .first()
        )
        if existing is not None:
            _apply_update(existing)
            relation, created = existing, False
        else:
            relation = QuestionRelation(
                question_id=a,
                related_id=b,
                relation_code=code,
                relation_label=label,
                note=note_val,
            )
            db.add(relation)
            created = True
        db.commit()
        db.refresh(relation)
    except IntegrityError:
        # A concurrent duplicate slipped past the pre-check: fall back to the
        # idempotent update instead of surfacing a 409.
        db.rollback()
        existing = (
            db.query(QuestionRelation)
            .filter(
                QuestionRelation.question_id == a,
                QuestionRelation.related_id == b,
                QuestionRelation.relation_code == code,
            )
            .first()
        )
        if existing is None:
            raise HTTPException(status_code=400, detail="建立关联失败")
        _apply_update(existing)
        db.commit()
        db.refresh(existing)
        relation, created = existing, False
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"建立关联失败: {str(exc)}")

    schedule_database_export(background_tasks, operation="create_question_relation")
    return {"status": "success", "relation_id": relation.id, "created": created}


@app.delete("/api/relations/{relation_id}")
def delete_question_relation(
    relation_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Delete a single typed relation row.  Never touches the questions table."""

    relation = (
        db.query(QuestionRelation)
        .filter(QuestionRelation.id == relation_id)
        .first()
    )
    if not relation:
        raise HTTPException(status_code=404, detail="未找到关联关系")
    try:
        db.delete(relation)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"删除关联失败: {str(exc)}")

    schedule_database_export(background_tasks, operation="delete_question_relation")
    return {"status": "success"}


@app.delete("/api/questions/{question_id}")
@serialize_asset_lifecycle
def delete_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    image_paths_to_check = list(db_question.image_paths)
    try:
        from sqlalchemy import func

        affected_paper_ids = [
            paper_id
            for (paper_id,) in db.query(PaperQuestion.paper_id)
            .filter(PaperQuestion.question_id == question_id)
            .distinct()
            .all()
        ]
        # 专题影响回传：级联删题目前，先记录受影响的专题，删后重排并明示
        # （topic_items.question_id 是 ON DELETE CASCADE，DB 级一致）。
        affected_topic_ids = [
            int(topic_id)
            for (topic_id,) in db.query(TopicItem.topic_id)
            .filter(TopicItem.question_id == question_id)
            .distinct()
            .all()
        ]
        if affected_paper_ids:
            remaining_scores = dict(
                db.query(
                    PaperQuestion.paper_id,
                    func.coalesce(func.sum(PaperQuestion.score), 0),
                )
                .filter(
                    PaperQuestion.paper_id.in_(affected_paper_ids),
                    PaperQuestion.question_id != question_id,
                )
                .group_by(PaperQuestion.paper_id)
                .all()
            )
            for paper in db.query(Paper).filter(
                Paper.id.in_(affected_paper_ids)
            ):
                paper.total_score = int(remaining_scores.get(paper.id, 0))
        db.delete(db_question)
        db.commit()
        # 受影响专题的 items 已被 DB 级联删除，把 position 重排为 0 基连续。
        if affected_topic_ids:
            from mathbank.topics import compact_item_positions

            for topic_id in affected_topic_ids:
                compact_item_positions(db, topic_id)
            db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"删除题目失败: {str(e)}")

    # The database delete is complete.  Image cleanup is intentionally
    # best-effort so a locked/missing file cannot make the client believe the
    # question still exists and submit a duplicate delete.
    try:
        delete_unreferenced_question_assets(db, image_paths_to_check)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit delete cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )

    # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
    schedule_database_export(background_tasks, operation="delete_question")

    return {
        "status": "success",
        "message": "题目删除成功",
        # 前端 toast「该题已从 N 个专题移除」
        "removed_from_topics": affected_topic_ids,
    }

# ----------------- Category Hierarchy Autocomplete API -----------------

# Backward-compatible names; authoritative data lives in JSON resources.
RENJIAO_A_CURRICULUM = load_curriculum("A")
RENJIAO_B_CURRICULUM = load_curriculum("B")
SUJIAO_CURRICULUM = load_curriculum("S")
HUJIAO_CURRICULUM = load_curriculum("H")

METADATA_FILE = str(DATA_BACKUP_DIR / ("custom_metadata_test.json" if IS_TESTING else "custom_metadata.json"))
METADATA_CACHE = {}

def get_current_curriculum():
    return METADATA_CACHE.get("curriculum", RENJIAO_A_CURRICULUM)

def load_or_init_metadata():
    global METADATA_CACHE
    default_metadata = build_default_metadata("A")
    
    # Ensure backup directory exists
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Verify schema
                if isinstance(loaded, dict) and "question_types" in loaded and "difficulties" in loaded and "curriculum" in loaded:
                    # Self-heal metadata file (e.g. add 常规题, update simplified book names)
                    modified = False
                    # 人教A版固定三级难度：强制统一为 easy / medium / hard。
                    default_difficulties = build_default_metadata("A")["difficulties"]
                    current_values = [d.get("value") for d in loaded.get("difficulties", [])]
                    default_values = [d["value"] for d in default_difficulties]
                    if current_values != default_values:
                        loaded["difficulties"] = default_difficulties
                        modified = True
                        
                    curriculum = loaded.get("curriculum", {})
                    mappings = {
                        "选择性必修一": "选修一",
                        "选择性必修二": "选修二",
                        "选择性必修三": "选修三",
                        "必修第一册": "必修一",
                        "必修第二册": "必修二",
                        "必修第三册": "必修三",
                        "必修第四册": "必修四",
                    }
                    new_curriculum = {}
                    for comp, chapters in curriculum.items():
                        mapped_comp = mappings.get(comp, comp)
                        if mapped_comp != comp:
                            modified = True
                        new_curriculum[mapped_comp] = chapters
                    if modified:
                        loaded["curriculum"] = new_curriculum
                        try:
                            write_private_text_atomic(
                                METADATA_FILE,
                                json.dumps(loaded, ensure_ascii=False, indent=2),
                            )
                            print(f"[Metadata Self-Heal] Upgraded {METADATA_FILE} with simplified book names and three-level difficulties.")
                        except Exception as e:
                            print(f"[Metadata Self-Heal Error] Failed to write updated metadata: {e}")
                    
                    METADATA_CACHE = loaded
                    print(f"[Metadata] Loaded custom metadata from {METADATA_FILE}")
                    return
        except Exception as e:
            print(f"[Metadata Warning] Error loading {METADATA_FILE}: {e}. Overwriting with default.")
            
    # Self-heal / initialize
    try:
        write_private_text_atomic(
            METADATA_FILE,
            json.dumps(default_metadata, ensure_ascii=False, indent=2),
        )
        print(f"[Metadata] Initialized default metadata at {METADATA_FILE}")
    except Exception as e:
        print(f"[Metadata Error] Could not write default metadata: {e}")
        
    METADATA_CACHE = default_metadata

# Load metadata on startup
load_or_init_metadata()

def get_active_version_code() -> str:
    """返回当前启用的教材大纲版本码。

    本项目仅支持人教A版（2019）。四级章节树只存在 A2019.json，标签体系
    （mathbank/tags.py）也硬编码 A 版，因此这里固定返回 "A"，不再按章节名
    启发式猜测 B/S/H 版本（历史遗留的多版本启发式已废弃，避免在元数据里
    出现"第一章"这类关键字时误判成 B 版、进而读取不存在的 B2019.json）。
    """

    return "A"

@app.get("/api/config/metadata")
def get_metadata_config():
    return METADATA_CACHE


@app.get("/api/config/sub-knowledge")
def get_sub_knowledge_config(compulsory: str = None, chapter: str = None,
                             knowledge: str = None, db: Session = Depends(get_db)):
    version = get_active_version_code()
    filters = {"compulsory": compulsory, "chapter": chapter, "knowledge": knowledge}
    paths = set()
    for comp, chapters in get_current_curriculum().items():
        if not isinstance(chapters, dict):
            continue
        for chap, leaves in chapters.items():
            if not isinstance(leaves, list):
                continue
            for leaf in leaves:
                if isinstance(leaf, str) and leaf.strip():
                    paths.add((comp, chap, leaf))
    existing = set(db.query(QuestionSubKnowledge.compulsory, QuestionSubKnowledge.chapter,
                            QuestionSubKnowledge.knowledge).join(QuestionCurriculum).filter(
                                QuestionCurriculum.version_code == version).all())
    paths.update(existing)
    def matching(path):
        return all(not value or path[index] == value for index, value in enumerate(filters.values()))
    def as_dict(path):
        return dict(zip(("compulsory", "chapter", "knowledge"), path))
    return {
        "version_code": version,
        "sub_knowledge": [as_dict(path) for path in sorted(paths) if matching(path)],
        "existing_sub_knowledge": [as_dict(path) for path in sorted(existing) if matching(path)],
    }

@app.get("/api/config/curriculum-presets/{version}")
def get_curriculum_preset_config(version: str):
    try:
        return get_curriculum_preset(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

def route_chapter(comp: str, chap: str, know: str, target: str) -> tuple[str, str, str]:
    """跨大纲版本智能章节与小节路由翻译算法，返回 (new_compulsory, new_chapter, new_knowledge)"""
    combined = f"{comp} {chap} {know}"
    new_comp, new_chap = "", ""
    if target == "A":
        if "集合" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "2. 一元二次函数、方程和不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "4. 指数函数与对数函数"
        elif "三角函数" in combined or "三角恒等" in combined: new_comp, new_chap = "必修一", "5. 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "3. 函数的概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "复数" in combined: new_comp, new_chap = "必修二", "7. 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "8. 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "1. 空间向量与立体几何"
        elif "直线" in combined or "圆的方程" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "3. 圆锥曲线的方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "数列" in combined: new_comp, new_chap = "选修二", "4. 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "5. 一元函数的导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修三", "6. 计数原理"
        elif "概率" in combined or "随机变量" in combined or "分布" in combined: new_comp, new_chap = "选修三", "7. 随机变量及其分布"
        elif "统计" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修三", "8. 成对数据的统计分析"
        else: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
    elif target == "B":
        if "集合" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第二章 等式与不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修二", "第四章 指数函数、对数函数与幂函数"
        elif "三角函数" in combined: new_comp, new_chap = "必修三", "第七章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第三章 函数"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修四", "第九章 解三角形"
        elif "数量积" in combined or "三角恒等" in combined: new_comp, new_chap = "必修三", "第八章 向量的数量积与三角恒等变换"
        elif "平面向量" in combined: new_comp, new_chap = "必修二", "第六章 平面向量初步"
        elif "复数" in combined: new_comp, new_chap = "必修四", "第十章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修四", "第十一章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第一章 空间向量与立体几何"
        elif "直线" in combined or "圆" in combined or "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "数列" in combined: new_comp, new_chap = "选修三", "第五章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修三", "第六章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第三章 排列、组合与二项式定理"
        elif "随机变量" in combined or "条件概率" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第四章 概率与统计"
        elif "统计" in combined or "概率" in combined: new_comp, new_chap = "必修二", "第五章 统计与概率"
        else: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
    elif target == "S":
        if "集合" in combined: new_comp, new_chap = "必修一", "第1章 集合"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第2章 常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第3章 不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "第4章 指数与对数"
        elif "三角函数" in combined: new_comp, new_chap = "必修一", "第7章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第5章 函数概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "第11章 解三角形"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "第9章 平面向量"
        elif "三角恒等" in combined: new_comp, new_chap = "必修二", "第10章 三角恒等变换"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第12章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "第13章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修二", "第6章 空间向量与立体几何"
        elif "直线" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "第2章 圆与方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第3章 圆锥曲线与方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "数列" in combined: new_comp, new_chap = "选修一", "第4章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修一", "第5章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第7章 计数原理"
        elif "随机变量" in combined or "条件概率" in combined: new_comp, new_chap = "选修二", "第8章 概率"
        elif "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第9章 统计"
        elif "统计" in combined: new_comp, new_chap = "必修二", "第14章 统计"
        elif "概率" in combined: new_comp, new_chap = "必修二", "第15章 概率"
        else: new_comp, new_chap = "必修一", "第1章 集合"
    elif target == "H":
        if "集合与逻辑" in combined or ("集合" in combined and "选修" not in comp): new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第 2 章 等式与不等式"
        elif "幂、指数" in combined or "指数与对数" in combined or ("指数" in combined and "函数" not in combined) or ("对数" in combined and "函数" not in combined): new_comp, new_chap = "必修一", "第 3 章 幂、指数与对数"
        elif "幂函数" in combined or "指数函数" in combined or "对数函数" in combined: new_comp, new_chap = "必修一", "第 4 章 幂函数、指数函数与对数函数"
        elif "反函数" in combined or "函数的概念" in combined or ("函数" in combined and "三角" not in combined and "导数" not in combined and "选修" not in comp and "必修二" not in comp and "必修三" not in comp): new_comp, new_chap = "必修一", "第 5 章 函数的概念、性质及应用"
        elif "解三角形" in combined or "正弦定理" in combined or "余弦定理" in combined or "常用三角公式" in combined or ("三角" in combined and "函数" not in combined): new_comp, new_chap = "必修二", "第 6 章 三角"
        elif "三角函数" in combined: new_comp, new_chap = "必修二", "第 7 章 三角函数"
        elif "平面向量" in combined or ("向量" in combined and "空间" not in combined): new_comp, new_chap = "必修二", "第 8 章 平面向量"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第 9 章 复数"
        elif "空间直线" in combined or "空间点" in combined or ("立体几何" in combined and "空间向量" not in combined and "简单几何体" not in combined and "球" not in combined and "柱体" not in combined and "锥体" not in combined): new_comp, new_chap = "必修三", "第 10 章 空间直线与平面"
        elif "简单几何体" in combined or "柱体" in combined or "锥体" in combined or "多面体" in combined or "球" in combined: new_comp, new_chap = "必修三", "第 11 章 简单几何体"
        elif "古典概" in combined or "随机现象" in combined or ("概率" in combined and "条件概率" not in combined and "随机变量" not in combined and "分布" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 12 章 概率初步"
        elif "总体与样本" in combined or "抽样" in combined or "统计图表" in combined or ("统计" in combined and "成对" not in combined and "回归" not in combined and "列联表" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 13 章 统计"
        elif "红绿灯" in combined or "优惠券" in combined or "车辆转弯" in combined or "雨中行" in combined or "出租车" in combined or "家具" in combined or "登山" in combined or "包装彩带" in combined or "削菠萝" in combined or "高度测量" in combined or "外卖" in combined or "必修四" in comp: new_comp, new_chap = "必修四", "第 1 部分 数学建模活动案例"
        elif "平面直角坐标系中的直线" in combined or "直线与方程" in combined or ("直线" in combined and "空间" not in combined and "圆锥曲线" not in combined): new_comp, new_chap = "选修一", "第 1 章 平面直角坐标系中的直线"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined or ("圆" in combined and "圆锥曲线" in combined): new_comp, new_chap = "选修一", "第 2 章 圆锥曲线"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第 3 章 空间向量及其应用"
        elif "数列" in combined or "等差数列" in combined or "等比数列" in combined or "数学归纳法" in combined: new_comp, new_chap = "选修一", "第 4 章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "第 5 章 导数及其应用"
        elif "计数原理" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第 6 章 计数原理"
        elif "条件概率" in combined or "随机变量" in combined or "常用分布" in combined or "二项分布" in combined or "正态分布" in combined: new_comp, new_chap = "选修二", "第 7 章 概率初步（续）"
        elif "成对数据" in combined or "线性回归" in combined or "列联表" in combined or "独立性检验" in combined or "回归" in combined: new_comp, new_chap = "选修二", "第 8 章 成对数据的统计分析"
        elif "刹车距离" in combined or "易拉罐" in combined or "珠穆朗玛峰" in combined or "水葫芦" in combined or "铅球" in combined or "电梯调度" in combined or "存款计划" in combined or "民生巨变" in combined or "教室里的照明" in combined or "选修三" in comp: new_comp, new_chap = "选修三", "第 1 部分 数学建模活动案例"
        else: new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"

    active_v = get_active_version_code()
    if target == active_v:
        c_tree = METADATA_CACHE.get("curriculum", {})
    else:
        try:
            c_tree = load_curriculum(target)
        except ValueError:
            c_tree = {}
    
    valid_knows = c_tree.get(new_comp, {}).get(new_chap, [])
    new_know = know if know in valid_knows else ""
    return new_comp, new_chap, new_know

@app.post("/api/config/metadata")
def save_metadata_config(
    payload: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    global METADATA_CACHE
    # Validation
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求 Payload 格式错误")
        
    for field in ["question_types", "difficulties", "curriculum"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"元数据配置缺少核心字段: '{field}'")
            
    # Simple validate question_types and difficulties lists
    if not isinstance(payload["question_types"], list) or not isinstance(payload["difficulties"], list):
        raise HTTPException(status_code=400, detail="question_types 或 difficulties 必须是数组列表")
        
    if not isinstance(payload["curriculum"], dict):
        raise HTTPException(status_code=400, detail="curriculum 必须是字典对象")
        
    old_metadata = METADATA_CACHE
    metadata_path = Path(METADATA_FILE)
    old_file_contents = (
        metadata_path.read_text(encoding="utf-8") if metadata_path.exists() else None
    )
    file_replaced = False
    transaction_committed = False

    # Update the curriculum mirror and metadata as one compensated operation.
    try:
        # 本项目仅支持人教A版（2019），源与目标版本码均固定为 A。此处曾保留
        # 一套「按章节名关键字猜测 B/S/H 版本」的历史启发式，它会与单教材版本
        # 定案冲突：只要元数据里出现「第一章」一类字样就误判为 B 版，进而触发
        # 下面的全量 UPDATE questions 覆盖用户已确认的分类。定案后该启发式
        # 已废弃（另见 get_active_version_code），故不再猜测版本。
        source_version = get_active_version_code()
        target_version = get_active_version_code()

        # Incremental migration if curriculum version shifts
        if source_version != target_version:
            # Check and run incremental migration for all questions that do not have classifications for target_version
            all_questions = db.query(Question).all()
            for q in all_questions:
                target_map = db.query(QuestionCurriculum).filter(
                    QuestionCurriculum.question_id == q.id,
                    QuestionCurriculum.version_code == target_version
                ).first()
                if target_map is None:
                    target_map = QuestionCurriculum(question_id=q.id, version_code=target_version)
                    db.add(target_map)
                    source_map = db.query(QuestionCurriculum).filter(
                        QuestionCurriculum.question_id == q.id,
                        QuestionCurriculum.version_code == source_version
                    ).first()
                    if source_map and source_map.compulsory:
                        new_comp, new_chap, new_know = route_chapter(
                            source_map.compulsory, source_map.chapter, source_map.knowledge, target_version
                        )
                        target_map.compulsory = new_comp
                        target_map.chapter = new_chap
                        target_map.knowledge = new_know
        # Batch update main questions table categories with target version values
        from sqlalchemy import text
        db.flush()
        db.execute(text("""
            UPDATE questions 
            SET category_compulsory = COALESCE((SELECT compulsory FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_chapter = COALESCE((SELECT chapter FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_knowledge = COALESCE((SELECT knowledge FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), '')
        """), {"v": target_version})

        write_private_text_atomic(
            metadata_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        file_replaced = True
        db.commit()
        transaction_committed = True
    except Exception as e:
        db.rollback()
        if not transaction_committed:
            METADATA_CACHE = old_metadata
        if file_replaced and not transaction_committed:
            try:
                if old_file_contents is None:
                    metadata_path.unlink(missing_ok=True)
                else:
                    write_private_text_atomic(metadata_path, old_file_contents)
            except OSError as restore_error:
                print(
                    "[Metadata] Failed to restore metadata after DB rollback "
                    f"(type={type(restore_error).__name__})."
                )
        raise HTTPException(status_code=500, detail=f"保存元数据失败: {str(e)}")

    # Everything below is post-commit and must not change the successful save
    # into an error response or compensate already-durable database changes.
    METADATA_CACHE = payload
    print(
        f"[Metadata] Saved new custom metadata to {METADATA_FILE} "
        f"(Detected version: {target_version})"
    )
    schedule_database_export(background_tasks, operation="save_metadata")
    return {"status": "success", "message": "元数据配置保存成功！"}

# ----------------- DB Statistics API -----------------

def _chapter_statistics(db: Session) -> tuple[dict, list, list]:
    """Roll multi-value chapter tags up into 册 / 章 / 节 statistics.

    The classification system stores a question's curriculum location as
    multi-value ``question_tags`` rows (``dim='chapter'``) whose codes point into
    the four-level tree (册 / 章 / 节 / 小节).  This helper turns those rows into
    the shapes the statistics panel needs:

      * ``compulsory_chapter_counts`` -- ``{册名: {章名: count}}`` (backward
        compatible, display names use the same format as ``tags.js``:
        book ``name`` and ``第N章 <name>``).
      * ``chapter_stats`` -- one entry per (册, 章) carrying the codes plus a
        per-section breakdown, so the panel can drill down without re-deriving
        codes on the frontend.
      * ``book_catalog`` -- every book of the active curriculum tree in
        teaching order, zero-count books included, each with its full chapter
        list.  The statistics panel builds its stage dropdown from this so
        stages without any question yet stay visible and selectable.

    Compatibility: a question with no chapter tag falls back to its legacy
    ``category_compulsory`` / ``category_chapter`` fields, and a question with
    neither lands in the ``未分类`` bucket.  ``migrate_db.py`` backfills tags
    from the legacy fields so existing data keeps resolving correctly.
    """

    index = curriculum_index()

    def _node(code: str):
        return index.get(str(code or "").strip())

    # question_id -> chapter tag codes (any depth)
    tag_codes: dict[int, list[str]] = {}
    for qid, code in db.query(QuestionTag.question_id, QuestionTag.code).filter(
        QuestionTag.dim == "chapter"
    ).all():
        tag_codes.setdefault(qid, []).append(code)

    chapter_buckets: dict[tuple[str, str], int] = {}
    section_question_ids: dict[str, set] = {}
    for qid, codes in tag_codes.items():
        chapters: set[tuple[str, str]] = set()
        for code in codes:
            node = _node(code)
            if not node:
                continue
            if node.get("level") == "book":
                # 册级标签（只选了册次）：无章号，归入该册"未分章节"桶
                chapters.add((node.get("book", ""), "__BOOK__"))
                continue
            book_code = node.get("book", "")
            chapter_code = f"{book_code}-C{node.get('chapter_no')}"
            if chapter_code in index:
                chapters.add((book_code, chapter_code))
            if node.get("level") in ("section", "subsection"):
                section_code = f"{chapter_code}-S{node.get('section_no')}"
                if section_code in index:
                    section_question_ids.setdefault(section_code, set()).add(qid)
        for pair in chapters:
            chapter_buckets[pair] = chapter_buckets.get(pair, 0) + 1

    # legacy fallback for questions without any chapter tag
    legacy_buckets: dict[tuple[str, str], int] = {}
    for qid, comp, chap in db.query(
        Question.id, Question.category_compulsory, Question.category_chapter
    ).all():
        if qid in tag_codes:
            continue
        comp_name = (comp or "").strip() or "未分类"
        chap_name = (chap or "").strip() or "未分章节"
        legacy_buckets[(comp_name, chap_name)] = legacy_buckets.get((comp_name, chap_name), 0) + 1

    book_order = {"B1": 1, "B2": 2, "B3": 3, "X1": 4, "X2": 5, "X3": 6}

    def _book_sort_key(code: str):
        return (book_order.get(code, 99), code)

    def _chapter_sort_key(code: str):
        node = _node(code)
        return ((node or {}).get("chapter_no") or 999, code)

    compulsory_chapter_counts: dict[str, dict[str, int]] = {}
    chapter_stats: list[dict] = []

    grouped: dict[str, dict[str, int]] = {}
    for (book_code, chapter_code), count in chapter_buckets.items():
        inner = grouped.setdefault(book_code, {})
        inner[chapter_code] = inner.get(chapter_code, 0) + count

    for book_code in sorted(grouped, key=_book_sort_key):
        book_node = _node(book_code)
        book_name = (book_node or {}).get("name") or book_code
        for chapter_code in sorted(grouped[book_code], key=_chapter_sort_key):
            if chapter_code == "__BOOK__":
                chapter_name = "未分章节"
            else:
                ch_node = _node(chapter_code)
                chapter_name = (
                    f"第{ch_node.get('chapter_no')}章 {ch_node.get('name')}"
                    if ch_node
                    else chapter_code
                )
            count = grouped[book_code][chapter_code]
            compulsory_chapter_counts.setdefault(book_name, {})[chapter_name] = count

            sections: list[dict] = []
            for section_code, qids in section_question_ids.items():
                if not section_code.startswith(chapter_code + "-S"):
                    continue
                sec_node = _node(section_code)
                sections.append(
                    {
                        "code": section_code,
                        "name": (sec_node or {}).get("name") or section_code,
                        "count": len(qids),
                    }
                )
            sections.sort(key=lambda item: item["code"])
            chapter_stats.append(
                {
                    "book_code": book_code,
                    "book_name": book_name,
                    "chapter_code": chapter_code,
                    "chapter_name": chapter_name,
                    "count": count,
                    "sections": sections,
                }
            )

    # append legacy-only buckets to the backward-compatible nested shape
    for (comp_name, chap_name), count in legacy_buckets.items():
        inner = compulsory_chapter_counts.setdefault(comp_name, {})
        inner[chap_name] = inner.get(chap_name, 0) + count

    # Full stage catalog: every book of the curriculum tree in teaching
    # order, zero-count books included, each with its complete chapter list
    # (zero-count chapters included) so the panel never hides a stage.
    book_catalog: list[dict] = []
    for book in load_curriculum_tree("A").get("books", []):
        book_code = book.get("code", "")
        chapters_out: list[dict] = []
        book_count = 0
        for chapter in book.get("chapters", []):
            chapter_code = chapter.get("code", "")
            chapter_count = grouped.get(book_code, {}).get(chapter_code, 0)
            book_count += chapter_count
            chapters_out.append(
                {
                    "chapter_code": chapter_code,
                    "chapter_name": f"第{chapter.get('no')}章 {chapter.get('name', '')}",
                    "count": chapter_count,
                }
            )
        book_level_count = grouped.get(book_code, {}).get("__BOOK__", 0)
        if book_level_count:
            book_count += book_level_count
            chapters_out.append(
                {
                    "chapter_code": "__BOOK__",
                    "chapter_name": "未分章节",
                    "count": book_level_count,
                }
            )
        book_catalog.append(
            {
                "book_code": book_code,
                "book_name": book.get("name", book_code),
                "count": book_count,
                "chapters": chapters_out,
            }
        )

    # Questions carrying neither chapter tags nor usable legacy fields stay
    # reachable through a trailing pseudo stage.
    untagged = legacy_buckets.get(("未分类", "未分章节"), 0)
    if untagged:
        book_catalog.append(
            {
                "book_code": "__UNTAGGED__",
                "book_name": "未分类",
                "count": untagged,
                "chapters": [],
            }
        )

    return compulsory_chapter_counts, chapter_stats, book_catalog


def _merge_case_insensitive_counts(
    counts: dict[str, int],
    preferred: "set[str] | frozenset[str] | None" = None,
) -> dict[str, int]:
    """Fold case-variant keys together, for reading/display only.

    Historic rows may carry a different casing than the current vocabulary and
    are deliberately left untouched.  Only the outgoing payload is folded, so
    ``典型题`` written as ``Typical``/``typical`` still shows up as one bar.
    The vocabulary spelling wins when both variants exist.
    """

    preferred = set(preferred or ())
    merged: dict[str, int] = {}
    canonical: dict[str, str] = {}
    for key, value in counts.items():
        fold = str(key).casefold()
        amount = int(value or 0)
        keeper = canonical.get(fold)
        if keeper is None:
            canonical[fold] = key
            merged[key] = amount
            continue
        merged[keeper] += amount
        if keeper not in preferred and key in preferred:
            total = merged.pop(keeper)
            canonical[fold] = key
            merged[key] = total
    return merged


@app.get("/api/stats")
def get_db_stats(db: Session = Depends(get_db)):
    try:
        total = db.query(Question).count()
        easy = db.query(Question).filter(Question.difficulty == "easy").count()
        medium = db.query(Question).filter(Question.difficulty == "medium").count()
        hard = db.query(Question).filter(Question.difficulty == "hard").count()
        
        # 册/章/节 统计：从多值标签系统（question_tags）读取，旧字段仅作兜底
        compulsory_chapter_counts, chapter_stats, book_catalog = _chapter_statistics(db)

        # 功能标签分布：词表取值先全部补 0，再用一次 group_by 覆盖真实计数。
        # 这样刚加入词表、库里还没人用的取值（如"典型题"）也会出现在统计里，
        # 而词表之外的历史 code（无 CHECK 约束，可能存在）同样不会丢。
        # 累加阶段原样保留大小写（含历史脏数据），不在此处归并。
        raw_function_tag_counts: dict[str, int] = {
            item["code"]: 0 for item in dimension_values("function")
        }
        for code, count in (
            db.query(QuestionTag.code, func.count(QuestionTag.question_id))
            .filter(QuestionTag.dim == "function")
            .group_by(QuestionTag.code)
            .all()
        ):
            key = str(code or "").strip()
            if not key:
                continue
            raw_function_tag_counts.setdefault(key, 0)
            raw_function_tag_counts[key] += int(count or 0)

        # 仅读取/展示环节做大小写归一（忽略大小写合并），库里的取值不动。
        function_tag_counts = _merge_case_insensitive_counts(
            raw_function_tag_counts,
            preferred={item["code"] for item in dimension_values("function")},
        )

        # Daily additions in local time (UTC+8)
        date_rows = db.query(Question.created_at).all()
        daily_adds = {}
        for (created_at,) in date_rows:
            if created_at:
                # Convert UTC to UTC+8 local time
                local_time = created_at + datetime.timedelta(hours=8)
                date_str = local_time.strftime("%Y-%m-%d")
                daily_adds[date_str] = daily_adds.get(date_str, 0) + 1

        return {
            "status": "success",
            "total_count": total,
            "easy_count": easy,
            "medium_count": medium,
            "hard_count": hard,
            "compulsory_chapter_counts": compulsory_chapter_counts,
            "chapter_stats": chapter_stats,
            "book_catalog": book_catalog,
            "function_tag_counts": function_tag_counts,
            "daily_adds": daily_adds
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"获取统计数据失败: {str(e)}"},
            status_code=500
        )

@app.get("/api/categories")
def list_categories(db: Session = Depends(get_db)):
    # Initialize with predefined curriculum
    hierarchy = {}
    for comp, chapters in get_current_curriculum().items():
        hierarchy[comp] = {}
        for chap, sections in chapters.items():
            hierarchy[comp][chap] = list(sections)
            
    # Also fetch any custom entries from DB
    results = db.query(
        Question.category_compulsory,
        Question.category_chapter,
        Question.category_knowledge
    ).distinct().all()
    
    for comp, chap, know in results:
        if not comp:
            continue
        if comp not in hierarchy:
            hierarchy[comp] = {}
        if not chap:
            continue
        if chap not in hierarchy[comp]:
            hierarchy[comp][chap] = []
        if know and know not in hierarchy[comp][chap]:
            hierarchy[comp][chap].append(know)
            
    return hierarchy

# ----------------- AI Auto-Classification API -----------------

def _merge_method_tag_hints(payload: dict, content: str) -> dict:
    """模型漏给方法类标签时，用规则线索补齐。

    AI 常常只答题型 / 难度 / 章节，把「宏观思想」「中观策略」留空。此时用
    题干里的方法关键词给出 1~3 条候选，让自动打标不至于整片空白。返回的内容
    仍只是**建议**：前端是 chips 勾选态，老师点一下就能取消，不会静默入库。
    """

    if not isinstance(payload, dict):
        return payload
    if payload.get("thought_codes") or payload.get("strategy_codes"):
        return payload
    hints = suggest_method_tags(content)
    if hints.get("thought_codes"):
        payload["thought_codes"] = hints["thought_codes"]
    if hints.get("strategy_codes"):
        payload["strategy_codes"] = hints["strategy_codes"]
    return payload


@app.post("/api/ai/classify")
def ai_classify(content: str = Form(...)):
    classify_model = (
        os.getenv("PREFER_CLASSIFY_MODEL") 
        or os.getenv("DEEPSEEK_CLASSIFY_MODEL") 
        or os.getenv("PREFER_PARSE_MODEL") 
        or "deepseek-flash"
    )
    
    provider = resolve_text_provider(classify_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法自动智能分类！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    # 必选项「教材章节」的候选节点：章级全量 + 规则层检索出的节/小节级 top-N
    chapter_options = []
    try:
        tree = load_curriculum_tree("A")
        for book in tree.get("books", []):
            for chapter in book.get("chapters", []):
                chapter_options.append((chapter.get("code", ""), chapter.get("path", "")))
    except Exception:
        chapter_options = []

    try:
        section_candidates = suggest_chapter_candidates(content, top_n=8)
    except Exception:
        section_candidates = []
    # 章级全量在前，节级候选在后，按 code 去重并保持顺序
    merged_options = list(chapter_options)
    seen_codes = {code for code, _ in merged_options}
    for code, path in section_candidates:
        if code not in seen_codes:
            merged_options.append((code, path))
            seen_codes.add(code)

    try:
        difficulty_prior, _prior_evidence = estimate_difficulty_prior(content)
    except Exception:
        difficulty_prior = ""

    try:
        system_instructions = build_classification_system_prompt(
            merged_options,
            difficulty_prior=difficulty_prior,
            section_candidates=section_candidates,
        )
        def parse_model_json(message: str) -> dict:
            text = (message or "").strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("AI 返回的不是 JSON 对象")
            return parsed

        def call_model(repair_hint: str = "") -> dict:
            messages = [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": f"题目内容:\n{content}"},
            ]
            if repair_hint:
                messages.append({"role": "user", "content": repair_hint})
            payload = {
                "model": model_name,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": 1024,
            }
            payload = apply_model_thinking_policy(
                payload,
                provider=provider,
                task="classify",
            )
            response = post_chat_completion(
                provider,
                payload,
                timeout=30,
                provider_name=provider_name,
            )
            raw = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            return parse_model_json(raw)

        try:
            try:
                result = call_model()
            except ValueError:
                # JSON 解析失败：带着修复提示重试一次
                result = call_model(
                    "你上一次的输出不是合法 JSON。请只输出一个 JSON 对象，"
                    "包含 chapter_code、question_type、difficulty、reason 四个 key，"
                    "不要任何 Markdown 标记或解释文字。"
                )
        except Exception as exc:
            # 全部失败：降级为纯规则结果，不再抛 500
            fallback = classify_with_rules(content, {})
            return {
                "status": "partial",
                "message": f"AI 智能分类失败（{exc}），已回退到规则层结果，请人工核对。",
                **_merge_method_tag_hints(fallback, content),
            }

        # 题型 / 难度 / 章节统一由规则层与模型输出融合
        fused = classify_with_rules(content, result)
        _merge_method_tag_hints(fused, content)
        return {"status": "success", **fused}

    except Exception as e:
        try:
            degraded = classify_with_rules(content, {})
        except Exception:
            degraded = {}
        _merge_method_tag_hints(degraded, content)
        return JSONResponse(
            content={
                "status": "partial",
                "message": f"AI 智能分类失败: {str(e)}",
                **degraded,
            },
            status_code=200,
        )

# ----------------- LaTeX Batch Paper Import APIs -----------------

@app.post("/api/upload/tex-source")
def upload_tex_source(file: UploadFile = File(...)):
    """Decode and inspect a single TeX source file without executing it."""
    filename = file.filename or ""
    if not filename.lower().endswith(".tex"):
        return JSONResponse(
            content={"status": "error", "message": "上传文件格式不正确，必须为 .tex 格式！"},
            status_code=400,
        )
    try:
        content = read_stream_limited(file.file, MAX_TEX_BYTES)
        result = decode_and_prepare_tex(content)
        return {
            "status": "success",
            "source": result["source"],
            "title": result["title"],
            "diagnostics": result["diagnostics"],
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "TeX 文件过大，请上传 5MB 以内的单文件试卷源码！"},
            status_code=413,
        )
    except ValueError as exc:
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=400)


@app.post("/api/upload/markdown-source")
def upload_markdown_source(file: UploadFile = File(...)):
    """Decode a bounded Markdown source without reading its linked assets."""
    if not (file.filename or "").lower().endswith(".md"):
        return JSONResponse(
            content={"status": "error", "message": "上传文件格式不正确，必须为 .md 格式！"},
            status_code=400,
        )
    try:
        result = decode_and_prepare_markdown(read_stream_limited(file.file, MAX_MARKDOWN_BYTES))
        return {
            "status": "success",
            "source": result["source"],
            "title": result["title"],
            "diagnostics": result["diagnostics"],
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "Markdown 文件过大，请上传 5MB 以内的单文件试卷源码！"},
            status_code=413,
        )
    except ValueError as exc:
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=400)


@app.post("/api/upload/batch")
def upload_batch_images(files: List[UploadFile] = File(...)):
    try:
        if not files or len(files) > 20:
            return JSONResponse(
                content={"status": "error", "message": "配图数量必须为 1 至 20 张。"},
                status_code=400,
            )
        validated = []
        total_bytes = 0
        seen_names: set[str] = set()
        for file in files:
            original_name = tex_asset_basename(file.filename or "image") or "image"
            normalized_name = original_name.casefold()
            if normalized_name in seen_names:
                raise ValueError(f"存在重名配图 {original_name}，请保留一张或先重命名。")
            seen_names.add(normalized_name)
            try:
                raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
            except UploadTooLargeError as exc:
                raise ValueError(f"图片 {original_name} 超过 10MB。") from exc
            total_bytes += len(raw)
            if total_bytes > 50 * 1024 * 1024:
                raise ValueError("配图总大小不能超过 50MB。")
            try:
                normalized = normalize_raster_image(raw)
            except InvalidImageError as exc:
                raise ValueError(f"图片 {original_name} 不是安全的栅格图片。") from exc
            validated.append((original_name, normalized))

        mapping = {}
        for original_name, normalized in validated:
            filename = f"{uuid.uuid4().hex}{normalized.extension}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(normalized.data)
            relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
            mapping[original_name] = relative_path
            
        return {
            "status": "success",
            "mapping": mapping
        }
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        return JSONResponse(
            content={"status": "error", "message": f"批量图片上传失败: {str(e)}"},
            status_code=400
        )


def parse_paper_text_internal(
    latex_content: str,
    generate_answers_bool: bool,
    *,
    diagnostics: dict | None = None,
    preserve_source_answers: bool = False,
    pdf_retry: bool = False,
    check_cancelled=lambda: None,
    report_attempt=lambda _event: None,
    connection_retry: bool = True,
) -> list:
    """内部通用函数：调用选定的 LLM 接口，将 LaTeX 试卷内容解析拆分为结构化 JSON 卡片"""
    check_cancelled()
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
    provider = resolve_text_provider(parse_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        raise ValueError(f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。")

    system_instructions = build_pdf_parse_system_prompt(
        get_current_curriculum(), generate_answers_bool
    )

    max_output_tokens = 65536

    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": latex_content}
        ],
        "response_format": {
            "type": "json_object"
        },
        "temperature": 0.2,
        "max_tokens": max_output_tokens
    }
    
    data = apply_model_thinking_policy(
        data,
        provider=provider,
        task="parse",
    )
    
    if pdf_retry:
        parsed_questions = request_pdf_paper_completion(
            provider, data, post=post_chat_completion,
            raw_markdown="" if preserve_source_answers else latex_content,
            diagnostics=diagnostics, check_cancelled=check_cancelled, report_attempt=report_attempt,
        )
    else:
        check_cancelled()
        transport_kwargs = ({"retry_connection": False, "allow_redirects": False}
                            if not connection_retry else {})
        response = post_chat_completion(
            provider,
            data,
            timeout=PAPER_SPLIT_TIMEOUT_SECONDS,
            provider_name=provider_name,
            **transport_kwargs,
        )
        check_cancelled()
        parsed_questions = parse_paper_completion(
            response.json(),
            raw_markdown="" if preserve_source_answers else latex_content,
            diagnostics=diagnostics,
        )
    if preserve_source_answers:
        validate_import_knowledge(parsed_questions, get_current_curriculum())
        # Compare the original response before answer filtering or formula edits.
        return parsed_questions

    validate_import_knowledge(parsed_questions, get_current_curriculum())

    # 强制进行静默净化：若未勾选自动生成答案，则对于没有带有 [EXTRACTED_ORIGINAL] 的解析和解答，将其强行抹平为空。
    for q in parsed_questions:
        ans = q.get("answer_markdown", "")
        if not ans:
            q["answer_markdown"] = ""
        elif not generate_answers_bool:
            if "[EXTRACTED_ORIGINAL]" in ans:
                q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
            else:
                q["answer_markdown"] = ""
        else:
            q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()

        for field in ("content", "answer_markdown"):
            value = q.get(field, "")
            if isinstance(value, str) and value:
                q[field] = normalize_question_math_markdown(value)
        
    return parsed_questions


def validate_import_knowledge(questions: list[dict], curriculum: dict) -> None:
    """Reject fabricated import subsections; empty knowledge means whole chapter."""
    for index, question in enumerate(questions, start=1):
        compulsory = question.get("category_compulsory", "")
        chapter = question.get("category_chapter", "")
        # Older parse providers may omit this optional field. Normalize omission
        # to explicit whole-chapter status; never synthesize the chapter as a leaf.
        knowledge = question.get("category_knowledge", "")
        question["category_knowledge"] = knowledge
        if not isinstance(knowledge, str):
            raise ValueError(f"第 {index} 题的小节字段无效，请重新导入并核对。")
        if compulsory or chapter:
            if compulsory not in curriculum or chapter not in curriculum[compulsory]:
                raise ValueError(f"第 {index} 题的学段或章节不在当前教材版本中，请重新导入并核对。")
        if knowledge and (compulsory not in curriculum or chapter not in curriculum[compulsory]
                          or knowledge not in curriculum[compulsory][chapter]):
            raise ValueError(f"第 {index} 题的小节不属于所选版本、学段和章节，请重新导入并核对。")


@app.post("/api/ai/parse-paper")
def ai_parse_paper(
    latex_content: str = Form(...),
    paper_title: str = Form(""),
    image_mapping_json: str = Form("{}"),
    generate_answers: str = Form("false"),
    source_format: str = Form("tex"),
):
    if source_format not in ("tex", "markdown"):
        return JSONResponse(
            content={"status": "error", "message": "源码格式仅支持 tex 或 markdown。"},
            status_code=400,
        )
    generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
    provider = resolve_text_provider(parse_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        image_mapping = json.loads(image_mapping_json)
        if not isinstance(image_mapping, dict):
            image_mapping = {}
    except Exception:
        image_mapping = {}

    try:
        tex_result = (prepare_markdown_source(latex_content) if source_format == "markdown"
                      else prepare_tex_source(latex_content))
        tex_diagnostics = tex_result["diagnostics"]
        model_source, math_locks = lock_visible_math(
            tex_result["model_source"],
            ("MD_" if source_format == "markdown" else "TEX_") + uuid.uuid4().hex[:16],
            literal_ranges=(markdown_literal_ranges(tex_result["model_source"])
                            if source_format == "markdown" else ()),
        )
        tex_diagnostics["math_locks_created"] = len(math_locks)
        if not paper_title.strip() and tex_result["title"]:
            paper_title = tex_result["title"]

        system_instructions = build_import_parse_system_prompt(get_current_curriculum(), source_format)

        max_output_tokens = 65536

        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": model_source}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.2,
            "max_tokens": max_output_tokens
        }
        
        data = apply_model_thinking_policy(
            data,
            provider=provider,
            task="parse",
        )
        
        response = post_chat_completion(
            provider,
            data,
            timeout=PAPER_SPLIT_TIMEOUT_SECONDS,
            provider_name=provider_name,
        )
            
        parsed_questions = parse_paper_completion(
            response.json(), diagnostics=tex_diagnostics,
        )
        validate_import_knowledge(parsed_questions, get_current_curriculum())
        lock_report = reconcile_visible_math(
            parsed_questions, math_locks, tex_result["model_source"],
            tex_comments=source_format != "markdown",
        )
        previous_warnings = list(tex_diagnostics.get("warnings", []))
        tex_diagnostics.update(lock_report)
        tex_diagnostics["warnings"] = previous_warnings + lock_report.get("warnings", [])
        finalize_source_answers(parsed_questions, tex_result["model_source"])
        tex_diagnostics["source_review_count"] = sum(
            bool(q.get("source_review", {}).get("required")) for q in parsed_questions
        )
        tex_diagnostics["question_count_actual"] = len(parsed_questions)
        estimated_count = tex_diagnostics.get("question_count_estimate", 0)
        if estimated_count and estimated_count != len(parsed_questions):
            tex_diagnostics.setdefault("warnings", []).append(
                f"源码约识别到 {estimated_count} 道题，但模型返回 {len(parsed_questions)} 道，请重点核对是否漏题或误拆。"
            )

        for graphic_ref in tex_diagnostics.get("referenced_graphics", []):
            graphic_ref = str(graphic_ref)
            candidates = []
            for question in parsed_questions:
                content_graphics = (markdown_image_references(
                    question.get("content", "") + "\n" + question.get("answer_markdown", ""),
                ) if source_format == "markdown" else re.findall(
                    r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}",
                    question.get("content", ""),
                ))
                question_refs = content_graphics + [
                    str(value) for value in question.get("referenced_images", [])
                ]
                if any(tex_asset_references_match(graphic_ref, value) for value in question_refs):
                    candidates.append(question)
            if len(candidates) == 1 and not any(
                tex_asset_references_match(graphic_ref, existing)
                for existing in candidates[0]["referenced_images"]
            ):
                candidates[0]["referenced_images"].append(graphic_ref)
            elif not candidates:
                tex_diagnostics.setdefault("unassigned_source_images", []).append(graphic_ref)
        
        # Translate referenced_images to server paths
        for q in parsed_questions:
            # 智能提取出处双重保险：AI 提取优先，若 AI 未提取则尝试正则从 content 中提取
            extracted_source = q.get("source")
            content_str = q.get("content", "")
            
            # 正则匹配题干开头形如 "10. (2019·全国·高考真题)已知..." 的出处
            # group(1): 题号前缀, group(2): 左括号, group(3): 出处内容, group(4): 右括号
            prefix_match = re.match(r'^(\s*(?:\d+[\.、\s]*)?)([\(（])([^\(（\)）\s]{4,})([\)）])', content_str)
            if prefix_match:
                if not extracted_source:
                    extracted_source = prefix_match.group(3).strip()
                # 剔除题干中的出处括号及前面的题号前缀，保持题干纯净
                to_remove = prefix_match.group(1) + prefix_match.group(2) + prefix_match.group(3) + prefix_match.group(4)
                content_str = content_str.replace(to_remove, "", 1).strip()
                # 移除可能残存的开头符号（如句点或顿号）
                content_str = re.sub(r'^[\s、\.．]+', '', content_str)
                q["content"] = content_str
                
            q["source"] = (extracted_source or paper_title).strip()
            
            # Clean up double-escaped literal \n in fields
            for field in ["content", "answer_markdown"]:
                if field in q and isinstance(q[field], str):
                    text = q[field]
                    # Replace literal "\n" safely using negative lookahead (so it doesn't touch commands like \normalsize or \nabla)
                    if source_format == "tex":
                        text = re.sub(r'\\n(?![a-zA-Z])', '\n', text)
                    q[field] = text

            if source_format == "markdown":
                map_markdown_question_images(q, image_mapping, tex_diagnostics)
                prepare_markdown_question_for_storage(q, tex_diagnostics)
                continue
            
            # Map images
            mapped_images = []
            ref_imgs = q.get("referenced_images", [])
            for ref_name in ref_imgs:
                ref_name = str(ref_name)
                # Direct match or fuzzy match
                found_path = None
                for orig_name, serv_path in image_mapping.items():
                    if tex_asset_references_match(ref_name, orig_name):
                        found_path = serv_path
                        break
                if found_path:
                    if found_path not in mapped_images:
                        mapped_images.append(found_path)
                    include_pattern = re.compile(
                        r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{\s*([^}]+?)\s*\}"
                    )
                    q["content"] = include_pattern.sub(
                        lambda match: (
                            f"![插图]({found_path})"
                            if tex_asset_references_match(ref_name, match.group(1))
                            else match.group(0)
                        ),
                        q["content"],
                    )
                else:
                    tex_diagnostics.setdefault("unmapped_images", []).append(str(ref_name))
                    
            q["image_paths"] = mapped_images
            
            # If AI didn't map it in content text but referenced it, append it to content
            for img_path in mapped_images:
                if img_path not in q["content"]:
                    q["content"] += f"\n\n![插图]({img_path})\n\n"

        unmapped_images = sorted(set(tex_diagnostics.get("unmapped_images", [])))
        unassigned_images = sorted(set(tex_diagnostics.get("unassigned_source_images", [])))
        tex_diagnostics["unmapped_images"] = unmapped_images
        tex_diagnostics["unassigned_source_images"] = unassigned_images
        if unmapped_images:
            tex_diagnostics.setdefault("warnings", []).append(
                f"以下 {'Markdown' if source_format == 'markdown' else 'TeX'} 配图未找到同名上传文件：" + "、".join(unmapped_images[:8])
            )
        if unassigned_images:
            tex_diagnostics.setdefault("warnings", []).append(
                "以下配图未能确定所属题目：" + "、".join(unassigned_images[:8])
            )
        make_source_review_advisory(parsed_questions, tex_diagnostics)
        return {
            "status": "success",
            "questions": parsed_questions,
            "tex_diagnostics": tex_diagnostics,
            "source_format": source_format,
        }
    except Exception as e:
        return JSONResponse(
            content={
                "status": "error", "message": f"试卷解析失败: {str(e)}",
                "tex_diagnostics": locals().get("tex_diagnostics", {}),
            },
            status_code=500
        )

@app.get("/api/sources")
def get_sources(db: Session = Depends(get_db)):
    results = db.query(Question.source).distinct().all()
    sources = []
    for r in results:
        val = r[0]
        if val and val.strip():
            sources.append(val.strip())
            
    # Sort alphabetically (case-insensitive)
    sources.sort(key=str.lower)
    return sources

@app.post("/api/shutdown")
def shutdown_server(
    x_mathbank_launch_id: str | None = Header(
        None, alias="X-MathBank-Launch-ID"
    ),
):
    if not x_mathbank_launch_id or not secrets.compare_digest(
        x_mathbank_launch_id, SERVER_INSTANCE_ID
    ):
        raise HTTPException(status_code=409, detail="Server instance changed")

    def stop_server():
        time.sleep(0.5)
        # Raising SIGINT inside this process lets uvicorn's installed handler
        # run its normal lifespan shutdown.  On Windows, os.kill(SIGINT) would
        # call TerminateProcess instead of delivering a cooperative console
        # control event.
        try:
            signal.raise_signal(signal.SIGINT)
        except Exception as exc:
            _SHUTDOWN_SCHEDULED.clear()
            print(f"[shutdown] Failed to raise cooperative SIGINT: {exc}")

    with _SHUTDOWN_SCHEDULE_LOCK:
        if _SHUTDOWN_SCHEDULED.is_set():
            return {
                "status": "already_stopping",
                "message": "题库系统已在关闭中...",
            }
        _SHUTDOWN_SCHEDULED.set()
        worker = threading.Thread(target=stop_server, daemon=True)
        try:
            worker.start()
        except Exception:
            _SHUTDOWN_SCHEDULED.clear()
            raise
    
    return {"status": "success", "message": "题库系统正在关闭中..."}


# ----------------- Storage Promotion Engine -----------------

@serialize_asset_lifecycle
def rollback_question_asset_promotions(promotions: list[tuple[Path, Path]]) -> None:
    """Best-effort compensation when a DB transaction rejects promoted files."""

    for source, destination in reversed(promotions):
        try:
            # Only newly created copies are logged. Reused permanent files
            # can belong to another committed question and are never removed.
            if destination.is_file() and source.is_file():
                destination.unlink()
        except OSError as exc:
            print(f"[Storage Rollback] Failed to restore a promoted asset: {type(exc).__name__}")


def _referenced_question_assets(db: Session) -> set[Path]:
    """Resolve every stored question image reference with one database query."""

    resolved_references: set[Path] = set()
    rows = db.query(
        Question._image_paths,
        Question.content,
        Question.answer_markdown,
        Question.review,
        Question._content_tikz_assets,
        Question._answer_tikz_assets,
        Question.tikz_reference_image_path,
    ).all()
    for (
        raw_paths,
        content,
        answer_markdown,
        review,
        content_assets,
        answer_assets,
        legacy_reference,
    ) in rows:
        references = []
        try:
            parsed = json.loads(raw_paths or "[]")
            if not isinstance(parsed, list):
                raise ValueError("题目图片清单不是数组，已停止清理")
            references.extend(parsed)
            references.extend(structured_question_assets(
                content_assets, answer_assets, legacy_reference=legacy_reference,
            ))
        except (TypeError, ValueError) as exc:
            raise ValueError("题目图片记录损坏，已停止清理") from exc
        references.extend(embedded_question_assets(content, answer_markdown, review))
        references.extend(
            re.findall(
                r'/static/(?:uploads|test_uploads)/[a-zA-Z0-9_./-]+',
                f"{content or ''}\n{answer_markdown or ''}\n{review or ''}",
            )
        )
        for reference in references:
            try:
                resolved = resolve_upload_asset(
                    reference,
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                    require_file=False,
                )
            except AssetSecurityError:
                continue
            resolved_references.add(resolved)
    return resolved_references


@serialize_asset_lifecycle
def delete_unreferenced_question_assets(db: Session, references) -> int:
    """Retain committed-away images recoverably after checking every reference."""

    candidates: set[Path] = set()
    for reference in set(references or []):
        try:
            candidates.add(
                resolve_upload_asset(
                    reference,
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                    require_file=False,
                )
            )
        except AssetSecurityError:
            print("[Storage Cleanup] Skipped an invalid legacy image path.")

    if not candidates:
        return 0
    referenced = _referenced_question_assets(db)
    removed = 0
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate not in referenced:
                relative = candidate.relative_to(Path(UPLOAD_DIR).resolve()).as_posix()
                removed += int(quarantine_asset(
                    f"/{UPLOAD_DIR_REL}/{relative}", uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL, reason="question-reference-removed",
                ))
        except (OSError, AssetLifecycleError):
            print("[Storage Cleanup] Skipped an unavailable legacy image path.")
    return removed


@serialize_asset_lifecycle
def promote_question_temp_assets(
    content: str,
    answer_markdown: str,
    image_paths_list: list,
    *,
    review: str = "",
    promotion_log: list[tuple[Path, Path]] | None = None,
    path_map: dict[str, str] | None = None,
) -> tuple:
    """Copy temporary images durably, preserving drafts and shared import cards."""
    import shutil

    if not isinstance(image_paths_list, list):
        raise AssetSecurityError("image_paths 必须是插图路径数组。")

    # review images participate in the same promotion/validation path as the
    # 题干 and 答案 so an image referenced only by the 评述 is never orphaned.
    embedded_paths = embedded_question_assets(content, answer_markdown, review)
    all_references = [value for value in image_paths_list if value] + embedded_paths

    # Validate the complete set before moving anything.  A bad second path must
    # not leave the first path half-promoted.
    canonical_by_input: dict[str, str] = {}
    resolved_by_canonical: dict[str, Path] = {}
    for reference in all_references:
        canonical = normalize_upload_asset_reference(
            reference,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        canonical_by_input[reference] = canonical
        resolved_by_canonical.setdefault(
            canonical,
            resolve_upload_asset(
                canonical,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            ),
        )

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_root = Path(TMP_UPLOAD_DIR).resolve()
    promoted_by_canonical: dict[str, str] = {}
    for canonical, source in resolved_by_canonical.items():
        if source.parent == temp_root:
            destination_url = f"/{UPLOAD_DIR_REL}/{source.name}"
            destination = resolve_upload_asset(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
                require_file=False,
            )
            # A previous card may already have copied this same shared image.
            # Reuse only byte-identical content and never overwrite a collision.
            from mathbank.asset_lifecycle import restore_asset
            restore_asset(destination, uploads_dir=upload_root)
            if destination.exists():
                with source.open("rb") as original, destination.open("rb") as existing:
                    while True:
                        left, right = original.read(1024 * 1024), existing.read(1024 * 1024)
                        if left != right:
                            raise AssetSecurityError("目标插图文件已存在且内容不同，已停止覆盖。")
                        if not left:
                            break
            else:
                # Exclusive creation plus the lifecycle lock makes rollback
                # ownership unambiguous even under concurrent batch imports.
                output = destination.open("xb")
                try:
                    with output, source.open("rb") as original:
                        shutil.copyfileobj(original, output)
                        output.flush()
                        os.fsync(output.fileno())
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                if promotion_log is not None:
                    promotion_log.append((source, destination))
            promoted_by_canonical[canonical] = normalize_upload_asset_reference(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
            )
        elif source.is_relative_to(upload_root):
            promoted_by_canonical[canonical] = canonical
        else:  # Defensive; resolve_upload_asset should already make this impossible.
            raise AssetSecurityError("临时插图越出了上传目录。")

    replacements: dict[str, str] = {}
    for original, canonical in canonical_by_input.items():
        promoted = promoted_by_canonical[canonical]
        replacements[original] = promoted
        replacements[canonical] = promoted

    new_content = rewrite_question_asset_paths(content, replacements)
    new_answer = rewrite_question_asset_paths(answer_markdown, replacements)
    new_review = rewrite_question_asset_paths(review, replacements)
    if path_map is not None:
        path_map.update({old: new for old, new in replacements.items() if old != new})

    updated_paths: list[str] = []
    for original in image_paths_list:
        if not original:
            continue
        promoted = promoted_by_canonical[canonical_by_input[original]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    for embedded in embedded_paths:
        promoted = promoted_by_canonical[canonical_by_input[embedded]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    return new_content, new_answer, new_review, updated_paths


@app.post("/api/ai/manual-crop-pdf")
def manual_crop_pdf(payload: dict):
    """用户在前端手动拖拽框选后，后端根据坐标裁剪 PDF 页面的特定区域"""
    try:
        import math

        if not isinstance(payload, dict):
            raise ValueError("裁剪参数格式不正确。")
        try:
            task_id = str(uuid.UUID(str(payload.get("task_id", ""))))
        except (ValueError, AttributeError) as exc:
            raise ValueError("任务 ID 格式不正确。") from exc
        task = DOCUMENT_TASKS.snapshot(task_id)
        if not task or task.get("document_type") != "pdf":
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF 任务！"},
                status_code=404,
            )
        if task.get("status") in {"cancelled", "error"}:
            raise ValueError("已取消或失败的 PDF 任务不能再裁剪。")
        page_index = int(payload.get("page_index", 0))
        selected_numbers = task.get("page_numbers")
        if page_index < 0 or (
            isinstance(selected_numbers, list) and page_index + 1 not in selected_numbers
        ) or (selected_numbers is None and page_index >= MAX_PDF_TASK_PAGES):
            raise ValueError("页码越界。")
        ymin = float(payload.get("ymin", 0))
        xmin = float(payload.get("xmin", 0))
        ymax = float(payload.get("ymax", 0))
        xmax = float(payload.get("xmax", 0))
        coordinates = (ymin, xmin, ymax, xmax)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("裁剪坐标必须是有限数值。")
        if not (
            0 <= ymin < ymax <= 100
            and 0 <= xmin < xmax <= 100
        ):
            raise ValueError("裁剪坐标必须位于 0–100，且框选区域不能为空。")

        img_filename = f"pdf_page_{task_id}_{page_index}.png"
        img_filepath = Path(TMP_UPLOAD_DIR) / img_filename
        
        if not img_filepath.is_file() or img_filepath.is_symlink():
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF 页面图片！"},
                status_code=404
            )
            
        with Image.open(img_filepath) as img:
            img.load()
            w, h = img.size

            # Convert percentage to pixels.
            left = max(0, min((xmin / 100.0) * w, w - 1))
            top = max(0, min((ymin / 100.0) * h, h - 1))
            right = max(left + 1, min((xmax / 100.0) * w, w))
            bottom = max(top + 1, min((ymax / 100.0) * h, h))
            cropped = img.crop((left, top, right, bottom))

        crop_filename = f"pdf_crop_{task_id}_{uuid.uuid4().hex[:12]}.png"
        crop_filepath = Path(TMP_UPLOAD_DIR) / crop_filename
        cropped.save(crop_filepath, format="PNG")
        
        img_url = f"/{UPLOAD_DIR_REL}/tmp/{crop_filename}"
        if not DOCUMENT_TASKS.add_temp_asset(task_id, img_url):
            crop_filepath.unlink(missing_ok=True)
            raise ValueError("任务记录已过期，无法登记裁剪图片。")
        return {"status": "success", "image_path": img_url}
    except ValueError as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=500
        )


def extract_title_from_latex(latex: str) -> str:
    """从 LaTeX 源码中尝试自动提取试卷标题"""
    if not latex:
        return ""
    import re
    
    def clean_latex(txt: str) -> str:
        # 移除字体大小命令等
        txt = re.sub(r'\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt|heiti|kaishu|fangsong|songti)', '', txt)
        # 解包 textbf 等
        txt = re.sub(r'\\text(bf|it|sf|tt)?\s*\{([^}]+)\}', r'\2', txt)
        txt = txt.replace('{', '').replace('}', '').replace('\\\\', '\n').strip()
        lines = [line.strip() for line in txt.split('\n') if line.strip()]
        if lines:
            return lines[0][:60]
        return ""

    # 1. 尝试匹配 \title{...}
    match = re.search(r'\\title\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    # 2. 尝试匹配 \chead{...}
    match = re.search(r'\\chead\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned and "页" not in cleaned and "绝密" not in cleaned:
            return cleaned
            
    # 3. 尝试匹配 \begin{center} ... \end{center} 头部区域
    top_part = latex[:1500]
    match = re.search(r'\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}', top_part)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    return ""


# ----------------- PDF Import & AI Parsing Backend Logic -----------------

def ocr_pdf_page_image(image_path: str, *, check_cancelled=lambda: None,
                       report_attempt=lambda _event: None, page_index: int = None) -> str:
    """PDF pages share the configured provider's bounded retry budget."""
    check_cancelled()
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    provider = resolve_ocr_provider(prefer_engine)
    if not provider.api_key or not provider.chat_completions_url:
        raise ValueError("PDF 页面识别所用的识图服务未配置，请在系统设置中配置所选平台。")
    return ocr_via_provider(image_path, provider, pdf_page=True, check_cancelled=check_cancelled,
                            report_attempt=report_attempt, page_index=page_index)


def process_ocr_illustrations(text: str) -> str:
    """(已关闭 AI 自动插图裁剪) 仅进行安全标签清洗，擦除任何潜在的视觉定位标签或 box 坐标标记，返回纯净 OCR 结果"""
    import re
    if not text:
        return text
    
    # 1. 擦除 Qwen 视觉定位标签: <|box_start|>(ymin,xmin,ymax,xmax)<|box_end|>
    cleaned = re.sub(r"(?i)<\|box_start\|>.*?<\|box_end\|>", "", text)
    
    # 2. 擦除 ILLUSTRATION_BOX 标签: [ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]
    cleaned = re.sub(r"(?i)\[ILLUSTRATION_BOX:.*?\]", "", cleaned)
    cleaned = re.sub(r"(?i)ILLUSTRATION_BOX\s*[:：\(（\[\s]*[^\]\)\n\r]+[\s\]\)]*", "", cleaned)
    
    return normalize_table_math_wrappers(cleaned).strip()


def find_source_page_by_overlap(q_text: str, ocr_results: list) -> int:
    """利用 3-shingle（三字符切片）特征重合度，计算题目最可能所属的 PDF 原始物理页码"""
    if not q_text or not ocr_results:
        return 0
    
    import re
    def clean_for_compare(t: str) -> str:
        # 仅保留中文字符、英文字母和数字，过滤掉干扰公式渲染的标点符号
        return "".join(re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]', t))
        
    cleaned_q = clean_for_compare(q_text)
    if not cleaned_q:
        return 0
        
    best_page = 0
    max_overlap = -1
    
    for idx, page_text in enumerate(ocr_results):
        if not page_text:
            continue
        cleaned_page = clean_for_compare(page_text)
        
        # 构建 3-shingle 切片集合
        if len(cleaned_q) >= 3:
            shingles_q = set(cleaned_q[i:i+3] for i in range(len(cleaned_q)-2))
        else:
            shingles_q = {cleaned_q}
            
        if len(cleaned_page) >= 3:
            shingles_page = set(cleaned_page[i:i+3] for i in range(len(cleaned_page)-2))
        else:
            shingles_page = {cleaned_page}
            
        overlap = len(shingles_q.intersection(shingles_page))
        if overlap > max_overlap:
            max_overlap = overlap
            best_page = idx
            
    return best_page

@app.post("/api/paper/ai-select")
def ai_select_paper(payload: dict, db: Session = Depends(get_db)):
    """AI 智能选题：结合用户指定的 PREFER_SOLVE_MODEL 大模型与 math-teaching 教研引擎组卷"""
    require_expected_curriculum_version(payload.get("expected_version_code"))
    try:
        prompt = payload.get("prompt", "").strip()
        question_type = payload.get("question_type", "")
        difficulty = payload.get("difficulty", "")
        compulsory = payload.get("compulsory", "")
        chapter = payload.get("chapter", "")
        knowledge = payload.get("knowledge", "")
        raw_include_sub = payload.get("include_sub_knowledge", False)
        include_sub = raw_include_sub is True or str(raw_include_sub).lower() in {"true", "1"}
        try:
            sub_paths = normalize_sub_knowledge(payload.get("sub_knowledge", []))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        limit = max(1, min(int(payload.get("limit", 5)), 20))

        # 0. 自然语言意图智能分析 (NL Intent Parser)
        extracted_topics = []
        is_review_intent = False
        if prompt:
            is_review_intent = any(k in prompt for k in ['做过', '考过', '已抽过', '已用过', '复习', '旧题', '重做', '错题', '以往', '历史'])
            num_match = re.search(r'([一二三四五六七八九十1-9]+)\s*道', prompt)
            cn_to_num = {'一':1, '两':2, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}
            if num_match:
                val = num_match.group(1)
                limit = cn_to_num.get(val, int(val) if val.isdigit() else limit)

            if not question_type:
                if '填空' in prompt: question_type = 'fill_in_blank'
                elif '单选' in prompt: question_type = 'single_choice'
                elif '多选' in prompt: question_type = 'multi_choice'
                elif '解答' in prompt: question_type = 'detailed_answer'

            known_topics = ['立体几何', '集合', '函数', '导数', '数列', '三角函数', '平面向量', '概率', '解析几何', '圆锥曲线', '复数', '不等式', '排列组合']
            extracted_topics = [t for t in known_topics if t in prompt]

        # 1. 结构化过滤基础题目池
        query = db.query(Question).options(
            selectinload(Question.curriculum_mappings).selectinload(QuestionCurriculum.sub_knowledge_rows)
        )
        if question_type:
            query = query.filter(Question.question_type == question_type)
        if difficulty:
            query = query.filter(Question.difficulty == difficulty)
        query = apply_curriculum_filters(
            query, compulsory, chapter, knowledge, include_sub_knowledge=include_sub,
            sub_knowledge=sub_paths, sub_knowledge_mode=payload.get("sub_knowledge_mode", "any"),
        )
            
        if is_review_intent:
            # 复习/旧题模式：优先提取已使用频次高的题目
            review_query = query.filter(Question.usage_count > 0).order_by(Question.usage_count.desc(), Question.id.desc())
            candidates = review_query.limit(35).all()
            if not candidates:
                candidates = query.order_by(Question.id.desc()).limit(35).all()
        else:
            # 默认鲜活模式：优先提取从未被使用过的冷门题目
            candidates = query.order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()
            if not candidates and not any((question_type, difficulty, compulsory, chapter, knowledge, sub_paths)):
                candidates = db.query(Question).order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()

        # 2. 解题、拆卷、分类和组卷共用同一供应商解析规则。
        # 不会因为某家 Key 缺失而静默改用另一家。
        target_model = (
            os.getenv("PREFER_SOLVE_MODEL")
            or os.getenv("PREFER_PARSE_MODEL")
            or "deepseek-flash"
        )
        provider = resolve_text_provider(target_model)
        api_key = provider.api_key
        api_base = provider.api_base
        model_name = provider.model_name
        provider_name = provider.provider_label

        api_error_detail = None
        if prompt and candidates:
            if not api_key or not api_base:
                api_error_detail = (
                    f"指定的 AI 解题模型 ({target_model}) 未配置有效的 "
                    f"API Key 或 Base URL（{provider.credential_label}）。"
                )
            else:
                candidate_items = []
                for q in candidates:
                    clean_stem = re.sub(r'[\r\n]+', ' ', q.content[:80])
                    candidate_items.append({
                        "id": q.id,
                        "question_type": q.question_type,
                        "difficulty": q.difficulty,
                        "usage_count": q.usage_count or 0,
                        "knowledge": q.category_knowledge or "整章定位（待细分）",
                        "tags": q.tags or "",
                        "stem_excerpt": clean_stem
                    })

                system_prompt, user_content = build_paper_selection_prompts(
                    teacher_prompt=prompt,
                    limit=limit,
                    candidates=candidate_items,
                    is_review_intent=is_review_intent,
                )

                try:
                    payload_data = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "temperature": 0.3
                    }
                    payload_data = apply_model_thinking_policy(
                        payload_data,
                        provider=provider,
                        task="paper_selection",
                    )
                    response = post_chat_completion(
                        provider,
                        payload_data,
                        timeout=20,
                        provider_name=provider_name,
                    )
                    res_json = response.json()
                    raw_content = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if raw_content.startswith("```"):
                        raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
                        raw_content = re.sub(r"\s*```$", "", raw_content)

                    parsed = json.loads(raw_content)
                    raw_selected_ids = parsed.get("selected_ids", [])
                    ai_analysis = parsed.get("ai_analysis", "")

                    if raw_selected_ids and isinstance(raw_selected_ids, list):
                        # The model may only rank the candidate IDs that were
                        # actually supplied after local filters.  This prevents
                        # prompt output from bypassing chapter/type constraints
                        # or selecting arbitrary records from the database.
                        allowed_ids = {question.id for question in candidates}
                        selected_ids = []
                        seen_ids = set()
                        for raw_id in raw_selected_ids:
                            try:
                                selected_id = int(raw_id)
                            except (TypeError, ValueError):
                                continue
                            if (
                                selected_id in allowed_ids
                                and selected_id not in seen_ids
                            ):
                                selected_ids.append(selected_id)
                                seen_ids.add(selected_id)
                            if len(selected_ids) >= limit:
                                break
                        db_selected = db.query(Question).filter(Question.id.in_(selected_ids)).all()
                        id_map = {q.id: q for q in db_selected}
                        seq_map = get_seq_mapping(db, selected_ids)
                        final_questions = [{**id_map[qid].to_dict(get_active_version_code()), "seq_num": seq_map.get(qid)} for qid in selected_ids if qid in id_map]

                        if final_questions:
                            return {
                                "status": "success",
                                "data": final_questions,
                                "count": len(final_questions),
                                "ai_analysis": ai_analysis,
                                "model_used": f"{provider_name} ({model_name})",
                                "fallback": False
                            }
                except Exception as llm_err:
                    api_error_detail = f"{provider_name} API 请求失败: {str(llm_err)}"

        # 3. 降级本地算法（带明确错误反馈）
        fallback_questions = []
        if extracted_topics:
            for topic in extracted_topics:
                sub_query = query
                sub_query = sub_query.filter(
                    (Question.content.like(f"%{_escape_like(topic)}%", escape="\\")) |
                    (Question.category_chapter.like(f"%{_escape_like(topic)}%", escape="\\")) |
                    (Question.category_knowledge.like(f"%{_escape_like(topic)}%", escape="\\")) |
                    (Question.tags.like(f"%{_escape_like(topic)}%", escape="\\"))
                )
                order_clause = Question.usage_count.desc() if is_review_intent else Question.usage_count.asc()
                for q in sub_query.order_by(order_clause, Question.id.desc()).all():
                    if q not in fallback_questions:
                        fallback_questions.append(q)

        # 补足数量
        if len(fallback_questions) < limit:
            for q in candidates:
                if q not in fallback_questions:
                    fallback_questions.append(q)
                if len(fallback_questions) >= limit:
                    break

        selected_fallback = fallback_questions[:limit]
        seq_map = get_seq_mapping(db, [q.id for q in selected_fallback])
        result = [{**q.to_dict(get_active_version_code()), "seq_num": seq_map.get(q.id)} for q in selected_fallback]
        
        topic_str = "、".join(extracted_topics) if extracted_topics else "通用知识点"
        err_banner = f"⚠️ 【AI 解题模型调用未成功】: {api_error_detail}\n系统已为您自动启动本地教研算法，根据意图（{topic_str}）在本地题库中筛选并组合了 {len(result)} 道精选题目。" if api_error_detail else f"【本地智能筛选分析】已为您自动识别意图（{topic_str}），从题库中精准挑选并组合了鲜活试题。"
        
        return {
            "status": "success",
            "data": result,
            "count": len(result),
            "ai_analysis": err_banner,
            "model_used": f"⚠️ 模型调用失败 ({target_model}) ➔ 退回本地算法" if api_error_detail else "本地算法",
            "fallback": True
        }
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"AI 智能选题失败: {str(e)}"}, status_code=500)


def post_process_pdf_parsed_questions(parsed_questions: list, paper_title: str, task_id: str = None, ocr_results: list = None, *, source_body_preserved: bool = False, preserved_indices=None) -> list:
    """PDF/Word 解析卡片后处理：修复图片路径并登记资产，保留正文中的图片锚点。

    image_paths 负责资产生命周期，不能替代选项、表格或正文中的图片位置。
    """
    import re
    import os
    import glob
    preserved = (set(range(len(parsed_questions))) if source_body_preserved else set(preserved_indices or ()))
    if any(type(index) is not int or not 0 <= index < len(parsed_questions) for index in preserved):
        raise ValueError("保真题目位置无效。")

    # 0. 规范化所有拆解题目的填空下划线为 \fillin 宏
    for index, q in enumerate(parsed_questions):
        if q.get("content"):
            q["content"] = (normalize_source_fillin(q["content"], normalize_fillin_macro)
                            if index in preserved else normalize_fillin_macro(q["content"]))

    # 0.1 归位教材分类标签：模型通常能抄对章节名称，却会漏掉目录前的编号
    # （如 "2. 直线和圆的方程" 被写成 "直线和圆的方程"），导致审查卡片的学段/
    # 章节下拉框无法预选、题目只能人工逐题补。只要能在当前教材目录中唯一还原，
    # 就写回精确的目录标签；无法唯一还原时保持原值交给人工。
    current_curriculum = get_current_curriculum()
    for q in parsed_questions:
        resolved = canonical_category_labels(
            current_curriculum,
            q.get("category_compulsory", ""),
            q.get("category_chapter", ""),
            q.get("category_knowledge") or "",
        )
        if resolved is not None:
            q["category_compulsory"], q["category_chapter"], q["category_knowledge"] = resolved

    # 1. 搜集该 PDF 任务在 tmp 文件夹中生成的所有物理裁剪图片，按生成时间（mtime）进行排序
    task_crop_urls = []
    if task_id:
        crop_pattern = os.path.join(TMP_UPLOAD_DIR, f"pdf_crop_{task_id}_*.png")
        crop_files = glob.glob(crop_pattern)
        crop_files.sort(key=lambda x: os.path.getmtime(x))
        task_crop_urls = [f"/{UPLOAD_DIR_REL}/tmp/{os.path.basename(f)}" for f in crop_files]
        print(f"[PDF PostProcess] 发现任务 {task_id} 的实际裁剪图片 {len(task_crop_urls)} 张: {task_crop_urls}")

    # 2. 顺序提取出所有题目中未成功解析的插图占位符（例如 图1.png, 图2.png, 图1, 图2 等，特征是不以 /static/ 开头的图片引用路径）
    placeholders_in_order = []
    placeholder_seen = set()
    
    # 匹配 Markdown 图片格式: ![alt](url)
    md_pattern = r'!\[.*?\]\(([^)]+)\)'
    # 匹配 LaTeX 图片格式: \includegraphics[...]{path}
    latex_pattern = r'\\includegraphics(?:\[.*?\])?\{([^}]+)\}'
    
    for index, q in enumerate(parsed_questions):
        if index in preserved:
            continue
        for field in ["content", "answer_markdown"]:
            text_val = q.get(field, "")
            if isinstance(text_val, str):
                # 提取 Markdown 图片占位符
                for m in re.finditer(md_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)
                # 提取 LaTeX 图片占位符
                for m in re.finditer(latex_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)

    # 3. 建立占位符与物理裁剪图片路径的 1-to-1 映射关系
    mapping = {}
    for idx, ph in enumerate(placeholders_in_order):
        if idx < len(task_crop_urls):
            mapping[ph] = task_crop_urls[idx]
    if mapping:
        print(f"[PDF PostProcess] 成功建立占位符修复映射: {mapping}")

    # 4. 对每个题目卡片进行字段修补、占位符替换与资源晋升准备
    for index, q in enumerate(parsed_questions):
        q["source"] = (q.get("source") or paper_title).strip()
        
        # 清理多余的双重转义 \n
        if index not in preserved:
            for field in ["content", "answer_markdown"]:
                if field in q and isinstance(q[field], str):
                    text = q[field]
                    text = re.sub(r'\\n(?![a-zA-Z])', '\n', text)
                    q[field] = text

        # 智能替换 Markdown 和 LaTeX 字段中的图片占位符
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                # 替换已建立映射的非标准路径
                for ph, real_url in (() if index in preserved else mapping.items()):
                    if ph in q[field]:
                        q[field] = q[field].replace(ph, real_url)
                        # 如果是 LaTeX 的 \includegraphics 语法，顺带转换为 Markdown 图片语法以供前端预览渲染
                        latex_img_pattern = r'\\includegraphics(?:\[.*?\])?\{' + re.escape(real_url) + r'\}'
                        q[field] = re.sub(latex_img_pattern, f'![插图]({real_url})', q[field])

        # 寻找本题正文中夹带的所有临时图片 URL (注意：UUID 中含有 -，所以 regex 必须支持 [a-zA-Z0-9_-]+)
        found_crops = {}
        if index in preserved:
            # Literal source examples are not live image references. The
            # optimizer already conserves real anchors in both source fields.
            for path in embedded_question_assets(q.get("content", ""), q.get("answer_markdown", "")):
                if "/tmp/" in path:
                    found_crops[path] = None
        else:
            for field in ["content", "answer_markdown"]:
                if field in q and isinstance(q[field], str):
                    for match in re.finditer(r'/static/(?:uploads|test_uploads)/tmp/[a-zA-Z0-9_.-]+', q[field]):
                        found_crops[match.group(0)] = None
                    
        # 顺带检查 referenced_images 属性并应用修复映射
        ref_imgs = [] if index in preserved else q.get("referenced_images", [])
        for ref in ref_imgs:
            mapped_ref = mapping.get(ref, ref)
            if "/tmp/" in mapped_ref:
                filename = os.path.basename(mapped_ref)
                found_crops[f"/{UPLOAD_DIR_REL}/tmp/{filename}"] = None
                
        # 按正文、解答、补充引用的首次出现顺序登记，不能用无序集合打乱图片。
        q["image_paths"] = list(found_crops)

    # 5. 极致兜底机制：如果大模型在拆题时完全删除了图片占位标记或路径，导致最终题目关联的图片为空，
    # 我们利用 3-shingle 文本重合度，将原始 PDF 物理页面产生的物理插图自动关联绑定回拆分出的题目！
    if ocr_results and task_id and len(preserved) < len(parsed_questions):
        page_crops = {}
        for p_idx, page_text in enumerate(ocr_results):
            # 获取当前页生成的所有 pdf_crop_ 临时文件 URL
            urls_on_page = re.findall(r'/static/uploads(?:_test|/test_uploads|/uploads)?/tmp/pdf_crop_[a-zA-Z0-9_-]+\.png', page_text or "")
            page_crops[p_idx] = list(dict.fromkeys(urls_on_page))
            
        print(f"[PDF PostProcess Failsafe] 每页识别到的插图关系: {page_crops}")
        
        for index, q in enumerate(parsed_questions):
            if index in preserved:
                continue
            if not q.get("image_paths"):
                p_source = find_source_page_by_overlap(q.get("content", ""), ocr_results)
                crops = page_crops.get(p_source, [])
                if crops:
                    q["image_paths"] = crops
                    print(f"[PDF PostProcess Failsafe] 成功通过重合度，将第 {p_source + 1} 页的插图 {crops} 兜底分配给题目: {q.get('content')[:40]}...")

    # Keep image markup in place. The preview already skips thumbnails for images
    # rendered in Markdown; stripping markup here empties image-only choices and
    # destroys the relationship between an option label and its graph.
    return parsed_questions


def run_pdf_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
    page_range: str = None,
    pdf_strategy: str = "native_preferred",
    pdf_verify_suspicions: bool = True,
):
    """PDF parsing with bounded OCR concurrency and cooperative cancellation."""

    import concurrent.futures

    temp_assets: list[str] = []
    tmp_pdf_path = Path(TMP_UPLOAD_DIR) / f"{task_id}.pdf"
    diagnostics: dict = {}
    layout_result = None
    page_urls = []
    target_page_indices = []
    ocr_results = []
    source_pages = None
    vision_lock = threading.Lock()

    def check_pdf_cancelled():
        DOCUMENT_TASKS.check_cancelled(task_id)
        if not DOCUMENT_TASKS.exists(task_id):
            raise TaskCancelled("PDF 任务已移除。")

    def report_pdf_attempt(event):
        check_pdf_cancelled()
        with vision_lock:
            report = diagnostics.setdefault("pdf_vision", {
                "timeout_seconds": DOCUMENT_AI_TIMEOUT_SECONDS, "max_attempts": 2, "attempts": [],
            })
            event = {**event, "page_number": event["page_index"] + 1}
            identity = (event["page_index"], event["stage"], event["attempt"])
            previous = next((item for item in report["attempts"]
                             if (item["page_index"], item["stage"], item["attempt"]) == identity), None)
            if previous is None:
                report["attempts"].append(event)
            else:
                previous.update(event)
            report["calls"] = len(report["attempts"])
            report["retries"] = sum(item["attempt"] > 1 for item in report["attempts"])
            report["usage"] = collected_usage(report["attempts"])
            report["usage_complete"] = all(all(key in item["usage"] for key in
                                               ("prompt_tokens", "completion_tokens", "total_tokens"))
                                           for item in report["attempts"])
            if event["status"] == "running":
                log = f"第 {event['page_number']} 页识别：第 {event['attempt']} / 2 次尝试，每次等待上限 {DOCUMENT_AI_TIMEOUT_SECONDS} 秒..."
            elif event.get("retrying"):
                log = f"第 {event['page_number']} 页识别失败，准备重试一次：{event['error']}"
            else:
                log = None
            changes = {"diagnostics": copy.deepcopy(diagnostics)}
            if log:
                changes["log"] = log
            DOCUMENT_TASKS.update(task_id, **changes)

    def report_pdf_split_attempt(event):
        check_pdf_cancelled()
        if event.get("stream_progress"):
            received = event.get("stream_content_characters", 0)
            log = f"模型已返回 {received} 个正文字符，正在接收完整拆题结果..." if received else None
        elif event["status"] == "running":
            log = f"正在拆解已识别的试卷文本：第 {event['attempt']} / 2 次尝试，每次等待上限 {PAPER_SPLIT_TIMEOUT_SECONDS} 秒..."
        elif event.get("retrying"):
            log = f"试卷文本拆题失败，准备重试一次：{event['error']}"
        else:
            log = None
        changes = {"diagnostics": copy.deepcopy(diagnostics)}
        if log:
            changes["log"] = log
        DOCUMENT_TASKS.update(task_id, **changes)

    def retain_split_input(source_content, model_source):
        # Task-local diagnostic evidence; this is not a persistent response
        # cache or a promise that a failed upload can be resumed automatically.
        cache = {"source_sha256": hashlib.sha256(file_bytes).hexdigest(),
                 "model_source_sha256": hashlib.sha256(model_source.encode("utf-8")).hexdigest(),
                 "input_characters": len(model_source), "status": "complete",
                 "source_markdown": source_content, "model_source": model_source}
        if len(json.dumps(cache, ensure_ascii=False)) > PAPER_SPLIT_CACHE_MAX_CHARACTERS:
            cache = {key: value for key, value in cache.items() if key not in ("source_markdown", "model_source")}
            cache["status"] = "metadata_only"
        DOCUMENT_TASKS.update(task_id, pdf_source_cache=cache)

    try:
        import pymupdf as fitz
    except ImportError:
        DOCUMENT_TASKS.fail(
            task_id,
            "本地 Python 环境未安装 PyMuPDF，请通过 pip install pymupdf 安装依赖！",
            document_type="pdf",
        )
        return

    try:
        DOCUMENT_TASKS.check_cancelled(task_id)
        if pdf_strategy not in PDF_STRATEGIES:
            raise ValueError("不支持的 PDF 解析策略。")
        tmp_pdf_path.write_bytes(file_bytes)
        DOCUMENT_TASKS.update(
            task_id,
            status="processing_images",
            progress=10,
            log="已接收文件，正在渲染 PDF 高清页面...",
            document_type="pdf",
            temp_assets=[],
        )

        page_images: list[str] = []
        page_urls: list[str] = []
        page_layout_infos: dict[int, dict] = {}
        joint_page_results: dict[int, dict] = {}
        with fitz.open(tmp_pdf_path) as document:
            total_pages = len(document)
            if total_pages == 0:
                raise ValueError("此 PDF 没有有效页面，或者格式已损坏！")
            target_page_indices = parse_page_range(page_range, total_pages)
            if len(target_page_indices) > MAX_PDF_TASK_PAGES:
                raise ValueError(
                    f"单次最多解析 {MAX_PDF_TASK_PAGES} 页，请填写较小的页码范围。"
                )

            for page_num in target_page_indices:
                DOCUMENT_TASKS.check_cancelled(task_id)
                page = document.load_page(page_num)
                if pdf_strategy == "layout_aware":
                    page_layout_infos[page_num] = inspect_pdf_page(page, page_num)
                estimated_pixels = int(
                    (page.rect.width / 72 * 150) * (page.rect.height / 72 * 150)
                )
                if estimated_pixels > 30_000_000:
                    raise ValueError(f"第 {page_num + 1} 页尺寸异常，已停止高清渲染。")
                pixmap = page.get_pixmap(dpi=150)
                image_filename = f"pdf_page_{task_id}_{page_num}.png"
                image_path = Path(TMP_UPLOAD_DIR) / image_filename
                pixmap.save(image_path)
                image_url = f"/{UPLOAD_DIR_REL}/tmp/{image_filename}"
                page_images.append(str(image_path))
                page_urls.append(image_url)
                temp_assets.append(image_url)
                DOCUMENT_TASKS.update(
                    task_id,
                    page_images=list(page_urls),
                    page_numbers=[number + 1 for number in target_page_indices[:len(page_urls)]],
                    temp_assets=list(temp_assets),
                )

        tmp_pdf_path.unlink(missing_ok=True)
        DOCUMENT_TASKS.check_cancelled(task_id)
        total_target_pages = len(target_page_indices)

        if pdf_strategy == "force_ocr":
            inspector_result = {"pages": [], "pdf_type": "scanned"}
            inspector_pages = {}
        else:
            inspector_result = inspect_and_extract_pdf(
                file_bytes,
                task_id,
                page_indices=target_page_indices,
                include_source_review_evidence=True,
            )
            inspector_pages = {
                int(page.get("page_index")): page
                for page in inspector_result.get("pages", [])
                if page.get("page_index") is not None
            }
            diagnostics["pdf_native_quality"] = [
                {"page_number": index + 1, "reasons": page["quality_reasons"]}
                for index, page in inspector_pages.items() if page.get("quality_reasons")
            ]
            diagnostics["pdf_native_repair"] = [
                {"page_number": index + 1, **page["native_repair"]}
                for index, page in inspector_pages.items() if page.get("native_repair")
            ]
        DOCUMENT_TASKS.check_cancelled(task_id)

        ocr_results = [None] * total_target_pages
        pages_requiring_ocr = []
        native_page_count = 0
        for local_idx, page_num in enumerate(target_page_indices):
            page_info = inspector_pages.get(page_num)
            native_text = str((page_info or {}).get("markdown") or "").strip()
            if page_info and not page_info.get("needs_ocr") and native_text:
                ocr_results[local_idx] = (
                    f"<!-- MATHBANK_PDF_PAGE:{page_num + 1} -->\n{native_text}"
                )
                native_page_count += 1
            else:
                pages_requiring_ocr.append(local_idx)

        # Work from physical PDF rows, never from a scrambled Markdown table.
        # Planning is local and conservative; only a validated plan changes the
        # paid request. A failed paid regional request is never retried as a page.
        regional_plans: dict[int, dict] = {}
        if pdf_strategy != "force_ocr" and pages_requiring_ocr:
            from mathbank.pdf_native_regions import plan_pdf_regions
            with fitz.open(stream=file_bytes, filetype="pdf") as document:
                for local_idx in pages_requiring_ocr:
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    page_num = target_page_indices[local_idx]
                    info = page_layout_infos.get(page_num)
                    if info is None:
                        info = inspect_pdf_page(document[page_num], page_num)
                        page_layout_infos[page_num] = info
                    plan = plan_pdf_regions(document[page_num], info)
                    if plan is not None:
                        regional_plans[local_idx] = plan

        # Auxiliary views do not own text/slots and are not regional OCR or
        # independent verification. Render from the original PDF serially;
        # HTTP workers get only internally registered paths and metadata.
        detail_results: dict[int, dict] = {}
        if pdf_strategy == "layout_aware":
            from mathbank.pdf_vision_details import render_pdf_detail_views
            detail_bytes_remaining = 80 * 1024 * 1024

            def register_detail_asset(path):
                if path not in temp_assets:
                    temp_assets.append(path)
                if not DOCUMENT_TASKS.add_temp_asset(task_id, path):
                    raise TaskCancelled("PDF 任务已移除。")
                check_pdf_cancelled()

            full_page_indices = [index for index in pages_requiring_ocr if index not in regional_plans]
            if full_page_indices:
                DOCUMENT_TASKS.update(task_id, log="正在准备原页局部放大视图，辅助精读小字和数学符号...")
                with fitz.open(stream=file_bytes, filetype="pdf") as document:
                    for local_idx in full_page_indices:
                        check_pdf_cancelled()
                        page_num = target_page_indices[local_idx]
                        try:
                            details = render_pdf_detail_views(
                                document[page_num], page_index=page_num,
                                output_dir=Path(TMP_UPLOAD_DIR),
                                asset_prefix=f"pdf_detail_{task_id}_{page_num}",
                                url_prefix=f"/{UPLOAD_DIR_REL}/tmp",
                                register_asset=register_detail_asset,
                                check_cancelled=check_pdf_cancelled,
                                byte_budget=detail_bytes_remaining,
                            )
                        except TaskCancelled:
                            raise
                        except Exception:
                            # Optional local preparation must not discard the
                            # already rendered complete navigation page.
                            details = {"status": "skipped", "views": [], "total_png_bytes": 0,
                                       "notes": ["本页高清辅助视图无法准备，继续使用整页识图。"]}
                        detail_results[local_idx] = details
                        detail_bytes_remaining -= details.get("total_png_bytes", 0)
            diagnostics["pdf_detail_views"] = {
                "prepared_pages": sum(bool(item["views"]) for item in detail_results.values()),
                "prepared_images": sum(len(item["views"]) for item in detail_results.values()),
                "separate_detail_requests": 0, "independent_verification": False,
                "pages": [{"page_number": target_page_indices[index] + 1,
                           "status": item["status"], "notes": list(item["notes"]),
                           "views": [{key: view[key] for key in
                                      ("id", "page_index", "bbox", "actualdpi", "width", "height", "sha256")}
                                     for view in item["views"]]}
                          for index, item in detail_results.items()],
            }
        extraction_pages = []
        for local_idx, page_num in enumerate(target_page_indices):
            plan = regional_plans.get(local_idx)
            extraction_pages.append({
                "page_number": page_num + 1,
                "mode": ("regional" if plan else "full_vision") if local_idx in pages_requiring_ocr else "native",
                "structure_repaired": inspector_pages.get(page_num, {}).get("native_repair", {}).get("status") == "repaired",
                "native_characters": plan["native_characters"] if plan else 0,
                "region_count": len(plan["regions"]) if plan else 0,
                "image_area_ratio": plan["area_ratio"] if plan else (1 if local_idx in pages_requiring_ocr else 0),
            })
        diagnostics["pdf_extraction"] = {
            "native_pages": native_page_count,
            "repaired_pages": sum(
                inspector_pages.get(page_num, {}).get("native_repair", {}).get("status") == "repaired"
                and not inspector_pages.get(page_num, {}).get("needs_ocr")
                for page_num in target_page_indices
            ),
            "regional_pages": len(regional_plans),
            "full_vision_pages": len(pages_requiring_ocr) - len(regional_plans),
            "native_characters_reused": sum(plan["native_characters"] for plan in regional_plans.values()),
            "pages": extraction_pages,
        }
        DOCUMENT_TASKS.update(task_id, diagnostics=diagnostics)

        if native_page_count:
            print(
                f"[PDF Inspector Flow] 原生直提 {native_page_count} 页，"
                f"视觉 OCR {len(pages_requiring_ocr)} 页 "
                f"(Type: {inspector_result.get('pdf_type')})",
                flush=True,
            )

        if not pages_requiring_ocr:
            DOCUMENT_TASKS.update(
                task_id,
                status="ai_splitting",
                progress=60,
                log=(
                    f"pdf-inspector 已按所选范围可靠提取 {native_page_count} 页原生文本，"
                    "正在连续拆题..."
                ),
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )
        else:
            if pdf_strategy == "force_ocr":
                extraction_log = f"按所选全页识图模式，正在识别 {total_target_pages} 页文字与公式..."
            elif regional_plans:
                extraction_log = (
                    f"所选 {total_target_pages} 页：原生直提 {native_page_count} 页，"
                    f"局部识别 {len(regional_plans)} 页，"
                    f"整页识别 {len(pages_requiring_ocr) - len(regional_plans)} 页；"
                    "局部页保留可靠原文，仅发送需要补全的区域..."
                )
            elif native_page_count == 0:
                extraction_log = (
                    f"已尝试原生提取，所选 {total_target_pages} 页的文字或公式未通过质量检查，"
                    "改用逐页视觉识别..."
                )
            else:
                extraction_log = (
                    f"所选 {total_target_pages} 页中，{native_page_count} 页采用原生文字，"
                    f"其余 {len(pages_requiring_ocr)} 页因提取质量问题改用视觉识别..."
                )
            DOCUMENT_TASKS.update(
                task_id,
                status="ocr_extraction",
                progress=30,
                log=extraction_log,
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )

            def ocr_worker(local_idx, image_path):
                acquired = False
                try:
                    while not acquired:
                        check_pdf_cancelled()
                        acquired = PDF_OCR_SEMAPHORE.acquire(timeout=0.25)
                    check_pdf_cancelled()
                    real_page_num = target_page_indices[local_idx] + 1
                    joint = None
                    if local_idx in regional_plans:
                        from mathbank.pdf_region_vision import request_pdf_regions
                        joint = request_pdf_regions(
                            image_path, page_layout_infos[real_page_num - 1], regional_plans[local_idx],
                            include_figures=pdf_strategy == "layout_aware",
                            check_cancelled=check_pdf_cancelled, report_attempt=report_pdf_attempt,
                            include_transcription_evidence=True,
                        )
                        raw_text = joint["markdown"]
                    elif pdf_strategy == "layout_aware":
                        from mathbank.pdf_page_vision import request_pdf_page
                        detail_kwargs = ({"detail_views": detail_results[local_idx]["views"]}
                                         if detail_results.get(local_idx, {}).get("views") else {})
                        joint = request_pdf_page(
                            image_path, page_layout_infos[real_page_num - 1],
                            check_cancelled=check_pdf_cancelled, report_attempt=report_pdf_attempt,
                            **detail_kwargs,
                            include_transcription_evidence=True,
                        )
                        raw_text = joint["markdown"]
                    else:
                        raw_text = ocr_pdf_page_image(image_path, check_cancelled=check_pdf_cancelled,
                                                     report_attempt=report_pdf_attempt,
                                                     page_index=real_page_num - 1)
                    print(
                        f"[PDF OCR] 第 {real_page_num} 页识别完成 "
                        f"(characters={len(raw_text)})."
                    )
                    return local_idx, raw_text, None, joint
                except TaskCancelled:
                    raise
                except Exception as ocr_error:
                    return local_idx, "", str(ocr_error), None
                finally:
                    if acquired:
                        PDF_OCR_SEMAPHORE.release()

            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(pages_requiring_ocr), 4),
                thread_name_prefix="mathbank-pdf-ocr",
            )
            futures = []
            try:
                for local_idx in pages_requiring_ocr:
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    futures.append(
                        executor.submit(ocr_worker, local_idx, page_images[local_idx])
                    )

                completed = 0
                page_errors = []
                for future in concurrent.futures.as_completed(futures):
                    check_pdf_cancelled()
                    local_idx, text, error, joint = future.result()
                    if error:
                        real_page_num = target_page_indices[local_idx] + 1
                        page_errors.append({"page_number": real_page_num, "error": error})
                    else:
                        processed_text = process_ocr_illustrations(text)
                        real_page_num = target_page_indices[local_idx] + 1
                        if joint is not None:
                            joint_page_results[real_page_num - 1] = joint
                        ocr_results[local_idx] = (
                            f"<!-- MATHBANK_PDF_PAGE:{real_page_num} -->\n"
                            f"{processed_text.strip()}"
                        )
                    completed += 1
                    progress = 30 + int(
                        (completed / len(pages_requiring_ocr)) * 40
                    )
                    DOCUMENT_TASKS.update(
                        task_id,
                        progress=progress,
                        log=(
                            f"视觉转译进度: {completed} / "
                            f"{len(pages_requiring_ocr)} 页已处理..." +
                            (f"（局部识别 {len(regional_plans)} 页）" if regional_plans else "") +
                            (f"；{len(page_errors)} 页未完成，已保留其他页面结果。" if page_errors else "")
                        ),
                    )
                if page_errors:
                    diagnostics["pdf_failed_pages"] = sorted(page_errors, key=lambda item: item["page_number"])
                    raise RuntimeError("；".join(f"解析第 {item['page_number']} 页出错: {item['error']}"
                                                 for item in diagnostics["pdf_failed_pages"]))
            finally:
                cancelled = DOCUMENT_TASKS.is_cancelled(task_id)
                if cancelled:
                    for future in futures:
                        future.cancel()
                executor.shutdown(wait=not cancelled, cancel_futures=True)

        regional_results = [joint_page_results[target_page_indices[index]] for index in regional_plans]
        if "pdf_detail_views" in diagnostics:
            successful_details = [item.get("detail_input", {}) for item in joint_page_results.values()
                                  if item.get("detail_input", {}).get("status") == "included"]
            diagnostics["pdf_detail_views"]["successful_input_pages"] = len(successful_details)
            diagnostics["pdf_detail_views"]["successful_input_images"] = sum(
                item["detail_count"] for item in successful_details)
            for index, details in detail_results.items():
                joint = joint_page_results.get(target_page_indices[index], {})
                for note in [*details["notes"], *joint.get("detail_input", {}).get("notes", [])]:
                    if joint.get("layout") is not None:
                        joint["layout"].setdefault("notes", []).append(note)
        if regional_results:
            diagnostics["pdf_regional_usage"] = {
                key: sum(item["usage"][key] for item in regional_results)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if all(key in item.get("usage", {}) for item in regional_results)
            }
        DOCUMENT_TASKS.check_cancelled(task_id)
        if pdf_strategy == "layout_aware":
            def check_layout_cancelled():
                DOCUMENT_TASKS.check_cancelled(task_id)
                if not DOCUMENT_TASKS.exists(task_id):
                    raise TaskCancelled("PDF 任务已移除。")

            def register_figure_asset(path):
                temp_assets.append(path)
                if not DOCUMENT_TASKS.add_temp_asset(task_id, path):
                    raise TaskCancelled("PDF 任务已移除。")
                check_layout_cancelled()

            def report_layout_progress(local_index, message):
                check_layout_cancelled()
                DOCUMENT_TASKS.update(
                    task_id, status="layout_analysis",
                    progress=72 + int(7 * local_index / max(1, total_target_pages)),
                    log=message,
                )

            # Each independent layout request shares the process-wide vision
            # bound. Local validation/cropping never occupies an HTTP permit.
            layout_result = enrich_pdf_with_figures(
                file_bytes, target_page_indices, page_images, page_urls,
                ocr_results, {target_page_indices[index] for index in pages_requiring_ocr},
                output_dir=Path(TMP_UPLOAD_DIR), url_prefix=f"/{UPLOAD_DIR_REL}/tmp",
                task_id=task_id, check_cancelled=check_layout_cancelled,
                register_asset=register_figure_asset, report_progress=report_layout_progress,
                report_attempt=report_pdf_attempt,
                precomputed_layouts={index: item["layout"] for index, item in joint_page_results.items()},
                precomputed_page_infos=page_layout_infos,
                vision_semaphore=PDF_OCR_SEMAPHORE,
            )
            ocr_results = layout_result["page_texts"]
            diagnostics["pdf_layout"] = layout_result["diagnostics"]
            diagnostics["pdf_layout"]["joint_visual_calls"] = sum(item.get("visual_calls", 1) for item in joint_page_results.values())
            diagnostics["pdf_layout"]["regional_visual_calls"] = sum(item.get("visual_calls", 1) for item in regional_results)
            diagnostics["pdf_joint_usage"] = {
                key: sum(item["usage"][key] for item in joint_page_results.values())
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if joint_page_results and all(key in item.get("usage", {}) for item in joint_page_results.values())
            }
            DOCUMENT_TASKS.update(task_id, pdf_layout={
                "schema": layout_result["schema"], "pages": layout_result["pages"],
            }, diagnostics=diagnostics)
        # Retain the exact first-pass transcription used for splitting. Later
        # source review can check its ranges without re-running page recognition.
        # Keep complete pages or no page cache; never certify truncated evidence.
        source_pages = []
        if sum(len(str(text or "")) for text in ocr_results) <= 500_000:
            for local_idx, page_num in enumerate(target_page_indices):
                page = next((item for item in (layout_result or {}).get("pages", [])
                             if item["page_index"] == page_num), {})
                origin = ("regional_vision" if local_idx in regional_plans else
                          "joint_vision" if page_num in joint_page_results else
                          "ocr" if local_idx in pages_requiring_ocr else
                          "native_repaired" if inspector_pages.get(page_num, {}).get("native_repair", {}).get("status") == "repaired"
                          else "native")
                source_pages.append({"page_number": page_num + 1, "origin": origin,
                                     "markdown": str(ocr_results[local_idx] or ""),
                                     "figures": page.get("figures", [])})
            DOCUMENT_TASKS.update(task_id, pdf_source_pages=source_pages)
        full_latex_content = merge_pdf_page_texts(ocr_results)
        if not full_latex_content.strip():
            raise ValueError("所选 PDF 页面未能提取出可解析的文字内容。")

        DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=80,
            log="文本与公式准备就绪！正在调用大模型拆解题目与标注属性...",
        )
        DOCUMENT_TASKS.check_cancelled(task_id)

        paper_title = os.path.splitext(filename)[0]
        auto_title = extract_title_from_latex(full_latex_content)
        if auto_title:
            paper_title = auto_title
        if layout_result is not None:
            # Page markers track provenance, never count as question text and
            # must not become an artificial break in a cross-page question.
            source_content = re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->", "", full_latex_content)
            locked_source, math_locks = lock_visible_math(source_content, task_id.replace("-", "")[:16])
            diagnostics["math_locks_created"] = len(math_locks)
            retain_split_input(source_content, locked_source)
            from mathbank.pdf_hybrid_request import prepare_pdf_task_plan, request_pdf_hybrid
            from mathbank.pdf_hybrid_plan import pdf_hybrid_plan_diagnostics
            source_assets = {}
            for page in layout_result.get("pages", []):
                for figure in page.get("figures", []):
                    url = figure.get("image_path")
                    if isinstance(url, str):
                        source_assets[url] = str(resolve_upload_asset(url, uploads_dir=UPLOAD_DIR,
                            url_prefix=UPLOAD_DIR_REL))
            pdf_plan = prepare_pdf_task_plan(source_content, diagnostics, source_pages=source_pages,
                layout_result=layout_result, task_id=task_id, generation=0,
                source_document_sha256=hashlib.sha256(file_bytes).hexdigest(),
                native_evidence=inspector_result.get("_native_source_review_evidence"),
                witnesses={index: value.get("_transcription_witness") for index, value in joint_page_results.items()},
                transcript_normalizer=process_ocr_illustrations, figure_assets=source_assets)
            diagnostics["pdf_hybrid"] = pdf_hybrid_plan_diagnostics(pdf_plan)
            pdf_preserved_indices = frozenset()
            if pdf_plan["mode"] in {"hybrid", "whole_metadata"}:
                parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
                hybrid_result = request_pdf_hybrid(pdf_plan, get_current_curriculum(),
                    provider=resolve_text_provider(parse_model), post=post_chat_completion, diagnostics=diagnostics,
                    normalize_fillin=normalize_fillin_macro, task_id=task_id, generation=0,
                    check_cancelled=check_pdf_cancelled,
                    full_source_fallback=lambda: parse_paper_text_internal(locked_source, False,
                        diagnostics=diagnostics, preserve_source_answers=True, pdf_retry=False,
                        connection_retry=False, check_cancelled=check_pdf_cancelled))
                parsed_questions = hybrid_result.questions
                pdf_preserved_indices = hybrid_result.preserved_indices
                diagnostics["pdf_hybrid"]["partial"] = hybrid_result.partial
            else:
                parsed_questions = parse_paper_text_internal(
                    locked_source, False, diagnostics=diagnostics, preserve_source_answers=True,
                    pdf_retry=True, check_cancelled=check_pdf_cancelled, report_attempt=report_pdf_split_attempt,
                )
            DOCUMENT_TASKS.check_cancelled(task_id)
            staged = copy.deepcopy(parsed_questions) if pdf_preserved_indices else parsed_questions
            diagnostics.update(reconcile_visible_math(staged, math_locks, source_content))
            if pdf_preserved_indices:
                for index, candidate in enumerate(staged):
                    if index not in pdf_preserved_indices:
                        parsed_questions[index] = candidate
                    elif candidate.get("source_review"):
                        parsed_questions[index]["source_review"] = candidate["source_review"]
            finalize_source_answers(parsed_questions, source_content)
            apply_pdf_layout_reviews(parsed_questions, layout_result, diagnostics)
            isolate_shared_pdf_figures(
                parsed_questions, layout_result, output_dir=Path(TMP_UPLOAD_DIR),
                url_prefix=f"/{UPLOAD_DIR_REL}/tmp", register_asset=register_figure_asset,
                check_cancelled=check_layout_cancelled,
            )
        else:
            pdf_preserved_indices = frozenset()
            retain_split_input(full_latex_content, full_latex_content)
            parsed_questions = parse_paper_text_internal(
                full_latex_content,
                False,  # Extract original answers only; the frontend solves missing answers.
                diagnostics=diagnostics,
                pdf_retry=True, check_cancelled=check_pdf_cancelled, report_attempt=report_pdf_split_attempt,
            )
        DOCUMENT_TASKS.check_cancelled(task_id)
        final_questions = post_process_pdf_parsed_questions(
            parsed_questions,
            paper_title,
            task_id,
            None if layout_result is not None else ocr_results,
            preserved_indices=pdf_preserved_indices,
        )
        if layout_result is not None:
            from mathbank.pdf_symbol_risks import annotate_pdf_symbol_risks
            annotate_pdf_symbol_risks(final_questions, diagnostics)
            # Refresh explanations only: reapplying layout attachment here
            # would undo the independently cloned image identity above.
            refresh_pdf_review_items(final_questions, layout_result, diagnostics)
            if pdf_verify_suspicions and diagnostics.get("source_review_count"):
                DOCUMENT_TASKS.update(
                    task_id, status="source_verification", progress=90,
                    log="正在对照原页核验疑点，发现明确差异后将尝试修正并复核...",
                    diagnostics=diagnostics,
                )
                acquired = False
                try:
                    from mathbank.pdf_source_verify import verify_pdf_source_suspicions
                    while not acquired:
                        DOCUMENT_TASKS.check_cancelled(task_id)
                        acquired = PDF_OCR_SEMAPHORE.acquire(timeout=0.25)
                    diagnostics["pdf_source_verification"] = verify_pdf_source_suspicions(
                        final_questions, diagnostics, page_urls,
                        [number + 1 for number in target_page_indices],
                        source_pages=source_pages,
                        candidate_image_paths=list(temp_assets),
                        progress=lambda message: DOCUMENT_TASKS.update(task_id, log=message),
                        check_cancelled=lambda: DOCUMENT_TASKS.check_cancelled(task_id),
                    )
                except TaskCancelled:
                    raise
                except Exception as exc:
                    diagnostics["pdf_source_verification"] = {
                        "status": "failed", "pending": diagnostics.get("source_review_count", 0),
                        "notes": [f"原页核验未完成（{type(exc).__name__}），已保留拆题结果和原核对提示。"],
                    }
                finally:
                    if acquired:
                        PDF_OCR_SEMAPHORE.release()
            else:
                diagnostics["pdf_source_verification"] = {
                    "status": "no_candidates" if pdf_verify_suspicions else "disabled",
                    "calls": 0, "checked": 0, "confirmed": 0,
                    "pending": diagnostics.get("source_review_count", 0),
                    "skipped": 0, "usage": {},
                }
        make_source_review_advisory(final_questions, diagnostics)
        DOCUMENT_TASKS.check_cancelled(task_id)
        partial = bool(diagnostics.get("pdf_hybrid", {}).get("partial"))
        completion_log = (f"PDF 已保留 {len(final_questions)} 道完整题目；部分疑点题组未完成，原文与未匹配题段可展开核对。"
                          if partial else "拆分完成，可选择题目导入；原文和配图说明可按需展开查看。")
        completed = DOCUMENT_TASKS.complete(
            task_id,
            log=completion_log,
            partial=partial,
            data=final_questions,
            generate_answers=generate_answers,
            page_images=list(page_urls),
            page_numbers=[number + 1 for number in target_page_indices],
            temp_assets=list(temp_assets),
            document_type="pdf",
            diagnostics=diagnostics,
        )
        if not completed:
            _delete_task_temp_assets(temp_assets)
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        # Keep source pages and completed transcripts available for inspection
        # after a failed page. The task's normal expiry owns asset cleanup.
        evidence = source_pages
        if evidence is None:
            evidence = [{"page_number": page_num + 1, "markdown": str(ocr_results[index] or ""),
                         "status": "completed" if ocr_results[index] else "failed"}
                        for index, page_num in enumerate(target_page_indices) if index < len(ocr_results)]
            evidence = evidence if sum(len(item["markdown"]) for item in evidence) <= 500_000 else None
        DOCUMENT_TASKS.fail(
            task_id,
            f"PDF 智能拆解解析失败: {str(ex)}",
            document_type="pdf",
            diagnostics=copy.deepcopy(diagnostics), pdf_source_pages=evidence,
            page_images=list(page_urls), page_numbers=[number + 1 for number in target_page_indices],
            temp_assets=list(temp_assets),
        )
    finally:
        tmp_pdf_path.unlink(missing_ok=True)


def parse_page_range(range_str: str, total_pages: int) -> list:
    """
    解析用户输入的页码范围字符串（1-indexed），转换为包含 0-indexed 页面索引的列表。
    支持格式如 "1-5", "1,3,5", "1-3,5,7-9"。
    """
    if total_pages <= 0:
        raise ValueError("PDF 没有有效页面。")
    if not range_str or not range_str.strip():
        return list(range(total_pages))

    pages = set()
    parts = str(range_str).replace(" ", "").split(",")
    for part in parts:
        if not part:
            raise ValueError("页码范围格式无效。")
        if "-" in part:
            sub_parts = part.split("-")
            if len(sub_parts) != 2:
                raise ValueError("页码范围格式无效。")
            try:
                start = int(sub_parts[0])
                end = int(sub_parts[1])
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if start < 1 or end < start or end > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.update(range(start - 1, end))
        else:
            try:
                page_number = int(part)
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if page_number < 1 or page_number > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.add(page_number - 1)

    if not pages:
        raise ValueError("页码范围不能为空。")
    return sorted(pages)


# ----------------- PDF Upload & Task Routing Endpoints -----------------

@app.post("/api/upload/pdf-task")
def upload_pdf_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
    page_range: Optional[str] = Form(None),
    pdf_strategy: str = Form("native_preferred"),
    pdf_verify_suspicions: bool = Form(True),
):
    try:
        if pdf_strategy not in PDF_STRATEGIES:
            return JSONResponse(content={"status": "error", "message": "不支持的 PDF 解析策略。"}, status_code=400)
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .pdf 格式！"},
                status_code=400
            )
            
        # Incremental cap avoids loading an arbitrarily large multipart file.
        try:
            content = read_stream_limited(file.file, MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "PDF 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if not content.lstrip().startswith(b"%PDF-"):
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 PDF 文档！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="任务已排队，正在准备运行异步切片分析...",
            document_type="pdf",
            temp_assets=[],
        )
        try:
            DOCUMENT_TASKS.submit(
                task_id,
                run_pdf_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
                page_range,
                pdf_strategy,
                bool(pdf_verify_suspicions) if pdf_strategy == "layout_aware" else False,
            )
        except TaskQueueFull as exc:
            DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
            )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 PDF 解析任务失败: {str(e)}"},
            status_code=500
        )


@serialize_asset_lifecycle
def _delete_task_temp_assets(paths: list) -> int:
    """Retain task files recoverably so old drafts and result URLs still work."""
    removed = 0
    tmp_root = Path(TMP_UPLOAD_DIR).resolve()
    # Old clients/databases may still contain durable references into tmp.
    # Keep them in place for exports and full backups as well as HTTP reads.
    from mathbank.database import SessionLocal
    try:
        with SessionLocal() as db:
            referenced = _referenced_question_assets(db)
    except Exception as exc:
        print(f"[Storage Retention] Could not verify task image references; kept files ({type(exc).__name__}).")
        return 0
    for url in paths or []:
        try:
            full_path = resolve_upload_asset(
                str(url),
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
                require_file=False,
            )
        except AssetSecurityError:
            continue
        if full_path.parent != tmp_root or full_path in referenced:
            continue
        if full_path.is_file():
            try:
                removed += int(quarantine_asset(
                    str(url), uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL, reason="document-task-expired-or-cancelled",
                ))
            except (OSError, AssetLifecycleError):
                pass
    return removed


def run_docx_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
    docx_verify_suspicions: bool = True,
):
    temp_assets = []
    diagnostics = {}
    try:
        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.update(
            task_id,
            status="extracting_docx",
            progress=25,
            log="已接收 Word 试卷，正在安全提取 OMML 公式、文字与配图...",
            document_type="docx",
            temp_assets=[],
        )

        # 2. 安全提取 Word Markdown；资产先放入 tmp，入库时再晋升。
        docx_res = extract_docx_markdown(
            file_bytes,
            output_dir=TMP_UPLOAD_DIR,
            url_prefix=f"/{UPLOAD_DIR_REL}/tmp",
            asset_prefix=f"word_{task_id}",
            include_source_asset_evidence=True,
            include_source_review_evidence=True,
        )
        temp_assets = docx_res.get("image_paths", [])
        if not docx_res.get("success") or not docx_res.get("markdown"):
            raise ValueError(docx_res.get("error") or "未能从 Word 文档中提取出有效试题内容！")

        full_markdown_content = docx_res["markdown"]
        img_count = docx_res.get("image_count", 0)
        diagnostics = docx_res.get("diagnostics", {})
        extraction_diagnostics = copy.deepcopy(diagnostics)
        extraction_review_evidence = docx_res.get("_source_review_evidence")
        source_document_sha256 = hashlib.sha256(file_bytes).hexdigest()
        diagnostics["word_extraction_warnings"] = list(diagnostics.get("warnings", []))
        converted_count = diagnostics.get("omml_converted", 0) + diagnostics.get("mtef_converted", 0)
        review_count = diagnostics.get("review_required", 0)
        extraction_log = (
            f"Word 提取完成：{converted_count} 个公式已转换，{img_count} 张图片已保留"
            + ((f"，{review_count} 处提取疑点将在拆题后自动核验。" if docx_verify_suspicions
                else f"，{review_count} 处提取疑点，自动核验已关闭。") if review_count else "。")
        )

        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=70,
            log=extraction_log + " 正在调用教研模型拆题...",
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
        )

        # 3. 智能提取标题与题目切片
        paper_title = os.path.splitext(filename)[0]
        auto_title = extract_title_from_latex(full_markdown_content)
        if auto_title:
            paper_title = auto_title

        # Keep every formula visible in-place for the model's mathematical
        # understanding, while assigning an immutable ID. The model returns
        # the ID and the server restores the exact Word-extracted source.
        locked_markdown_content, math_locks = lock_visible_math(
            full_markdown_content,
            task_id.replace("-", "")[:16],
        )
        diagnostics["math_locks_created"] = len(math_locks)
        DOCUMENT_TASKS.check_cancelled(task_id)
        from mathbank.word_hybrid_plan import build_word_hybrid_plan, word_hybrid_plan_diagnostics
        from mathbank.word_hybrid_request import request_word_hybrid
        hybrid_plan = None
        preserved_indices = frozenset()
        if docx_res.get("_source_review_evidence") is not None:
            hybrid_plan = build_word_hybrid_plan(full_markdown_content, diagnostics,
                task_id=task_id, generation=0, source_document_sha256=hashlib.sha256(file_bytes).hexdigest(),
                min_metadata_source_fraction=0.5,
                asset_evidence=docx_res.get("_source_asset_evidence"),
                source_review_evidence=docx_res.get("_source_review_evidence"))
            diagnostics["word_hybrid"] = word_hybrid_plan_diagnostics(hybrid_plan)
        # Keep verified independent source bodies local. A single metadata-only
        # request can avoid copying every formula/image/answer through the LLM.
        # Ambiguous or damaged source structure continues through full splitting.
        source_plan = None
        try:
            source_plan = prepare_word_source_metadata(full_markdown_content, diagnostics,
                asset_evidence=docx_res.get("_source_asset_evidence"))
            if hybrid_plan is not None:
                if hybrid_plan["mode"] == "whole_metadata":
                    source_plan = hybrid_plan["_whole_source_metadata_plan"]
                elif source_plan["eligible"]:
                    source_plan["eligible"] = False
                    source_plan["fallback_reasons"] = hybrid_plan["global_reasons"] or ["native_scope_not_whole_reliable"]
            diagnostics["word_source_metadata"] = source_metadata_diagnostics(source_plan)
        except Exception as exc:
            # The optimization must never block an otherwise usable import.
            diagnostics["word_source_metadata"] = {
                "status": "fallback", "eligible": False, "calls": 0,
                "error_type": type(exc).__name__,
            }
        parsed_questions = None
        if hybrid_plan is not None and hybrid_plan["mode"] == "hybrid":
            parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
            result = request_word_hybrid(hybrid_plan, get_current_curriculum(),
                provider=resolve_text_provider(parse_model), post=post_chat_completion, diagnostics=diagnostics,
                normalize_fillin=normalize_fillin_macro, task_id=task_id, generation=0,
                check_cancelled=lambda: DOCUMENT_TASKS.check_cancelled(task_id),
                full_source_fallback=lambda: parse_paper_text_internal(locked_markdown_content, False,
                    diagnostics=diagnostics, preserve_source_answers=True, connection_retry=False,
                    check_cancelled=lambda: DOCUMENT_TASKS.check_cancelled(task_id)))
            parsed_questions = result.questions
            preserved_indices = result.preserved_indices
            diagnostics["word_hybrid"]["partial"] = result.partial
        if parsed_questions is None and source_plan is not None and source_plan["eligible"]:
            parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
            try:
                parsed_questions = request_word_source_metadata(
                    source_plan, get_current_curriculum(), provider=resolve_text_provider(parse_model),
                    post=post_chat_completion, diagnostics=diagnostics,
                    normalize_fillin=normalize_fillin_macro,
                    check_cancelled=lambda: DOCUMENT_TASKS.check_cancelled(task_id),
                )
            except TaskCancelled:
                raise
            except Exception as exc:
                diagnostics["word_source_metadata"].update(
                    status="fallback", error_type=type(exc).__name__,
                )
        if parsed_questions is None:
            DOCUMENT_TASKS.check_cancelled(task_id)
            fallback_kwargs = {}
            if diagnostics["word_source_metadata"].get("calls"):
                # At most one metadata POST followed by one ordinary split POST.
                fallback_kwargs["connection_retry"] = False
                fallback_kwargs["check_cancelled"] = lambda: DOCUMENT_TASKS.check_cancelled(task_id)
            parsed_questions = parse_paper_text_internal(
                locked_markdown_content,
                False,  # Extract original answers; solve reviewed questions in the frontend.
                diagnostics=diagnostics,
                preserve_source_answers=True,
                **fallback_kwargs,
            )
        # Keep a bounded task-local baseline before local validation. A local
        # post-processing failure must not erase the already paid split output.
        # This is not a cross-upload model cache and is never a normal log entry.
        word_source_cache = {
            "source_markdown": full_markdown_content,
            "split_questions": parsed_questions,
            "source_sha256": hashlib.sha256(file_bytes).hexdigest(),
        }
        if len(json.dumps(word_source_cache, ensure_ascii=False)) <= 500_000:
            DOCUMENT_TASKS.update(task_id, docx_source_cache=copy.deepcopy(word_source_cache))
        staged = copy.deepcopy(parsed_questions) if preserved_indices else parsed_questions
        lock_report = reconcile_visible_math(staged, math_locks, full_markdown_content)
        if preserved_indices:
            for index, candidate in enumerate(staged):
                if index not in preserved_indices:
                    parsed_questions[index] = candidate
                    continue
                original = parsed_questions[index]
                changed = any(original.get(field) != candidate.get(field) for field in ("content", "answer_markdown"))
                if candidate.get("source_review"):
                    original["source_review"] = candidate["source_review"]
                if changed:
                    review = original.setdefault("source_review", {})
                    review["required"] = True
                    review.setdefault("reasons", []).append("全卷来源比对未唯一对应，已保留局部认证原文，请对照原卷核对。")
        previous_warnings = list(diagnostics.get("warnings", []))
        diagnostics.update(lock_report)
        diagnostics["warnings"] = previous_warnings + lock_report.get("warnings", [])
        finalize_source_answers(parsed_questions, full_markdown_content)
        diagnostics["source_review_count"] = sum(
            bool(q.get("source_review", {}).get("required")) for q in parsed_questions
        )

        DOCUMENT_TASKS.check_cancelled(task_id)
        if preserved_indices:
            final_questions = post_process_pdf_parsed_questions(
                parsed_questions, paper_title, task_id, [full_markdown_content], preserved_indices=preserved_indices)
        elif diagnostics["word_source_metadata"].get("status") == "used":
            final_questions = post_process_pdf_parsed_questions(
                parsed_questions, paper_title, task_id, [full_markdown_content],
                source_body_preserved=True,
            )
        else:
            final_questions = post_process_pdf_parsed_questions(parsed_questions, paper_title, task_id, [full_markdown_content])
        prepare_word_extraction_reviews(
            final_questions, diagnostics, full_markdown_content,
            extraction_diagnostics=extraction_diagnostics,
            source_review_evidence=extraction_review_evidence,
            source_document_sha256=source_document_sha256,
        )
        if docx_verify_suspicions and diagnostics.get("source_review_count"):
            def check_word_cancelled():
                DOCUMENT_TASKS.check_cancelled(task_id)

            def register_word_page(path):
                temp_assets.append(path)
                DOCUMENT_TASKS.update(task_id, temp_assets=list(temp_assets))

            DOCUMENT_TASKS.update(
                task_id, status="source_verification", progress=90,
                log="正在将原 Word 渲染成页面，仅对剩余疑点进行原文核验...",
                diagnostics=diagnostics,
            )
            acquired = False
            try:
                from mathbank.docx_source_evidence import prepare_docx_source_evidence
                from mathbank.docx_source_verify import verify_docx_source_suspicions

                evidence = prepare_docx_source_evidence(
                    file_bytes, output_dir=Path(TMP_UPLOAD_DIR), url_prefix=f"/{UPLOAD_DIR_REL}/tmp",
                    task_id=task_id, register_asset=register_word_page, check_cancelled=check_word_cancelled,
                )
                DOCUMENT_TASKS.update(task_id, docx_source_evidence=evidence)
                while not acquired:
                    check_word_cancelled()
                    acquired = PDF_OCR_SEMAPHORE.acquire(timeout=0.25)
                diagnostics["docx_source_verification"] = verify_docx_source_suspicions(
                    final_questions, diagnostics, full_markdown_content, evidence,
                    candidate_image_paths=list(temp_assets),
                    progress=lambda message: DOCUMENT_TASKS.update(task_id, log=message),
                    check_cancelled=check_word_cancelled,
                )
            except TaskCancelled:
                raise
            except Exception as exc:
                # Optional verification must never discard the completed split
                # or manufacture a successful review when its preparation fails.
                pending = sum(bool(q.get("source_review", {}).get("required")) for q in final_questions)
                diagnostics["source_review_count"] = pending
                diagnostics["docx_source_verification"] = {
                    "status": "failed", "pending": pending,
                    "notes": [f"原文核验未完成（{type(exc).__name__}），已保留拆题结果和原核对提示。"],
                }
            finally:
                if acquired:
                    PDF_OCR_SEMAPHORE.release()
        else:
            diagnostics["docx_source_verification"] = {
                "status": "no_candidates" if docx_verify_suspicions else "disabled",
                "calls": 0, "checked": 0, "confirmed": 0,
                "pending": diagnostics.get("source_review_count", 0), "usage": {},
            }
        finalize_word_extraction_reviews(
            final_questions, diagnostics, full_markdown_content,
            extraction_diagnostics=extraction_diagnostics,
            source_review_evidence=extraction_review_evidence,
            source_document_sha256=source_document_sha256,
        )
        make_source_review_advisory(final_questions, diagnostics)
        DOCUMENT_TASKS.check_cancelled(task_id)
        partial = bool(diagnostics.get("word_hybrid", {}).get("partial"))
        completion_log = (f"Word 已保留 {len(final_questions)} 道完整题目；部分疑点题组未完成，原文与未匹配题段可展开核对。"
                          if partial else "Word 拆分完成，可选择题目导入；原文说明可按需展开查看。")
        completed = DOCUMENT_TASKS.complete(
            task_id,
            log=completion_log,
            partial=partial,
            data=final_questions,
            generate_answers=generate_answers,
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
        )
        if not completed:
            _delete_task_temp_assets(temp_assets)
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        DOCUMENT_TASKS.fail(
            task_id,
            f"Word 试卷拆解失败: {str(ex)}",
            document_type="docx",
            diagnostics=diagnostics,
        )


@app.post("/api/upload/docx-task")
def upload_docx_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
    docx_verify_suspicions: str = Form("true"),
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".docx"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .docx 格式！"},
                status_code=400
            )
            
        try:
            content = read_stream_limited(file.file, MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "Word 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if len(content) < 4 or content[:4] != b"PK\x03\x04":
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 Word DOCX 压缩包！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="Word 任务已排队，正在准备安全提取公式与配图...",
            document_type="docx",
            temp_assets=[],
        )
        try:
            DOCUMENT_TASKS.submit(
                task_id,
                run_docx_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
                docx_verify_suspicions.lower() in ("true", "1", "yes"),
            )
        except TaskQueueFull as exc:
            DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
            )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 Word 解析任务失败: {str(e)}"},
            status_code=500
        )


@app.get("/api/tasks/{task_id}/status")
def get_pdf_task_status(task_id: str):
    task = DOCUMENT_TASKS.snapshot(task_id)
    if not task:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )
    return task


@app.post("/api/tasks/{task_id}/retain")
def retain_document_task(task_id: str):
    if DOCUMENT_TASKS.retain(task_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="任务已过期或不可续期；已生成图片仍可通过原地址恢复。")


@app.post("/api/tasks/{task_id}/cancel")
def cancel_pdf_task(task_id: str):
    current = DOCUMENT_TASKS.snapshot(task_id)
    if current is None:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )

    current_status = current.get("status")
    if current_status in {"completed", "error"}:
        # Never delete assets belonging to a task that already produced a
        # result.  The previous behavior reported success and could remove
        # completed PDF crop files after a late ESC/click.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": current_status,
            },
            status_code=409,
        )
    if current_status == "cancelled":
        return {
            "status": "success",
            "message": "任务已中止。",
            "task_status": "cancelled",
        }

    task = DOCUMENT_TASKS.cancel(task_id)
    if task is None:  # Defensive race guard; records are not normally removed here.
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404,
        )
    if task.get("status") != "cancelled":
        # The worker may have completed between the snapshot above and the
        # atomic cancel call.  Never delete assets from that completed result.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": task.get("status"),
            },
            status_code=409,
        )
    removed = _delete_task_temp_assets(list(task.get("temp_assets", [])))
    return {
        "status": "success",
        "message": f"任务已成功中止，已清理 {removed} 个临时资产",
        "task_status": "cancelled",
    }


@app.post("/api/ai/clear-temp-crops")
def clear_temp_crops(payload: dict):
    """物理删除传递来的未入库临时裁剪图片路径"""
    try:
        paths = payload.get("paths", [])
        if not isinstance(paths, list):
            raise ValueError("paths 必须是数组。")
        removed_count = _delete_task_temp_assets(paths)
        return {"status": "success", "message": f"成功物理清除 {removed_count} 张废弃插图图片。"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"清理临时插图出错: {str(e)}"},
            status_code=500
        )


# ----------------- Paper Generation API Endpoints -----------------

@app.get("/api/paper/questions")
def get_paper_questions(ids: str = "", db: Session = Depends(get_db)):
    """获取指定 ID 列表的完整题目数据（组卷试题篮批量拉取）"""
    if not ids:
        return {"status": "success", "data": []}
    try:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
        if not id_list:
            return {"status": "success", "data": []}
        questions = db.query(Question).filter(Question.id.in_(id_list)).all()
        q_map = {q.id: q.to_dict(get_active_version_code()) for q in questions}
        seq_map = get_seq_mapping(db, id_list)
        result = [
            {**q_map[qid], "seq_num": seq_map.get(qid)}
            for qid in id_list
            if qid in q_map
        ]
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/paper/save")
def save_paper(payload: dict, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """保存排版好的试卷，自增被选中题目的 usage_count"""
    try:
        if not isinstance(payload, dict):
            raise ValueError("试卷数据格式不正确。")
        title = str(payload.get("title", "未命名试卷")).strip()
        subtitle = str(payload.get("subtitle", "")).strip()
        paper_type = payload.get("paper_type", "exam")
        questions_payload = payload.get("questions", [])

        if not title or len(title) > 200 or len(subtitle) > 200:
            raise ValueError("试卷标题不能为空，且标题与副标题均不能超过 200 字。")
        if paper_type not in {"exam", "quiz", "exam_19"}:
            raise ValueError("不支持的试卷模板。")
        if not isinstance(questions_payload, list) or not questions_payload:
            raise ValueError("试卷中至少需要包含一道题目。")
        if len(questions_payload) > 200:
            raise ValueError("单份试卷不能超过 200 道题。")

        normalized_items = []
        seen_question_ids = set()
        for item in questions_payload:
            if not isinstance(item, dict):
                raise ValueError("试卷题目数据格式不正确。")
            question_id = int(item.get("id"))
            score = int(item.get("score", 5))
            if question_id <= 0 or score < 0 or score > 100:
                raise ValueError("题目 ID 或分值不在有效范围内。")
            if question_id in seen_question_ids:
                raise ValueError("同一道题不能在一份试卷中重复出现。")
            seen_question_ids.add(question_id)
            normalized_items.append((question_id, score))

        questions = db.query(Question).filter(
            Question.id.in_(seen_question_ids)
        ).all()
        question_map = {question.id: question for question in questions}
        missing_ids = sorted(seen_question_ids - set(question_map))
        if missing_ids:
            raise ValueError("试卷中包含已删除或不存在的题目。")

        total_score = sum(score for _question_id, score in normalized_items)
        
        meta = payload.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        meta["show_secret"] = payload.get("show_secret", True)
        meta["show_notice"] = payload.get("show_notice", True)
        meta["section_order"] = normalize_section_order(payload.get("section_order", meta.get("section_order")))

        paper = Paper(
            title=title,
            subtitle=subtitle,
            paper_type=paper_type,
            total_score=total_score,
            metadata_json=json.dumps(meta)
        )
        db.add(paper)
        db.flush()
        
        for idx, (qid, score) in enumerate(normalized_items):
            pq = PaperQuestion(
                paper_id=paper.id,
                question_id=qid,
                order_index=idx + 1,
                score=score
            )
            db.add(pq)
            question = question_map[qid]
            question.usage_count = (question.usage_count or 0) + 1
                
        db.commit()
        schedule_database_export(background_tasks, operation="save_paper")
        return {"status": "success", "message": "试卷保存成功！", "paper_id": paper.id}
    except (TypeError, ValueError) as e:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=400
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": f"保存试卷失败: {str(e)}"}, status_code=500)

@app.get("/api/papers")
def list_papers(db: Session = Depends(get_db)):
    """获取所有历史试卷列表"""
    try:
        from sqlalchemy import func

        rows = (
            db.query(Paper, func.count(PaperQuestion.id))
            .outerjoin(PaperQuestion, PaperQuestion.paper_id == Paper.id)
            .group_by(Paper.id)
            .order_by(Paper.created_at.desc())
            .all()
        )
        result = []
        for p, q_count in rows:
            d = p.to_dict()
            d["question_count"] = int(q_count)
            result.append(d)
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/papers/{paper_id}")
def get_paper_detail(paper_id: int, db: Session = Depends(get_db)):
    """获取单张试卷的详细信息及关联题目列表（用于一键载入）"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
        
        rows = (
            db.query(PaperQuestion, Question)
            .join(Question, Question.id == PaperQuestion.question_id)
            .filter(PaperQuestion.paper_id == paper_id)
            .order_by(PaperQuestion.order_index.asc())
            .all()
        )

        questions_list = [
            {
                "id": question.id,
                "score": paper_question.score,
                "question": question.to_dict(get_active_version_code()),
            }
            for paper_question, question in rows
        ]
                
        result = paper.to_dict()
        result["questions"] = questions_list
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """删除指定的历史试卷，并同步扣减关联题目的 usage_count"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
            
        pqs = db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).all()
        reference_counts = {}
        for paper_question in pqs:
            reference_counts[paper_question.question_id] = (
                reference_counts.get(paper_question.question_id, 0) + 1
            )
        questions = db.query(Question).filter(
            Question.id.in_(reference_counts)
        ).all()
        for question in questions:
            if question.usage_count:
                question.usage_count = max(
                    0,
                    question.usage_count - reference_counts.get(question.id, 0),
                )
                
        db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).delete()
        db.delete(paper)
        db.commit()
        schedule_database_export(background_tasks, operation="delete_paper")
        return {"status": "success", "message": "试卷记录已成功删除"}
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


class PaperExportValidationError(ValueError):
    """Raised when a paper export request cannot describe a complete paper."""


def _prepare_paper_export_questions(questions_input, db: Session) -> list[dict]:
    """Validate an export cart and hydrate every referenced question in order."""
    if not isinstance(questions_input, list) or not questions_input:
        raise PaperExportValidationError("卷面为空，试卷中至少需要包含一道题目。")
    if len(questions_input) > 200:
        raise PaperExportValidationError("单份试卷不能超过 200 道题。")

    normalized_items = []
    seen_question_ids = set()
    for item in questions_input:
        if not isinstance(item, dict):
            raise PaperExportValidationError("试卷题目数据格式不正确。")
        raw_question_id = item.get("id")
        if isinstance(raw_question_id, bool):
            raise PaperExportValidationError("试卷中包含无效的题目 ID。")
        if isinstance(raw_question_id, int):
            question_id = raw_question_id
        elif isinstance(raw_question_id, str) and raw_question_id.strip().isdigit():
            question_id = int(raw_question_id.strip())
        else:
            raise PaperExportValidationError("试卷中包含无效的题目 ID。")
        if question_id <= 0:
            raise PaperExportValidationError("试卷中包含无效的题目 ID。")
        if question_id in seen_question_ids:
            raise PaperExportValidationError("同一道题不能在一份试卷中重复出现。")
        seen_question_ids.add(question_id)
        normalized_items.append((item, question_id))

    questions_db = db.query(Question).filter(
        Question.id.in_(seen_question_ids)
    ).all()
    question_map = {question.id: question.to_dict(get_active_version_code()) for question in questions_db}
    if seen_question_ids - set(question_map):
        raise PaperExportValidationError("试卷中包含已删除或不存在的题目。")

    questions_data = []
    for item, question_id in normalized_items:
        question_data = dict(question_map[question_id])
        if item.get("figure_align"):
            question_data["figure_align"] = item.get("figure_align")
        if isinstance(item.get("figure_align_custom"), bool):
            question_data["figure_align_custom"] = item.get("figure_align_custom")
        if (
            isinstance(item.get("figure_size"), str)
            and item.get("figure_size") in FIGURE_SIZE_VALUES
        ):
            question_data["figure_size"] = item.get("figure_size")
        try:
            score = int(item.get("score", 5))
        except (TypeError, ValueError) as exc:
            raise PaperExportValidationError("试卷中包含无效的题目分值。") from exc
        export_item = {"question": question_data, "score": score}
        if item.get("solution_space") is not None:
            export_item["solution_space"] = item.get("solution_space")
        questions_data.append(export_item)
    return questions_data


@app.post("/api/paper/export/tex")
def export_paper_tex(payload: dict, db: Session = Depends(get_db)):
    """导出 LaTeX 源码 ZIP 压缩包"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        questions_data = _prepare_paper_export_questions(questions_input, db)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice, question_types=METADATA_CACHE.get("question_types", []), section_order=payload.get("section_order"))
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice, question_types=METADATA_CACHE.get("question_types", []), section_order=payload.get("section_order"))
        
        if paper_type == "exam_19":
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)
        zip_bytes = create_tex_zip_package(title, tex_main, tex_ans, image_paths, answer_sheet_tex=tex_answer_sheet)
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        encoded_filename = quote(f"{safe_title}.zip")
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_export.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except PaperExportValidationError as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成 LaTeX 源码失败: {str(e)}"}, status_code=500)

@app.post("/api/paper/export/bundle")
def export_paper_bundle(payload: dict, db: Session = Depends(get_db)):
    """一键导出合并全套 Zip 压缩包（包含 LaTeX 源码、相关插图以及已编译好的 PDF）"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        questions_data = _prepare_paper_export_questions(questions_input, db)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice, question_types=METADATA_CACHE.get("question_types", []), section_order=payload.get("section_order"))
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice, question_types=METADATA_CACHE.get("question_types", []), section_order=payload.get("section_order"))
        
        if paper_type == "exam_19":
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)

        # Pre-compile PDFs
        main_pdf_bytes, _ = compile_tex_to_pdf(tex_main, image_paths)
        ans_pdf_bytes, _ = compile_tex_to_pdf(tex_ans, image_paths)
        if paper_type == "exam_19" and tex_answer_sheet:
            answer_sheet_pdf_bytes, _ = compile_tex_to_pdf(tex_answer_sheet, image_paths)
        else:
            answer_sheet_pdf_bytes = None

        zip_bytes = create_full_bundle_zip_package(
            title, tex_main, tex_ans, image_paths,
            answer_sheet_tex=tex_answer_sheet,
            main_pdf_bytes=main_pdf_bytes,
            ans_pdf_bytes=ans_pdf_bytes,
            answer_sheet_pdf_bytes=answer_sheet_pdf_bytes
        )
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        filename = f"{safe_title}_全套归档.zip"
        encoded_filename = quote(filename)
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_bundle.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except PaperExportValidationError as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成全套合并包失败: {str(e)}"}, status_code=500)

def explain_latex_compile_error(log_text: str, tex_content: str) -> dict:
    """Explain one compile failure locally, then enrich it with the parse model."""
    diagnostic = build_local_latex_diagnostic(log_text, tex_content)
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv(
        "DEEPSEEK_PARSE_MODEL", "deepseek-flash"
    )
    provider = resolve_text_provider(parse_model)
    if not provider.api_key:
        diagnostic["ai_note"] = "试卷拆解模型未配置，当前显示本地诊断结果。"
        return diagnostic

    system_prompt, user_prompt = build_latex_error_explanation_prompts(diagnostic)
    payload = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    payload = apply_model_thinking_policy(
        payload,
        provider=provider,
        task="latex_diagnostic",
    )

    try:
        response = post_chat_completion(
            provider,
            payload,
            timeout=45,
            provider_name=provider.provider_label,
        )
        raw_text = response.json()["choices"][0]["message"]["content"].strip()
        ai_value = parse_ai_json(raw_text)
        return merge_ai_latex_diagnostic(diagnostic, ai_value)
    except Exception:
        diagnostic["ai_note"] = "AI 暂时无法解释该错误，当前显示本地诊断结果。"
        return diagnostic


@app.post("/api/paper/export/pdf")
def export_paper_pdf(payload: dict, db: Session = Depends(get_db)):
    """在线静默编译生成高清 PDF"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam_19")
        target = payload.get("target", "paper")  # "paper" or "sheet"
        include_answers = payload.get("include_answers", False)
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        questions_data = _prepare_paper_export_questions(questions_input, db)
                
        if target == "sheet":
            tex_content = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_content = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=include_answers, show_secret=show_secret, show_notice=show_notice, question_types=METADATA_CACHE.get("question_types", []), section_order=payload.get("section_order"))

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)
        pdf_bytes, log_or_err = compile_tex_to_pdf(tex_content, image_paths)
        
        if pdf_bytes:
            filename = f"sheet_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf" if target == "sheet" else f"paper_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            return Response(content=pdf_bytes, media_type="application/pdf", headers={
                "Content-Disposition": f'inline; filename="{filename}"'
            })
        else:
            diagnostic = explain_latex_compile_error(log_or_err, tex_content)
            return JSONResponse(
                content={
                    "status": "error",
                    "message": diagnostic.get("summary", "PDF 编译失败"),
                    "diagnostic": diagnostic,
                },
                status_code=400,
            )
    except PaperExportValidationError as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"编译 PDF 异常: {str(e)}"}, status_code=500)


@app.get("/api/runtime/pandoc/status")
def get_pandoc_runtime_status():
    """Report whether Word-native formula conversion is currently available."""
    install_state = PANDOC_INSTALL_MANAGER.snapshot()
    if install_state.get("status") in {"queued", "downloading", "verifying"}:
        return {"status": "success", "pandoc": install_state}
    return {"status": "success", "pandoc": pandoc_status()}


@app.post("/api/runtime/pandoc/install")
def install_pandoc_runtime():
    """Start or join the single verified app-local Pandoc installation task."""
    state = PANDOC_INSTALL_MANAGER.ensure()
    status_code = 200 if state.get("status") == "ready" else 202
    return JSONResponse(
        status_code=status_code,
        content={"status": "success", "pandoc": state},
    )


@app.get("/api/runtime/pandoc/install/{task_id}")
def get_pandoc_install_status(task_id: str):
    state = PANDOC_INSTALL_MANAGER.snapshot()
    if not state.get("task_id") or state.get("task_id") != task_id:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Pandoc 安装任务不存在或已过期。"},
        )
    return {"status": "success", "pandoc": state}


@app.post("/api/paper/export/word")
def export_paper_word(payload: dict, db: Session = Depends(get_db)):
    """导出包含试卷正文与含答案解析两个 Word 文件的 ZIP 压缩包。"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        as_single_docx = bool(payload.get("as_single_docx", False))
        include_answers = bool(payload.get("include_answers", False))
        questions_data = _prepare_paper_export_questions(questions_input, db)

        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', "_", title.strip()) or "试卷"

        if as_single_docx:
            docx_bytes, diagnostics = build_word_document(
                title,
                subtitle,
                paper_type,
                questions_data,
                include_answers=include_answers,
                show_secret=show_secret,
                show_notice=show_notice,
                uploads_dir=UPLOAD_DIR,
                question_types=METADATA_CACHE.get("question_types", []),
                section_order=payload.get("section_order"),
            )
            suffix = "_含答案与解析" if include_answers else ""
            filename = f"{safe_title}{suffix}.docx"
            encoded_filename = quote(filename)
            return Response(
                content=docx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={
                    "Content-Disposition": f"attachment; filename=\"paper.docx\"; filename*=utf-8''{encoded_filename}",
                    "X-Word-Native-Formulas": str(diagnostics.get("native_formulas", 0)),
                    "X-Word-Fallback-Formulas": str(diagnostics.get("fallback_formulas", 0)),
                    "X-Word-Failed-Formulas": str(diagnostics.get("failed_formulas", 0)),
                    "X-Word-Missing-Images": str(diagnostics.get("missing_images", 0)),
                    "X-Word-Answer-Card-Omitted": "1" if diagnostics.get("answer_card_omitted") else "0",
                },
            )

        # Default: build clean student docx and full teacher docx with answers into a ZIP bundle
        main_docx, main_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=False,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=UPLOAD_DIR,
            question_types=METADATA_CACHE.get("question_types", []),
            section_order=payload.get("section_order"),
        )
        ans_docx, ans_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=True,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=UPLOAD_DIR,
            question_types=METADATA_CACHE.get("question_types", []),
            section_order=payload.get("section_order"),
        )

        zip_bytes = create_word_bundle_zip(title, main_docx, ans_docx)
        filename = f"{safe_title}_Word打包.zip"
        encoded_filename = quote(filename)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=\"paper_word_bundle.zip\"; filename*=utf-8''{encoded_filename}",
                "X-Word-Native-Formulas": str(main_diag.get("native_formulas", 0) + ans_diag.get("native_formulas", 0)),
                "X-Word-Fallback-Formulas": str(main_diag.get("fallback_formulas", 0) + ans_diag.get("fallback_formulas", 0)),
                "X-Word-Failed-Formulas": str(main_diag.get("failed_formulas", 0) + ans_diag.get("failed_formulas", 0)),
                "X-Word-Missing-Images": str(main_diag.get("missing_images", 0) + ans_diag.get("missing_images", 0)),
                "X-Word-Answer-Card-Omitted": "1" if main_diag.get("answer_card_omitted") else "0",
            },
        )
    except PaperExportValidationError as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=400
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"生成 Word 试卷包失败: {str(e)}"},
            status_code=500,
        )


# ----------------- 专题工坊 API -----------------
#
# 风格约定（沿用全库既有约定）：
# - payload: dict 收 body（全库无 Pydantic）；Depends(get_db)；
# - 成功 {"status":"success","data":...}；参数错 400 / 不存在 404 / 意外 500；
# - 校验函数抛 ValueError 由路由转 400（照 save_paper）；
# - 专题变更刻意不调用 schedule_database_export：专题不在题库导出载荷内，
#   避免每次编辑白写大 JSON（sync_helper.export_database_to_files 不含专题表）。


@app.get("/api/config/topic-types")
def list_topic_types(db: Session = Depends(get_db)):
    """类型 / 关联 / 视图 / 场景 / 难度配比白名单（照 /api/relation-types）。"""

    from mathbank.topics import (
        DIFFICULTY_PROFILES,
        LEVEL_LABEL,
        TOPIC_LINK_CODES,
        TOPIC_LINK_LABELS,
        TOPIC_TYPES,
        TOPIC_TYPE_LABELS,
        TOPIC_USAGE_SCENES,
        TOPIC_USAGE_SCENE_LABELS,
        TOPIC_VIEW_MODES,
        TOPIC_VIEW_MODE_LABELS,
    )

    used_custom_codes = [
        row[0]
        for row in db.query(TopicLink.link_code)
        .filter(TopicLink.link_code.like("custom:%"))
        .distinct()
        .all()
    ]
    return {
        "status": "success",
        "data": {
            "topic_types": [
                {"code": code, "label": TOPIC_TYPE_LABELS.get(code, code)}
                for code in TOPIC_TYPES
            ],
            "link_codes": [
                {"code": code, "label": TOPIC_LINK_LABELS.get(code, code)}
                for code in TOPIC_LINK_CODES
            ]
            + [{"code": code, "label": code} for code in used_custom_codes],
            "view_modes": [
                {"code": code, "label": TOPIC_VIEW_MODE_LABELS.get(code, code)}
                for code in TOPIC_VIEW_MODES
            ],
            "usage_scenes": [
                {"code": code, "label": TOPIC_USAGE_SCENE_LABELS.get(code, code)}
                for code in TOPIC_USAGE_SCENES
            ],
            "levels": [
                {"code": level, "label": LEVEL_LABEL[level]}
                for level in ("easy", "medium", "hard")
            ],
            "difficulty_profiles": DIFFICULTY_PROFILES,
        },
    }


def _topic_list_row(db: Session, topic: Topic) -> dict:
    """列表行：带 item_count / child_count（GROUP BY 聚合，照 list_papers）。"""

    item_count = (
        db.query(TopicItem).filter(TopicItem.topic_id == topic.id).count()
    )
    child_count = (
        db.query(Topic).filter(Topic.parent_topic_id == topic.id).count()
    )
    row = topic.to_dict()
    row["item_count"] = int(item_count)
    row["child_count"] = int(child_count)
    return row


@app.get("/api/topics")
def list_topics(
    type: str = None,
    status: str = None,
    parent_id: Optional[int] = None,
    root_only: bool = False,
    is_template: Optional[bool] = None,
    q: str = None,
    sort: str = "updated",
    db: Session = Depends(get_db),
):
    """专题列表：类型/状态/父专题/模板/关键词过滤 + 计数聚合。"""

    query = db.query(Topic)
    if type:
        query = query.filter(Topic.topic_type == str(type).strip())
    if status:
        query = query.filter(Topic.status == str(status).strip())
    if parent_id is not None:
        query = query.filter(Topic.parent_topic_id == int(parent_id))
    if root_only:
        query = query.filter(Topic.parent_topic_id.is_(None))
    if is_template is not None:
        query = query.filter(Topic.is_template == bool(is_template))
    keyword = str(q or "").strip()
    if keyword:
        like = f"%{_escape_like(keyword)}%"
        query = query.filter(
            or_(Topic.title.like(like, escape="\\"), Topic.description.like(like, escape="\\"))
        )
    if sort == "title":
        query = query.order_by(Topic.title.asc())
    elif sort == "created":
        query = query.order_by(Topic.created_at.desc())
    elif sort == "used":
        query = query.order_by(Topic.usage_count.desc(), Topic.updated_at.desc())
    else:
        query = query.order_by(Topic.updated_at.desc())
    topics = query.all()
    return {"status": "success", "data": [_topic_list_row(db, topic) for topic in topics]}


@app.post("/api/topics")
def create_topic(payload: dict, db: Session = Depends(get_db)):
    """创建专题（空标题/类型非法/父不存在/环/超深 → 400）。"""

    from mathbank.topics import (
        TOPIC_MAX_DEPTH,
        create_topic_with_items,
        validate_topic_type,
    )

    try:
        title = str(payload.get("title") or "").strip()
        parent_topic_id = payload.get("parent_topic_id")
        if parent_topic_id is not None:
            if db.get(Topic, int(parent_topic_id)) is None:
                raise ValueError("父专题不存在")
            # 新专题尚无自身 id，环检测只需确认父链不超深
            cursor, depth = int(parent_topic_id), 0
            while cursor is not None:
                cursor = db.execute(
                    text("SELECT parent_topic_id FROM topics WHERE id = :i"),
                    {"i": cursor},
                ).scalar()
                depth += 1
                if depth > TOPIC_MAX_DEPTH:
                    raise ValueError(f"专题嵌套层级过深（上限 {TOPIC_MAX_DEPTH} 层）")
        question_ids = payload.get("question_ids") or []
        topic_id = create_topic_with_items(
            db,
            title=title,
            topic_type=validate_topic_type(payload.get("topic_type") or "knowledge"),
            description=str(payload.get("description") or ""),
            parent_topic_id=parent_topic_id,
            view_mode=str(payload.get("view_mode") or "linear"),
            question_ids=question_ids,
            notes={
                int(key): str(value)
                for key, value in (payload.get("notes") or {}).items()
            },
            group_of={
                int(key): (int(value) if value is not None else None)
                for key, value in (payload.get("group_of") or {}).items()
            },
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"创建专题失败: {exc}"})
    return {"status": "success", "data": {"id": topic_id}}


@app.get("/api/topics/{topic_id}")
def get_topic_detail(topic_id: int, db: Session = Depends(get_db)):
    """专题详情：topic + groups + items（一次 join hydrate，禁 N+1）。"""

    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    groups = (
        db.query(TopicGroup)
        .filter(TopicGroup.topic_id == topic_id)
        .order_by(TopicGroup.position.asc(), TopicGroup.id.asc())
        .all()
    )
    rows = (
        db.query(TopicItem, Question)
        .join(Question, Question.id == TopicItem.question_id)
        .filter(TopicItem.topic_id == topic_id)
        .order_by(TopicItem.position.asc(), TopicItem.id.asc())
        .all()
    )
    version_count = (
        db.query(TopicVersion).filter(TopicVersion.topic_id == topic_id).count()
    )
    children = (
        db.query(Topic).filter(Topic.parent_topic_id == topic_id)
        .order_by(Topic.updated_at.desc())
        .all()
    )
    links = (
        db.query(TopicLink)
        .filter(
            or_(
                TopicLink.source_topic_id == topic_id,
                TopicLink.target_topic_id == topic_id,
            )
        )
        .order_by(TopicLink.created_at.asc(), TopicLink.id.asc())
        .all()
    )
    link_topic_ids = {
        row.target_topic_id if row.source_topic_id == topic_id else row.source_topic_id
        for row in links
    }
    link_topics = (
        {topic.id: topic for topic in db.query(Topic).filter(Topic.id.in_(link_topic_ids)).all()}
        if link_topic_ids
        else {}
    )
    seq_map = get_seq_mapping(db, [item.question_id for item, _ in rows])
    active_version = get_active_version_code()
    return {
        "status": "success",
        "data": {
            "topic": topic.to_dict(),
            "groups": [group.to_dict() for group in groups],
            "items": [
                {
                    "seq_num": index + 1,
                    **item.to_dict(),
                    "bank_seq_num": seq_map.get(item.question_id),
                    "question": question.to_dict(active_version),
                }
                for index, (item, question) in enumerate(rows)
            ],
            "children": [_topic_list_row(db, child) for child in children],
            "links": [
                {
                    **row.to_dict(),
                    "direction": (
                        "out" if row.source_topic_id == topic_id else "in"
                    ),
                    "other_topic": (
                        link_topics[
                            row.target_topic_id
                            if row.source_topic_id == topic_id
                            else row.source_topic_id
                        ].to_dict()
                        if (
                            row.target_topic_id
                            if row.source_topic_id == topic_id
                            else row.source_topic_id
                        )
                        in link_topics
                        else None
                    ),
                }
                for row in links
            ],
            "version_count": int(version_count),
        },
    }


@app.put("/api/topics/{topic_id}")
def update_topic(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """改元信息；parent 走环检测 + 深度上限。"""

    from mathbank.topics import (
        ensure_no_topic_cycle,
        validate_topic_status,
        validate_topic_type,
        validate_topic_view_mode,
    )

    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    try:
        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if not title:
                raise ValueError("专题标题不能为空")
            if len(title) > 200:
                raise ValueError("专题标题不能超过 200 字")
            topic.title = title
        if "topic_type" in payload:
            topic.topic_type = validate_topic_type(payload.get("topic_type"))
        if "description" in payload:
            topic.description = str(payload.get("description") or "")
        if "view_mode" in payload:
            topic.view_mode = validate_topic_view_mode(payload.get("view_mode"))
        if "status" in payload:
            topic.status = validate_topic_status(payload.get("status"))
        if "is_template" in payload:
            topic.is_template = bool(payload.get("is_template"))
        if "parent_topic_id" in payload:
            parent_topic_id = payload.get("parent_topic_id")
            if parent_topic_id is None:
                topic.parent_topic_id = None
            else:
                if db.get(Topic, int(parent_topic_id)) is None:
                    raise ValueError("父专题不存在")
                ensure_no_topic_cycle(db, topic_id, int(parent_topic_id))
                topic.parent_topic_id = int(parent_topic_id)
        db.commit()
        db.refresh(topic)
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"更新专题失败: {exc}"})
    return {"status": "success", "data": topic.to_dict()}


@app.delete("/api/topics/{topic_id}")
def delete_topic(topic_id: int, db: Session = Depends(get_db)):
    """删专题：有子专题 400（DB RESTRICT 兜底）；否则级联删。"""

    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    child_count = (
        db.query(Topic).filter(Topic.parent_topic_id == topic_id).count()
    )
    if child_count:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "请先移除子专题后再删除母专题"},
        )
    try:
        db.delete(topic)
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse(
            status_code=400, content={"status": "error", "message": f"删除专题失败: {exc}"}
        )
    return {"status": "success", "message": "专题已删除"}


@app.post("/api/topics/{topic_id}/duplicate")
def duplicate_topic_endpoint(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """深拷贝 groups+items，自动建 derived_from 链接。"""

    from mathbank.topics import duplicate_topic

    try:
        new_id = duplicate_topic(db, topic_id, title=str(payload.get("title") or ""))
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"复制专题失败: {exc}"})
    return {"status": "success", "data": {"id": new_id}}


@app.post("/api/topics/{topic_id}/items")
def add_topic_items(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """批量加题 {question_ids, group_id?, note?}；已存在跳过；超 300 → 400。"""

    from mathbank.topics import add_items_to_topic

    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    question_ids = payload.get("question_ids")
    if not isinstance(question_ids, list):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "question_ids 必须是数组"},
        )
    group_id = payload.get("group_id")
    try:
        result = add_items_to_topic(
            db,
            topic,
            question_ids,
            group_id=(int(group_id) if group_id is not None else None),
            note=str(payload.get("note") or ""),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"添加题目失败: {exc}"})
    return {"status": "success", "data": result}


@app.put("/api/topics/{topic_id}/items/order")
def reorder_topic_items_endpoint(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """全量重排：读入内存→重排→两段式写位单事务（不用 SQL 增量位移）。"""

    from mathbank.topics import reorder_topic_items

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    ordered = payload.get("question_ids")
    if not isinstance(ordered, list):
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "question_ids 必须是数组"}
        )
    try:
        reorder_topic_items(db, topic_id, [int(value) for value in ordered])
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"重排失败: {exc}"})
    return {"status": "success", "message": "排序已保存"}

@app.put("/api/topics/{topic_id}/items/{item_id}")
def update_topic_item(topic_id: int, item_id: int, payload: dict, db: Session = Depends(get_db)):
    """改 note / 转组。"""

    item = (
        db.query(TopicItem)
        .filter(TopicItem.id == item_id, TopicItem.topic_id == topic_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="专题题目不存在")
    try:
        if "note" in payload:
            item.note = str(payload.get("note") or "")
        if "group_id" in payload:
            group_id = payload.get("group_id")
            if group_id is None:
                item.group_id = None
            else:
                group = db.get(TopicGroup, int(group_id))
                if group is None or group.topic_id != topic_id:
                    raise ValueError("分组不存在或不属于该专题")
                item.group_id = group.id
        db.commit()
        db.refresh(item)
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    return {"status": "success", "data": item.to_dict()}


@app.delete("/api/topics/{topic_id}/items/{item_id}")
def remove_topic_item(topic_id: int, item_id: int, db: Session = Depends(get_db)):
    """移出并重排 position（0 基连续）。"""

    from mathbank.topics import compact_item_positions

    item = (
        db.query(TopicItem)
        .filter(TopicItem.id == item_id, TopicItem.topic_id == topic_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="专题题目不存在")
    try:
        db.delete(item)
        db.flush()
        compact_item_positions(db, topic_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": f"移出失败: {exc}"})
    return {"status": "success", "message": "已移出专题"}



@app.post("/api/topics/{topic_id}/groups")
def create_topic_group(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """建分组（支持预设空分组：备课先搭框架再拖题）。"""

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    try:
        title = str(payload.get("title") or "").strip()
        if len(title) > 120:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "分组标题不能超过 120 字"},
            )
        max_position = (
            db.query(func.max(TopicGroup.position))
            .filter(TopicGroup.topic_id == topic_id)
            .scalar()
        )
        # 0 基连续：与 topic_items.position、compact_group_positions 的写位口径一致
        # （首个分组落 0，而非 1；重排/压缩后新建仍延续该序列）。
        group = TopicGroup(
            topic_id=topic_id,
            title=title,
            position=(int(max_position) + 1) if max_position is not None else 0,
            note=str(payload.get("note") or ""),
        )
        db.add(group)
        db.flush()
        db.commit()
        db.refresh(group)
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"创建分组失败: {exc}"})
    return {"status": "success", "data": group.to_dict()}


@app.put("/api/topics/{topic_id}/groups/order")
def reorder_topic_groups_endpoint(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """分组全量重排：两段式写位单事务（必须声明在 groups/{group_id} 之前）。"""

    from mathbank.topics import reorder_topic_groups

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    ordered = payload.get("group_ids")
    if not isinstance(ordered, list):
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "group_ids 必须是数组"}
        )
    try:
        reorder_topic_groups(db, topic_id, [int(value) for value in ordered])
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"分组重排失败: {exc}"})
    return {"status": "success", "message": "分组顺序已保存"}


@app.put("/api/topics/{topic_id}/groups/{group_id}")
def update_topic_group(topic_id: int, group_id: int, payload: dict, db: Session = Depends(get_db)):
    """改分组标题 / 批注。"""

    group = (
        db.query(TopicGroup)
        .filter(TopicGroup.id == group_id, TopicGroup.topic_id == topic_id)
        .first()
    )
    if group is None:
        raise HTTPException(status_code=404, detail="分组不存在")
    try:
        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if len(title) > 120:
                raise ValueError("分组标题不能超过 120 字")
            group.title = title
        if "note" in payload:
            group.note = str(payload.get("note") or "")
        db.commit()
        db.refresh(group)
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    return {"status": "success", "data": group.to_dict()}


@app.delete("/api/topics/{topic_id}/groups/{group_id}")
def delete_topic_group(topic_id: int, group_id: int, db: Session = Depends(get_db)):
    """删分组：items.group_id 置 NULL（组内题变未分组，不删题）。"""

    group = (
        db.query(TopicGroup)
        .filter(TopicGroup.id == group_id, TopicGroup.topic_id == topic_id)
        .first()
    )
    if group is None:
        raise HTTPException(status_code=404, detail="分组不存在")
    try:
        db.delete(group)
        db.flush()
        from mathbank.topics import compact_group_positions

        compact_group_positions(db, topic_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": f"删除分组失败: {exc}"})
    return {"status": "success", "message": "分组已删除，组内题目已移至未分组"}


# ---- 专题创建方式：从标签 / 从试卷 / 从学情 / 从课程树 ----

#: 从标签生成专题时允许的标签维度（question_tags.dim 词表子集）。
TOPIC_FROM_TAGS_DIMS = ("thought", "strategy", "micro", "function", "custom", "chapter")
#: 一节精讲课 ≈ 8-12 题；超过 30 那是章节不是专题。
TOPIC_FROM_TAGS_DEFAULT_LIMIT = 12
TOPIC_FROM_TAGS_MIN_LIMIT = 5
TOPIC_FROM_TAGS_MAX_LIMIT = 30


@app.post("/api/topics/from-tags")
def create_topic_from_tags(payload: dict, db: Session = Depends(get_db)):
    """标签 → 专题：单值小节 + 标签维度筛选，按难度分层抽样后落库。

    约束（本地定制已禁多值小节筛选）：只走 category_knowledge 单值小节与
    question_tags 标签维度，绝不传 sub_knowledge。
    """

    from mathbank.topics import (
        DIFFICULTY_ORDER,
        DIFFICULTY_PROFILES,
        create_topic_with_items,
        validate_topic_type,
    )

    try:
        title = str(payload.get("title") or "").strip()
        tag_dim = str(payload.get("tag_dim") or "strategy").strip()
        tag_codes = [str(code).strip() for code in (payload.get("tag_codes") or []) if str(code).strip()]
        if not tag_codes:
            raise ValueError("请至少选择一个标签")
        if tag_dim not in TOPIC_FROM_TAGS_DIMS:
            raise ValueError(f"标签维度必须是：{' / '.join(TOPIC_FROM_TAGS_DIMS)}")
        compulsory = payload.get("compulsory") or None
        chapter = payload.get("chapter") or None
        knowledge = payload.get("knowledge") or None
        max_questions = int(payload.get("max_questions") or TOPIC_FROM_TAGS_DEFAULT_LIMIT)
        max_questions = max(
            TOPIC_FROM_TAGS_MIN_LIMIT, min(TOPIC_FROM_TAGS_MAX_LIMIT, max_questions)
        )
        topic_type = validate_topic_type(payload.get("topic_type") or "method")

        query = (
            db.query(Question)
            .join(QuestionTag, QuestionTag.question_id == Question.id)
            .filter(QuestionTag.dim == tag_dim, QuestionTag.code.in_(tag_codes))
            .distinct()
        )
        query = apply_curriculum_filters(
            query, compulsory, chapter, knowledge
        )
        candidates = query.all()
        if not candidates:
            raise ValueError("没有题目匹配所选标签与筛选条件")

        target = DIFFICULTY_PROFILES.get(topic_type) or DIFFICULTY_PROFILES["knowledge"]
        # 分层抽样：各难度配额 = round(target[level]×max_questions)；
        # 层内排序（有答案 DESC, usage_count DESC, id ASC）——教师验证过的题优先入选；
        # 某层不足余额顺延。排序键全稳定 → 重新生成结果一致。
        by_level = {level: [] for level in DIFFICULTY_ORDER}
        for question in candidates:
            level = str(question.difficulty or "").strip().lower()
            by_level.setdefault(level, []).append(question)
        picked_ids: list[int] = []
        overflow: list = []
        for level in DIFFICULTY_ORDER:
            quota = round(target.get(level, 0.0) * max_questions)
            layer = sorted(
                by_level.get(level, []),
                key=lambda q: (
                    0 if str(q.answer_markdown or "").strip() else 1,
                    -(int(q.usage_count or 0)),
                    q.id,
                ),
            )
            picked_ids.extend(question.id for question in layer[:quota])
            overflow.extend(layer[quota:])
        remaining = max_questions - len(picked_ids)
        if remaining > 0:
            overflow.sort(
                key=lambda q: (
                    0 if str(q.answer_markdown or "").strip() else 1,
                    -(int(q.usage_count or 0)),
                    q.id,
                )
            )
            picked_ids.extend(question.id for question in overflow[:remaining])
        # 保持题库物理顺序，position 稳定可复现
        picked_ids = sorted(set(picked_ids))

        topic_id = create_topic_with_items(
            db,
            title=title or f"标签专题：{'、'.join(tag_codes[:3])}",
            topic_type=topic_type,
            description=str(payload.get("description") or ""),
            question_ids=picked_ids,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"生成专题失败: {exc}"})
    return {
        "status": "success",
        "data": {
            "id": topic_id,
            "question_ids": picked_ids,
            "candidate_count": len(candidates),
            "filters": {
                "tag_dim": tag_dim,
                "tag_codes": tag_codes,
                "compulsory": compulsory,
                "chapter": chapter,
                "knowledge": knowledge,
                "max_questions": max_questions,
            },
        },
    }


def _chapter_label_map(db: Session, question_ids) -> dict[int, str]:
    """批量算题目的章节显示标签（chapter_display_label 坍缩多章节标签）。"""

    from mathbank.tags import chapter_display_label

    ids = [int(value) for value in question_ids]
    if not ids:
        return {}
    rows = db.execute(
        text(
            "SELECT question_id, code FROM question_tags "
            "WHERE dim = 'chapter' AND question_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).fetchall()
    codes_by_question: dict[int, list[str]] = {}
    for question_id, code in rows:
        codes_by_question.setdefault(int(question_id), []).append(str(code))
    labels: dict[int, str] = {}
    for question_id in ids:
        label = chapter_display_label(codes_by_question.get(question_id, []))
        if not label:
            question = db.get(Question, question_id)
            label = str((question.category_chapter if question else "") or "").strip()
        labels[question_id] = label or "未分章"
    return labels


@app.post("/api/topics/from-paper/{paper_id}")
def create_topic_from_paper(paper_id: int, payload: dict, db: Session = Depends(get_db)):
    """试卷 → 专题：主维度=章节；≥6 题且跨题型才按题型二次拆分。

    dry_run=true（默认）只返回草稿簇与 orphans 报告，不落库；
    dry_run=false 直接为每个合格簇建专题。<3 题的簇进 orphans 不静默建专题。
    """

    from mathbank.topics import create_topic_with_items, validate_topic_type

    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="试卷不存在")
    rows = (
        db.query(PaperQuestion, Question)
        .join(Question, Question.id == PaperQuestion.question_id)
        .filter(PaperQuestion.paper_id == paper_id)
        .order_by(PaperQuestion.order_index.asc(), PaperQuestion.id.asc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="试卷内没有题目")
    question_ids = [question.id for _, question in rows]
    labels = _chapter_label_map(db, question_ids)

    clusters: dict[str, list] = {}
    for _, question in rows:
        clusters.setdefault(labels.get(question.id, "未分章"), []).append(question)

    drafts: list[dict] = []
    orphans: list[dict] = []
    for label, questions in clusters.items():
        if len(questions) >= 6:
            type_groups: dict[str, list] = {}
            for question in questions:
                type_groups.setdefault(str(question.question_type or ""), []).append(question)
            if len(type_groups) >= 2:
                for question_type, group in sorted(type_groups.items()):
                    if len(group) >= 3:
                        drafts.append(
                            {
                                "title": f"{paper.title}·{label}·{question_type or '综合'}",
                                "chapter": label,
                                "question_type": question_type,
                                "question_ids": [question.id for question in group],
                            }
                        )
                    else:
                        orphans.extend(
                            {
                                "question_id": question.id,
                                "chapter": label,
                                "reason": "按题型二次拆分后不足 3 题",
                            }
                            for question in group
                        )
                continue
        if len(questions) >= 3:
            drafts.append(
                {
                    "title": f"{paper.title}·{label}",
                    "chapter": label,
                    "question_type": "",
                    "question_ids": [question.id for question in questions],
                }
            )
        else:
            orphans.extend(
                {
                    "question_id": question.id,
                    "chapter": label,
                    "reason": "该章节簇不足 3 题，未静默建专题",
                }
                for question in questions
            )

    dry_run = payload.get("dry_run", True)
    if dry_run:
        return {
            "status": "success",
            "data": {
                "paper_id": paper_id,
                "paper_title": paper.title,
                "drafts": drafts,
                "orphans": orphans,
            },
        }

    try:
        topic_type = validate_topic_type(payload.get("topic_type") or "knowledge")
        title_prefix = str(payload.get("title_prefix") or "").strip()
        created = []
        for draft in drafts:
            title = f"{title_prefix}{draft['title']}" if title_prefix else draft["title"]
            topic_id = create_topic_with_items(
                db,
                title=title,
                topic_type=topic_type,
                description=str(payload.get("description") or ""),
                question_ids=draft["question_ids"],
            )
            created.append({"id": topic_id, "title": title, "question_ids": draft["question_ids"]})
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"反推专题失败: {exc}"})
    return {
        "status": "success",
        "data": {"created": created, "orphans": orphans},
    }


@app.post("/api/topics/from-usage")
def create_topic_from_usage(payload: dict, db: Session = Depends(get_db)):
    """低正确率题 → 专题（V2 错题生成的务实降级：无错题本表，从学情聚合）。"""

    from mathbank.topics import (
        TOPIC_MAX_ITEMS,
        create_topic_with_items,
        validate_topic_type,
    )

    try:
        threshold = float(payload.get("threshold", 0.4))
        threshold = min(1.0, max(0.0, threshold))
        min_sample = int(payload.get("min_sample", 10))
        limit = max(1, min(int(payload.get("limit", 20)), TOPIC_MAX_ITEMS))
        rows = db.execute(
            text(
                "SELECT s.question_id, s.correct_rate, s.sample_size "
                "FROM question_usage_stats s "
                "JOIN (SELECT question_id, MAX(recorded_at) AS latest "
                "      FROM question_usage_stats GROUP BY question_id) l "
                "  ON l.question_id = s.question_id AND l.latest = s.recorded_at "
                "WHERE s.correct_rate IS NOT NULL "
                "  AND s.correct_rate < :threshold AND s.sample_size >= :min_sample "
                "ORDER BY s.correct_rate ASC, s.question_id ASC LIMIT :limit"
            ),
            {"threshold": threshold, "min_sample": min_sample, "limit": limit},
        ).fetchall()
        question_ids = [int(row[0]) for row in rows]
        if not question_ids:
            raise ValueError("没有符合低正确率条件的学情记录")
        topic_type = validate_topic_type(payload.get("topic_type") or "wrong_retry")
        topic_id = create_topic_with_items(
            db,
            title=str(payload.get("title") or "").strip() or "低正确率题再练",
            topic_type=topic_type,
            description=str(payload.get("description") or ""),
            question_ids=question_ids,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"生成专题失败: {exc}"})
    return {
        "status": "success",
        "data": {
            "id": topic_id,
            "question_ids": question_ids,
            "threshold": threshold,
            "min_sample": min_sample,
        },
    }


@app.post("/api/topics/from-curriculum")
def create_topic_from_curriculum(payload: dict, db: Session = Depends(get_db)):
    """课程树节点 → 专题（V3 知识图谱生成的务实降级：复用课程索引）。"""

    from mathbank.topics import (
        TOPIC_MAX_ITEMS,
        create_topic_with_items,
        validate_topic_type,
    )

    try:
        compulsory = payload.get("compulsory") or None
        chapter = payload.get("chapter") or None
        knowledge = payload.get("knowledge") or None
        if not any((compulsory, chapter, knowledge)):
            raise ValueError("请至少选择必修、章节或知识点之一")
        limit = max(1, min(int(payload.get("limit", 12)), TOPIC_MAX_ITEMS))
        query = db.query(Question)
        query = apply_curriculum_filters(query, compulsory, chapter, knowledge)
        # 层内排序与 from-tags 一致：有答案、被多次验证过的题优先
        candidates = sorted(
            query.all(),
            key=lambda q: (
                0 if str(q.answer_markdown or "").strip() else 1,
                -(int(q.usage_count or 0)),
                q.id,
            ),
        )[:limit]
        question_ids = [question.id for question in candidates]
        if not question_ids:
            raise ValueError("该课程节点下没有题目")
        topic_id = create_topic_with_items(
            db,
            title=str(payload.get("title") or "").strip()
            or "课程专题：{}{}{}".format(
                str(compulsory or ""), str(chapter or ""), str(knowledge or "")
            ),
            topic_type=validate_topic_type(payload.get("topic_type") or "knowledge"),
            description=str(payload.get("description") or ""),
            question_ids=question_ids,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"生成专题失败: {exc}"})
    return {"status": "success", "data": {"id": topic_id, "question_ids": question_ids}}


# ---- 专题版本历史 ----

@app.get("/api/topics/{topic_id}/versions")
def list_topic_versions(topic_id: int, db: Session = Depends(get_db)):
    """版本列表（version_no 降序）。"""

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    versions = (
        db.query(TopicVersion)
        .filter(TopicVersion.topic_id == topic_id)
        .order_by(TopicVersion.version_no.desc())
        .all()
    )
    return {"status": "success", "data": [version.to_dict() for version in versions]}


@app.post("/api/topics/{topic_id}/versions")
def save_topic_version_endpoint(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """存快照（append-only）。"""

    from mathbank.topics import save_topic_version

    try:
        version = save_topic_version(db, topic_id, note=str(payload.get("note") or ""))
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"保存版本失败: {exc}"})
    return {"status": "success", "data": version}


@app.post("/api/topics/{topic_id}/versions/{version_no}/restore")
def restore_topic_version_endpoint(
    topic_id: int, version_no: int, db: Session = Depends(get_db)
):
    """回滚 = 追加新版本：先存当前为新版，再整体替换。"""

    from mathbank.topics import restore_topic_from_snapshot

    try:
        version = restore_topic_from_snapshot(db, topic_id, version_no)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"恢复版本失败: {exc}"})
    return {"status": "success", "data": version}


@app.delete("/api/topics/{topic_id}/versions/{version_no}")
def delete_topic_version_endpoint(
    topic_id: int, version_no: int, db: Session = Depends(get_db)
):
    """单版删除（保留至少 1 版）。"""

    version = (
        db.query(TopicVersion)
        .filter(TopicVersion.topic_id == topic_id, TopicVersion.version_no == version_no)
        .first()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    remaining = (
        db.query(TopicVersion).filter(TopicVersion.topic_id == topic_id).count()
    )
    if remaining <= 1:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "至少保留一个版本，无法删除"},
        )
    try:
        db.delete(version)
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": f"删除版本失败: {exc}"})
    return {"status": "success", "message": "版本已删除"}


# ---- 专题间关联 ----

@app.get("/api/topics/{topic_id}/links")
def list_topic_links(topic_id: int, db: Session = Depends(get_db)):
    """关联列表（含对端专题摘要，双向都返回）。"""

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    rows = (
        db.query(TopicLink)
        .filter(
            or_(
                TopicLink.source_topic_id == topic_id,
                TopicLink.target_topic_id == topic_id,
            )
        )
        .order_by(TopicLink.created_at.asc(), TopicLink.id.asc())
        .all()
    )
    other_ids = {
        row.target_topic_id if row.source_topic_id == topic_id else row.source_topic_id
        for row in rows
    }
    others = (
        {topic.id: topic for topic in db.query(Topic).filter(Topic.id.in_(other_ids)).all()}
        if other_ids
        else {}
    )
    items = []
    for row in rows:
        other_id = (
            row.target_topic_id if row.source_topic_id == topic_id else row.source_topic_id
        )
        other = others.get(other_id)
        if other is None:
            continue
        items.append(
            {
                **row.to_dict(),
                "direction": "out" if row.source_topic_id == topic_id else "in",
                "other_topic": {"id": other.id, "title": other.title, "topic_type": other.topic_type},
            }
        )
    return {"status": "success", "data": items}


@app.post("/api/topics/{topic_id}/links")
def create_topic_link(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """建关联：白名单 + custom: 逃生舱 + 禁自连 + IntegrityError 幂等回退。"""

    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    from mathbank.topics import validate_topic_link_code

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    try:
        code = validate_topic_link_code(payload.get("link_code"))
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    try:
        target_topic_id = int(payload.get("target_topic_id"))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "target_topic_id 必须为整数"}
        )
    if target_topic_id == int(topic_id):
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "不能与自身建立关联"}
        )
    target = db.get(Topic, target_topic_id)
    if target is None:
        raise HTTPException(status_code=404, detail="目标专题不存在")

    label = str(payload.get("link_label") or "").strip()
    note = str(payload.get("note") or "")

    def _apply_update(row: TopicLink) -> None:
        if label:
            row.link_label = label
        if note:
            row.note = note

    try:
        existing = (
            db.query(TopicLink)
            .filter(
                TopicLink.source_topic_id == topic_id,
                TopicLink.target_topic_id == target_topic_id,
                TopicLink.link_code == code,
            )
            .first()
        )
        if existing is not None:
            _apply_update(existing)
            link, created = existing, False
        else:
            link = TopicLink(
                source_topic_id=topic_id,
                target_topic_id=target_topic_id,
                link_code=code,
                link_label=label,
                note=note,
            )
            db.add(link)
            created = True
        db.commit()
        db.refresh(link)
    except SAIntegrityError:
        db.rollback()
        existing = (
            db.query(TopicLink)
            .filter(
                TopicLink.source_topic_id == topic_id,
                TopicLink.target_topic_id == target_topic_id,
                TopicLink.link_code == code,
            )
            .first()
        )
        if existing is None:
            return JSONResponse(
                status_code=400, content={"status": "error", "message": "建立关联失败"}
            )
        _apply_update(existing)
        db.commit()
        db.refresh(existing)
        link, created = existing, False
    except Exception as exc:
        db.rollback()
        return JSONResponse(
            status_code=400, content={"status": "error", "message": f"建立关联失败: {exc}"}
        )
    return {"status": "success", "data": {"link_id": link.id, "created": created}}


@app.delete("/api/topic-links/{link_id}")
def delete_topic_link(link_id: int, db: Session = Depends(get_db)):
    """删关联（级联由 DB 负责，这里只删行）。"""

    link = db.get(TopicLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="关联不存在")
    try:
        db.delete(link)
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": f"删除关联失败: {exc}"})
    return {"status": "success", "message": "关联已删除"}


# ---- 使用记录与学情 ----

@app.post("/api/topics/{topic_id}/usage")
def record_topic_usage(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """记录使用 + 学情批量录入（usage_count+1、last_used_at=now）。

    question_stats: [{question_id, correct_rate?, avg_seconds?, sample_size?, note?}]
    手工记录为追加式（天然形成「第 1 次 0.4 → 第 2 次 0.8」趋势）。
    """

    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    question_stats = payload.get("question_stats") or []
    if not isinstance(question_stats, list):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "question_stats 必须是数组"},
        )
    try:
        topic.usage_count = int(topic.usage_count or 0) + 1
        topic.last_used_at = _utcnow_naive()
        recorded = 0
        for entry in question_stats:
            if not isinstance(entry, dict):
                raise ValueError("question_stats 每项必须是对象")
            try:
                question_id = int(entry.get("question_id"))
            except (TypeError, ValueError):
                raise ValueError("question_id 必须为整数")
            if db.get(Question, question_id) is None:
                raise ValueError(f"题目不存在: {question_id}")
            correct_rate = entry.get("correct_rate")
            if correct_rate is not None:
                correct_rate = float(correct_rate)
                if not 0.0 <= correct_rate <= 1.0:
                    raise ValueError("correct_rate 必须在 0 到 1 之间")
            avg_seconds = entry.get("avg_seconds")
            if avg_seconds is not None:
                avg_seconds = int(avg_seconds)
                if avg_seconds < 0:
                    raise ValueError("avg_seconds 不能为负")
            sample_size = int(entry.get("sample_size") or 0)
            if sample_size < 0:
                raise ValueError("sample_size 不能为负")
            db.add(
                QuestionUsageStat(
                    question_id=question_id,
                    topic_id=topic_id,
                    record_kind=str(entry.get("record_kind") or "manual"),
                    correct_rate=correct_rate,
                    avg_seconds=avg_seconds,
                    sample_size=sample_size,
                    note=str(entry.get("note") or ""),
                )
            )
            recorded += 1
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"记录失败: {exc}"})
    return {"status": "success", "data": {"recorded": recorded, "usage_count": topic.usage_count}}


@app.get("/api/topics/{topic_id}/usage")
def list_topic_usage(topic_id: int, db: Session = Depends(get_db)):
    """事件列表 + 逐题聚合（最近一条 + 历史趋势）。"""

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    rows = (
        db.query(QuestionUsageStat)
        .filter(QuestionUsageStat.topic_id == topic_id)
        .order_by(QuestionUsageStat.recorded_at.desc(), QuestionUsageStat.id.desc())
        .limit(200)
        .all()
    )
    by_question: dict[int, list[dict]] = {}
    for row in rows:
        by_question.setdefault(int(row.question_id), []).append(row.to_dict())
    aggregated = [
        {
            "question_id": question_id,
            "latest": entries[0],
            "history": entries,
        }
        for question_id, entries in sorted(by_question.items())
    ]
    return {
        "status": "success",
        "data": {
            "usage_count": db.get(Topic, topic_id).usage_count or 0,
            "last_used_at": (
                db.get(Topic, topic_id).last_used_at.isoformat() + "Z"
                if db.get(Topic, topic_id).last_used_at
                else None
            ),
            "events": [row.to_dict() for row in rows],
            "by_question": aggregated,
        },
    }


# ---- 智能归纳：推荐 / 洞察 / AI ----

@app.get("/api/topics/{topic_id}/recommendations")
def topic_recommendations(
    topic_id: int,
    limit: int = 5,
    diversity: bool = True,
    db: Session = Depends(get_db),
):
    """规则引擎推荐：同模型 / 同解法 / 变式（score + reasons 可解释）。"""

    from mathbank.topic_recommender import recommend_questions

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    limit = max(1, min(int(limit), 20))
    try:
        items = recommend_questions(db, topic_id, limit=limit, diversity=diversity)
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": f"推荐失败: {exc}"}
        )
    return {"status": "success", "data": items}


@app.get("/api/topics/{topic_id}/insights")
def topic_insights(topic_id: int, db: Session = Depends(get_db)):
    """配比警告 + 题目级触发 + 专题级汇总。"""

    from mathbank.topic_insights import topic_insights_report
    from mathbank.topics import check_difficulty_balance

    if db.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    try:
        balance = check_difficulty_balance(db, topic_id)
        insights = topic_insights_report(db, topic_id)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": f"洞察失败: {exc}"}
        )
    return {"status": "success", "data": {"difficulty_balance": balance, **insights}}


@app.post("/api/topics/{topic_id}/ai-recommend")
def topic_ai_recommend(topic_id: int, payload: dict, db: Session = Depends(get_db)):
    """LLM 推荐（M3 可选）：候选池本地过滤 → 模型只从候选 id 选。

    军规：只发题目 id + 截断题干 + 是否有答案，不发图；输出只是建议；
    Key 缺失明确报错，不静默降级。
    """

    import re as _re

    from mathbank.ai_json import parse_ai_json
    from mathbank.ai_http import post_chat_completion
    from mathbank.ai_providers import resolve_text_provider
    from mathbank.prompts import build_topic_recommendation_prompts
    from mathbank.topic_recommender import recommend_questions
    from mathbank.topics import TOPIC_TYPE_LABELS

    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    limit = max(1, min(int(payload.get("limit", 5)), 20))
    teacher_hint = str(payload.get("teacher_hint") or "").strip()

    # 候选池：规则引擎放大 limit 取池，模型只在池内选。
    pool = recommend_questions(db, topic_id, limit=20, diversity=False)
    if not pool:
        return {
            "status": "success",
            "data": {"selected": [], "message": "规则引擎未找到候选题目"},
        }
    candidate_ids = [entry["question_id"] for entry in pool]
    candidate_rows = (
        db.query(Question).filter(Question.id.in_(candidate_ids)).all()
    )
    questions_by_id = {question.id: question for question in candidate_rows}
    candidate_items = []
    for entry in pool:
        question = questions_by_id.get(entry["question_id"])
        if question is None:
            continue
        candidate_items.append(
            {
                "id": question.id,
                "question_type": question.question_type or "",
                "difficulty": question.difficulty or "",
                "usage_count": int(question.usage_count or 0),
                "knowledge": question.category_knowledge or "整章定位（待细分）",
                "tags": question.tags or "",
                "has_answer": bool(str(question.answer_markdown or "").strip()),
                "stem_excerpt": _re.sub(r"[\r\n]+", " ", str(question.content or "")[:80]),
                "rule_reasons": entry["reasons"],
            }
        )

    target_model = os.getenv("PREFER_SOLVE_MODEL") or os.getenv("PREFER_PARSE_MODEL") or "deepseek-flash"
    try:
        provider = resolve_text_provider(target_model)
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"AI 模型解析失败: {exc}"},
        )
    if not provider.api_key or not provider.api_base:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": (
                    f"指定的 AI 模型 ({target_model}) 未配置有效的 API Key 或 "
                    f"Base URL（{provider.credential_label}）。AI 推荐为可选功能，"
                    "请在工作台右上角设置面板进行配置。"
                ),
            },
        )

    system_prompt, user_content = build_topic_recommendation_prompts(
        topic_title=topic.title,
        topic_type=TOPIC_TYPE_LABELS.get(topic.topic_type, topic.topic_type),
        candidates=candidate_items,
        limit=limit,
        teacher_hint=teacher_hint,
    )
    data = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }
    try:
        response = post_chat_completion(provider, data, timeout=120)
        body = response.json()
        parsed = parse_ai_json(
            body.get("choices", [{}])[0].get("message", {}).get("content", "")
        )
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"AI 推荐失败: {exc}"},
        )

    allowed = set(candidate_ids)
    selected: list[dict] = []
    seen: set[int] = set()
    reasons = parsed.get("reasons") if isinstance(parsed, dict) else {}
    for raw_id in parsed.get("selected_ids", []) if isinstance(parsed, dict) else []:
        try:
            question_id = int(raw_id)
        except (TypeError, ValueError):
            continue  # 模型幻觉出的非整数 id 直接丢弃
        if question_id not in allowed or question_id in seen:
            continue
        seen.add(question_id)
        selected.append(
            {
                "question_id": question_id,
                "reason": str((reasons or {}).get(str(question_id), "")),
            }
        )
        if len(selected) >= limit:
            break
    return {
        "status": "success",
        "data": {
            "selected": selected,
            "ai_analysis": str(parsed.get("ai_analysis", "")) if isinstance(parsed, dict) else "",
            "candidate_count": len(candidate_items),
            "note": "AI 输出只是建议，确认后才会入库",
        },
    }


# ---- 流通：导出 / 导入 ----

TOPIC_EXPORT_FORMAT = "mathbank-topic"
TOPIC_EXPORT_VERSION = 1


@app.get("/api/topics/{topic_id}/export")
def export_topic(topic_id: int, db: Session = Depends(get_db)):
    """JSON 导出（本地文件交换；共享/校本库明确不做）。"""

    import datetime as _dt

    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="专题不存在")
    groups = (
        db.query(TopicGroup)
        .filter(TopicGroup.topic_id == topic_id)
        .order_by(TopicGroup.position.asc(), TopicGroup.id.asc())
        .all()
    )
    rows = (
        db.query(TopicItem, Question)
        .join(Question, Question.id == TopicItem.question_id)
        .filter(TopicItem.topic_id == topic_id)
        .order_by(TopicItem.position.asc(), TopicItem.id.asc())
        .all()
    )
    group_title_by_id = {group.id: group.title for group in groups}
    return {
        "status": "success",
        "data": {
            "format": TOPIC_EXPORT_FORMAT,
            "version": TOPIC_EXPORT_VERSION,
            "exported_at": _dt.datetime.now(_dt.timezone.utc)
            .replace(tzinfo=None)
            .isoformat()
            + "Z",
            "topic": topic.to_dict(),
            "groups": [group.to_dict() for group in groups],
            "items": [
                {
                    "position": index,
                    "group_title": group_title_by_id.get(item.group_id, ""),
                    "note": item.note or "",
                    "question": {
                        "id": question.id,
                        "content": question.content or "",
                        "question_type": question.question_type or "",
                        "difficulty": question.difficulty or "",
                        "answer_markdown": question.answer_markdown or "",
                        "source": question.source or "",
                    },
                }
                for index, (item, question) in enumerate(rows)
            ],
        },
    }


@app.post("/api/topics/import")
def import_topic(payload: dict, db: Session = Depends(get_db)):
    """JSON 导入：按题干全文匹配回填 question_id，未匹配进 unresolved。"""

    from mathbank.topics import create_topic_with_items

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict) or data.get("format") != TOPIC_EXPORT_FORMAT:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "不是有效的专题导出文件（format 不匹配）"},
        )
    topic_data = data.get("topic") or {}
    items = data.get("items") or []
    if not isinstance(items, list):
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "items 必须是数组"}
        )
    try:
        title = str(topic_data.get("title") or "").strip()
        # 全文匹配：题干完全相同才回填；空/多匹配都进 unresolved 人工关联。
        question_ids: list[int] = []
        notes: dict[int, str] = {}
        # group_title → 本题库内匹配到的题目 id（按首次出现顺序建分组）。
        grouped: dict[str, list[int]] = {}
        unresolved: list[dict] = []
        for index, entry in enumerate(items):
            if not isinstance(entry, dict):
                continue
            content = str((entry.get("question") or {}).get("content") or "").strip()
            matched_id = None
            if content:
                rows = (
                    db.query(Question.id)
                    .filter(Question.content == content)
                    .limit(2)
                    .all()
                )
                if len(rows) == 1:
                    matched_id = int(rows[0][0])
            if matched_id is None:
                unresolved.append(
                    {
                        "index": index,
                        "content_excerpt": content[:80],
                        "reason": "未在题库中找到唯一匹配的题干",
                    }
                )
                continue
            if matched_id in question_ids:
                unresolved.append(
                    {
                        "index": index,
                        "content_excerpt": content[:80],
                        "reason": "与已匹配题目重复（同一专题内同题禁重）",
                    }
                )
                continue
            question_ids.append(matched_id)
            notes[matched_id] = str(entry.get("note") or "")
            group_title = str(entry.get("group_title") or "").strip()
            if group_title:
                grouped.setdefault(group_title, []).append(matched_id)
        if not question_ids:
            raise ValueError("没有可匹配的题目，无法导入（详见 unresolved）")
        topic_id = create_topic_with_items(
            db,
            title=title or "导入的专题",
            topic_type=str(topic_data.get("topic_type") or "knowledge"),
            description=str(topic_data.get("description") or ""),
            view_mode=str(topic_data.get("view_mode") or "linear"),
            question_ids=question_ids,
            notes=notes,
        )
        # 分组还原：导出 JSON 自带 group_title，按首次出现顺序重建分组，
        # 让「导出 → 改标题 → 导入」保持 round-trip 忠实度（空标题 = 未分组）。
        if grouped:
            new_items = (
                db.query(TopicItem)
                .filter(TopicItem.topic_id == topic_id)
                .order_by(TopicItem.position.asc(), TopicItem.id.asc())
                .all()
            )
            item_by_question = {item.question_id: item for item in new_items}
            for position, (group_title, members) in enumerate(grouped.items()):
                group = TopicGroup(
                    topic_id=topic_id, title=group_title, position=position
                )
                db.add(group)
                db.flush()
                for question_id in members:
                    item = item_by_question.get(question_id)
                    if item is not None:
                        item.group_id = group.id
            db.flush()
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"status": "error", "message": f"导入失败: {exc}"})
    return {
        "status": "success",
        "data": {
            "id": topic_id,
            "matched_count": len(question_ids),
            "unresolved": unresolved,
        },
    }


# ----------------- Mount Static Folder last to allow API override -----------------
app.mount("/static", RetainedUploadStaticFiles(
    directory=str(STATIC_DIR), uploads_dir=UPLOAD_DIR, url_prefix=UPLOAD_DIR_REL,
), name="static")
