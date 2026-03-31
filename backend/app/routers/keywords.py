from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import List

from ..database import get_db
from ..models import User, Keyword, Document
from ..schemas import KeywordCreate, KeywordOut
from ..auth import get_current_user
from .search import DocumentResult, SearchResponse

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
    db.commit()
    db.refresh(kw)
    return kw


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
    db.delete(kw)
    db.commit()
