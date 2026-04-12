"""K8sService._validate_container_path 단위 테스트 (P2-iter3 #3).

방어 대상 — 경로 트래버설, prefix collision, 절대경로 강제, 제어문자 차단.
Pod 내부 파일 쓰기(write_local_file_to_pod) 호출 전 게이트 역할을 하므로,
이 함수가 무너지면 SSRF/RCE 위험이 있다.
"""

import pytest

from app.services.k8s_service import K8sService, K8sServiceError


class TestValidateContainerPath:
    """`K8sService._validate_container_path` 7 케이스."""

    def test_valid_path_passes(self):
        """기본 허용 경로 — /home/node/workspace 하위 파일."""
        result = K8sService._validate_container_path(
            "/home/node/workspace/team/USER/file.xlsx"
        )
        assert result == "/home/node/workspace/team/USER/file.xlsx"

    def test_parent_traversal_rejected(self):
        """`..` 세그먼트가 base 를 탈출하면 거부."""
        with pytest.raises(K8sServiceError):
            K8sService._validate_container_path(
                "/home/node/workspace/../../etc/passwd"
            )

    def test_prefix_collision_rejected(self):
        """`/home/node/workspace-evil` 같은 prefix 충돌 우회를 차단.

        startswith 기반 검증은 이 케이스를 막지 못하지만 commonpath 기반은 막는다.
        """
        with pytest.raises(K8sServiceError):
            K8sService._validate_container_path(
                "/home/node/workspace-evil/file.txt"
            )

    def test_relative_path_rejected(self):
        """절대 경로가 아니면 거부."""
        with pytest.raises(K8sServiceError):
            K8sService._validate_container_path("workspace/file.xlsx")

    def test_outside_base_rejected(self):
        """base 바깥의 절대 경로 거부 (/etc, /root 등)."""
        with pytest.raises(K8sServiceError):
            K8sService._validate_container_path("/etc/shadow")

    def test_control_characters_rejected(self):
        """NUL 및 제어문자 포함 경로 거부 — shell/argv 주입 방지."""
        with pytest.raises(K8sServiceError):
            K8sService._validate_container_path(
                "/home/node/workspace/file\x00.txt"
            )

    def test_empty_string_rejected(self):
        """빈 문자열 거부."""
        with pytest.raises(K8sServiceError):
            K8sService._validate_container_path("")
