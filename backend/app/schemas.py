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
    plan_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Keywords ---

class KeywordCreate(BaseModel):
    keyword: str
    doc_type_filter: Optional[str] = None    # "ZAKON,UREDBA" ili None
    institution_filter: Optional[str] = None
    part_filter: Optional[str] = None        # "SL" | "MU" | None


class KeywordOut(BaseModel):
    id: int
    keyword: str
    doc_type_filter: Optional[str] = None
    institution_filter: Optional[str] = None
    part_filter: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Admin ---

class AdminStats(BaseModel):
    total_users: int
    free_users: int
    active_users: int
    expired_users: int
