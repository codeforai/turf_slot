from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, Field, StringConstraints

from app.enums import UserRole
from app.schemas import LocalDatetime, ORMModel

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9]{10,14}$")]
Password = Annotated[str, Field(min_length=8, max_length=128)]
LowerEmail = Annotated[EmailStr, AfterValidator(lambda v: v.lower())]


class RegisterRequest(BaseModel):
    first_name: Name
    last_name: Name
    email: LowerEmail
    phone: Phone
    password: Password
    role: Literal["user", "owner"] = "user"
    accept_terms: Literal[True] = Field(description="Must be true to register")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class PasswordResetRequest(BaseModel):
    email: LowerEmail


class PasswordResetConfirm(BaseModel):
    email: LowerEmail
    code: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
    new_password: Password


class PasswordChange(BaseModel):
    current_password: str
    new_password: Password


class UserUpdate(BaseModel):
    first_name: Name | None = None
    last_name: Name | None = None
    phone: Phone | None = None


class UserOut(ORMModel):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    role: UserRole
    is_active: bool
    profile_image_url: str | None
    created_at: LocalDatetime


class UserMini(ORMModel):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
