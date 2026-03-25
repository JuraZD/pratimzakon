#!/usr/bin/env python3
"""
Migracijska skripta za PratimZakon.
Dodaje nove stupce za sustav paketa (Free/Pro/Expert).

Pokretanje:
    cd backend && python migrate.py
"""

import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app.database import engine
from sqlalchemy import text


MIGRATIONS = [
    # users: plan (free | pro | expert)
    (
        "users.plan",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'free'"
    ),
    # users: include_mu – uključi međunarodne ugovore
    (
        "users.include_mu",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS include_mu BOOLEAN DEFAULT FALSE"
    ),
    # keywords: document_types filter
    (
        "keywords.document_types",
        "ALTER TABLE keywords ADD COLUMN IF NOT EXISTS document_types VARCHAR NULL"
    ),
    # documents: pdf_url
    (
        "documents.pdf_url",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS pdf_url TEXT NULL"
    ),
    # documents: part (SL | MU)
    (
        "documents.part",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS part VARCHAR DEFAULT 'SL'"
    ),
    # Sinkronizacija plana za postojeće aktivne korisnike (keyword_limit > 3 → pro, > 15 → expert)
    (
        "sync: plan za aktivne korisnike s 15 kw",
        "UPDATE users SET plan = 'pro' WHERE subscription_status = 'active' AND keyword_limit <= 15 AND plan = 'free'"
    ),
    (
        "sync: plan za aktivne korisnike s >15 kw",
        "UPDATE users SET plan = 'expert' WHERE subscription_status = 'active' AND keyword_limit > 15 AND plan = 'free'"
    ),
]


def ensure_admin():
    from app.auth import hash_password
    from sqlalchemy.orm import Session
    from app.models import User
    import secrets

    with Session(engine) as db:
        existing = db.query(User).filter(User.email == "admin@admin.com").first()
        if existing:
            logging.info("Admin korisnik već postoji — preskačem.")
            return

        admin = User(
            email="admin@admin.com",
            password_hash=hash_password("12345"),
            email_verified=True,
            subscription_status="active",
            subscription_end=None,
            keyword_limit=9999,
            plan="expert",
            plan_type="plus",
            include_mu=True,
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        db.add(admin)
        db.commit()
        logging.info("Admin korisnik kreiran: admin@admin.com")


def run():
    with engine.connect() as conn:
        for name, sql in MIGRATIONS:
            try:
                conn.execute(text(sql))
                conn.commit()
                logging.info(f"OK: {name}")
            except Exception as e:
                logging.error(f"GREŠKA pri '{name}': {e}")
                conn.rollback()

    logging.info("Migracija završena.")
    ensure_admin()


if __name__ == "__main__":
    run()
