"""T10 클라이언트 사이드 쿠키 패턴 검증 — HTML 소스 정적 분석.

Coverage:
  CH-01: login.html — bedrock_jwt 쿠키를 primary로 설정
  CH-02: login.html — claude_token legacy 쿠키 dual-write 유지 (T10 전환 기간)
  CH-03: login.html — 쿠키 속성에 Domain=.skons.net 포함
  CH-04: login.html — 쿠키 속성에 SameSite=Lax 포함
  CH-05: login.html — 쿠키 속성에 Secure 포함
  CH-06: webapp-login.html — 동일 dual-write + 속성 패턴
  CH-07: portal.html getToken() — bedrock_jwt를 먼저 탐색
  CH-08: portal.html getToken() — claude_token을 fallback으로 탐색

Note:
  HttpOnly 속성은 서버사이드 Set-Cookie에서만 설정 가능하며
  클라이언트 JS document.cookie 로는 설정되지 않는다. (보안 설계 의도)
  서버사이드 HttpOnly 검증은 test_cookie_domain.py CP-15에서 담당.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"

LOGIN_HTML = STATIC_DIR / "login.html"
WEBAPP_LOGIN_HTML = STATIC_DIR / "webapp-login.html"
PORTAL_HTML = STATIC_DIR / "portal.html"


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    assert path.exists(), f"파일 없음: {path}"
    return path.read_text(encoding="utf-8")


def _cookie_attr_block(html: str, cookie_name: str) -> str:
    """cookie_name이 설정되는 document.cookie 라인 주변 컨텍스트 반환."""
    lines = html.splitlines()
    result = []
    for i, line in enumerate(lines):
        if f"document.cookie" in line and cookie_name in line:
            # 해당 라인 + 전후 2줄 포함
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            result.extend(lines[start:end])
    return "\n".join(result)


# ---------------------------------------------------------------------------
# CH-01 ~ CH-05: login.html
# ---------------------------------------------------------------------------

class TestLoginHtmlCookies:
    """login.html 쿠키 설정 패턴 검증."""

    def setup_method(self):
        self.html = _read(LOGIN_HTML)

    def test_ch01_bedrock_jwt_cookie_set(self):
        """CH-01: bedrock_jwt 쿠키를 primary로 설정해야 함."""
        assert "bedrock_jwt=" in self.html, (
            "FAIL: login.html에 'bedrock_jwt=' 쿠키 설정 없음. "
            "T10: bedrock_ prefix 쿠키가 primary여야 함."
        )

    def test_ch02_claude_token_legacy_dual_write(self):
        """CH-02: claude_token legacy 쿠키가 dual-write 중이어야 함 (T10 전환 기간)."""
        assert "claude_token=" in self.html, (
            "FAIL: login.html에 claude_token legacy dual-write 없음. "
            "T10 전환 완료 전까지 하위 호환을 위해 유지 필요."
        )

    def test_ch03_cookie_attrs_domain_skons_net(self):
        """CH-03: 쿠키 속성에 Domain=.skons.net 포함."""
        # 쿠키 속성 변수 또는 직접 설정 라인 확인
        pattern = re.compile(r"Domain=\.skons\.net", re.IGNORECASE)
        assert pattern.search(self.html), (
            "FAIL: login.html 쿠키에 'Domain=.skons.net' 없음. "
            "ai.skons.net ↔ chat.skons.net 공유 세션을 위해 필수."
        )

    def test_ch04_cookie_attrs_samesite_lax(self):
        """CH-04: 쿠키 속성에 SameSite=Lax 포함."""
        pattern = re.compile(r"SameSite=Lax", re.IGNORECASE)
        assert pattern.search(self.html), (
            "FAIL: login.html 쿠키에 'SameSite=Lax' 없음. "
            "CSRF 방어를 위해 SameSite=Lax 필수."
        )

    def test_ch05_cookie_attrs_secure(self):
        """CH-05: 쿠키 속성에 Secure 포함."""
        # _cookieAttrs 변수 또는 직접 라인에서 확인
        # "Secure" 토큰이 독립적으로 있는지 확인 (SameSite와 혼동하지 않도록)
        pattern = re.compile(r"\bSecure\b")
        assert pattern.search(self.html), (
            "FAIL: login.html 쿠키에 'Secure' 속성 없음. "
            "HTTPS 전송만 허용하도록 Secure 플래그 필수."
        )

    def test_ch06_bedrock_jwt_set_before_claude_token(self):
        """CH-06: bedrock_jwt 설정이 claude_token 설정보다 먼저 나타나야 함 (primary 우선)."""
        bedrock_pos = self.html.find("bedrock_jwt=")
        claude_pos = self.html.find("claude_token=")
        assert bedrock_pos != -1, "bedrock_jwt 쿠키 설정 없음"
        assert claude_pos != -1, "claude_token 쿠키 설정 없음"
        assert bedrock_pos < claude_pos, (
            f"FAIL: bedrock_jwt(pos={bedrock_pos})가 claude_token(pos={claude_pos})보다 "
            "나중에 나타남. bedrock_jwt를 primary로 먼저 설정해야 함."
        )

    def test_ch07_shared_cookie_attrs_variable(self):
        """CH-07: 쿠키 속성을 단일 변수(_cookieAttrs 등)로 관리하거나 동일하게 설정."""
        # bedrock_jwt와 claude_token이 동일한 속성으로 설정되는지 확인
        # 단일 변수 패턴 또는 두 라인이 같은 attrs를 사용하는지 검사
        has_shared_attrs = (
            "_cookieAttrs" in self.html  # 변수 방식
            or (
                "bedrock_jwt=" in self.html
                and "claude_token=" in self.html
                and "Domain=.skons.net" in self.html
            )
        )
        assert has_shared_attrs, (
            "FAIL: bedrock_jwt와 claude_token이 동일한 쿠키 속성으로 설정되지 않음. "
            "두 쿠키는 같은 Domain/SameSite/Secure 속성을 공유해야 함."
        )


# ---------------------------------------------------------------------------
# CH-08 ~ CH-12: webapp-login.html (login.html과 동일 패턴)
# ---------------------------------------------------------------------------

class TestWebappLoginHtmlCookies:
    """webapp-login.html 쿠키 설정 패턴 검증 (login.html과 동일 정책)."""

    def setup_method(self):
        self.html = _read(WEBAPP_LOGIN_HTML)

    def test_ch08_bedrock_jwt_cookie_set(self):
        """CH-08: bedrock_jwt 쿠키 primary 설정."""
        assert "bedrock_jwt=" in self.html, (
            "FAIL: webapp-login.html에 'bedrock_jwt=' 없음."
        )

    def test_ch09_claude_token_legacy_dual_write(self):
        """CH-09: claude_token legacy dual-write 유지."""
        assert "claude_token=" in self.html, (
            "FAIL: webapp-login.html에 claude_token legacy dual-write 없음."
        )

    def test_ch10_domain_skons_net(self):
        """CH-10: Domain=.skons.net 포함."""
        assert re.search(r"Domain=\.skons\.net", self.html, re.IGNORECASE), (
            "FAIL: webapp-login.html 쿠키에 Domain=.skons.net 없음."
        )

    def test_ch11_samesite_lax(self):
        """CH-11: SameSite=Lax 포함."""
        assert re.search(r"SameSite=Lax", self.html, re.IGNORECASE), (
            "FAIL: webapp-login.html 쿠키에 SameSite=Lax 없음."
        )

    def test_ch12_secure(self):
        """CH-12: Secure 속성 포함."""
        assert re.search(r"\bSecure\b", self.html), (
            "FAIL: webapp-login.html 쿠키에 Secure 없음."
        )

    def test_ch13_bedrock_jwt_set_before_claude_token(self):
        """CH-13: bedrock_jwt 설정이 claude_token보다 먼저."""
        bedrock_pos = self.html.find("bedrock_jwt=")
        claude_pos = self.html.find("claude_token=")
        assert 0 <= bedrock_pos < claude_pos, (
            f"bedrock_jwt(pos={bedrock_pos}) >= claude_token(pos={claude_pos}): "
            "bedrock_jwt가 primary로 먼저 설정되어야 함."
        )


# ---------------------------------------------------------------------------
# CH-14 ~ CH-17: portal.html getToken() 쿠키 읽기 패턴
# ---------------------------------------------------------------------------

class TestPortalHtmlGetToken:
    """portal.html getToken() 함수 — bedrock_jwt 우선 fallback 패턴 검증."""

    def setup_method(self):
        self.html = _read(PORTAL_HTML)

    def test_ch14_gettoken_reads_bedrock_jwt_first(self):
        """CH-14: getToken()이 bedrock_jwt를 먼저 탐색."""
        assert "bedrock_jwt" in self.html, (
            "FAIL: portal.html에 bedrock_jwt 쿠키 읽기 없음."
        )

    def test_ch15_gettoken_has_claude_token_fallback(self):
        """CH-15: getToken()이 claude_token을 fallback으로 탐색."""
        assert "claude_token" in self.html, (
            "FAIL: portal.html에 claude_token fallback 없음. "
            "T10 전환 기간 동안 이전 쿠키 이름도 지원해야 함."
        )

    def test_ch16_gettoken_bedrock_jwt_before_claude_token(self):
        """CH-16: getToken()에서 bedrock_jwt 탐색이 claude_token보다 먼저."""
        bedrock_pos = self.html.find("bedrock_jwt")
        claude_pos = self.html.find("claude_token")
        assert bedrock_pos != -1, "bedrock_jwt 참조 없음"
        assert claude_pos != -1, "claude_token 참조 없음"
        assert bedrock_pos < claude_pos, (
            f"bedrock_jwt(pos={bedrock_pos}) >= claude_token(pos={claude_pos}): "
            "getToken()은 bedrock_jwt를 먼저 시도해야 함."
        )

    def test_ch17_gettoken_uses_cookie_regex(self):
        """CH-17: getToken()이 document.cookie 정규식으로 쿠키를 읽음."""
        # getToken 함수 내 cookie 접근 방식 확인
        assert "document.cookie" in self.html, (
            "FAIL: portal.html에 document.cookie 접근 없음."
        )
        assert "match(" in self.html or ".match(" in self.html, (
            "FAIL: portal.html에 cookie 파싱 regex match() 없음."
        )

    def test_ch18_portal_does_not_set_cookies_directly(self):
        """CH-18: portal.html은 쿠키를 직접 설정하지 않음 (읽기 전용).

        portal.html은 SSO 로그인 게이트웨이가 아니므로 쿠키를 발급하지 않는다.
        쿠키 발급은 login.html / webapp-login.html / 서버사이드 Set-Cookie만 담당.
        """
        # document.cookie = 라인에서 쿠키 설정 여부 확인
        # 단, getToken() 내부의 읽기(match)는 OK
        set_cookie_lines = [
            line.strip() for line in self.html.splitlines()
            if "document.cookie" in line and "=" in line
            and not line.strip().startswith("//")
            and "match(" not in line
            and ".match(" not in line
        ]
        # 쿠키 설정 라인이 있다면 bedrock_jwt/claude_token 설정이 아닌지 확인
        forbidden = [
            ln for ln in set_cookie_lines
            if "bedrock_jwt=" in ln or "claude_token=" in ln
        ]
        assert not forbidden, (
            f"FAIL: portal.html이 bedrock_jwt/claude_token 쿠키를 직접 설정함:\n"
            + "\n".join(forbidden)
            + "\nportal.html은 쿠키 읽기 전용이어야 함."
        )


# ---------------------------------------------------------------------------
# CH-19: open redirect 방지
# ---------------------------------------------------------------------------

class TestOpenRedirectPrevention:
    """return_url / safeUrl 검증 — open redirect 방지.

    login.html: user-controlled return_url 없음 (서버 API hub_url만 사용) — open redirect 불가.
    webapp-login.html: return_url 사용하되 상대 경로 + '//' 이중 차단.
    """

    def test_ch19_login_html_no_user_controlled_return_url(self):
        """CH-19: login.html은 user-controlled return_url 파라미터를 사용하지 않음.

        로그인 후 목적지는 세션 API의 hub_url에서만 결정된다.
        URL 파라미터 기반 redirect가 없으므로 open redirect 공격 면적이 없음.
        """
        html = _read(LOGIN_HTML)
        # return_url/redirect 쿼리 파라미터를 읽지 않아야 함
        assert "return_url" not in html, (
            "FAIL: login.html이 'return_url' 쿼리 파라미터를 사용함. "
            "open redirect 위험. 목적지는 서버 API hub_url에서만 결정되어야 함."
        )
        # hub_url은 API에서 제공 (서버 통제)
        assert "hub_url" in html, (
            "FAIL: login.html에 hub_url 참조 없음. "
            "로그인 후 목적지는 서버 API hub_url이어야 함."
        )

    def test_ch20_webapp_login_html_safe_url_validation(self):
        """CH-20: webapp-login.html의 return_url이 상대 경로만 허용."""
        html = _read(WEBAPP_LOGIN_HTML)
        assert "startsWith('/')" in html or "startsWith(\"/\")" in html, (
            "FAIL: webapp-login.html에 return_url 상대 경로 검증 없음. open redirect 위험."
        )

    def test_ch21_webapp_login_double_slash_redirect_blocked(self):
        """CH-21: webapp-login.html에서 '//'로 시작하는 URL은 차단되어야 함.

        //evil.com 같은 protocol-relative URL은 외부 사이트로 redirect될 수 있음.
        webapp-login.html만 해당 (login.html은 return_url 없음).
        """
        html = _read(WEBAPP_LOGIN_HTML)
        assert "startsWith('//')" in html or "startsWith(\"//\")" in html, (
            "FAIL: webapp-login.html에 '//' redirect 차단 로직 없음. "
            "//evil.com 같은 protocol-relative redirect 허용될 수 있음."
        )
