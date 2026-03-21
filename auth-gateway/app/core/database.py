from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, echo=settings.debug)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_db() -> Session:
    """FastAPI dependency: DB 세션 제공."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
