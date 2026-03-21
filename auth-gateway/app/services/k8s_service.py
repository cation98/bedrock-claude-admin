"""Kubernetes Pod 관리 서비스.

사용자 로그인 시 개인용 Claude Code 터미널 Pod을 동적으로 생성/삭제.

K8s 개념 정리:
  - Pod: 컨테이너 실행 단위 (1 Pod = 1 사용자 터미널)
  - Namespace: Pod을 논리적으로 그룹화 (claude-sessions)
  - ServiceAccount: Pod에 AWS 권한을 부여하는 "신분증"
  - Label: Pod에 메타데이터 부착 (사용자명, 세션 타입 등)
"""

import logging
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.core.config import Settings

logger = logging.getLogger(__name__)


class K8sServiceError(Exception):
    """K8s 작업 실패."""
    pass


class K8sService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.namespace = settings.k8s_namespace

        # K8s 클라이언트 초기화
        # - in_cluster=True: EKS Pod 내부에서 실행 시 (자동 인증)
        # - in_cluster=False: 로컬 개발 시 (~/.kube/config 사용)
        if settings.k8s_in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()

        self.v1 = client.CoreV1Api()

    def _pod_name(self, username: str) -> str:
        """사용자별 고유 Pod 이름 생성."""
        safe_name = username.lower().replace("_", "-")
        return f"claude-terminal-{safe_name}"

    def create_pod(self, username: str, session_type: str = "workshop") -> str:
        """사용자용 Claude Code 터미널 Pod 생성.

        Args:
            username: 사번 (e.g. N1102359)
            session_type: workshop | daily

        Returns:
            pod_name: 생성된 Pod 이름
        """
        pod_name = self._pod_name(username)

        # 이미 실행 중인 Pod이 있는지 확인
        existing = self.get_pod_status(pod_name)
        if existing and existing.get("phase") in ("Pending", "Running"):
            logger.info(f"Pod {pod_name} already exists, reusing")
            return pod_name

        pod_manifest = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels={
                    "app": "claude-terminal",
                    "user": username.lower(),
                    "session-type": session_type,
                },
            ),
            spec=client.V1PodSpec(
                service_account_name=self.settings.k8s_service_account,
                restart_policy="Never",
                active_deadline_seconds=self.settings.k8s_pod_ttl_seconds,
                containers=[
                    client.V1Container(
                        name="terminal",
                        image=self.settings.k8s_pod_image,
                        ports=[client.V1ContainerPort(container_port=7681, name="ttyd")],
                        env=[
                            client.V1EnvVar(name="CLAUDE_CODE_USE_BEDROCK", value="1"),
                            client.V1EnvVar(name="AWS_REGION", value=self.settings.bedrock_region),
                            client.V1EnvVar(
                                name="ANTHROPIC_DEFAULT_SONNET_MODEL",
                                value=self.settings.bedrock_sonnet_model,
                            ),
                            client.V1EnvVar(
                                name="ANTHROPIC_DEFAULT_HAIKU_MODEL",
                                value=self.settings.bedrock_haiku_model,
                            ),
                            client.V1EnvVar(name="GIT_USER_NAME", value=username),
                            client.V1EnvVar(name="GIT_USER_EMAIL", value=f"{username}@skons.net"),
                            client.V1EnvVar(
                                name="DATABASE_URL",
                                value=self.settings.workshop_database_url,
                            ),
                        ],
                        resources=client.V1ResourceRequirements(
                            requests={
                                "cpu": self.settings.k8s_pod_cpu_request,
                                "memory": self.settings.k8s_pod_memory_request,
                            },
                            limits={
                                "cpu": self.settings.k8s_pod_cpu_limit,
                                "memory": self.settings.k8s_pod_memory_limit,
                            },
                        ),
                        readiness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(path="/", port=7681),
                            initial_delay_seconds=5,
                            period_seconds=10,
                        ),
                        liveness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(path="/", port=7681),
                            initial_delay_seconds=10,
                            period_seconds=30,
                        ),
                    )
                ],
            ),
        )

        try:
            self.v1.create_namespaced_pod(namespace=self.namespace, body=pod_manifest)
            logger.info(f"Pod {pod_name} created for user {username}")
            return pod_name
        except ApiException as e:
            if e.status == 409:  # Already exists
                logger.info(f"Pod {pod_name} already exists")
                return pod_name
            logger.error(f"Failed to create pod {pod_name}: {e}")
            raise K8sServiceError(f"Failed to create pod: {e.reason}")

    def delete_pod(self, pod_name: str) -> bool:
        """Pod 삭제."""
        try:
            self.v1.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                grace_period_seconds=10,
            )
            logger.info(f"Pod {pod_name} deleted")
            return True
        except ApiException as e:
            if e.status == 404:
                return True  # 이미 삭제됨
            logger.error(f"Failed to delete pod {pod_name}: {e}")
            raise K8sServiceError(f"Failed to delete pod: {e.reason}")

    def get_pod_status(self, pod_name: str) -> dict | None:
        """Pod 상태 조회."""
        try:
            pod = self.v1.read_namespaced_pod(name=pod_name, namespace=self.namespace)
            return {
                "name": pod.metadata.name,
                "phase": pod.status.phase,  # Pending, Running, Succeeded, Failed
                "pod_ip": pod.status.pod_ip,
                "start_time": pod.status.start_time,
            }
        except ApiException as e:
            if e.status == 404:
                return None
            raise K8sServiceError(f"Failed to get pod status: {e.reason}")

    def list_pods(self, label_selector: str | None = None) -> list[dict]:
        """Pod 목록 조회."""
        selector = label_selector or "app=claude-terminal"
        try:
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=selector,
            )
            return [
                {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "user": pod.metadata.labels.get("user", "unknown"),
                    "session_type": pod.metadata.labels.get("session-type", "unknown"),
                    "pod_ip": pod.status.pod_ip,
                    "start_time": pod.status.start_time,
                }
                for pod in pods.items
            ]
        except ApiException as e:
            logger.error(f"Failed to list pods: {e}")
            raise K8sServiceError(f"Failed to list pods: {e.reason}")

    def delete_all_pods(self, label_selector: str | None = None) -> int:
        """모든 터미널 Pod 일괄 삭제 (관리자용)."""
        pods = self.list_pods(label_selector)
        deleted = 0
        for pod in pods:
            if self.delete_pod(pod["name"]):
                deleted += 1
        return deleted
