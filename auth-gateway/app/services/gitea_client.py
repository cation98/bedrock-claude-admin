"""Gitea admin client for user provisioning and token management.

Note: Tokens issued by Gitea's /tokens endpoint are SHA1 hex strings (40 chars).
The entrypoint.sh in User Pod assumes tokens are hex-safe for embedding in URL
credentials. Do not alter token generation to include non-hex characters.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

import httpx


@dataclass
class GiteaUserInfo:
    id: int
    login: str


class GiteaProvisioningError(Exception):
    """Raised when Gitea admin API returns an unexpected error."""


class GiteaClient:
    """Client for Gitea admin API — user provisioning + token issuance.

    Uses admin token auth. All methods are idempotent where possible.
    """

    def __init__(self, base_url: str, admin_token: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"token {admin_token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def ensure_user(self, sso_id: str, email: str) -> GiteaUserInfo:
        """Return existing user or create new. Idempotent."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self._base_url}/api/v1/users/{sso_id}",
                headers=self._headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "id" not in data or "login" not in data:
                    raise GiteaProvisioningError(
                        f"Gitea user response missing id/login for {sso_id}: {resp.text}"
                    )
                return GiteaUserInfo(id=data["id"], login=data["login"])

            if resp.status_code != 404:
                raise GiteaProvisioningError(
                    f"Unexpected response from Gitea when looking up user "
                    f"{sso_id}: HTTP {resp.status_code} — {resp.text}"
                )

            create_resp = client.post(
                f"{self._base_url}/api/v1/admin/users",
                headers=self._headers,
                json={
                    "username": sso_id,
                    "email": email,
                    "password": self._random_password(),
                    "must_change_password": False,
                    "send_notify": False,
                    "source_id": 0,
                },
            )
            if create_resp.status_code != 201:
                raise GiteaProvisioningError(
                    f"Failed to create Gitea user {sso_id}: "
                    f"HTTP {create_resp.status_code} — {create_resp.text}"
                )
            data = create_resp.json()
            if "id" not in data or "login" not in data:
                raise GiteaProvisioningError(
                    f"Gitea create-user response missing id/login for {sso_id}: {create_resp.text}"
                )
            return GiteaUserInfo(id=data["id"], login=data["login"])

    def issue_user_token(self, user_login: str, token_name: str) -> str:
        """Issue a new access token for a user. Returns the SHA1 hex token.

        Gitea's /users/{login}/tokens endpoint requires authentication as that user.
        Strategy: admin sets a one-time password, mints the token via basic auth,
        then immediately re-randomizes the password so it's never reused.
        """
        one_time_pw = self._random_password()
        with httpx.Client(timeout=self._timeout) as client:
            # Step 1: admin sets a known temp password
            patch_resp = client.patch(
                f"{self._base_url}/api/v1/admin/users/{user_login}",
                headers=self._headers,
                json={"source_id": 0, "login_name": user_login,
                      "password": one_time_pw, "must_change_password": False},
            )
            if patch_resp.status_code not in (200, 204):
                raise GiteaProvisioningError(
                    f"Failed to set temp password for {user_login}: "
                    f"HTTP {patch_resp.status_code} — {patch_resp.text}"
                )

            # Step 2: mint token using basic auth as the user
            token_resp = client.post(
                f"{self._base_url}/api/v1/users/{user_login}/tokens",
                auth=(user_login, one_time_pw),
                headers={"Content-Type": "application/json"},
                json={"name": token_name, "scopes": ["write:repository", "read:user", "write:user"]},
            )
            if token_resp.status_code != 201:
                raise GiteaProvisioningError(
                    f"Failed to issue Gitea token for {user_login}: "
                    f"HTTP {token_resp.status_code} — {token_resp.text}"
                )

            # Step 3: re-randomize password so the one-time value is invalidated
            client.patch(
                f"{self._base_url}/api/v1/admin/users/{user_login}",
                headers=self._headers,
                json={"source_id": 0, "login_name": user_login,
                      "password": self._random_password(), "must_change_password": False},
            )

        sha1 = token_resp.json().get("sha1")
        if not sha1:
            raise GiteaProvisioningError(
                f"Gitea token response missing 'sha1' for {user_login}: {token_resp.text}"
            )
        return sha1

    @staticmethod
    def _random_password() -> str:
        """Generate a random password for user creation. Not used for login; users auth via SSO."""
        return secrets.token_urlsafe(24)
