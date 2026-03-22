"""
Pretraga arhive Narodnih novina.
Dostupno samo Expert korisnicima.
"""

from datetime import date
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_

from ..database import get_db
from ..models import Document, User
from ..auth import get_current_user

router = APIRouter(prefix="/search", tags=["search"])


class DocumentResult(BaseModel):
    id: int
    title: str
    url: str
    pdf_url: Optional[str]
    type: Optional[str]
    institution: Optional[str]
    legal_area: Optional[str]
    date_document: Optional[date]
    published_date: Optional[date]
    part: Optional[str]
    issue_number: Optional[int]

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    total: int
    page: int
    per_page: int
    results: List[DocumentResult]


@router.get("/", response_model=SearchResponse)
def search_documents(
    q: Optional[str] = Query(None, description="Tekst za pretragu u naslovu"),
    doc_type: Optional[str] = Query(None, description="Tip: ZAKON, UREDBA, PRAVILNIK..."),
    institution: Optional[str] = Query(None, description="Institucija (substring)"),
    part: Optional[str] = Query(None, description="SL ili MU"),
    date_from: Optional[date] = Query(None, description="Datum objave od (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Datum objave do (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if getattr(current_user, "plan_type", "free") != "expert":
        raise HTTPException(
            status_code=403,
            detail="Pretraga arhive dostupna je samo korisnicima Expert paketa.",
        )

    if not q and not doc_type and not institution and not part and not date_from and not date_to:
        raise HTTPException(status_code=400, detail="Unesite barem jedan parametar pretrage.")

    query = db.query(Document)

    if q:
        terms = q.strip().split()
        for term in terms:
            query = query.filter(Document.title.ilike(f"%{term}%"))

    if doc_type:
        types = [t.strip().upper() for t in doc_type.split(",")]
        query = query.filter(or_(*[Document.type.ilike(t) for t in types]))

    if institution:
        query = query.filter(Document.institution.ilike(f"%{institution}%"))

    if part:
        query = query.filter(Document.part == part.upper())

    if date_from:
        query = query.filter(Document.published_date >= date_from)

    if date_to:
        query = query.filter(Document.published_date <= date_to)

    total = query.count()
    results = (
        query
        .order_by(Document.published_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return SearchResponse(total=total, page=page, per_page=per_page, results=results)
