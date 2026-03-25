from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import List

from ..database import get_db
from ..models import User, Keyword, Document
from ..schemas import KeywordCreate, KeywordOut
from ..auth import get_current_user

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
        raise HTTPException(status_code=400, detail="Ključna riječ ne smije biti prazna")
    if len(keyword) < 2:
        raise HTTPException(status_code=400, detail="Ključna riječ mora imati najmanje 2 znaka")

    existing = db.query(Keyword).filter(
        Keyword.user_id == current_user.id,
        Keyword.keyword == keyword,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ključna riječ već postoji")

    if len(current_user.keywords) >= current_user.keyword_limit:
        raise HTTPException(
            status_code=403,
            detail=f"Dostigli ste limit od {current_user.keyword_limit} ključnih riječi. Nadogradite paket.",
        )

    kw = Keyword(
        user_id=current_user.id,
        keyword=keyword,
        doc_type_filter=data.doc_type_filter or None,
        institution_filter=data.institution_filter or None,
        part_filter=data.part_filter or None,
    )
    db.add(kw)
    db.commit()
    db.refresh(kw)
    return kw


@router.get("/activity")
def keyword_activity(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Zadnje pronađeni dokumenti i pogoci po ključnoj riječi (zadnjih 30 dana)."""
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
        raise HTTPException(status_code=404, detail="Ključna riječ nije pronađena")
    db.delete(kw)
    db.commit()
