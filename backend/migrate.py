#!/usr/bin/env python3
"""
backend/migrate.py

Blocking DB init + idempotentne ALTER/UPDATE migracije.
Render startCommand pokreće ovu skriptu prije Uvicorn-a.

Pokretanje lokalno:
  cd backend && python migrate.py
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

from app.database import Base, engine  # noqa: E402

MIGRATIONS: list[tuple[str, str]] = [
    ("users.plan", "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'free'"),
    ("users.include_mu", "ALTER TABLE users ADD COLUMN IF NOT EXISTS include_mu BOOLEAN DEFAULT FALSE"),
    ("keywords.document_types", "ALTER TABLE keywords ADD COLUMN IF NOT EXISTS document_types VARCHAR NULL"),
    ("documents.pdf_url", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS pdf_url TEXT NULL"),
    ("documents.part", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS part VARCHAR DEFAULT 'SL'"),
    (
        "users.email_notifications_enabled",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_notifications_enabled BOOLEAN DEFAULT TRUE",
    ),
    (
        "sync: backfill email_notifications_enabled NULL->TRUE",
        "UPDATE users SET email_notifications_enabled = TRUE WHERE email_notifications_enabled IS NULL",
    ),
    (
        "sync: copy from legacy typo column (if exists)",
        """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'users'
      AND column_name = 'email_notofications_enabled'
  ) THEN
    EXECUTE 'UPDATE users SET email_notifications_enabled = email_notofications_enabled';
  END IF;
END $$;
        """,
    ),
    (
        "sync: legacy inactive => notifications off",
        "UPDATE users SET email_notifications_enabled = FALSE WHERE subscription_status = 'inactive'",
    ),
    ("sync: legacy inactive => free", "UPDATE users SET subscription_status = 'free' WHERE subscription_status = 'inactive'"),
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
    ("sync: keyword_limit za basic", "UPDATE users SET keyword_limit = 5 WHERE plan = 'basic' AND keyword_limit < 5"),
    ("sync: keyword_limit za plus", "UPDATE users SET keyword_limit = 20 WHERE plan = 'plus' AND keyword_limit < 20"),
    (
        "fix: cap basic keyword_limit to 5",
        "UPDATE users SET keyword_limit = 5 WHERE plan = 'basic' AND keyword_limit > 5",
    ),
    (
        "fix: trim basic keywords table to 5 per user",
        """
DELETE FROM keywords
WHERE id IN (
    SELECT k.id
    FROM keywords k
    INNER JOIN users u ON u.id = k.user_id
    WHERE u.plan = 'basic'
    AND k.id NOT IN (
        SELECT k2.id
        FROM keywords k2
        WHERE k2.user_id = k.user_id
        ORDER BY k2.id
        LIMIT 5
    )
);
""",
    ),
]


def run() -> None:
    # Kritično: osiguraj da tablice postoje prije ALTER-a
    Base.metadata.create_all(bind=engine)

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
