"""
Agent orchestration — Korak 4.

Četiri jasno odvojene faze:
  1. Dohvat podataka   — deterministički Python, bez Claudea
  2. AI analiza        — Claude poziva provjeri_relevantnost
  3. Escalation        — deterministički kod odlučuje, ne Claude
  4. Notifikacija      — direktni poziv, nije Claude tool

Anti-pattern koji se izbjegava:
  ❌ Claude odlučuje hoće li poslati notifikaciju
  ✅ Kod odlučuje na temelju eksplicitnih pravila

Pokretanje:
  python -m app.agent
"""

import json
import logging
import os
from datetime import date, timedelta

import anthropic
from pydantic import BaseModel

from .ai.matcher import CLAUDE_MODEL
from .models import Document, User
from .tools.definitions import TOOLS
from .tools.executor import execute_tool

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MAX_ITERATIONS = 20


# ── Strukturirani output AI analize ──────────────────────────────────────────

class MatchResult(BaseModel):
    doc_id: int
    user_id: int
    relevantno: bool
    razlog: str


# ── Faza 1: Deterministički dohvat podataka ──────────────────────────────────

def _get_new_documents(db, broj_dana: int = 1) -> list:
    cutoff = date.today() - timedelta(days=broj_dana)
    return (
        db.query(Document)
        .filter(Document.published_date >= cutoff)
        .order_by(Document.published_date.desc())
        .limit(50)
        .all()
    )


def _get_active_users(db) -> list:
    users = (
        db.query(User)
        .filter(
            User.email_verified == True,
            User.email_notifications_enabled == True,
        )
        .all()
    )
    return [u for u in users if u.keywords or getattr(u, "situation", None)]


# ── Faza 2: AI analiza relevantnosti ─────────────────────────────────────────

def _analyse(db, docs: list, users: list) -> list[MatchResult]:
    """
    Claude analizira parove dokument/korisnik i poziva provjeri_relevantnost.
    Claude JEDINO analizira — ne odlučuje o slanju notifikacija.
    """
    doc_lines = "\n".join(
        f"  doc_id={d.id}: {d.title[:80]} (tip={d.type})"
        for d in docs[:30]
    )
    user_lines = "\n".join(
        f"  user_id={u.id}: [{', '.join(kw.keyword for kw in u.keywords)}]"
        for u in users[:30]
    )

    task = (
        f"Analiziraj relevantnost novih zakona za korisnike.\n\n"
        f"Novi dokumenti ({len(docs)}):\n{doc_lines}\n\n"
        f"Korisnici ({len(users)}):\n{user_lines}\n\n"
        "Za svaki par dokument/korisnik koji bi MOGAO biti relevantan "
        "(na temelju ključnih riječi i tipa dokumenta), pozovi provjeri_relevantnost. "
        "Preskoči očite nematcheve. Ne šalji nikakve notifikacije — "
        "samo analiziraj i pozivaj provjeri_relevantnost."
    )

    messages = [{"role": "user", "content": task}]
    matches: list[MatchResult] = []

    for iteration in range(MAX_ITERATIONS):
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            tools=TOOLS,
            messages=messages,
        )

        logging.info(
            f"[Analyse] Iteracija {iteration + 1}, stop_reason={response.stop_reason}"
        )

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                logging.info(
                    f"[Analyse] → {block.name}({json.dumps(block.input, ensure_ascii=False)})"
                )
                result = execute_tool(block.name, block.input, db)
                logging.info(f"[Analyse] ← {str(result)[:100]}")

                if block.name == "provjeri_relevantnost" and "greška" not in result:
                    try:
                        matches.append(MatchResult(
                            doc_id=block.input["doc_id"],
                            user_id=block.input["user_id"],
                            **result,
                        ))
                    except Exception as e:
                        logging.warning(f"[Analyse] MatchResult parse error: {e}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

            messages.append({"role": "user", "content": tool_results})

            # Context pruning — sprječava rast konteksta u dugim sesijama.
            # Zadržava initial task + zadnje 4 poruke (2 tool_use + 2 tool_result).
            MAX_HISTORY = 4
            if len(messages) > 1 + MAX_HISTORY:
                messages = messages[:1] + messages[-MAX_HISTORY:]

    logging.info(f"[Analyse] Završeno — {len(matches)} parova provjereno")
    return matches


# ── Faza 3: Deterministička escalation pravila ────────────────────────────────

def _apply_escalation_rules(matches: list[MatchResult], users: list) -> set[int]:
    """
    Kod (ne Claude) odlučuje koji dokumenti idu na notifikaciju.

    Pravila (svako mora biti zadovoljeno):
      1. match.relevantno == True
      2. korisnik ima email_notifications_enabled
      3. korisnik je email_verified
    """
    user_map = {u.id: u for u in users}
    relevant_doc_ids: set[int] = set()

    for match in matches:
        if not match.relevantno:
            continue

        user = user_map.get(match.user_id)
        if not user:
            continue

        if not getattr(user, "email_notifications_enabled", False):
            logging.debug(
                f"[Escalation] Preskačem user {match.user_id} — notifikacije isključene"
            )
            continue

        if not getattr(user, "email_verified", False):
            logging.debug(
                f"[Escalation] Preskačem user {match.user_id} — email nije verificiran"
            )
            continue

        relevant_doc_ids.add(match.doc_id)
        logging.info(
            f"[Escalation] doc_id={match.doc_id} → eskalira "
            f"(user={match.user_id}, razlog='{match.razlog[:60]}')"
        )

    return relevant_doc_ids


# ── Glavni orchestrator ───────────────────────────────────────────────────────

def orchestrate(db, broj_dana: int = 1) -> dict:
    """
    Pokreće sve četiri faze redom.
    Jedino faza 2 koristi Claude — sve ostalo je deterministički Python.
    """
    logging.info("[Orchestrator] Start")

    # Faza 1 — deterministički dohvat
    docs = _get_new_documents(db, broj_dana)
    logging.info(f"[Orchestrator] Faza 1: {len(docs)} novih dokumenata")

    if not docs:
        logging.info("[Orchestrator] Nema novih dokumenata — završavam.")
        return {"docs": 0, "matches": 0, "notified": 0}

    users = _get_active_users(db)
    logging.info(f"[Orchestrator] Faza 1: {len(users)} aktivnih korisnika")

    if not users:
        logging.info("[Orchestrator] Nema aktivnih korisnika — završavam.")
        return {"docs": len(docs), "matches": 0, "notified": 0}

    # Faza 2 — AI analiza
    matches = _analyse(db, docs, users)

    # Faza 3 — deterministička escalation
    to_notify = _apply_escalation_rules(matches, users)
    logging.info(
        f"[Orchestrator] Faza 3: {len(to_notify)} dokumenata prošlo escalation"
    )

    # Faza 4 — direktna notifikacija (nije Claude tool)
    notified = 0
    if to_notify:
        from .email.notifier import send_keyword_notifications
        result = send_keyword_notifications(list(to_notify), db)
        notified = result.get("sent", 0)
        logging.info(f"[Orchestrator] Faza 4: {notified} emailova poslano")

    logging.info("[Orchestrator] Završeno.")
    return {
        "docs": len(docs),
        "matches": len([m for m in matches if m.relevantno]),
        "notified": notified,
    }


if __name__ == "__main__":
    import dotenv
    import sys

    dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = orchestrate(db)
        print("\n=== ORCHESTRATOR IZVJEŠTAJ ===")
        print(f"Novi dokumenti:      {result['docs']}")
        print(f"Relevantni matchevi: {result['matches']}")
        print(f"Emailova poslano:    {result['notified']}")
    finally:
        db.close()
