"""
Statistike za Expert korisnike.
Aggregati po tipu, instituciji, dijelu i vremenskoj liniji.
"""

from datetime import date, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from ..database import get_db
from ..models import Document, User
from ..auth import get_current_user

router = APIRouter(prefix="/stats", tags=["stats"])


class CountItem(BaseModel):
    label: str
    count: int


class MonthItem(BaseModel):
    month: str   # "2025-03"
    count: int


class StatsResponse(BaseModel):
    total_documents: int
    by_type: List[CountItem]
    by_institution: List[CountItem]
    by_part: List[CountItem]
    by_month: List[MonthItem]   # zadnjih 24 mjeseca


@router.get("/", response_model=StatsResponse)
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if getattr(current_user, "plan_type", "free") != "expert":
        raise HTTPException(
            status_code=403,
            detail="Statistike su dostupne samo korisnicima Expert paketa.",
        )

    total = db.query(func.count(Document.id)).scalar() or 0

    # Top 10 tipova dokumenta
    by_type_rows = (
        db.query(Document.type, func.count(Document.id).label("cnt"))
        .filter(Document.type.isnot(None), Document.type != "")
        .group_by(Document.type)
        .order_by(func.count(Document.id).desc())
        .limit(10)
        .all()
    )
    by_type = [CountItem(label=r.type, count=r.cnt) for r in by_type_rows]

    # Top 10 institucija
    by_inst_rows = (
        db.query(Document.institution, func.count(Document.id).label("cnt"))
        .filter(Document.institution.isnot(None), Document.institution != "")
        .group_by(Document.institution)
        .order_by(func.count(Document.id).desc())
        .limit(10)
        .all()
    )
    by_institution = [CountItem(label=r.institution, count=r.cnt) for r in by_inst_rows]

    # SL vs MU
    by_part_rows = (
        db.query(Document.part, func.count(Document.id).label("cnt"))
        .filter(Document.part.isnot(None))
        .group_by(Document.part)
        .order_by(func.count(Document.id).desc())
        .all()
    )
    by_part = [CountItem(label=r.part or "SL", count=r.cnt) for r in by_part_rows]

    # Dokumenti po mjesecu — zadnjih 24 mjeseca
    cutoff = date.today() - timedelta(days=365 * 2)
    by_month_rows = (
        db.query(
            func.to_char(Document.published_date, "YYYY-MM").label("month"),
            func.count(Document.id).label("cnt"),
        )
        .filter(Document.published_date >= cutoff)
        .group_by(func.to_char(Document.published_date, "YYYY-MM"))
        .order_by(func.to_char(Document.published_date, "YYYY-MM"))
        .all()
    )
    by_month = [MonthItem(month=r.month, count=r.cnt) for r in by_month_rows if r.month]

    return StatsResponse(
        total_documents=total,
        by_type=by_type,
        by_institution=by_institution,
        by_part=by_part,
        by_month=by_month,
    )
