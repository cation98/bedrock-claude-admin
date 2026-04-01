"""FastAPI lifespan용 백그라운드 스케줄러.

유휴 Pod 정리 + 토큰 사용량 스냅샷을 주기적으로 실행한다.
앱 프로세스 내부 asyncio 태스크로 동작 — 별도 패키지 불필요.
"""
import asyncio
import logging

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

    logger.info("토큰 스냅샷 스케줄러 시작 — 주기=3600s (1시간)")

    # 앱 기동 안정화 후 2분 대기
    await asyncio.sleep(120)

    while True:
        db = SessionLocal()
        try:
            result = do_snapshot(db, settings)
            logger.info(f"토큰 스냅샷 완료: {result}")
        except Exception as e:
            logger.error(f"토큰 스냅샷 오류: {e}", exc_info=True)
        finally:
            db.close()

        await asyncio.sleep(3600)  # 1시간마다


async def prompt_audit_loop(settings: Settings) -> None:
    """2시간마다 프롬프트 감사 실행 — 카테고리 분류 + 보안 위반 탐지."""
    from app.core.database import SessionLocal
    from app.services.prompt_audit_service import PromptAuditService

    logger.info("프롬프트 감사 스케줄러 시작 — 주기=7200s (2시간)")

    # 앱 기동 안정화 후 5분 대기
    await asyncio.sleep(300)

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

        await asyncio.sleep(7200)  # 2시간마다
