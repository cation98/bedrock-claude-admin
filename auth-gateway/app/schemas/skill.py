"""공유 스킬 API 스키마."""

import re
from datetime import datetime

from pydantic import BaseModel, field_validator

# 보안 위험 패턴: 외부 URL 실행, 시크릿 참조, 인코딩 우회
_DANGEROUS_PATTERNS = [
    r"curl\s+.*http",
    r"wget\s+.*http",
    r"\$AWS_SECRET",
    r"\$SSO_CLIENT_SECRET",
    r"base64\s+",
]

ALLOWED_CATEGORIES = {"skill", "claude-md", "prompt", "snippet"}


class SkillSubmitRequest(BaseModel):
    title: str
    description: str = ""
    category: str = "skill"
    content: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("제목은 3자 이상이어야 합니다")
        return v.strip()

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("내용이 너무 짧습니다")
        for pattern in _DANGEROUS_PATTERNS:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError(f"보안 위험 패턴이 감지되었습니다: {pattern}")
        return v.strip()

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category는 {ALLOWED_CATEGORIES} 중 하나여야 합니다")
        return v


class SkillResponse(BaseModel):
    id: int
    author_username: str
    author_name: str | None
    title: str
    description: str | None
    category: str
    content: str
    is_approved: bool
    approved_by: str | None
    approved_at: datetime | None
    usage_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillListResponse(BaseModel):
    total: int
    skills: list[SkillResponse]
