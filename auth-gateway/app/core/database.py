import logging
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# IRSA 토큰 마운트 시 DNS 해석이 지연될 수 있으므로 재시도
MAX_RETRIES = 5
RETRY_DELAY = 3  # seconds

engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

for _attempt in range(1, MAX_RETRIES + 1):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("DB connection established on attempt %d", _attempt)
        break
    except Exception as e:
        if _attempt < MAX_RETRIES:
            logger.warning("DB connection attempt %d/%d failed: %s. Retrying in %ds...", _attempt, MAX_RETRIES, e, RETRY_DELAY)
            time.sleep(RETRY_DELAY)
        else:
            logger.error("DB connection failed after %d attempts. Starting anyway (will retry on first request).", MAX_RETRIES)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_db() -> Session:
    """FastAPI dependency: DB 세션 제공."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
