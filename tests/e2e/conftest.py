"""E2E 테스트 공통 설정."""

import os
import pytest


def pytest_configure(config):
    """pytest.ini 없이 마커 등록."""
    config.addinivalue_line(
        "markers",
        "e2e: End-to-end 테스트 (실제 EKS 환경 필요)",
    )
    config.addinivalue_line(
        "markers",
        "critical: Critical regression 테스트 — CI 필수 실행",
    )
    config.addinivalue_line(
        "markers",
        "slow: 10초 이상 소요 테스트",
    )


@pytest.fixture(scope="session")
def env_config():
    """환경변수 config 집약."""
    return {
        "auth_gateway_url": os.environ.get("AUTH_GATEWAY_URL", ""),
        "open_webui_url": os.environ.get("OPEN_WEBUI_URL", ""),
        "bedrock_ag_url": os.environ.get("BEDROCK_AG_URL", ""),
        "admin_dashboard_url": os.environ.get("ADMIN_DASHBOARD_URL", ""),
        "test_user_token": os.environ.get("TEST_USER_TOKEN", ""),
        "test_admin_token": os.environ.get("TEST_ADMIN_TOKEN", ""),
        "test_pod_token": os.environ.get("TEST_POD_TOKEN", ""),
        "test_pod_name": os.environ.get("TEST_POD_NAME", "claude-terminal-testuser01"),
        "over_budget_user_token": os.environ.get("OVER_BUDGET_USER_TOKEN", ""),
    }
