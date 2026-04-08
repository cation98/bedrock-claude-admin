"""유휴 Pod 감지 및 정리 서비스.

3단계 유휴 판정 정책:
  1. WebSocket 연결 + Claude 실행 중 → 절대 종료 안 함
  2. WebSocket 연결 + Claude 미실행 + 파일 무활동 30분 → 종료
  3. WebSocket 미연결 + 파일 무활동 60분 → 종료
  4. 파일 활동이 30분 이내 → 유지 (백그라운드 작업)

종료 5분 전 경고: Pod 내 /tmp/.idle-warning 파일 생성
  → ttyd WebSocket 클라이언트가 감지하여 브라우저에 경고 표시
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

# 유휴 판정 시간 (분)
IDLE_TIMEOUT_NO_WS = 30      # WebSocket 미연결 + 파일 무활동 시 (30분)
IDLE_TIMEOUT_WS_IDLE = 30    # WebSocket 연결 + Claude 미실행 + 파일 무활동 시 (30분)
WARNING_BEFORE_MINUTES = 5   # 종료 5분 전 경고

# Pod 내부 종합 활동 확인 스크립트
# 반환: {last_file_activity, ws_clients, claude_running, webapp_running, now}
ACTIVITY_CHECK_SCRIPT = '''
import os, time, glob, json, subprocess

latest = 0

# 1. Claude Code JSONL 파일 (대화 기록)
for f in glob.glob("/home/node/.claude/projects/*/*.jsonl"):
    try:
        mt = os.path.getmtime(f)
        if mt > latest: latest = mt
    except: pass

# 2. Claude Code 세션 파일
for f in glob.glob("/home/node/.claude/*.json"):
    try:
        mt = os.path.getmtime(f)
        if mt > latest: latest = mt
    except: pass

# 3. workspace 내 최근 수정 파일
for f in glob.glob("/home/node/workspace/**", recursive=False):
    try:
        mt = os.path.getmtime(f)
        if mt > latest: latest = mt
    except: pass

# 4. bash history
try:
    mt = os.path.getmtime("/home/node/.bash_history")
    if mt > latest: latest = mt
except: pass

# 5. WebSocket 클라이언트 수 (ttyd 포트 7681 ESTABLISHED 연결)
ws_clients = 0
try:
    with open("/proc/net/tcp") as f:
        for line in f.readlines()[1:]:
            parts = line.split()
            local = parts[1]
            state = parts[3]
            port_hex = local.split(":")[1]
            if port_hex == "1E01" and state == "01":  # 7681 + ESTABLISHED
                ws_clients += 1
except: pass

# 6. Claude 프로세스 실행 여부
claude_running = False
try:
    result = subprocess.run(["pgrep", "-f", "claude"], capture_output=True, timeout=3)
    claude_running = result.returncode == 0
except: pass

# 7. 웹앱 (포트 3000) 실행 여부
webapp_running = False
try:
    with open("/proc/net/tcp") as f:
        for line in f.readlines()[1:]:
            parts = line.split()
            local = parts[1]
            state = parts[3]
            port_hex = local.split(":")[1]
            if port_hex == "0BB8" and state == "0A":  # 3000 + LISTEN
                webapp_running = True
                break
except: pass

print(json.dumps({
    "last_file_activity": latest,
    "ws_clients": ws_clients,
    "claude_running": claude_running,
    "webapp_running": webapp_running,
    "now": time.time()
}))
'''

# 경고 스크립트 — /tmp/.idle-warning 파일 생성
WARNING_SCRIPT = '''
import json, time
with open("/tmp/.idle-warning", "w") as f:
    json.dump({"warn_at": time.time(), "minutes_left": {minutes_left}}, f)
print("warning set")
'''


class IdleCleanupService:

    def __init__(self, k8s: K8sService, idle_timeout_minutes: int = IDLE_TIMEOUT_NO_WS):
        self.k8s = k8s
        self.idle_timeout_minutes = idle_timeout_minutes

    def _check_pod_activity(self, pod_name: str, namespace: str) -> dict | None:
        """Pod 내부 종합 활동 상태 반환. 2회 재시도 후 실패 시 None."""
        import json as _json
        import time as _time

        for attempt in range(2):
            try:
                v1 = k8s_client.CoreV1Api()
                resp = stream(
                    v1.connect_get_namespaced_pod_exec,
                    pod_name, namespace,
                    command=["python3", "-c", ACTIVITY_CHECK_SCRIPT],
                    container="terminal",
                    stderr=False, stdin=False, stdout=True, tty=False,
                    _request_timeout=15,
                )
                raw = resp.strip()
                if not raw:
                    raise ValueError("empty response")

                # kubernetes.stream이 바깥 따옴표를 추가하는 경우 제거
                if raw.startswith('"') and raw.endswith('"'):
                    raw = raw[1:-1]

                # 마지막 비어있지 않은 줄만 파싱
                lines = [ln for ln in raw.splitlines() if ln.strip()]
                if not lines:
                    raise ValueError("no parseable lines")
                last_line = lines[-1]

                # json.loads 시도 → 실패 시 Python repr을 JSON으로 변환 후 재시도
                try:
                    return _json.loads(last_line)
                except _json.JSONDecodeError:
                    # kubernetes.stream이 Python repr로 반환하는 경우 대응
                    # single quotes → double quotes, True/False/None → JSON 형식
                    fixed = last_line.replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null")
                    return _json.loads(fixed)
            except Exception as e:
                logger.warning(f"활동 확인 실패 ({pod_name}), attempt {attempt + 1}/2: {e}")
                if attempt < 1:
                    _time.sleep(2)
        return None

    def _send_warning(self, pod_name: str, namespace: str, minutes_left: int) -> None:
        """종료 경고를 Pod에 전송 — /tmp/.idle-warning 파일 생성."""
        try:
            v1 = k8s_client.CoreV1Api()
            script = WARNING_SCRIPT.replace("{minutes_left}", str(minutes_left))
            stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name, namespace,
                command=["python3", "-c", script],
                container="terminal",
                stderr=False, stdin=False, stdout=True, tty=False,
                _request_timeout=5,
            )
            logger.info(f"유휴 경고 전송: {pod_name} ({minutes_left}분 후 종료)")
        except Exception as e:
            logger.warning(f"유휴 경고 실패 ({pod_name}): {e}")

    def find_idle_sessions(self, db: DbSession) -> list[TerminalSession]:
        """3단계 유휴 판정 정책에 따라 종료 대상 세션 조회."""

        # 1차: DB last_active_at 기준 후보 (30분 이상 갱신 없는 세션)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=IDLE_TIMEOUT_WS_IDLE)
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

        idle_sessions = []
        warning_sessions = []
        now_epoch = datetime.now(timezone.utc).timestamp()

        for session in candidates:
            # 최소 생존 시간: 생성 후 15분 이내의 Pod은 절대 종료하지 않음
            pod_age_min = (datetime.now(timezone.utc) - session.created_at).total_seconds() / 60 if session.created_at else 999
            if pod_age_min < 15:
                logger.debug(f"신규 Pod 보호: {session.pod_name} (age={int(pod_age_min)}min)")
                continue

            activity = self._check_pod_activity(session.pod_name, self.k8s.namespace)

            if activity is None:
                # 활동 확인 실패 → 안전을 위해 활성으로 간주 (오판 방지)
                logger.warning(f"활동 확인 실패 — 활성으로 간주하여 유지: {session.pod_name}")
                continue

            ws_clients = activity.get("ws_clients", 0)
            claude_running = activity.get("claude_running", False)
            webapp_running = activity.get("webapp_running", False)
            last_file = activity.get("last_file_activity", 0)
            # 파일 활동 없음 → Pod 생성 시간 기준 (9999 대신)
            if last_file > 0:
                file_idle_min = (now_epoch - last_file) / 60
            elif session.created_at:
                file_idle_min = (datetime.now(timezone.utc) - session.created_at).total_seconds() / 60
            else:
                file_idle_min = 9999

            # ── 판정 로직 ──

            # 규칙 1: WebSocket + Claude 실행 중 → 절대 유지
            if ws_clients > 0 and claude_running:
                session.last_active_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"활성: {session.pod_name} — WS={ws_clients}, Claude=실행 중")
                continue

            # 규칙 4: 파일 활동이 최근이면 유지 (백그라운드 작업)
            if file_idle_min < IDLE_TIMEOUT_WS_IDLE:
                active_dt = datetime.fromtimestamp(last_file, tz=timezone.utc)
                session.last_active_at = active_dt
                db.commit()
                logger.info(
                    f"활동 감지: {session.pod_name} — "
                    f"파일 활동 {int(file_idle_min)}분 전"
                )
                continue

            # 규칙 2: WebSocket 연결 + Claude 미실행 + 30분 무활동 → 종료
            if ws_clients > 0 and file_idle_min >= IDLE_TIMEOUT_WS_IDLE:
                # 경고 체크: 30분 - 5분 = 25분 시점에 경고
                if file_idle_min < IDLE_TIMEOUT_WS_IDLE + WARNING_BEFORE_MINUTES:
                    self._send_warning(session.pod_name, self.k8s.namespace, WARNING_BEFORE_MINUTES)
                    logger.info(
                        f"유휴 경고: {session.pod_name} — WS 연결, Claude 미실행, "
                        f"파일 {int(file_idle_min)}분 무활동"
                    )
                    continue
                else:
                    logger.info(
                        f"유휴 종료: {session.pod_name} — WS 연결, Claude 미실행, "
                        f"파일 {int(file_idle_min)}분 무활동 (>{IDLE_TIMEOUT_WS_IDLE}분)"
                    )
                    idle_sessions.append(session)
                    continue

            # 규칙 3: WebSocket 미연결 + 60분 무활동 → 종료
            if ws_clients == 0 and file_idle_min >= IDLE_TIMEOUT_NO_WS:
                # 경고 체크: 60분 - 5분 = 55분 시점에 경고
                if file_idle_min < IDLE_TIMEOUT_NO_WS + WARNING_BEFORE_MINUTES:
                    self._send_warning(session.pod_name, self.k8s.namespace, WARNING_BEFORE_MINUTES)
                    logger.info(
                        f"유휴 경고: {session.pod_name} — WS 미연결, "
                        f"파일 {int(file_idle_min)}분 무활동"
                    )
                    continue
                else:
                    logger.info(
                        f"유휴 종료: {session.pod_name} — WS 미연결, "
                        f"파일 {int(file_idle_min)}분 무활동 (>{IDLE_TIMEOUT_NO_WS}분)"
                    )
                    idle_sessions.append(session)
                    continue

            # 그 외: 아직 유휴 아님
            logger.debug(
                f"대기: {session.pod_name} — WS={ws_clients}, Claude={claude_running}, "
                f"webapp={webapp_running}, file_idle={int(file_idle_min)}분"
            )

        return idle_sessions

    def _backup_pod(self, pod_name: str, namespace: str) -> bool:
        """Pod 내부에서 backup-chat 실행하여 대화이력을 EFS에 백업."""
        try:
            v1 = k8s_client.CoreV1Api()
            resp = stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name, namespace,
                command=[BACKUP_SCRIPT],
                container="terminal",
                stderr=True, stdin=False, stdout=True, tty=False,
                _request_timeout=BACKUP_TIMEOUT_SECONDS,
            )
            logger.info(f"backup-chat [{pod_name}]: {resp.strip() if resp else 'ok'}")
            return True
        except Exception as e:
            logger.warning(f"backup-chat failed for {pod_name} (계속 진행): {e}")
            return False

    def _snapshot_pod_tokens(self, pod_name: str, namespace: str) -> None:
        """Pod 종료 직전 토큰 사용량 스냅샷."""
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
        logger.info(f"유휴 Pod 종료: {pod_name} (user={session.username})")

        self._snapshot_pod_tokens(pod_name, namespace)
        self._backup_pod(pod_name, namespace)

        try:
            self.k8s.delete_pod(pod_name)
        except Exception as e:
            logger.error(f"Pod 삭제 실패 {pod_name}: {e}")

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
                if labels.get("role") not in ("presenter", "claude-dedicated"):
                    continue
                node_name = node.metadata.name
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
                            clusterName=cluster, nodegroupName=ng_name,
                            scalingConfig={"minSize": new_min, "maxSize": int(ng["scalingConfig"]["maxSize"]), "desiredSize": new_desired},
                        )
                        logger.info(f"전용 노드 제거: {node_name}, {ng_name} → desired={new_desired}")
                        drained.append(node_name)
                    except Exception as e:
                        logger.error(f"전용 노드 스케일다운 실패 ({node_name}): {e}")
        except Exception as e:
            logger.error(f"전용 노드 정리 오류: {e}", exc_info=True)
        return drained

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
                logger.error(f"유휴 정리 실패 ({session.pod_name}): {e}", exc_info=True)

        self._cleanup_empty_presenter_nodes()
        return terminated
