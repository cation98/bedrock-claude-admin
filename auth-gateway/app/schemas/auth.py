from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    name: str | None = None
    role: str = "user"


class UserInfo(BaseModel):
    username: str
    name: str | None = None
    phone_number: str | None = None
    role: str = "user"
    is_active: bool = True

    model_config = {"from_attributes": True}
