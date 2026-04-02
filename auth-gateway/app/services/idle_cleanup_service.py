"""유휴 Pod 감지 및 정리 서비스.

60분 이상 heartbeat가 없는 running Pod를 대상으로:
1. kubectl exec backup-chat (EFS에 대화이력 백업)
2. Pod/Service/Ingress 삭제
3. DB 세션 terminated 처리
"""
import logging
from datetime import datetime, timedelta, timezone

from kubernetes import client as k8s_client
from kubernetes.stream import stream
from sqlalchemy.orm import Session as DbSession

from app.models.session import TerminalSession
from app.models.user import User
from app.services.k8s_service import K8sService

logger = logging.getLogger(__name__)

BACKUP_TIMEOUT_SECONDS = 30
BACKUP_SCRIPT = "/home/node/.local/bin/backup-chat"

# pod_ttl별 유휴 타임아웃 (분)
IDLE_TIMEOUT_BY_TTL: dict[str, int | None] = {
    "unlimited": None,       # 유휴 정리 제외
    "weekday-office": None,  # 유휴 정리 제외 (스케줄 기반)
    "30d": 480,              # 8시간
    "7d": 480,               # 8시간
    "1d": 240,               # 4시간
    "8h": 120,               # 2시간
    "4h": 60,                # 1시간
}
DEFAULT_IDLE_TIMEOUT = 60    # 미지정 TTL의 기본값


class IdleCleanupService:

    def __init__(self, k8s: K8sService, idle_timeout_minutes: int = 60):
        self.k8s = k8s
        self.idle_timeout_minutes = idle_timeout_minutes

    def find_idle_sessions(self, db: DbSession) -> list[TerminalSession]:
        """유휴 세션 조회 — pod_ttl에 따라 유휴 임계값 차등 적용."""
        now = datetime.now(timezone.utc)
        running = (
            db.query(TerminalSession, User.pod_ttl)
            .join(User, TerminalSession.user_id == User.id)
            .filter(TerminalSession.pod_status == "running")
            .all()
        )

        idle_sessions = []
        for session, pod_ttl in running:
            timeout = IDLE_TIMEOUT_BY_TTL.get(pod_ttl, DEFAULT_IDLE_TIMEOUT)
            if timeout is None:
                # unlimited/weekday-office: skip idle cleanup
                continue
            cutoff = now - timedelta(minutes=timeout)
            if session.last_active_at and session.last_active_at < cutoff:
                idle_sessions.append(session)

        return idle_sessions

    def _backup_pod(self, pod_name: str, namespace: str) -> bool:
        """Pod 내부에서 backup-chat 실행하여 대화이력을 EFS에 백업. 성공 여부 반환."""
        try:
            v1 = k8s_client.CoreV1Api()
            resp = stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                command=[BACKUP_SCRIPT],
                container="terminal",
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _request_timeout=BACKUP_TIMEOUT_SECONDS,
            )
            logger.info(f"backup-chat [{pod_name}]: {resp.strip() if resp else 'ok'}")
            return True
        except Exception as e:
            logger.warning(f"backup-chat failed for {pod_name} (계속 진행): {e}")
            return False

    def _snapshot_pod_tokens(self, pod_name: str, namespace: str) -> None:
        """Pod 종료 직전 토큰 사용량 스냅샷 — DB에 최종 상태 보존."""
        try:
            from app.routers.admin import do_snapshot
            from app.core.database import SessionLocal
            from app.core.config import get_settings
            snap_db = SessionLocal()
            try:
                do_snapshot(snap_db, get_settings())
                logger.info(f"종료 전 토큰 스냅샷 완료: {pod_name}")
            finally:
                snap_db.close()
        except Exception as e:
            logger.warning(f"종료 전 토큰 스냅샷 실패 ({pod_name}): {e}")

    def _terminate_session(self, session: TerminalSession, db: DbSession) -> None:
        """토큰 스냅샷 → 백업 → K8s 리소스 삭제 → DB 상태 업데이트."""
        pod_name = session.pod_name
        namespace = self.k8s.namespace
        logger.info(f"유휴 Pod 종료: {pod_name} (user={session.username}, "
                    f"last_active={session.last_active_at})")

        # Step 0: 토큰 사용량 최종 스냅샷 (DB 영구 보존)
        self._snapshot_pod_tokens(pod_name, namespace)

        # Step 1: 대화이력 EFS 백업 (실패해도 삭제 계속)
        self._backup_pod(pod_name, namespace)

        # Step 2: K8s Pod/Service/Ingress 삭제
        try:
            self.k8s.delete_pod(pod_name)
        except Exception as e:
            logger.error(f"Pod 삭제 실패 {pod_name}: {e} (DB는 terminated 처리)")

        # Step 3: DB 세션 종료 처리
        session.pod_status = "terminated"
        session.terminated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"유휴 정리 완료: {pod_name}")

    def run_cleanup(self, db: DbSession) -> list[str]:
        """유휴 세션 정리 실행. 종료된 pod_name 목록 반환."""
        idle_sessions = self.find_idle_sessions(db)
        if not idle_sessions:
            logger.debug("유휴 Pod 없음")
            return []

        logger.info(f"유휴 Pod {len(idle_sessions)}개 발견 → 정리 시작")
        terminated = []
        for session in idle_sessions:
            try:
                self._terminate_session(session, db)
                terminated.append(session.pod_name)
            except Exception as e:
                logger.error(f"유휴 정리 오류 {session.pod_name}: {e}", exc_info=True)
        return terminated
