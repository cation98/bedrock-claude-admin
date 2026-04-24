from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None  # admin dashboard 등 body-based refresh 경로용
    token_type: str = "bearer"
    username: str
    name: str | None = None
    role: str = "user"


class LoginStep1Response(BaseModel):
    """2FA 필요: code_id 반환, 토큰 미발급."""
    requires_2fa: bool = True
    code_id: str
    phone_masked: str
    message: str = "인증코드가 발송되었습니다"


class Verify2faRequest(BaseModel):
    code_id: str
    code: str


class UserInfo(BaseModel):
    username: str
    name: str | None = None
    phone_number: str | None = None
    role: str = "user"
    is_active: bool = True
    storage_retention: str = "180d"  # 7d, 30d, 90d, 180d, unlimited

    model_config = {"from_attributes": True}
