"""Bedrock Claude Auth Gateway — FastAPI Application.

사내 SSO 인증 → K8s Pod 관리 → 웹 터미널 프록시
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.scheduler import idle_checker_loop, token_snapshot_loop, prompt_audit_loop, storage_cleanup_loop
from app.models.app import DeployedApp, AppACL, AppView  # noqa: F401 — create_all이 테이블 생성하도록 import
from app.models.survey import SurveyTemplate, SurveyAssignment, SurveyResponse  # noqa: F401
from app.models.file_share import SharedDataset, FileShareACL  # noqa: F401 — create_all이 테이블 생성하도록 import
from app.models.token_usage import TokenUsageHourly  # noqa: F401 — create_all이 테이블 생성하도록 import
from app.models.prompt_audit import PromptAuditSummary, PromptAuditFlag, PromptAuditConversation  # noqa: F401 — create_all이 테이블 생성하도록 import
from app.models.token_quota import TokenQuotaTemplate, TokenQuotaAssignment  # noqa: F401 — create_all이 테이블 생성하도록 import
from app.models.proxy import AllowedDomain, ProxyAccessLog  # noqa: F401 — create_all이 테이블 생성하도록 import
from app.models.bot import UserBot  # noqa: F401 — create_all이 user_bots 테이블 생성하도록 import
from app.models.skill import SharedSkill, SkillInstall  # noqa: F401 — create_all이 skill_installs 테이블 생성하도록 import
from app.models.file_governance import GovernedFile  # noqa: F401 — create_all이 governed_files 테이블 생성하도록 import
from app.models.file_audit import FileAuditLog  # noqa: F401 — create_all이 file_audit_logs 테이블 생성하도록 import
from app.routers import admin, apps, auth, bots, file_share, sessions, users, sms, skills, telegram, security, scheduling, infra_policy, surveys, app_proxy, portal
from app.routers.file_governance import router as governance_router
from app.routers.secure_files import router as secure_files_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


def _run_column_migration() -> None:
    """last_active_at 컬럼 존재 여부 확인 후 없으면 추가 (create_all 한계 보완).

    주의: DEFAULT NOW()로 컬럼 추가하면 모든 기존 행이 동일 타임스탬프를 가져
    60분 후 일괄 만료되는 cascade 문제가 발생한다.
    따라서 컬럼 추가 후 기존 행은 started_at 값으로 초기화한다.
    """
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='terminal_sessions' AND column_name='last_active_at'"
        ))
        if result.fetchone() is None:
            # NULL 허용으로 추가 후 각 행을 started_at으로 초기화
            conn.execute(text(
                "ALTER TABLE terminal_sessions "
                "ADD COLUMN last_active_at TIMESTAMPTZ"
            ))
            conn.execute(text(
                "UPDATE terminal_sessions "
                "SET last_active_at = started_at "
                "WHERE last_active_at IS NULL"
            ))
            conn.commit()
            logger.info("Migration: terminal_sessions.last_active_at 컬럼 추가 + started_at으로 초기화 완료")


def _run_can_deploy_apps_migration() -> None:
    """users 테이블에 can_deploy_apps 컬럼이 없으면 추가 (웹앱 배포 권한).

    기본값 FALSE — 관리자가 개별 승인해야 배포 가능.
    """
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='can_deploy_apps'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "ALTER TABLE users "
                "ADD COLUMN can_deploy_apps BOOLEAN DEFAULT FALSE"
            ))
            conn.commit()
            logger.info("Migration: users.can_deploy_apps 컬럼 추가 완료")


def _run_app_visibility_migration() -> None:
    """deployed_apps 테이블에 visibility, app_port 컬럼이 없으면 추가."""
    with engine.connect() as conn:
        # visibility 컬럼
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='deployed_apps' AND column_name='visibility'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "ALTER TABLE deployed_apps "
                "ADD COLUMN visibility VARCHAR(20) DEFAULT 'private'"
            ))
            logger.info("Migration: deployed_apps.visibility 컬럼 추가 완료")

        # app_port 컬럼
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='deployed_apps' AND column_name='app_port'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "ALTER TABLE deployed_apps "
                "ADD COLUMN app_port INTEGER DEFAULT 3000"
            ))
            logger.info("Migration: deployed_apps.app_port 컬럼 추가 완료")

        conn.commit()


def _run_proxy_secret_migration() -> None:
    """terminal_sessions 테이블에 proxy_secret 컬럼이 없으면 추가 (프록시 인증용)."""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='terminal_sessions' AND column_name='proxy_secret'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "ALTER TABLE terminal_sessions "
                "ADD COLUMN proxy_secret VARCHAR(64)"
            ))
            conn.commit()
            logger.info("Migration: terminal_sessions.proxy_secret 컬럼 추가 완료")


def _run_app_acl_grant_migration() -> None:
    """app_acl 테이블을 grant_type/grant_value 스키마로 마이그레이션."""
    with engine.connect() as conn:
        # grant_type 컬럼 추가
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='app_acl' AND column_name='grant_type'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "ALTER TABLE app_acl ADD COLUMN grant_type VARCHAR(10) NOT NULL DEFAULT 'user'"
            ))
            logger.info("Migration: app_acl.grant_type 컬럼 추가")

        # grant_value 컬럼 추가
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='app_acl' AND column_name='grant_value'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "ALTER TABLE app_acl ADD COLUMN grant_value VARCHAR(100) DEFAULT ''"
            ))
            # granted_username → grant_value 데이터 이관
            conn.execute(text(
                "UPDATE app_acl SET grant_value = granted_username WHERE granted_username IS NOT NULL AND grant_value = ''"
            ))
            conn.execute(text(
                "ALTER TABLE app_acl ALTER COLUMN grant_value SET NOT NULL"
            ))
            logger.info("Migration: app_acl.grant_value 컬럼 추가 + granted_username 데이터 이관")

        # 인덱스 추가
        result = conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='app_acl' AND indexname='ix_app_acl_grant'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "CREATE INDEX ix_app_acl_grant ON app_acl(app_id, grant_type, grant_value, revoked_at)"
            ))
            logger.info("Migration: app_acl grant 인덱스 추가")

        conn.commit()


def _run_skill_store_migration() -> None:
    """shared_skills 테이블에 스킬 스토어 컬럼 추가 + skill_installs 테이블 생성.

    create_all이 새 테이블(skill_installs)은 자동 생성하지만,
    기존 테이블(shared_skills)에 새 컬럼은 추가하지 않으므로 수동 마이그레이션.
    """
    with engine.connect() as conn:
        # shared_skills에 스토어용 컬럼 추가
        store_columns = {
            "owner_username": "VARCHAR(50)",
            "skill_name": "VARCHAR(100)",
            "display_name": "VARCHAR(200)",
            "skill_type": "VARCHAR(20) DEFAULT 'slash_command'",
            "skill_dir_name": "VARCHAR(100)",
            "install_count": "INTEGER DEFAULT 0",
            "is_active": "BOOLEAN DEFAULT TRUE",
            "updated_at": "TIMESTAMPTZ",
        }
        for col_name, col_type in store_columns.items():
            result = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='shared_skills' AND column_name=:col_name"
            ), {"col_name": col_name})
            if result.fetchone() is None:
                conn.execute(text(
                    f"ALTER TABLE shared_skills ADD COLUMN {col_name} {col_type}"
                ))
                logger.info(f"Migration: shared_skills.{col_name} 컬럼 추가 완료")

        # description 컬럼을 TEXT로 확장 (기존 VARCHAR(500) → TEXT)
        # 기존 컬럼이 varchar(500)이면 TEXT로 변경
        result = conn.execute(text(
            "SELECT data_type, character_maximum_length FROM information_schema.columns "
            "WHERE table_name='shared_skills' AND column_name='description'"
        ))
        row = result.fetchone()
        if row and row[0] == 'character varying' and row[1] and row[1] <= 500:
            conn.execute(text(
                "ALTER TABLE shared_skills ALTER COLUMN description TYPE TEXT"
            ))
            logger.info("Migration: shared_skills.description VARCHAR→TEXT 변경 완료")

        # title, content를 nullable로 변경 (스토어 스킬은 이 컬럼을 사용하지 않을 수 있음)
        for col in ("title", "content"):
            try:
                conn.execute(text(
                    f"ALTER TABLE shared_skills ALTER COLUMN {col} DROP NOT NULL"
                ))
            except Exception:
                pass  # 이미 nullable이면 무시

        # owner_username 인덱스 추가
        result = conn.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename='shared_skills' AND indexname='ix_shared_skills_owner'"
        ))
        if result.fetchone() is None:
            conn.execute(text(
                "CREATE INDEX ix_shared_skills_owner ON shared_skills(owner_username)"
            ))
            logger.info("Migration: shared_skills owner_username 인덱스 추가")

        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 라이프사이클 — DB 초기화 + 백그라운드 스케줄러 시작."""
    Base.metadata.create_all(bind=engine)
    _run_column_migration()
    _run_can_deploy_apps_migration()
    _run_app_visibility_migration()
    _run_proxy_secret_migration()
    _run_app_acl_grant_migration()
    _run_skill_store_migration()
    idle_task = asyncio.create_task(idle_checker_loop(settings))
    snapshot_task = asyncio.create_task(token_snapshot_loop(settings))
    audit_task = asyncio.create_task(prompt_audit_loop(settings))
    storage_task = asyncio.create_task(storage_cleanup_loop(settings))
    logger.info(f"{settings.app_name} started")
    yield
    idle_task.cancel()
    snapshot_task.cancel()
    audit_task.cancel()
    storage_task.cancel()
    try:
        await idle_task
    except asyncio.CancelledError:
        pass
    try:
        await snapshot_task
    except asyncio.CancelledError:
        pass
    try:
        await audit_task
    except asyncio.CancelledError:
        pass
    try:
        await storage_task
    except asyncio.CancelledError:
        pass
    logger.info(f"{settings.app_name} shutdown")


app = FastAPI(
    title=settings.app_name,
    description="AWS Bedrock Claude Code 사내 활용 플랫폼 — 인증 및 세션 관리",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://claude-admin.skons.net",
        "https://claude.skons.net",
        "http://localhost:3000",  # 로컬 개발용
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(users.router)
app.include_router(sms.router)
app.include_router(skills.router)
app.include_router(telegram.router)
app.include_router(security.router)
app.include_router(scheduling.router)
app.include_router(infra_policy.router)
app.include_router(apps.router)
app.include_router(bots.router)
app.include_router(file_share.router)
app.include_router(surveys.router)
app.include_router(portal.router)
app.include_router(governance_router)
app.include_router(secure_files_router)
# app_proxy는 catch-all 경로이므로 반드시 마지막에 등록
app.include_router(app_proxy.router)




static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root():
    return FileResponse(str(static_dir / "login.html"))


@app.get("/webapp-login")
async def webapp_login():
    """경량 로그인 페이지 — SSO+2FA 인증만, Pod 미생성."""
    return FileResponse(str(static_dir / "webapp-login.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "auth-gateway"}
