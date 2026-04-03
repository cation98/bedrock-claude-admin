"""FastAPI lifespan용 백그라운드 스케줄러.

유휴 Pod 정리 + 토큰 사용량 스냅샷 + 스토리지 보존 정리를 주기적으로 실행한다.
앱 프로세스 내부 asyncio 태스크로 동작 — 별도 패키지 불필요.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import Settings

logger = logging.getLogger(__name__)


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
    """매시간 토큰 사용량 스냅샷 → token_usage_daily + token_usage_hourly 저장."""
    # 순환 참조 방지를 위해 내부에서 임포트
    from app.core.database import SessionLocal
    from app.routers.admin import do_snapshot

    snapshot_interval = 600  # 10분마다
    logger.info(f"토큰 스냅샷 스케줄러 시작 — 주기={snapshot_interval}s (10분)")

    # 앱 기동 안정화 후 30초 대기
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

        await asyncio.sleep(CLEANUP_INTERVAL)
