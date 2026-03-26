#!/usr/bin/env python3
"""
backend/migrate.py

Idempotentne ALTER/UPDATE migracije za postojeću bazu.

Pokretanje:
  cd backend && python migrate.py

NAPOMENA:
  Ova skripta NE kreira admin korisnike.
  Admin se kreira RUČNO kroz backend/create_admin.py.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app.database import engine  # noqa: E402

MIGRATIONS: list[tuple[str, str]] = [
    ("users.plan", "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'free'"),
    ("users.include_mu", "ALTER TABLE users ADD COLUMN IF NOT EXISTS include_mu BOOLEAN DEFAULT FALSE"),
    ("keywords.document_types", "ALTER TABLE keywords ADD COLUMN IF NOT EXISTS document_types VARCHAR NULL"),
    ("documents.pdf_url", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS pdf_url TEXT NULL"),
    ("documents.part", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS part VARCHAR DEFAULT 'SL'"),
    (
        "sync: plan za aktivne korisnike s 15 kw",
        "UPDATE users SET plan = 'pro' WHERE subscription_status = 'active' AND keyword_limit <= 15 AND plan = 'free'",
    ),
    (
        "sync: plan za aktivne korisnike s >15 kw",
        "UPDATE users SET plan = 'expert' WHERE subscription_status = 'active' AND keyword_limit > 15 AND plan = 'free'",
    ),
    ("sync: plan pro→basic", "UPDATE users SET plan = 'basic', plan_type = 'basic' WHERE plan = 'pro'"),
    ("sync: plan expert→plus", "UPDATE users SET plan = 'plus', plan_type = 'plus' WHERE plan = 'expert'"),
    ("sync: keyword_limit za basic", "UPDATE users SET keyword_limit = 10 WHERE plan = 'basic' AND keyword_limit < 10"),
    ("sync: keyword_limit za plus", "UPDATE users SET keyword_limit = 20 WHERE plan = 'plus' AND keyword_limit < 20"),
]


def run() -> None:
    with engine.connect() as conn:
        for name, sql in MIGRATIONS:
            try:
                conn.execute(text(sql))
                conn.commit()
                logging.info("OK: %s", name)
            except Exception:
                conn.rollback()
                logging.exception("GREŠKA pri '%s'", name)

    logging.info("Migracija završena.")


if __name__ == "__main__":
    run()
