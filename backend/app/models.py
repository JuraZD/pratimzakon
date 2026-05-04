import secrets
from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Date,
)
from sqlalchemy.orm import relationship
from .database import Base

# Jedini izvor istine za limite ključnih riječi po planu
PLAN_LIMITS: dict[str, int] = {
    "free": 7,
    "basic": 5,
    "plus": 20,
}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    email_verified = Column(Boolean, default=False)
    email_notifications_enabled = Column(Boolean, default=True)
    subscription_status = Column(String, default="free")  # free | active | expired
    subscription_end = Column(Date, nullable=True)
    keyword_limit = Column(Integer, default=7)
    plan = Column(String, default="free")  # free | pro | expert
    include_mu = Column(Boolean, default=False)  # uključi međunarodne ugovore (MU)
    situation = Column(
        Text, nullable=True
    )  # dodatna informacija o korisniku (npr. "student", "pravnik", "poduzetnik"...) - opcionalno
    unsubscribe_token = Column(
        String, unique=True, default=lambda: secrets.token_urlsafe(32)
    )
    stripe_subscription_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    keywords = relationship(
        "Keyword", back_populates="user", cascade="all, delete-orphan"
    )
    keyword_groups = relationship(
        "KeywordGroup", back_populates="user", cascade="all, delete-orphan"
    )
    logs = relationship("Log", back_populates="user")
    settings = relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    push_subscriptions = relationship(
        "PushSubscription", back_populates="user", cascade="all, delete-orphan"
    )


class KeywordGroup(Base):
    __tablename__ = "keyword_groups"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)

    user = relationship("User", back_populates="keyword_groups")
    keywords = relationship("Keyword", back_populates="group", passive_deletes=True)


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    keyword = Column(String, nullable=False)
    # Filteri (NULL = bez filtera = sve)
    doc_type_filter = Column(String, nullable=True)  # npr. "ZAKON,UREDBA" ili NULL
    institution_filter = Column(String, nullable=True)  # npr. "Vlada RH" ili NULL
    part_filter = Column(String, nullable=True)  # "SL" | "MU" | NULL (= oba)
    group_id = Column(Integer, ForeignKey("keyword_groups.id", ondelete="SET NULL"), nullable=True)

    user = relationship("User", back_populates="keywords")
    group = relationship("KeywordGroup", back_populates="keywords")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False)  # HTML URL
    pdf_url = Column(Text, nullable=True)  # direktni PDF link
    type = Column(String)  # ZAKON, UREDBA, PRAVILNIK, ODLUKA...
    institution = Column(String, nullable=True)  # Sabor, Vlada RH, Ministarstvo...
    legal_area = Column(Text, nullable=True)  # pravno područje iz eli:is_about
    date_document = Column(Date, nullable=True)  # datum donošenja (eli:date_document)
    published_date = Column(Date)  # datum objave (eli:date_publication)
    part = Column(String, default="SL")  # SL = Službeni list | MU = Međunarodni ugovori
    issue_number = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(
        String, nullable=False
    )  # email_sent | scrape | subscription_expired | signup
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    detail = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="logs")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    weekly_digest_enabled = Column(Boolean, default=False)

    user = relationship("User", back_populates="settings")


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="push_subscriptions")
