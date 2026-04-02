"""현장 데이터 수집 양식 및 응답 모델.

survey_templates: 관리자가 만든 양식 (질문 목록 JSONB)
survey_assignments: 양식 → 현장 작업자 배포 (상태 + 진행 상태 추적)
survey_responses: 완료된 응답 데이터 (답변 JSONB + S3 사진 키)
"""

from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class SurveyTemplate(Base):
    """양식 템플릿 — 관리자가 생성하는 질문 양식.

    questions JSONB 형식:
    [
        {"type": "photo", "label": "현장 사진을 찍어주세요", "required": true},
        {"type": "text", "label": "특이사항을 적어주세요", "required": false},
        {"type": "choice", "label": "현장 상태", "options": ["양호","불량","긴급"], "required": true}
    ]
    """

    __tablename__ = "survey_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_username = Column(String(50), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    questions = Column(JSONB, nullable=False)
    status = Column(String(20), default="active")  # active, archived
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SurveyAssignment(Base):
    """양식 배포 — 특정 사용자에게 양식을 전송한 기록.

    State machine:
        pending → in_progress (첫 질문 응답 시)
        in_progress → completed (마지막 질문 응답 시)
        in_progress → expired (24시간 타임아웃)
        pending → expired (48시간 내 시작하지 않음)
    """

    __tablename__ = "survey_assignments"
    __table_args__ = (
        Index("ix_survey_assignments_target", "target_username", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("survey_templates.id"), nullable=False)
    target_username = Column(String(50), nullable=False)
    telegram_id = Column(String(50))  # 응답자 텔레그램 ID
    status = Column(String(20), default="pending")  # pending, in_progress, completed, expired
    current_question_idx = Column(Integer, default=0)
    partial_answers = Column(JSONB, default=list)  # 중간 저장 응답
    assigned_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))


class SurveyResponse(Base):
    """완료된 양식 응답 — assignment당 1개만 허용.

    answers JSONB 형식:
    [
        {"question_idx": 0, "type": "photo", "value": null, "s3_key": "USER01/3/photo_001.jpg"},
        {"question_idx": 1, "type": "text", "value": "배관 부식 발견"},
        {"question_idx": 2, "type": "choice", "value": "불량"}
    ]
    """

    __tablename__ = "survey_responses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assignment_id = Column(Integer, ForeignKey("survey_assignments.id"), unique=True, nullable=False)
    responder_username = Column(String(50), nullable=False, index=True)
    answers = Column(JSONB, nullable=False)
    completed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
