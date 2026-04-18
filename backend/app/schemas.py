from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, EmailStr


# --- Auth ---


class UserRegister(BaseModel):
    email: EmailStr
    password: str
    selected_plan: Optional[str] = None  # "pro" | "expert" | None


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
    email_notifications_enabled: bool
    subscription_status: str
    subscription_end: Optional[date]
    keyword_limit: int
    plan: str
    include_mu: bool
    plan_type: str
    situation: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Keywords ---


class KeywordGroupCreate(BaseModel):
    name: str


class KeywordGroupOut(BaseModel):
    id: int
    name: str
    keyword_count: int = 0

    model_config = {"from_attributes": True}


class KeywordCreate(BaseModel):
    keyword: str
    doc_type_filter: Optional[str] = None  # "ZAKON,UREDBA" ili None
    institution_filter: Optional[str] = None
    part_filter: Optional[str] = None  # "SL" | "MU" | None


class KeywordOut(BaseModel):
    id: int
    keyword: str
    doc_type_filter: Optional[str] = None
    institution_filter: Optional[str] = None
    part_filter: Optional[str] = None
    group_id: Optional[int] = None

    model_config = {"from_attributes": True}


class UserSettings(BaseModel):
    include_mu: Optional[bool] = None
    email_notifications_enabled: Optional[bool] = None
    situation: Optional[str] = None


# --- Admin ---


class AdminStats(BaseModel):
    total_users: int
    free_users: int
    active_users: int
    expired_users: int
