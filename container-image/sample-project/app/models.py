from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

Base = declarative_base()
engine = create_engine(settings.database_url, echo=settings.debug)
SessionLocal = sessionmaker(bind=engine)


class SafetyReport(Base):
    """안전 점검 보고서"""

    __tablename__ = "safety_reports"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    reporter_name = Column(String(100))
    location = Column(String(200))
    severity = Column(String(20))  # low, medium, high, critical
    status = Column(String(20), default="open")  # open, in_progress, resolved
    is_resolved = Column(Boolean, default=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
