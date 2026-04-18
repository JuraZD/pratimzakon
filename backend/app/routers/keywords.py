import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import List, Optional

from ..database import get_db
from ..models import User, Keyword, Document, Log, UserSettings
from ..schemas import KeywordCreate, KeywordOut
from ..auth import get_current_user
from .search import DocumentResult, SearchResponse

_TOOL_SUGESTIJE = {
    "name": "predlozi_kljucne_rijeci",
    "description": "Predloži relevantne ključne riječi za praćenje Narodnih novina.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kljucne_rijeci": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Lista od 1 do 5 kratkih ključnih riječi (1-3 riječi svaka) "
                    "relevantnih za hrvatsko zakonodavstvo. "
                    "Ne smiju se ponavljati već postojeće ključne riječi."
                ),
                "maxItems": 5,
            }
        },
        "required": ["kljucne_rijeci"],
    },
}


class SugestijeOutput(BaseModel):
    kljucne_rijeci: List[str]

router = APIRouter(prefix="/keywords", tags=["keywords"])


@router.get("/", response_model=List[KeywordOut])
def list_keywords(current_user: User = Depends(get_current_user)):
    return current_user.keywords


@router.post("/", response_model=KeywordOut, status_code=status.HTTP_201_CREATED)
def add_keyword(
    data: KeywordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    keyword = data.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="KljuÄna rijeÄ ne smije biti prazna")
    if len(keyword) < 2:
        raise HTTPException(status_code=400, detail="KljuÄna rijeÄ mora imati najmanje 2 znaka")

    existing = db.query(Keyword).filter(
        Keyword.user_id == current_user.id,
        Keyword.keyword == keyword,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="KljuÄna rijeÄ veÄ postoji")

    if len(current_user.keywords) >= current_user.keyword_limit:
        raise HTTPException(
            status_code=403,
            detail=f"Dostigli ste limit od {current_user.keyword_limit} kljuÄnih rijeÄi. Nadogradite paket.",
        )

    kw = Keyword(
        user_id=current_user.id,
        keyword=keyword,
        doc_type_filter=data.doc_type_filter or None,
        institution_filter=data.institution_filter or None,
        part_filter=data.part_filter or None,
    )
    db.add(kw)
    db.add(Log(event_type="keyword_change", user_id=current_user.id,
               detail=f"action:added|keyword:{keyword[:100]}"))
    db.commit()
    db.refresh(kw)
    return kw


class SituationUpdate(BaseModel):
    situation: Optional[str] = ""


@router.post("/situation")
def save_situation(
    data: SituationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sprema korisnikovu situaciju za personalizirane AI sažetke."""
    new_sit = data.situation.strip() if data.situation else None
    current_user.situation = new_sit
    db.add(current_user)
    # Log promjene — detail sadrži novi tekst situacije
    detail = f"title:{new_sit[:150]}" if new_sit else "title:(obrisano)"
    db.add(Log(event_type="situation_updated", user_id=current_user.id, detail=detail))
    db.commit()
    return {"message": "Situacija uspješno spremljena"}


@router.get("/activity")
def keyword_activity(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Zadnje pronaÄeni dokumenti i pogoci po kljuÄnoj rijeÄi (zadnjih 30 dana)."""
    keywords = current_user.keywords
    if not keywords:
        return {"recent_docs": [], "keyword_hits": []}

    kw_filters = [Document.title.ilike(f"%{kw.keyword}%") for kw in keywords]
    cutoff = date.today() - timedelta(days=30)

    recent_docs = (
        db.query(Document)
        .filter(or_(*kw_filters))
        .order_by(Document.published_date.desc())
        .limit(3)
        .all()
    )

    keyword_hits = []
    for kw in keywords:
        count = (
            db.query(func.count(Document.id))
            .filter(
                Document.title.ilike(f"%{kw.keyword}%"),
                Document.published_date >= cutoff,
            )
            .scalar()
        ) or 0
        keyword_hits.append({"keyword": kw.keyword, "hits": count})

    keyword_hits.sort(key=lambda x: x["hits"], reverse=True)

    return {
        "recent_docs": [
            {
                "title": d.title,
                "url": d.url,
                "published_date": str(d.published_date) if d.published_date else None,
                "type": d.type,
            }
            for d in recent_docs
        ],
        "keyword_hits": keyword_hits,
    }



@router.get("/{keyword_id}/documents", response_model=SearchResponse)
def keyword_documents(
    keyword_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated documents matching a specific keyword (last 30 days)."""
    kw = db.query(Keyword).filter(
        Keyword.id == keyword_id,
        Keyword.user_id == current_user.id,
    ).first()
    if not kw:
        raise HTTPException(status_code=404, detail="Ključna riječ nije pronađena")

    cutoff = date.today() - timedelta(days=30)
    query = db.query(Document).filter(
        Document.title.ilike(f"%{kw.keyword}%"),
        Document.published_date >= cutoff,
    )

    if kw.doc_type_filter:
        types = [t.strip().upper() for t in kw.doc_type_filter.split(",")]
        query = query.filter(or_(*[Document.type.ilike(t) for t in types]))
    if kw.institution_filter:
        query = query.filter(Document.institution.ilike(f"%{kw.institution_filter}%"))
    if kw.part_filter:
        query = query.filter(Document.part == kw.part_filter.upper())

    total = query.count()
    results = (
        query
        .order_by(Document.published_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return SearchResponse(total=total, page=page, per_page=per_page, results=results)

@router.delete("/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_keyword(
    keyword_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kw = db.query(Keyword).filter(
        Keyword.id == keyword_id,
        Keyword.user_id == current_user.id,
    ).first()

    if not kw:
        raise HTTPException(status_code=404, detail="KljuÄna rijeÄ nije pronaÄena")
    db.add(Log(event_type="keyword_change", user_id=current_user.id,
               detail=f"action:removed|keyword:{kw.keyword[:100]}"))
    db.delete(kw)
    db.commit()


# ── TJEDNI DIGEST ─────────────────────────────────────────────

@router.get("/digest-status")
def get_digest_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Vraća je li tjedni digest uključen za korisnika."""
    us = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    return {"enabled": us.weekly_digest_enabled if us else False}


@router.post("/digest-toggle")
def toggle_digest(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Uključi/isključi tjedni digest email."""
    us = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if us is None:
        us = UserSettings(user_id=current_user.id, weekly_digest_enabled=True)
        db.add(us)
    else:
        us.weekly_digest_enabled = not us.weekly_digest_enabled
    db.commit()
    return {"enabled": us.weekly_digest_enabled}


# ── AI PRIJEDLOG KLJUČNIH RIJEČI ───────────────────────────────────────────────

@router.get("/suggest")
def suggest_keywords(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI predlaže ključne riječi na temelju korisnikove situacije."""
    from ..ai.matcher import client

    situation = (current_user.situation or "").strip()
    if not situation:
        return {"suggestions": []}

    existing_kws = [kw.keyword for kw in current_user.keywords]
    existing_str = ", ".join(existing_kws) if existing_kws else "—"

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            tools=[_TOOL_SUGESTIJE],
            tool_choice={"type": "tool", "name": "predlozi_kljucne_rijeci"},
            messages=[{
                "role": "user",
                "content": (
                    "Na temelju korisnikove situacije, predloži do 5 relevantnih ključnih "
                    "riječi za praćenje Narodnih novina RH.\n\n"
                    f"Korisnikova situacija: {situation}\n"
                    f"Već prate: {existing_str}\n\n"
                    "Predloži SAMO nove ključne riječi (ne one koje već prate). "
                    "Kratki pojmovi, 1-3 riječi, relevantni za hrvatsko zakonodavstvo."
                ),
            }],
        )
        output = SugestijeOutput(**msg.content[0].input)
        existing_lower = {k.lower() for k in existing_kws}
        suggestions = [s for s in output.kljucne_rijeci if s.lower() not in existing_lower][:5]
        return {"suggestions": suggestions}

    except Exception as e:
        logging.error(f"AI suggest greška: {e}")
        raise HTTPException(status_code=500, detail="AI nije dostupan")
