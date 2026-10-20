from pydantic import BaseModel, Field


class SendCodeIn(BaseModel):
    phone: str = Field(min_length=6, max_length=20)


class SendCodeOut(BaseModel):
    ok: bool = True
    dev_code: str | None = None  # 仅开发模式返回


class LoginIn(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class LoginOut(BaseModel):
    token: str
    is_new_user: bool
    user_id: int
