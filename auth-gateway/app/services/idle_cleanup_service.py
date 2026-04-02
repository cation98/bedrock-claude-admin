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
from app.services.k8s_service import K8sService

logger = logging.getLogger(__name__)

BACKUP_TIMEOUT_SECONDS = 30
BACKUP_SCRIPT = "/home/node/.local/bin/backup-chat"

# 모든 사용자 공통: 2시간 미사용 시 종료
IDLE_TIMEOUT_MINUTES = 120

# Pod 내부에서 최근 활동 시각을 확인하는 스크립트
# Claude Code JSONL, bash_history, 터미널 프로세스 등의 최근 수정시각 확인
ACTIVITY_CHECK_SCRIPT = '''
import os, time, glob, json

latest = 0

# 1. Claude Code JSONL 파일 (대화 기록)
for f in glob.glob("/home/node/.claude/projects/*/*.jsonl"):
    try:
        mt = os.path.getmtime(f)
        if mt > latest:
            latest = mt
    except: pass

# 2. Claude Code 세션 파일
for f in glob.glob("/home/node/.claude/*.json"):
    try:
        mt = os.path.getmtime(f)
        if mt > latest:
            latest = mt
    except: pass

# 3. workspace 내 최근 수정 파일
for f in glob.glob("/home/node/workspace/**", recursive=False):
    try:
        mt = os.path.getmtime(f)
        if mt > latest:
            latest = mt
    except: pass

# 4. bash history
try:
    mt = os.path.getmtime("/home/node/.bash_history")
    if mt > latest:
        latest = mt
except: pass

print(json.dumps({"last_activity": latest, "now": time.time()}))
'''


class IdleCleanupService:

    def __init__(self, k8s: K8sService, idle_timeout_minutes: int = IDLE_TIMEOUT_MINUTES):
        self.k8s = k8s
        self.idle_timeout_minutes = idle_timeout_minutes

    def _check_pod_activity(self, pod_name: str, namespace: str) -> float | None:
        """Pod 내부의 실제 마지막 활동 시각(epoch)을 반환. 실패 시 None."""
        try:
            import json as _json
            v1 = k8s_client.CoreV1Api()
            resp = stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name, namespace,
                command=["python3", "-c", ACTIVITY_CHECK_SCRIPT],
                container="terminal",
                stderr=False, stdin=False, stdout=True, tty=False,
                _request_timeout=10,
            )
            data = _json.loads(resp.strip())
            return data.get("last_activity", 0)
        except Exception as e:
            logger.warning(f"활동 확인 실패 ({pod_name}): {e}")
            return None

    def find_idle_sessions(self, db: DbSession) -> list[TerminalSession]:
        """2시간 이상 실제 활동이 없는 running 세션 조회."""
        # DB 기준 후보 선정 (last_active_at 기반 1차 필터)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.idle_timeout_minutes)
        candidates = (
            db.query(TerminalSession)
            .filter(
                TerminalSession.pod_status == "running",
                TerminalSession.last_active_at < cutoff,
            )
            .all()
        )

        if not candidates:
            return []

        # Pod 내부 실제 활동 확인 (2차 필터)
        idle_sessions = []
        now_epoch = datetime.now(timezone.utc).timestamp()
        idle_cutoff_epoch = now_epoch - (self.idle_timeout_minutes * 60)

        for session in candidates:
            last_activity = self._check_pod_activity(
                session.pod_name, self.k8s.namespace
            )

            if last_activity is None:
                # Pod 접근 불가 → 이미 죽은 Pod일 수 있음, 정리 대상
                idle_sessions.append(session)
                continue

            if last_activity < idle_cutoff_epoch:
                # 실제로 2시간 이상 미사용
                logger.info(
                    f"유휴 확인: {session.pod_name} — "
                    f"마지막 활동 {int((now_epoch - last_activity) / 60)}분 전"
                )
                idle_sessions.append(session)
            else:
                # 실제로는 활동 중 → last_active_at 갱신
                active_dt = datetime.fromtimestamp(last_activity, tz=timezone.utc)
                session.last_active_at = active_dt
                db.commit()
                logger.info(
                    f"활동 감지: {session.pod_name} — "
                    f"last_active_at 갱신 ({int((now_epoch - last_activity) / 60)}분 전)"
                )

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

    def _cleanup_empty_presenter_nodes(self) -> list[str]:
        """전용(presenter) 노드에 사용자 Pod이 없으면 노드 제거."""
        drained = []
        try:
            v1 = k8s_client.CoreV1Api()
            nodes = v1.list_node().items
            for node in nodes:
                labels = node.metadata.labels or {}
                if labels.get("role") != "presenter":
                    continue

                node_name = node.metadata.name
                # 해당 노드의 사용자 Pod 확인
                pods = v1.list_pod_for_all_namespaces(
                    field_selector=f"spec.nodeName={node_name}",
                )
                user_pods = [
                    p for p in pods.items
                    if p.metadata.namespace not in ("kube-system",)
                    and not p.metadata.name.startswith("aws-node")
                    and not p.metadata.name.startswith("kube-proxy")
                    and not p.metadata.name.startswith("efs-csi")
                    and not p.metadata.name.startswith("coredns")
                ]

                if user_pods:
                    continue

                # 빈 전용 노드 → cordon + nodegroup scale down
                logger.info(f"전용 노드 {node_name}에 Pod 없음 → 노드 제거 시작")
                v1.patch_node(node_name, {"spec": {"unschedulable": True}})

                ng_name = labels.get("eks.amazonaws.com/nodegroup", "")
                if ng_name:
                    try:
                        import boto3
                        eks = boto3.client("eks", region_name="ap-northeast-2")
                        cluster = "bedrock-claude-eks"
                        ng = eks.describe_nodegroup(clusterName=cluster, nodegroupName=ng_name)["nodegroup"]
                        current = ng["scalingConfig"]["desiredSize"]
                        new_desired = max(0, current - 1)
                        new_min = min(ng["scalingConfig"]["minSize"], new_desired)
                        eks.update_nodegroup_config(
                            clusterName=cluster,
                            nodegroupName=ng_name,
                            scalingConfig={
                                "minSize": new_min,
                                "maxSize": int(ng["scalingConfig"]["maxSize"]),
                                "desiredSize": new_desired,
                            },
                        )
                        logger.info(f"전용 노드 제거: {node_name}, {ng_name} → desired={new_desired}")
                        drained.append(node_name)
                    except Exception as e:
                        logger.error(f"전용 노드 스케일다운 실패 ({node_name}): {e}")
                else:
                    logger.warning(f"전용 노드 {node_name}의 노드그룹을 확인할 수 없음")
        except Exception as e:
            logger.error(f"전용 노드 정리 오류: {e}", exc_info=True)
        return drained

    def run_cleanup(self, db: DbSession) -> list[str]:
        """유휴 세션 정리 실행. 종료된 pod_name 목록 반환."""
        idle_sessions = self.find_idle_sessions(db)
        if not idle_sessions:
            logger.debug("유휴 Pod 없음")
            # 세션 정리 없어도 빈 전용 노드는 확인
            self._cleanup_empty_presenter_nodes()
            return []

        logger.info(f"유휴 Pod {len(idle_sessions)}개 발견 → 정리 시작")
        terminated = []
        for session in idle_sessions:
            try:
                self._terminate_session(session, db)
                terminated.append(session.pod_name)
            except Exception as e:
                logger.error(f"유휴 정리 오류 {session.pod_name}: {e}", exc_info=True)

        # Pod 정리 후 빈 전용 노드 확인
        if terminated:
            self._cleanup_empty_presenter_nodes()

        return terminated
