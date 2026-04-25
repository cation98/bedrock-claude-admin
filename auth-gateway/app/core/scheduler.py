"""FastAPI lifespan용 백그라운드 스케줄러.

유휴 Pod 정리 + 토큰 사용량 스냅샷 + 스토리지 보존 정리를 주기적으로 실행한다.
앱 프로세스 내부 asyncio 태스크로 동작 — 별도 패키지 불필요.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from app.core.config import Settings

logger = logging.getLogger(__name__)


# ==================== 인-프로세스 스케줄러 락 ====================
# Redis가 없는 환경에서 동일 프로세스 내 중복 실행을 방지하는 단순 락.
# 멀티-프로세스 환경에서는 Redis SETNX로 대체해야 한다.

_scheduler_locks: dict[str, float] = {}  # lock_name → expiry_epoch


def acquire_scheduler_lock(lock_name: str, ttl_seconds: int = 300) -> bool:
    """스케줄러 락 획득 (SETNX 의미론).

    Args:
        lock_name: 락 이름 (고유 식별자)
        ttl_seconds: 락 유효 시간 (초)

    Returns:
        True이면 락 획득 성공, False이면 이미 락이 존재함.
    """
    now = time.monotonic()
    expiry = _scheduler_locks.get(lock_name, 0)
    if expiry > now:
        # 락이 아직 유효함
        return False
    # 락 획득
    _scheduler_locks[lock_name] = now + ttl_seconds
    return True


def release_scheduler_lock(lock_name: str) -> None:
    """스케줄러 락 해제."""
    _scheduler_locks.pop(lock_name, None)


async def idle_checker_loop(settings: Settings) -> None:
    """백그라운드 루프: 주기적으로 유휴 Pod를 탐지·정리한다."""
    # 순환 참조 방지를 위해 내부에서 임포트
    from app.core.database import SessionLocal
    from app.services.idle_cleanup_service import IdleCleanupService
    from app.services.k8s_service import K8sService

    logger.info(
        f"유휴 Pod 정리 스케줄러 시작 — "
        f"체크 주기={settings.idle_check_interval_seconds}s, "
        f"유휴 임계값={settings.idle_timeout_minutes}min"
    )

    # 첫 실행은 앱 기동 안정화 후 (1분 대기)
    await asyncio.sleep(60)

    while True:
        db = SessionLocal()
        try:
            k8s = K8sService(settings)
            svc = IdleCleanupService(k8s, idle_timeout_minutes=settings.idle_timeout_minutes)
            terminated = svc.run_cleanup(db)
            if terminated:
                logger.info(f"유휴 정리 완료: {terminated}")
        except Exception as e:
            logger.error(f"유휴 체커 오류: {e}", exc_info=True)
        finally:
            db.close()

        await asyncio.sleep(settings.idle_check_interval_seconds)


async def token_snapshot_loop(settings: Settings) -> None:
    """매 10분 토큰 사용량 스냅샷 → token_usage_daily + token_usage_hourly 저장.

    T20 활성화 후 usage-worker가 stream에서 SSOT로 동작하므로 deprecated.
    settings.snapshot_loop_enabled=true 설정 시에만 emergency backfill 모드로 동작.
    """
    if not settings.snapshot_loop_enabled:
        logger.info(
            "token_snapshot_loop disabled — usage-worker is SSOT after T20 activation. "
            "Set SNAPSHOT_LOOP_ENABLED=true to re-enable for emergency backfill."
        )
        return

    # 순환 참조 방지를 위해 내부에서 임포트
    from app.core.database import SessionLocal
    from app.routers.admin import do_snapshot

    snapshot_interval = 600  # 10분마다
    logger.info(f"토큰 스냅샷 스케줄러 시작 (emergency mode) — 주기={snapshot_interval}s")

    await asyncio.sleep(30)

    while True:
        db = SessionLocal()
        try:
            result = do_snapshot(db, settings)
            logger.info(f"토큰 스냅샷 완료: {result}")
        except Exception as e:
            logger.error(f"토큰 스냅샷 오류: {e}", exc_info=True)
        finally:
            db.close()

        await asyncio.sleep(snapshot_interval)


async def event_retention_loop(settings: Settings) -> None:
    """token_usage_event 90일 retention purge. 일 1회 실행.

    Bedrock 분기 빌링 cycle(3개월)과 일치 — 분기 단위 reconciliation 사고 복구
    윈도우 내 dedupe 보호. 실제 데이터 합산은 token_usage_daily/hourly에 누적되어
    영구 보존되므로 event 삭제는 dedupe 정보만 손실됨.

    Spec §4.3, §5.5
    """
    from app.core.database import SessionLocal
    from sqlalchemy import text

    cleanup_interval = 86400  # 24시간
    logger.info(f"token_usage_event retention 스케줄러 시작 — 주기={cleanup_interval}s, 보존 90 days")

    # 앱 기동 안정화 후 5분 대기
    await asyncio.sleep(300)

    while True:
        db = SessionLocal()
        try:
            result = db.execute(text(
                "DELETE FROM token_usage_event WHERE recorded_at < NOW() - INTERVAL '90 days'"
            ))
            db.commit()
            logger.info(f"token_usage_event purge 완료 — 삭제 row: {result.rowcount}")
        except Exception as e:
            logger.error(f"token_usage_event purge 오류: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(cleanup_interval)


async def prompt_audit_loop(settings: Settings) -> None:
    """2시간마다 프롬프트 감사 실행 — 카테고리 분류 + 보안 위반 탐지."""
    from app.core.database import SessionLocal
    from app.services.prompt_audit_service import PromptAuditService

    audit_interval = 1800  # 30분
    logger.info(f"프롬프트 감사 스케줄러 시작 — 주기={audit_interval}s (30분)")

    # 앱 기동 안정화 후 2분 대기
    await asyncio.sleep(120)

    while True:
        db = SessionLocal()
        try:
            svc = PromptAuditService()
            result = svc.collect_and_analyze(db, namespace=settings.k8s_namespace)
            logger.info(f"프롬프트 감사 완료: {result}")
        except Exception as e:
            logger.error(f"프롬프트 감사 오류: {e}", exc_info=True)
        finally:
            db.close()

        await asyncio.sleep(audit_interval)


# ==================== Storage Retention 파싱 ====================

# storage_retention 문자열 → timedelta 변환
_RETENTION_MAP = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "180d": timedelta(days=180),
}


def _parse_retention(value: str) -> timedelta | None:
    """storage_retention 값을 timedelta로 변환. unlimited이면 None 반환."""
    if value == "unlimited":
        return None
    return _RETENTION_MAP.get(value)


async def storage_cleanup_loop(settings: Settings) -> None:
    """24시간마다 스토리지 보존 기한 만료 사용자를 탐지하고 텔레그램 경고를 발송한다.

    동작 방식:
      1. storage_retention != 'unlimited'인 모든 승인 사용자를 조회한다.
      2. approved_at + retention 기간을 기준으로 만료 여부를 판별한다.
      3. 만료 3일 전 사용자에게 텔레그램 사전 경고를 보낸다.
      4. 만료된 사용자는 로그로 기록한다 (dry_run=True이면 실제 삭제 안 함).

    파일 삭제는 EFS 경로 /home/node/workspace/users/{username}/ 대상이지만,
    안전을 위해 기본값 dry_run=True로 로그만 남긴다.
    DRY_RUN 플래그를 False로 설정하면 실제 삭제를 수행한다.
    """
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.routers.telegram import TelegramMapping, send_telegram_message

    DRY_RUN = True  # 실제 삭제는 충분한 테스트 후 False로 변경
    CLEANUP_INTERVAL = 86400  # 24시간
    WARNING_DAYS_BEFORE = 3  # 만료 3일 전 경고

    logger.info(
        f"스토리지 정리 스케줄러 시작 — "
        f"주기={CLEANUP_INTERVAL}s (24시간), "
        f"dry_run={DRY_RUN}"
    )

    # 앱 기동 안정화 후 2분 대기
    await asyncio.sleep(120)

    while True:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)

            # storage_retention이 unlimited가 아닌 승인된 사용자 조회
            users = (
                db.query(User)
                .filter(
                    User.is_approved == True,  # noqa: E712
                    User.storage_retention != "unlimited",
                )
                .all()
            )

            warned_users: list[str] = []
            expired_users: list[str] = []

            for user in users:
                retention = _parse_retention(user.storage_retention)
                if retention is None:
                    continue

                # 기준일: approved_at 또는 created_at
                base_date = user.approved_at or user.created_at
                if base_date is None:
                    continue

                expires_at = base_date + retention
                days_remaining = (expires_at - now).total_seconds() / 86400

                # --- 만료 3일 전 경고 ---
                if 0 < days_remaining <= WARNING_DAYS_BEFORE:
                    warned_users.append(
                        f"{user.username}({user.name}) — "
                        f"보존={user.storage_retention}, "
                        f"만료={expires_at.strftime('%Y-%m-%d')}, "
                        f"잔여={days_remaining:.1f}일"
                    )

                    # 텔레그램 경고 발송
                    mapping = (
                        db.query(TelegramMapping)
                        .filter(TelegramMapping.username == user.username)
                        .first()
                    )
                    if mapping:
                        warning_msg = (
                            f"[스토리지 보존 경고]\n\n"
                            f"{user.name}님, 작업 공간 보존 기한이 "
                            f"{days_remaining:.0f}일 후 만료됩니다.\n\n"
                            f"보존 정책: {user.storage_retention}\n"
                            f"만료 예정: {expires_at.strftime('%Y-%m-%d')}\n\n"
                            f"만료 시 /home/node/workspace 데이터가 정리됩니다.\n"
                            f"필요한 파일은 미리 백업해 주세요."
                        )
                        try:
                            await send_telegram_message(
                                mapping.telegram_id, warning_msg, settings
                            )
                        except Exception as e:
                            logger.warning(
                                f"텔레그램 경고 발송 실패 ({user.username}): {e}"
                            )

                # --- 만료됨 ---
                elif days_remaining <= 0:
                    expired_users.append(
                        f"{user.username}({user.name}) — "
                        f"보존={user.storage_retention}, "
                        f"만료일={expires_at.strftime('%Y-%m-%d')}"
                    )

                    if DRY_RUN:
                        logger.info(
                            f"[DRY-RUN] 스토리지 삭제 대상: "
                            f"{user.username} — "
                            f"/home/node/workspace/users/{user.username.lower()}/"
                        )
                    else:
                        # 실제 삭제: EFS 경로 직접 제거
                        import shutil
                        workspace_path = (
                            f"/home/node/workspace/users/{user.username.lower()}"
                        )
                        try:
                            shutil.rmtree(workspace_path, ignore_errors=True)
                            logger.info(
                                f"스토리지 삭제 완료: {user.username} → {workspace_path}"
                            )
                        except Exception as e:
                            logger.error(
                                f"스토리지 삭제 실패 ({user.username}): {e}"
                            )

                        # 삭제 완료 텔레그램 알림
                        mapping = (
                            db.query(TelegramMapping)
                            .filter(TelegramMapping.username == user.username)
                            .first()
                        )
                        if mapping:
                            try:
                                await send_telegram_message(
                                    mapping.telegram_id,
                                    f"[스토리지 정리 완료]\n\n"
                                    f"{user.name}님의 작업 공간이 보존 기한 만료로 "
                                    f"정리되었습니다.\n"
                                    f"보존 정책: {user.storage_retention}",
                                    settings,
                                )
                            except Exception:
                                pass

            if warned_users:
                logger.info(
                    f"스토리지 만료 임박 경고 ({len(warned_users)}명): "
                    + "; ".join(warned_users)
                )
            if expired_users:
                logger.info(
                    f"스토리지 보존 만료{'[DRY-RUN]' if DRY_RUN else ''} "
                    f"({len(expired_users)}명): "
                    + "; ".join(expired_users)
                )
            if not warned_users and not expired_users:
                logger.info("스토리지 정리: 만료 대상 없음")

        except Exception as e:
            logger.error(f"스토리지 정리 오류: {e}", exc_info=True)
        finally:
            db.close()

        # ── 파일 수준 TTL 정리 ─────────────────────────────────
        await _run_file_ttl_cleanup()

        await asyncio.sleep(CLEANUP_INTERVAL)


async def _run_file_ttl_cleanup() -> None:
    """만료된 GovernedFile의 status를 'expired'로 업데이트하고 감사 로그를 기록한다.

    실제 파일 삭제는 Pod 에이전트에게 위임한다 (auth-gateway는 EFS에 직접 접근 불가).

    Issue #10: Redis 분산 락으로 멀티 레플리카 환경에서도 단일 실행을 보장.
    Redis 없으면 인메모리 fallback으로 자동 전환.
    """
    from app.core.redis_client import (
        acquire_scheduler_lock_redis,
        get_owner_id,
        release_scheduler_lock_redis,
    )

    owner_id = get_owner_id()

    if not acquire_scheduler_lock_redis("file_ttl_cleanup", owner_id, 300):
        logger.debug("file_ttl_cleanup 락 획득 실패 — 이미 실행 중")
        return

    from app.core.database import SessionLocal
    from app.models.file_governance import GovernedFile
    from app.models.file_audit import FileAuditLog

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        expired_files = (
            db.query(GovernedFile)
            .filter(
                GovernedFile.expires_at < now,
                GovernedFile.status == "active",
            )
            .all()
        )

        if not expired_files:
            logger.debug("파일 TTL 정리: 만료 대상 없음")
            return

        for gf in expired_files:
            gf.status = "expired"
            gf.updated_at = now

            audit = FileAuditLog(
                username=gf.username,
                action="expire",
                filename=gf.filename,
                file_path=gf.file_path,
                detail=(
                    f"TTL 만료: ttl_days={gf.ttl_days}, "
                    f"expires_at={gf.expires_at.isoformat() if gf.expires_at else None}"
                ),
            )
            db.add(audit)

            # TODO: Pod 에이전트에 파일 삭제 요청
            # POST http://claude-terminal-{username}:8080/internal/delete-file
            # auth-gateway는 EFS에 직접 접근하지 않으므로 Pod 에이전트를 통해 삭제한다.
            logger.info(
                f"파일 TTL 만료 처리: {gf.username}/{gf.filename} "
                f"(id={gf.id}, expires_at={gf.expires_at})"
            )

        db.commit()
        logger.info(f"파일 TTL 정리 완료: {len(expired_files)}개 파일 만료 처리")

    except Exception as e:
        logger.error(f"파일 TTL 정리 오류: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()
        # Issue #10: Lua 스크립트로 소유자 확인 후 해제 — 다른 레플리카의 락을 삭제하지 않음
        release_scheduler_lock_redis("file_ttl_cleanup", owner_id)


async def knowledge_extraction_loop(settings: Settings) -> None:
    """백그라운드 루프: 6시간마다 미처리 대화를 지식 그래프로 추출한다."""
    from app.core.database import SessionLocal
    from app.services.knowledge_extractor import run_extraction

    logger.info("knowledge extraction scheduler started — interval=6h")
    await asyncio.sleep(30)  # 앱 기동 안정화 대기

    while True:
        if acquire_scheduler_lock("knowledge_extraction", ttl_seconds=3600 * 6):
            db = SessionLocal()
            try:
                count = await asyncio.get_running_loop().run_in_executor(
                    None, run_extraction, db, "us-east-1"
                )
                logger.info(f"knowledge extraction done: {count} conversations processed")
            except Exception as exc:
                logger.error(f"knowledge extraction loop error: {exc}")
            finally:
                db.close()
                release_scheduler_lock("knowledge_extraction")
        await asyncio.sleep(6 * 3600)


async def knowledge_snapshot_loop(settings: Settings) -> None:
    """백그라운드 루프: 매일 스냅샷 집계."""
    from app.core.database import SessionLocal
    from app.services.knowledge_snapshot import run_snapshot

    logger.info("knowledge snapshot scheduler started — interval=24h")
    await asyncio.sleep(60)  # 앱 기동 안정화 대기

    while True:
        if acquire_scheduler_lock("knowledge_snapshot", ttl_seconds=3600 * 23):
            db = SessionLocal()
            try:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, run_snapshot, db
                )
                logger.info(f"knowledge snapshot done: {result}")
            except Exception as exc:
                logger.error(f"knowledge snapshot loop error: {exc}")
            finally:
                db.close()
                release_scheduler_lock("knowledge_snapshot")
        await asyncio.sleep(24 * 3600)
