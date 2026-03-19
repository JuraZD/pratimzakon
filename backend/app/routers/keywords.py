from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from ..models import User, Keyword
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

    kw = Keyword(user_id=current_user.id, keyword=keyword)
    db.add(kw)
    db.commit()
    db.refresh(kw)
    return kw


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
