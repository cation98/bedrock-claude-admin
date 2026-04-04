"""사용자별 텔레그램 봇 모델.

각 사용자가 자신의 텔레그램 봇을 등록하면, Gateway가 webhook을 받아
해당 사용자의 Pod로 전달하는 구조.

테이블:
  user_bots — 봇 토큰(암호화), webhook secret, 상태 관리
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, LargeBinary

from app.core.database import Base


class UserBot(Base):
    """사용자 등록 텔레그램 봇.

    - bot_token_encrypted: Fernet으로 암호화된 봇 토큰 (BYTEA)
    - bot_token_hash: SHA-256 해시 — webhook URL의 라우팅 키이자 중복 검사용
    - webhook_secret: Telegram X-Telegram-Bot-Api-Secret-Token 검증용 랜덤 값
    """

    __tablename__ = "user_bots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=False, index=True)  # users.username FK (논리적)
    bot_token_encrypted = Column(LargeBinary, nullable=False)
    bot_token_hash = Column(String(64), unique=True, nullable=False, index=True)
    bot_username = Column(String(100))  # @bot_username (from Telegram getMe)
    webhook_secret = Column(String(64), nullable=False)  # random hex, for Telegram verification
    status = Column(String(20), default="active")  # active | paused | error
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
