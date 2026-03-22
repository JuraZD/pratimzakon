"""
Migracijski skript za dodavanje novih stupaca u postojeću bazu.
Koristi ADD COLUMN IF NOT EXISTS — sigurno za višestruko pokretanje.
Poziva se automatski pri startu aplikacije.
"""

import logging
from sqlalchemy import text
from .database import engine

logger = logging.getLogger(__name__)

MIGRATIONS = [
    # --- users ---
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_type VARCHAR DEFAULT 'free'",

    # --- keywords ---
    "ALTER TABLE keywords ADD COLUMN IF NOT EXISTS doc_type_filter VARCHAR",
    "ALTER TABLE keywords ADD COLUMN IF NOT EXISTS institution_filter VARCHAR",
    "ALTER TABLE keywords ADD COLUMN IF NOT EXISTS part_filter VARCHAR",

    # --- documents ---
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS pdf_url TEXT",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS institution VARCHAR",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS legal_area TEXT",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS date_document DATE",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS part VARCHAR DEFAULT 'SL'",
]


def run_migrations():
    """Izvršava sve migracije. Idempotentno — sigurno za višestruko pokretanje."""
    with engine.connect() as conn:
        for sql in MIGRATIONS:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception as e:
                logger.warning(f"Migracija preskočena ({e.__class__.__name__}): {sql[:60]}...")
                conn.rollback()
    logger.info("Migracije završene.")
