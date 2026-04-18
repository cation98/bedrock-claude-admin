import pytest
from unittest.mock import patch, MagicMock
from app.services.gitea_client import GiteaClient, GiteaUserInfo, GiteaProvisioningError


@pytest.fixture
def client():
    return GiteaClient(
        base_url="https://gitea.test",
        admin_token="admin-token-xyz",
    )


def test_ensure_user_creates_when_missing(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.get.return_value = MagicMock(status_code=404)
        instance.post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": 42, "login": "N1102359"},
        )

        info = client.ensure_user(sso_id="N1102359", email="user@skons.net")

    assert info.login == "N1102359"
    assert info.id == 42
    assert instance.post.called


def test_ensure_user_returns_existing(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": 42, "login": "N1102359"},
        )

        info = client.ensure_user(sso_id="N1102359", email="user@skons.net")

    assert info.login == "N1102359"
    assert not instance.post.called


def test_ensure_user_raises_on_unexpected_error(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.get.return_value = MagicMock(status_code=500, text="internal error")

        with pytest.raises(GiteaProvisioningError):
            client.ensure_user(sso_id="N1102359", email="user@skons.net")


def test_ensure_user_raises_on_create_failure(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.get.return_value = MagicMock(status_code=404)
        instance.post.return_value = MagicMock(status_code=422, text="invalid email")

        with pytest.raises(GiteaProvisioningError):
            client.ensure_user(sso_id="N1102359", email="bad")


def test_issue_token_returns_sha1(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"sha1": "abc123def456", "name": "session-1"},
        )

        token = client.issue_user_token(user_login="N1102359", token_name="session-1")

    assert token == "abc123def456"


def test_issue_token_raises_on_failure(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.post.return_value = MagicMock(status_code=500, text="fail")

        with pytest.raises(GiteaProvisioningError):
            client.issue_user_token(user_login="N1102359", token_name="session-1")


def test_issue_token_raises_when_sha1_missing(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"name": "session-1"},  # sha1 missing
            text="{}",
        )
        with pytest.raises(GiteaProvisioningError):
            client.issue_user_token(user_login="N1102359", token_name="session-1")


def test_ensure_user_raises_when_response_malformed(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"login": "N1102359"},  # id missing
            text="{}",
        )
        with pytest.raises(GiteaProvisioningError):
            client.ensure_user(sso_id="N1102359", email="user@skons.net")
