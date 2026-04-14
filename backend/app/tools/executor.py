"""
Tool executor — mapira tool name na Python funkciju i izvršava je.
"""

import json
import logging
from datetime import date, timedelta

from ..models import Document, User


def execute_tool(name: str, input_data: dict, db) -> dict:
    """Dispatchira tool poziv na odgovarajuću Python funkciju."""
    if name == "dohvati_nedavne_dokumente":
        return _dohvati_nedavne_dokumente(db, **input_data)
    if name == "dohvati_korisnike":
        return _dohvati_korisnike(db)
    if name == "provjeri_relevantnost":
        return _provjeri_relevantnost(db, **input_data)
    if name == "posalji_notifikacije":
        return _posalji_notifikacije(db, **input_data)
    raise ValueError(f"Nepoznat tool: {name}")


def _dohvati_nedavne_dokumente(db, broj_dana: int) -> dict:
    cutoff = date.today() - timedelta(days=broj_dana)
    docs = (
        db.query(Document)
        .filter(Document.published_date >= cutoff)
        .order_by(Document.published_date.desc())
        .limit(100)
        .all()
    )
    return {
        "ukupno": len(docs),
        "dokumenti": [
            {
                "id": d.id,
                "naslov": d.title,
                "tip": d.type,
                "datum": str(d.published_date) if d.published_date else None,
            }
            for d in docs
        ],
    }


def _dohvati_korisnike(db) -> dict:
    users = (
        db.query(User)
        .filter(User.email_verified == True)
        .all()
    )
    aktivni = [
        u for u in users
        if u.keywords or getattr(u, "situation", None)
    ]
    return {
        "ukupno": len(aktivni),
        "korisnici": [
            {
                "id": u.id,
                "email": u.email,
                "kljucne_rijeci": [kw.keyword for kw in u.keywords],
                "ima_situaciju": bool(getattr(u, "situation", None)),
            }
            for u in aktivni
        ],
    }


def _provjeri_relevantnost(db, doc_id: int, user_id: int) -> dict:
    from ..ai.matcher import check_document_for_user

    doc = db.get(Document, doc_id)
    user = db.get(User, user_id)

    if not doc:
        return {"greška": f"Dokument {doc_id} nije pronađen"}
    if not user:
        return {"greška": f"Korisnik {user_id} nije pronađen"}

    is_relevant, reason = check_document_for_user(doc, user)
    return {"relevantno": is_relevant, "razlog": reason or "-"}


def _posalji_notifikacije(db, doc_ids: list) -> dict:
    from ..email.notifier import send_keyword_notifications

    try:
        result = send_keyword_notifications(doc_ids, db)
        return {
            "uspjeh": True,
            "poslano": result.get("sent", 0),
            "greške": result.get("failed", 0),
        }
    except Exception as e:
        logging.error(f"Greška pri slanju notifikacija: {e}")
        return {"uspjeh": False, "poruka": str(e)}
