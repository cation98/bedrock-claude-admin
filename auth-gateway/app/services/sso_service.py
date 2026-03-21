"""SSO 인증 서비스.

sso.skons.net의 커스텀 JSON API를 통해 사내 구성원 인증.
O-Guard 프로젝트의 인증 패턴을 재사용.

Flow:
  1. username + password → SHA-256(pw + salt) → Base64 인코딩
  2. SSO_AUTH_URL로 인코딩된 자격증명 전송 → access_token 발급
  3. SSO_AUTH_URL2로 access_token 전송 → 사용자 정보(사번, 전화번호) 조회
"""

import json
import logging

import httpx

from app.core.config import Settings
from app.core.security import encode_password

logger = logging.getLogger(__name__)


class SSOAuthError(Exception):
    """SSO 인증 실패."""

    def __init__(self, message: str, detail: str | None = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class SSOService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def authenticate(self, username: str, password: str) -> dict:
        """SSO 인증 수행.

        Returns:
            dict: {"username": str, "name": str, "phone_number": str | None}
        """
        # Step 1: 비밀번호 인코딩
        encoded_password = encode_password(password, self.settings.pw_encoding_salt)

        # Step 2: SSO 토큰 발급
        access_token = await self._get_sso_token(username, encoded_password)

        # Step 3: 사용자 정보 조회
        user_info = await self._get_user_info(access_token)

        return user_info

    async def _get_sso_token(self, username: str, encoded_password: str) -> str:
        """SSO AUTH_URL에서 access_token 발급."""
        payload = {
            "clientIdentifier": self.settings.sso_client_id,
            "clientSecret": self.settings.sso_client_secret,
            "userName": username,
            "password": encoded_password,
            "authMethod": self.settings.sso_auth_method,
            "scopes": [self.settings.sso_scopes] if self.settings.sso_scopes else [],
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(self.settings.sso_auth_url, json=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"SSO auth request failed: {e}")
                raise SSOAuthError("SSO server connection failed", str(e))

        # 에러 확인
        error_msg = data.get("ErrorMessage") or data.get("Error") or data.get("Message")
        if error_msg:
            raise SSOAuthError("SSO authentication failed", error_msg)

        # 토큰 추출
        token_key = self.settings.sso_token_key
        token = data.get(token_key)
        if not token:
            raise SSOAuthError("SSO token not found in response")

        return token

    async def _get_user_info(self, access_token: str) -> dict:
        """SSO AUTH_URL2에서 사용자 정보 조회."""
        payload = {
            "clientIdentifier": self.settings.sso_client_id,
            "clientSecret": self.settings.sso_client_secret,
            "accessToken": access_token,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(self.settings.sso_auth_url2, json=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"SSO userinfo request failed: {e}")
                raise SSOAuthError("SSO userinfo request failed", str(e))

        # AccessProtectedResourceResult 파싱 (JSON 문자열)
        result_str = data.get("AccessProtectedResourceResult", "")
        if not result_str:
            raise SSOAuthError("Empty user info from SSO")

        try:
            user_data = json.loads(result_str) if isinstance(result_str, str) else result_str
        except json.JSONDecodeError:
            raise SSOAuthError("Failed to parse SSO user info")

        # 전화번호 추출 (claims에서)
        phone_number = None
        for claim in user_data.get("claims", []):
            if "mobilephone" in claim.get("Type", ""):
                phone_number = claim.get("Value")
                break

        return {
            "username": user_data.get("name", ""),
            "name": user_data.get("name", ""),
            "phone_number": phone_number,
        }
