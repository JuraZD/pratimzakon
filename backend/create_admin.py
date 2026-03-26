# 0) provjeri da si u rootu repo-a
cd /workspaces/pratimzakon

# 1) popravi backend/migrate.py (OVO mora biti "čist" file, bez admina/lozinki)
cat > backend/migrate.py <<'PY'
#!/usr/bin/env python3
"""
backend/migrate.py

Migracijska skripta za PratimZakon.

Svrha: idempotentne ALTER/UPDATE migracije za postojeću bazu.

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
PY

# 2) kreiraj backend/create_admin.py (ovdje je OK da postoji "12345" u listi zabranjenih lozinki)
cat > backend/create_admin.py <<'PY'
#!/usr/bin/env python3
"""
backend/create_admin.py

One-off skripta za kreiranje (ili reset) admin korisnika.

Koristi se RUČNO (lokalno ili u production shellu).

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
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL", "").strip(), help="Admin email (default: ADMIN_EMAIL)")
    parser.add_argument(
        "--password",
        default=os.getenv("ADMIN_PASSWORD", "").strip(),
        help="Admin password (default: ADMIN_PASSWORD)",
    )
    parser.add_argument("--reset-password", action="store_true", help="Ako korisnik postoji, resetira lozinku.")
    args = parser.parse_args()

    email = (args.email or "").strip() or _require_env("ADMIN_EMAIL")
    password = (args.password or "").strip() or _require_env("ADMIN_PASSWORD")

    _upsert_admin(email=email, password=password, reset_password=args.reset_password)


if __name__ == "__main__":
    main()
PY

# 3) dopuni backend/.env.template ako fali ADMIN_PASSWORD (one-off)
grep -q "^ADMIN_PASSWORD=" backend/.env.template || printf "\n\n# One-off: koristi se samo za backend/create_admin.py (NE za runtime aplikacije)\nADMIN_PASSWORD=your-very-long-random-password\n" >> backend/.env.template

# 4) validacije
chmod +x backend/migrate.py backend/create_admin.py || true
python -m py_compile backend/migrate.py backend/create_admin.py

# 5) mora biti OK (migrate.py ne smije sadržavati 12345/admin@admin.com/ensure_admin)
grep -nE "admin@admin.com|12345|ensure_admin" backend/migrate.py || echo "OK: migrate.py je čist"

# 6) commit/push (ti si već na branchu hotfix/p0-remove-default-main; ovo push-a taj branch)
git status
git add backend/migrate.py backend/create_admin.py backend/.env.template
git commit -m "Hotfix(P0): remove default admin seeding; add create_admin one-off script"
git push -u origin HEAD