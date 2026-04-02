"""현장 데이터 수집 양식 스키마."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, field_validator


class SurveyQuestionType(str, Enum):
    text = "text"
    photo = "photo"
    choice = "choice"


class SurveyQuestionSchema(BaseModel):
    """양식 질문 항목."""

    type: SurveyQuestionType
    label: str
    required: bool = True
    options: list[str] | None = None

    @field_validator("options")
    @classmethod
    def choice_must_have_options(cls, v: list[str] | None, info) -> list[str] | None:
        """choice 타입은 options 필수, 그 외 타입은 options 불필요."""
        question_type = info.data.get("type")
        if question_type == SurveyQuestionType.choice:
            if not v or len(v) < 1:
                raise ValueError("choice 타입은 최소 1개의 options가 필요합니다")
        return v


class CreateSurveyRequest(BaseModel):
    """양식 생성 요청."""

    title: str
    description: str = ""
    questions: list[SurveyQuestionSchema]

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, v: list[SurveyQuestionSchema]) -> list[SurveyQuestionSchema]:
        if len(v) < 1:
            raise ValueError("최소 1개의 질문이 필요합니다")
        if len(v) > 20:
            raise ValueError("질문은 최대 20개까지 가능합니다")
        return v


class SurveyTemplateResponse(BaseModel):
    """양식 템플릿 응답."""

    id: int
    owner_username: str
    title: str
    description: str | None = ""
    questions: list[dict]
    status: str
    created_at: datetime | None = None
    response_count: int = 0

    model_config = {"from_attributes": True}


class AssignSurveyRequest(BaseModel):
    """양식 배포(할당) 요청."""

    target_usernames: list[str]


class SurveyAssignmentResponse(BaseModel):
    """양식 배포(할당) 응답."""

    id: int
    template_id: int
    target_username: str
    telegram_id: str | None = None
    status: str
    current_question_idx: int = 0
    partial_answers: list[dict] | None = None
    assigned_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class SurveyResponseItem(BaseModel):
    """완료된 양식 응답 항목."""

    id: int
    assignment_id: int
    responder_username: str
    answers: list[dict]
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}
