"""2FA 인증 코드 생성/검증 서비스.

보안 흐름:
  1. generate_code() - 6자리 코드 생성, DB 저장 (5분 만료)
  2. verify_code()   - 코드 검증 (만료/시도 횟수/일치 확인)
  3. check_lockout() - 계정 잠금 여부 확인 (15분 내 3회 실패 시)

모든 함수는 동기(sync) SQLAlchemy 세션을 사용.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.two_factor_code import TwoFactorCode

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────
CODE_EXPIRY_MINUTES = 5       # 코드 유효기간
MAX_ATTEMPTS = 5              # 코드당 최대 시도 횟수
LOCKOUT_WINDOW_MINUTES = 15   # 잠금 판정 시간 창
LOCKOUT_THRESHOLD = 3         # 시간 창 내 max-attempts 도달 횟수 → 잠금


# ── 예외 클래스 ────────────────────────────────────────

class TwoFactorError(Exception):
    """2FA 관련 기본 예외."""
    pass


class CodeExpiredError(TwoFactorError):
    """인증 코드가 만료됨."""
    pass


class CodeInvalidError(TwoFactorError):
    """인증 코드가 일치하지 않음."""

    def __init__(self, message: str, remaining_attempts: int):
        super().__init__(message)
        self.remaining_attempts = remaining_attempts


class MaxAttemptsError(TwoFactorError):
    """최대 시도 횟수 초과."""
    pass


class AccountLockedError(TwoFactorError):
    """계정 일시 잠금 (반복 실패)."""

    def __init__(self, message: str, remaining_seconds: int):
        super().__init__(message)
        self.remaining_seconds = remaining_seconds


# ── 서비스 함수 ────────────────────────────────────────

def generate_code(username: str, phone_number: str, db: Session) -> tuple[str, str]:
    """6자리 인증 코드를 생성하고 DB에 저장.

    Args:
        username: 사번 (e.g. N1102359)
        phone_number: 수신 전화번호
        db: SQLAlchemy 세션

    Returns:
        (code_id, code) 튜플. code_id는 이후 verify_code()에 사용.
    """
    code = f"{secrets.randbelow(1000000):06d}"
    now = datetime.now(timezone.utc)

    record = TwoFactorCode(
        username=username,
        code=code,
        phone_number=phone_number,
        created_at=now,
        expires_at=now + timedelta(minutes=CODE_EXPIRY_MINUTES),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info(f"2FA code generated for user={username}, code_id={record.id}")
    return record.id, code


def verify_code(code_id: str, input_code: str, db: Session) -> bool:
    """인증 코드 검증.

    Args:
        code_id: generate_code()가 반환한 레코드 ID
        input_code: 사용자가 입력한 6자리 코드
        db: SQLAlchemy 세션

    Returns:
        True if 코드가 유효하고 일치

    Raises:
        TwoFactorError: 레코드를 찾을 수 없음
        CodeExpiredError: 코드 만료
        MaxAttemptsError: 시도 횟수 초과
        CodeInvalidError: 코드 불일치 (remaining_attempts 포함)
    """
    record = db.query(TwoFactorCode).filter(TwoFactorCode.id == code_id).first()
    if not record:
        raise TwoFactorError("Invalid code_id")

    now = datetime.now(timezone.utc)

    # 이미 인증 완료된 코드
    if record.verified:
        raise TwoFactorError("Code already verified")

    # 만료 확인
    # SQLite는 timezone-naive datetime을 반환하므로 비교 전 UTC로 정규화
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise CodeExpiredError("Code has expired")

    # 시도 횟수 초과 확인
    if record.attempts >= MAX_ATTEMPTS:
        raise MaxAttemptsError("Maximum verification attempts exceeded")

    # 코드 비교 (timing-safe comparison)
    if not secrets.compare_digest(record.code, input_code):
        record.attempts += 1
        db.commit()
        remaining = MAX_ATTEMPTS - record.attempts
        logger.warning(
            f"2FA verification failed for code_id={code_id}, "
            f"attempts={record.attempts}/{MAX_ATTEMPTS}"
        )
        raise CodeInvalidError(
            f"Invalid code, {remaining} attempts remaining",
            remaining_attempts=remaining,
        )

    # 성공
    record.verified = True
    db.commit()
    logger.info(f"2FA code verified for code_id={code_id}, user={record.username}")
    return True


def check_lockout(username: str, db: Session) -> None:
    """계정 잠금 여부 확인.

    15분 내 MAX_ATTEMPTS에 도달한 코드가 LOCKOUT_THRESHOLD개 이상이면 잠금.

    Args:
        username: 사번
        db: SQLAlchemy 세션

    Raises:
        AccountLockedError: 계정이 잠금 상태 (remaining_seconds 포함)
    """
    window_start = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)

    # 시간 창 내에서 최대 시도 횟수에 도달한 코드 수 조회
    maxed_out_count = (
        db.query(TwoFactorCode)
        .filter(
            TwoFactorCode.username == username,
            TwoFactorCode.created_at >= window_start,
            TwoFactorCode.attempts >= MAX_ATTEMPTS,
        )
        .count()
    )

    if maxed_out_count >= LOCKOUT_THRESHOLD:
        # 가장 최근 실패 코드의 생성 시각 기준으로 잠금 해제 시각 계산
        latest = (
            db.query(TwoFactorCode)
            .filter(
                TwoFactorCode.username == username,
                TwoFactorCode.attempts >= MAX_ATTEMPTS,
            )
            .order_by(TwoFactorCode.created_at.desc())
            .first()
        )
        if latest:
            # SQLite는 timezone-naive datetime을 반환하므로 UTC로 정규화
            created_at = latest.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            unlock_at = created_at + timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
            remaining = int((unlock_at - datetime.now(timezone.utc)).total_seconds())
            remaining = max(remaining, 0)
        else:
            remaining = 0

        logger.warning(
            f"Account locked for user={username}, "
            f"maxed_out_codes={maxed_out_count}, remaining_seconds={remaining}"
        )
        raise AccountLockedError(
            f"Account temporarily locked due to repeated failures. "
            f"Try again in {remaining} seconds.",
            remaining_seconds=remaining,
        )
