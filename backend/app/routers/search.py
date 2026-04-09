"""
Pretraga arhive Narodnih novina.
Dostupno svim prijavljenim korisnicima.
"""

from datetime import date
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

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


@router.get("/latest-issue")
def get_latest_issue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Vraća broj i datum posljednjeg broja Narodnih novina u bazi."""
    result = (
        db.query(Document.issue_number, Document.published_date)
        .filter(Document.issue_number.isnot(None))
        .order_by(Document.published_date.desc(), Document.issue_number.desc())
        .first()
    )
    if not result:
        return {"issue_number": None, "published_date": None, "label": None}

    issue_number, published_date = result
    year = published_date.year if published_date else None
    label = f"NN {issue_number}/{year}" if issue_number and year else None
    return {"issue_number": issue_number, "published_date": str(published_date) if published_date else None, "label": label}


@router.get("/institutions")
def get_institutions(
    q: str = Query("", description="Početak naziva institucije"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Vraća listu institucija koje odgovaraju upitu (za autocomplete)."""
    if len(q.strip()) < 1:
        return []

    results = (
        db.query(Document.institution)
        .filter(Document.institution.isnot(None))
        .filter(Document.institution != "")
        .filter(Document.institution.ilike(f"%{q.strip()}%"))
        .group_by(Document.institution)
        .order_by(func.count(Document.institution).desc())
        .limit(15)
        .all()
    )
    return [row[0] for row in results if row[0]]


@router.get("/summarize/{document_id}")
def summarize_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generira AI sažetak dokumenta personaliziran za korisnika."""
    from ..ai.matcher import generate_summary

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nije pronađen.")

    situation = getattr(current_user, "situation", "") or ""
    summary = generate_summary(doc, situation)

    if not summary:
        raise HTTPException(status_code=500, detail="Nije moguće generirati sažetak.")

    return {"document_id": document_id, "summary": summary}


@router.get("/document/{document_id}", response_model=DocumentResult)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dohvaća jedan dokument po ID-u."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nije pronađen.")
    return doc
