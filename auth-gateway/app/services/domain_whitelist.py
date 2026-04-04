"""도메인 화이트리스트 서비스.

DB에서 허용된 도메인 목록을 로드하고, 인메모리 캐시로 빠른 조회를 지원.
와일드카드 도메인은 dot-prefix 매칭으로 보안 취약점을 방지.

예: *.amazonaws.com
  - bedrock.us-east-1.amazonaws.com  → 허용 (서브도메인)
  - amazonaws.com                    → 허용 (베이스 도메인 자체)
  - evilamazonaws.com                → 차단 (dot-prefix가 없으므로 불일치)
"""

import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.models.proxy import AllowedDomain

logger = logging.getLogger(__name__)

# 캐시 만료 시간 (초)
CACHE_TTL_SECONDS = 60


class DomainWhitelist:
    """도메인 화이트리스트 — DB 기반 + 인메모리 캐시."""

    def __init__(self):
        self._exact_domains: set[str] = set()
        self._wildcard_bases: set[str] = set()  # 와일드카드의 베이스 도메인 (e.g. 'amazonaws.com')
        self._last_refresh: float = 0.0
        self._initialized: bool = False

    def refresh(self, db: Session) -> None:
        """DB에서 허용 도메인 목록을 다시 로드하여 캐시를 갱신."""
        try:
            domains = db.query(AllowedDomain).filter(
                AllowedDomain.enabled.is_(True)
            ).all()

            exact: set[str] = set()
            wildcard_bases: set[str] = set()

            for d in domains:
                domain_lower = d.domain.lower().strip()
                if d.is_wildcard:
                    # '*.amazonaws.com' → 'amazonaws.com'
                    base = domain_lower.removeprefix("*.")
                    wildcard_bases.add(base)
                else:
                    exact.add(domain_lower)

            self._exact_domains = exact
            self._wildcard_bases = wildcard_bases
            self._last_refresh = time.monotonic()
            self._initialized = True
            logger.info(
                f"DomainWhitelist refreshed: {len(exact)} exact, "
                f"{len(wildcard_bases)} wildcard bases"
            )
        except Exception as e:
            logger.error(f"DomainWhitelist refresh failed: {e}")
            # 실패 시 기존 캐시 유지, 초기화 안 된 경우 빈 상태

    def _ensure_fresh(self, db: Session) -> None:
        """캐시가 만료되었으면 자동으로 갱신."""
        now = time.monotonic()
        if not self._initialized or (now - self._last_refresh) > CACHE_TTL_SECONDS:
            self.refresh(db)

    def is_allowed(self, host: str, db: Session) -> bool:
        """주어진 호스트가 화이트리스트에 있는지 확인.

        Args:
            host: 검사할 도메인 (e.g. 'bedrock.us-east-1.amazonaws.com')
            db: SQLAlchemy 세션

        Returns:
            True이면 허용, False이면 차단
        """
        self._ensure_fresh(db)

        host_lower = host.lower().strip()

        # 1) 정확한 도메인 매칭
        if host_lower in self._exact_domains:
            return True

        # 2) 와일드카드 매칭 — dot-prefix 필수 (보안)
        # *.amazonaws.com 매칭 규칙:
        #   - host == 'amazonaws.com'                         → 허용 (베이스 자체)
        #   - host == 'bedrock.us-east-1.amazonaws.com'       → 허용 (.amazonaws.com 으로 끝남)
        #   - host == 'evilamazonaws.com'                     → 차단 (dot 없이 끝나므로 불일치)
        for base in self._wildcard_bases:
            if host_lower == base:
                return True
            if host_lower.endswith("." + base):
                return True

        return False

    @property
    def exact_domains(self) -> set[str]:
        """현재 캐시된 정확한 도메인 목록 (테스트용)."""
        return self._exact_domains.copy()

    @property
    def wildcard_bases(self) -> set[str]:
        """현재 캐시된 와일드카드 베이스 도메인 목록 (테스트용)."""
        return self._wildcard_bases.copy()


# 싱글톤 인스턴스 — 각 프로세스(uvicorn, proxy)에서 독립적으로 생성됨. 캐시는 프로세스별로 독립 동작.
domain_whitelist = DomainWhitelist()
