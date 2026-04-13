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


class TestWriteLocalFileToPod:
    """P2-BUG3: `write_local_file_to_pod` 는 auth-gateway 이미지에 없는
    `kubectl` 바이너리가 아니라, 이미 프로젝트 전반에서 쓰는
    `kubernetes.stream.stream` 을 통해 Pod exec API 로 파일을 전송해야 한다.
    RED: 현재 kubectl subprocess 경로 → stream 호출 0회 → FAIL.
    GREEN: refactor 후 `stream(... command=["tar", ...], stdin=True, ...)` 호출 관측.
    """

    def _build_service(self, monkeypatch, stream_captured):
        from app.services import k8s_service as ks_mod

        monkeypatch.setattr(ks_mod.config, "load_incluster_config", lambda: None)
        monkeypatch.setattr(ks_mod.config, "load_kube_config", lambda: None)

        # v1.connect_get_namespaced_pod_exec 속성 접근이 stream() 인자 평가
        # 단계에서 발생하므로, sentinel callable 을 가진 fake v1 제공.
        class _FakeV1:
            def connect_get_namespaced_pod_exec(self, *a, **kw):
                raise AssertionError("real API should not run — stream is mocked")

        monkeypatch.setattr(ks_mod.client, "CoreV1Api", lambda: _FakeV1())
        monkeypatch.setattr(ks_mod, "NetworkingV1Api", lambda: object())

        class _FakeStreamResp:
            def __init__(self):
                self._closed = False

            def write_stdin(self, data):
                pass

            def update(self, timeout=None):
                pass

            def peek_stderr(self):
                return ""

            def read_stderr(self):
                return ""

            def peek_stdout(self):
                return ""

            def read_stdout(self):
                return ""

            def is_open(self):
                return not self._closed

            def close(self):
                self._closed = True

            @property
            def returncode(self):
                return 0

        def _fake_stream(api_fn, *args, **kwargs):
            stream_captured.append(
                {"command": kwargs.get("command"), "kwargs": kwargs}
            )
            return _FakeStreamResp()

        # refactor 후 k8s_service 모듈이 `stream` 을 import 할 것이라 가정.
        # import 전에도 테스트가 동작하도록 raising=False.
        monkeypatch.setattr(ks_mod, "stream", _fake_stream, raising=False)

        class _Settings:
            k8s_namespace = "claude-sessions"
            k8s_in_cluster = False

        return ks_mod.K8sService(_Settings())

    def test_uses_kubernetes_stream_not_kubectl_subprocess(
        self, monkeypatch, tmp_path
    ):
        import asyncio

        captured: list = []
        svc = self._build_service(monkeypatch, captured)

        local = tmp_path / "src.xlsx"
        local.write_bytes(b"hello world")

        # 현재 kubectl subprocess 경로가 실 클러스터를 건드리지 않도록 차단.
        async def _boom(*a, **kw):
            raise FileNotFoundError("no kubectl in test env")

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", _boom, raising=True
        )

        try:
            # asyncio.run()은 항상 새 이벤트 루프를 생성하므로 get_event_loop()
            # deprecated + 루프 closed 문제(Python 3.12)를 회피한다.
            asyncio.run(
                svc.write_local_file_to_pod(
                    "USER",
                    "/home/node/workspace/file.xlsx",
                    str(local),
                )
            )
        except Exception:
            # 현재 구현은 kubectl 없으면 K8sServiceError. refactor 후엔 성공.
            # 어느 쪽이든 여기서 stream 호출 여부만 평가한다.
            pass

        assert captured, (
            "kubernetes.stream.stream was not called — "
            "write_local_file_to_pod still uses kubectl subprocess"
        )

        commands = [c["command"] for c in captured if c.get("command")]
        assert any(
            cmd and "tar" in cmd[0] for cmd in commands
        ), f"Expected a `tar` exec command, got: {commands}"

        tar_calls = [
            c for c in captured
            if c.get("command") and "tar" in c["command"][0]
        ]
        for call in tar_calls:
            assert call["kwargs"].get("stdin") is True, (
                f"tar call must have stdin=True for streaming: {call['kwargs']}"
            )
            assert call["kwargs"].get("container") == "terminal", (
                f"exec must target the `terminal` container: {call['kwargs']}"
            )
