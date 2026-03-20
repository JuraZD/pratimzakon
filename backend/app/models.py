import secrets
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
    Integer, String, Text, Date
)
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    email_verified = Column(Boolean, default=False)
    subscription_status = Column(String, default="free")  # free | active | expired
    subscription_end = Column(Date, nullable=True)
    keyword_limit = Column(Integer, default=3)
    plan = Column(String, default="free")  # free | pro | expert
    include_mu = Column(Boolean, default=False)  # uključi međunarodne ugovore (MU)
    unsubscribe_token = Column(String, unique=True, default=lambda: secrets.token_urlsafe(32))
    created_at = Column(DateTime, default=datetime.utcnow)

    keywords = relationship("Keyword", back_populates="user", cascade="all, delete-orphan")
    logs = relationship("Log", back_populates="user")


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    keyword = Column(String, nullable=False)
    document_types = Column(String, nullable=True)  # npr. "ZAKON,UREDBA" – null = svi tipovi

    user = relationship("User", back_populates="keywords")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    pdf_url = Column(Text, nullable=True)
    type = Column(String)
    part = Column(String, default="SL")  # SL (službeni) | MU (međunarodni ugovori)
    published_date = Column(Date)
    issue_number = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)  # email_sent | scrape | subscription_expired | signup
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    detail = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="logs")
