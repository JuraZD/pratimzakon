"""
Tool executor — mapira tool name na Python funkciju i izvršava je.

Jedini aktivni tool: provjeri_relevantnost.
Ostali toolovi (dohvati_nedavne_dokumente, dohvati_korisnike,
posalji_notifikacije) uklonjeni su iz TOOLS u Koraku 4 —
orchestrator ih poziva direktno, ne Claude.
"""

from ..models import Document, User


def execute_tool(name: str, input_data: dict, db) -> dict:
    """Dispatchira tool poziv na odgovarajuću Python funkciju."""
    if name == "provjeri_relevantnost":
        return _provjeri_relevantnost(db, **input_data)
    raise ValueError(f"Nepoznat tool: {name}")


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
