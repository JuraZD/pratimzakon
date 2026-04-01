"""
Statistike za Plus korisnike.
Aggregati po tipu, instituciji, dijelu, vremenskoj liniji + nove analitike.
"""
from datetime import date, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, cast, Integer
from ..database import get_db
from ..models import Document, User, Log, Keyword
from ..auth import get_current_user, user_has_plan

router = APIRouter(prefix="/stats", tags=["stats"])


class CountItem(BaseModel):
    label: str
    count: int


class MonthItem(BaseModel):
    month: str   # "2025-03"
    count: int


class YoYItem(BaseModel):
    month: str   # "03" (just the month number)
    year: int
    count: int


class WeekdayItem(BaseModel):
    weekday: int   # 0=Mon … 6=Sun (Python convention via PostgreSQL DOW: 0=Sun…6=Sat, remapped)
    label: str
    count: int


class StatsResponse(BaseModel):
    total_documents: int
    by_type: List[CountItem]
    by_institution: List[CountItem]
    by_part: List[CountItem]
    by_month: List[MonthItem]          # last 24 months (for timeline + moving avg)
    by_year_month: List[YoYItem]       # last 2 full years for YoY
    by_weekday: List[WeekdayItem]      # publications by day of week
    user_growth: List[MonthItem]       # new signups per month (last 24 months)
    plan_distribution: List[CountItem] # FREE / BASIC / PLUS counts
    keyword_hits: List[CountItem]      # top keywords by match count (from logs)
    email_stats: List[CountItem]       # email_sent vs bounced (from logs)


@router.get("/", response_model=StatsResponse)
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not user_has_plan(current_user, "plus", "expert"):
        raise HTTPException(
            status_code=403,
            detail="Statistike su dostupne samo korisnicima Plus paketa.",
        )

    total = db.query(func.count(Document.id)).scalar() or 0

    # ── Top 10 tipova dokumenta (normalized: lowercase + strip)
    by_type_rows = (
        db.query(
            func.lower(func.trim(Document.type)).label("dtype"),
            func.count(Document.id).label("cnt"),
        )
        .filter(Document.type.isnot(None), Document.type != "")
        .group_by(func.lower(func.trim(Document.type)))
        .order_by(func.count(Document.id).desc())
        .limit(10)
        .all()
    )
    by_type = [CountItem(label=r.dtype, count=r.cnt) for r in by_type_rows]

    # ── Top 10 institucija
    by_inst_rows = (
        db.query(Document.institution, func.count(Document.id).label("cnt"))
        .filter(Document.institution.isnot(None), Document.institution != "")
        .group_by(Document.institution)
        .order_by(func.count(Document.id).desc())
        .limit(10)
        .all()
    )
    by_institution = [CountItem(label=r.institution, count=r.cnt) for r in by_inst_rows]

    # ── SL vs MU
    by_part_rows = (
        db.query(Document.part, func.count(Document.id).label("cnt"))
        .filter(Document.part.isnot(None))
        .group_by(Document.part)
        .order_by(func.count(Document.id).desc())
        .all()
    )
    by_part = [CountItem(label=r.part or "SL", count=r.cnt) for r in by_part_rows]

    # ── Dokumenti po mjesecu — zadnjih 24 mjeseca
    cutoff_24m = date.today() - timedelta(days=365 * 2)
    by_month_rows = (
        db.query(
            func.to_char(Document.published_date, "YYYY-MM").label("month"),
            func.count(Document.id).label("cnt"),
        )
        .filter(Document.published_date >= cutoff_24m)
        .group_by(func.to_char(Document.published_date, "YYYY-MM"))
        .order_by(func.to_char(Document.published_date, "YYYY-MM"))
        .all()
    )
    by_month = [MonthItem(month=r.month, count=r.cnt) for r in by_month_rows if r.month]

    # ── Year-over-Year: last 2 full calendar years
    current_year = date.today().year
    yoy_rows = (
        db.query(
            extract("year", Document.published_date).label("yr"),
            extract("month", Document.published_date).label("mo"),
            func.count(Document.id).label("cnt"),
        )
        .filter(
            extract("year", Document.published_date).in_([current_year - 1, current_year])
        )
        .group_by(
            extract("year", Document.published_date),
            extract("month", Document.published_date),
        )
        .order_by(
            extract("year", Document.published_date),
            extract("month", Document.published_date),
        )
        .all()
    )
    MONTH_NAMES = ["", "Sij", "Velj", "Ožu", "Tra", "Svi", "Lip", "Srp", "Kol", "Ruj", "Lis", "Stu", "Pro"]
    by_year_month = [
        YoYItem(month=MONTH_NAMES[int(r.mo)], year=int(r.yr), count=r.cnt)
        for r in yoy_rows
    ]

    # ── Dokumenti po danu u tjednu (0=Ned, 1=Pon … 6=Sub u PostgreSQL DOW)
    # Remap PostgreSQL DOW (0=Sun) to Mon-Sun (0=Mon … 6=Sun)
    DAY_LABELS = ["Pon", "Uto", "Sri", "Čet", "Pet", "Sub", "Ned"]
    weekday_rows = (
        db.query(
            extract("dow", Document.published_date).label("dow"),
            func.count(Document.id).label("cnt"),
        )
        .filter(Document.published_date.isnot(None))
        .group_by(extract("dow", Document.published_date))
        .order_by(extract("dow", Document.published_date))
        .all()
    )
    # Remap: PostgreSQL DOW 0=Sun→6, 1=Mon→0, ..., 6=Sat→5
    weekday_map = {}
    for r in weekday_rows:
        pg_dow = int(r.dow)
        py_dow = (pg_dow - 1) % 7  # Mon=0 … Sun=6
        weekday_map[py_dow] = int(r.cnt)
    by_weekday = [
        WeekdayItem(weekday=i, label=DAY_LABELS[i], count=weekday_map.get(i, 0))
        for i in range(7)
    ]

    # ── User growth (signups per month, last 24 months)
    from sqlalchemy import DateTime
    growth_rows = (
        db.query(
            func.to_char(User.created_at, "YYYY-MM").label("month"),
            func.count(User.id).label("cnt"),
        )
        .filter(User.created_at >= cutoff_24m)
        .group_by(func.to_char(User.created_at, "YYYY-MM"))
        .order_by(func.to_char(User.created_at, "YYYY-MM"))
        .all()
    )
    user_growth = [MonthItem(month=r.month, count=r.cnt) for r in growth_rows if r.month]

    # ── Plan distribution
    plan_rows = (
        db.query(User.plan, func.count(User.id).label("cnt"))
        .group_by(User.plan)
        .all()
    )
    PLAN_LABELS = {"free": "Free", "basic": "Basic", "plus": "Plus", "pro": "Basic", "expert": "Plus"}
    plan_agg: dict = {}
    for r in plan_rows:
        label = PLAN_LABELS.get(r.plan or "free", r.plan or "free")
        plan_agg[label] = plan_agg.get(label, 0) + r.cnt
    plan_distribution = [CountItem(label=k, count=v) for k, v in sorted(plan_agg.items())]

    # ── Keyword hits from logs (top keywords by notification match)
    kw_hit_rows = (
        db.query(Log.detail, func.count(Log.id).label("cnt"))
        .filter(Log.event_type == "keyword_match")
        .filter(Log.detail.isnot(None))
        .group_by(Log.detail)
        .order_by(func.count(Log.id).desc())
        .limit(10)
        .all()
    )
    keyword_hits = [CountItem(label=r.detail, count=r.cnt) for r in kw_hit_rows]

    # ── Email stats from logs
    email_event_rows = (
        db.query(Log.event_type, func.count(Log.id).label("cnt"))
        .filter(Log.event_type.in_(["email_sent", "email_bounced", "unsubscribe", "signup"]))
        .group_by(Log.event_type)
        .all()
    )
    EMAIL_LABELS = {
        "email_sent": "Email poslan",
        "email_bounced": "Odbijeni",
        "unsubscribe": "Odjavljeni",
        "signup": "Registracije",
    }
    email_stats = [
        CountItem(label=EMAIL_LABELS.get(r.event_type, r.event_type), count=r.cnt)
        for r in email_event_rows
    ]

    return StatsResponse(
        total_documents=total,
        by_type=by_type,
        by_institution=by_institution,
        by_part=by_part,
        by_month=by_month,
        by_year_month=by_year_month,
        by_weekday=by_weekday,
        user_growth=user_growth,
        plan_distribution=plan_distribution,
        keyword_hits=keyword_hits,
        email_stats=email_stats,
    )
