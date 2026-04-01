"""보안 정책 스키마 + 템플릿 상수."""
from pydantic import BaseModel


# 보안 등급별 기본 템플릿
SECURITY_TEMPLATES = {
    "basic": {
        "security_level": "basic",
        "db_access": {
            "safety": {"allowed": False, "tables": []},
            "tango": {"allowed": False, "tables": []},
            "doculog": {"allowed": False, "tables": []},
            "platform": {"allowed": False, "tables": []},
        },
        "allowed_skills": ["report", "share"],
        "can_see_schema": False,
        "restricted_topics": [],
    },
    "standard": {
        "security_level": "standard",
        "db_access": {
            "safety": {"allowed": True, "tables": ["*"]},
            "tango": {"allowed": True, "tables": ["*"]},
            "doculog": {"allowed": False, "tables": []},
            "platform": {"allowed": False, "tables": []},
        },
        "allowed_skills": ["*"],
        "can_see_schema": True,
        "restricted_topics": [],
    },
    "full": {
        "security_level": "full",
        "db_access": {
            "safety": {"allowed": True, "tables": ["*"]},
            "tango": {"allowed": True, "tables": ["*"]},
            "doculog": {"allowed": True, "tables": ["*"]},
            "platform": {"allowed": False, "tables": []},
        },
        "allowed_skills": ["*"],
        "can_see_schema": True,
        "restricted_topics": [],
    },
}

TEMPLATE_DESCRIPTIONS = {
    "basic": "스킬만 사용, DB 직접접근 불가, 스키마 비노출",
    "standard": "허용 DB/테이블만 접근, 전체 스킬, 스키마 노출",
    "full": "전체 접근 (platform DB 제외), 전체 스킬",
}


class DbAccessEntry(BaseModel):
    allowed: bool
    tables: list[str]


class SecurityPolicyData(BaseModel):
    security_level: str = "standard"
    db_access: dict[str, DbAccessEntry] = {}
    allowed_skills: list[str] = ["*"]
    can_see_schema: bool = True
    restricted_topics: list[str] = []


class SecurityPolicyUpdateRequest(BaseModel):
    security_policy: SecurityPolicyData


class SecurityPolicyResponse(BaseModel):
    user_id: int
    username: str
    security_level: str
    security_policy: dict
    pod_restart_required: bool = False


class ApplyTemplateRequest(BaseModel):
    template_name: str


class SecurityTemplateItem(BaseModel):
    name: str
    description: str
    security_policy: dict


class SecurityTemplateListResponse(BaseModel):
    templates: list[SecurityTemplateItem]
