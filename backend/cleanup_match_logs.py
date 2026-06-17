#!/usr/bin/env python3
"""
backend/cleanup_match_logs.py

Jednokratna skripta za čišćenje starih `keyword_match` logova kod kojih je u
polje `keyword` greškom spremljeno AI obrazloženje (cijela rečenica) umjesto
stvarne ključne riječi. Takvi zapisi su nastali prije popravka u kojem AI
matchevi dobivaju pravu ključnu riječ ili oznaku "AI procjena".

Detekcija (heuristika): zapis je "neispravan" ako vrijednost u `keyword`
izgleda kao rečenica (preduga ili previše riječi) I NIJE jedna od trenutnih
ključnih riječi tog korisnika (case-insensitive). Tako se izbjegava brisanje
legitimnih (eventualno duljih) ključnih riječi.

Po defaultu radi DRY-RUN (samo ispiše što bi obrisao). Za stvarno brisanje
dodaj --apply.

Primjeri:
  cd backend
  python cleanup_match_logs.py                 # pregled (dry-run)
  python cleanup_match_logs.py --apply         # stvarno obriši
  python cleanup_match_logs.py --min-len 60 --min-words 7 --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app.database import SessionLocal  # noqa: E402
from app.models import Keyword, Log  # noqa: E402

# Oznaka koju novi kod koristi za AI matcheve — nikad nije "neispravna".
AI_MATCH_LABEL = "AI procjena"


def _parse_keyword(detail: str) -> str:
    """Izvuče vrijednost polja `keyword` iz Log.detail (key:val|key:val|...)."""
    for segment in (detail or "").split("|"):
        key, sep, val = segment.partition(":")
        if sep and key.strip() == "keyword":
            return val.strip()
    return ""


def _looks_like_sentence(keyword: str, min_len: int, min_words: int) -> bool:
    """True ako keyword izgleda kao rečenica (AI obrazloženje), ne kao ključna riječ."""
    if not keyword or keyword == AI_MATCH_LABEL:
        return False
    return len(keyword) >= min_len or len(keyword.split()) >= min_words


def run_cleanup(apply: bool, min_len: int, min_words: int) -> int:
    session = SessionLocal()
    try:
        # Mapa user_id -> skup trenutnih ključnih riječi (lowercase)
        user_keywords: dict[int, set[str]] = {}
        for user_id, kw in session.query(Keyword.user_id, Keyword.keyword).all():
            user_keywords.setdefault(user_id, set()).add((kw or "").strip().lower())

        logs = (
            session.query(Log)
            .filter(Log.event_type == "keyword_match")
            .order_by(Log.timestamp.desc())
            .all()
        )

        to_delete: list[Log] = []
        for log in logs:
            kw = _parse_keyword(log.detail or "")
            if not _looks_like_sentence(kw, min_len, min_words):
                continue
            # Sačuvaj ako je to stvarno trenutna ključna riječ tog korisnika
            current = user_keywords.get(log.user_id, set())
            if kw.lower() in current:
                continue
            to_delete.append(log)

        logging.info(
            f"Pregledano {len(logs)} keyword_match logova — "
            f"neispravnih (keyword = rečenica): {len(to_delete)}"
        )
        for log in to_delete:
            kw = _parse_keyword(log.detail or "")
            preview = kw[:80] + ("…" if len(kw) > 80 else "")
            logging.info(f"  [user={log.user_id} log_id={log.id}] keyword='{preview}'")

        if not to_delete:
            logging.info("Nema ničega za čišćenje.")
            return 0

        if not apply:
            logging.info("DRY-RUN — ništa nije obrisano. Pokreni s --apply za stvarno brisanje.")
            return 0

        for log in to_delete:
            session.delete(log)
        session.commit()
        logging.info(f"Obrisano {len(to_delete)} neispravnih keyword_match logova.")
        return len(to_delete)

    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Čišćenje starih keyword_match logova s AI rečenicom u keyword polju")
    parser.add_argument("--apply", action="store_true", help="Stvarno obriši (default je dry-run)")
    parser.add_argument("--min-len", type=int, default=45, help="Minimalna duljina znakova da se keyword smatra rečenicom (default: 45)")
    parser.add_argument("--min-words", type=int, default=6, help="Minimalan broj riječi da se keyword smatra rečenicom (default: 6)")
    args = parser.parse_args()
    run_cleanup(args.apply, args.min_len, args.min_words)
