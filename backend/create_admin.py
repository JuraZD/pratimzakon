#!/usr/bin/env python3
"""
backend/create_admin.py

One-off skripta za kreiranje (ili reset) admin korisnika.

Primjer:
  cd backend
  export ADMIN_EMAIL="admin@your-domain.com"
  export ADMIN_PASSWORD="very-long-random-password"
  python create_admin.py

Reset lozinke:
  python create_admin.py --reset-password
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app.auth import hash_password  # noqa: E402
from app.database import engine  # noqa: E402
from app.models import User  # noqa: E402


@dataclass(frozen=True)
class AdminDefaults:
    subscription_status: str = "active"
    plan: str = "plus"
    plan_type: str = "plus"
    keyword_limit: int = 9999
    include_mu: bool = True
    email_verified: bool = True


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Nedostaje env var: {name}")
    return value


def _validate_password(password: str) -> None:
    if len(password) < 12:
        raise SystemExit("ADMIN_PASSWORD mora imati barem 12 znakova.")
    if password in {"12345", "password", "admin", "changeme", "change-me-in-production"}:
        raise SystemExit("ADMIN_PASSWORD je preslab / zabranjena vrijednost.")


def _upsert_admin(email: str, password: str, reset_password: bool) -> None:
    defaults = AdminDefaults()
    _validate_password(password)

    with Session(engine) as db:
        existing = db.query(User).filter(User.email == email).first()

        if existing and not reset_password:
            logging.info("Admin korisnik već postoji (%s). Nema promjena.", email)
            return

        if not existing:
            user = User(
                email=email,
                password_hash=hash_password(password),
                email_verified=defaults.email_verified,
                subscription_status=defaults.subscription_status,
                subscription_end=None,
                keyword_limit=defaults.keyword_limit,
                plan=defaults.plan,
                plan_type=defaults.plan_type,
                include_mu=defaults.include_mu,
            )
            db.add(user)
            db.commit()
            logging.info("Admin korisnik kreiran: %s", email)
            return

        existing.password_hash = hash_password(password)
        existing.email_verified = True
        db.commit()
        logging.info("Admin korisnik ažuriran (reset lozinke): %s", email)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/reset admin user (one-off).")
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL", "").strip())
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", "").strip())
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args()

    email = (args.email or "").strip() or _require_env("ADMIN_EMAIL")
    password = (args.password or "").strip() or _require_env("ADMIN_PASSWORD")

    _upsert_admin(email=email, password=password, reset_password=args.reset_password)


if __name__ == "__main__":
    main()
