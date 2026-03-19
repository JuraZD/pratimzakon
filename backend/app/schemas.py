from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, EmailStr


# --- Auth ---

class UserRegister(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    email_verified: bool
    subscription_status: str
    subscription_end: Optional[date]
    keyword_limit: int
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Keywords ---

class KeywordCreate(BaseModel):
    keyword: str


class KeywordOut(BaseModel):
    id: int
    keyword: str

    model_config = {"from_attributes": True}


# --- Admin ---

class AdminStats(BaseModel):
    total_users: int
    free_users: int
    active_users: int
    expired_users: int
