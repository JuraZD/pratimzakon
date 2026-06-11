"""
GET /feed/{token}       -> Atom XML feed
GET /feed/{token}/json  -> JSON feed
Autentifikacija: unsubscribe_token korisnika (nema potrebe za JWT-om, feed citaci ne salju headere).
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Log, Document

router = APIRouter(prefix="/feed", tags=["feed"])

_LIMIT = 50
_DASHBOARD_URL = "https://jurazd.github.io/pratimzakon/frontend/dashboard.html"


def _get_user(token: str, db: Session) -> User:
    user = db.query(User).filter(User.unsubscribe_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Feed nije pronaden")
    return user


def _parse_detail(detail: str) -> dict:
    """
    Parsira Log.detail string formata:
        keyword:naziv|doc_id:123|title:Naslov s | i : znakovima
    Otporno na | i : unutar vrijednosti.
    """
    parts = {}
    for segment in detail.split("|"):
        if ":" in segment:
            key, _, val = segment.partition(":")
            key = key.strip()
            if key in ("keyword", "doc_id", "title", "url"):
                parts[key] = val.strip()
    return parts


def _get_matches(user: User, db: Session) -> list[dict]:
    logs = (
        db.query(Log)
        .filter(Log.user_id == user.id, Log.event_type == "keyword_match")
        .order_by(Log.timestamp.desc())
        .limit(_LIMIT)
        .all()
    )
    results = []
    seen = set()
    for log in logs:
        detail = log.detail or ""
        parts = _parse_detail(detail)
        doc_id = parts.get("doc_id", "")
        kw = parts.get("keyword", "-")
        key = f"{kw}:{doc_id}"
        if key in seen:
            continue
        seen.add(key)
        url = parts.get("url", "")
        if not url and doc_id and doc_id.isdigit():
            doc = db.query(Document).filter(Document.id == int(doc_id)).first()
            url = doc.url if doc else ""
        if not url:
            url = _DASHBOARD_URL
        ts = log.timestamp
        if ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            matched_at = ts.isoformat()
        else:
            matched_at = datetime.now(timezone.utc).isoformat()
        results.append({
            "doc_id": doc_id,
            "title": parts.get("title", "Nepoznat dokument"),
            "keyword": kw,
            "url": url,
            "matched_at": matched_at,
        })
    return results


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@router.get("/{token}", response_class=Response)
def atom_feed(token: str, db: Session = Depends(get_db)):
    user = _get_user(token, db)
    matches = _get_matches(user, db)
    now = datetime.now(timezone.utc).isoformat()

    entries = ""
    for m in matches:
        entries += f"""
  <entry>
    <id>pratimzakon:match:{m['doc_id']}:{_xml_escape(m['keyword'])}</id>
    <title>{_xml_escape(m['title'])}</title>
    <link href="{_xml_escape(m['url'])}"/>
    <updated>{m['matched_at']}</updated>
    <summary>Kljucna rijec: {_xml_escape(m['keyword'])}</summary>
  </entry>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>pratimzakon:feed:{user.id}</id>
  <title>PratimZakon - matchevi za {_xml_escape(user.email)}</title>
  <updated>{now}</updated>
  <link rel="self" href="https://pratimzakon.onrender.com/feed/{token}"/>
  <generator>PratimZakon</generator>
  {entries}
</feed>"""

    return Response(content=xml, media_type="application/atom+xml; charset=utf-8")


@router.get("/{token}/json")
def json_feed(token: str, db: Session = Depends(get_db)):
    user = _get_user(token, db)
    matches = _get_matches(user, db)
    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": f"PratimZakon - {user.email}",
        "home_page_url": "https://jurazd.github.io/pratimzakon",
        "feed_url": f"https://pratimzakon.onrender.com/feed/{token}/json",
        "items": [
            {
                "id": f"pratimzakon:match:{m['doc_id']}:{m['keyword']}",
                "title": m["title"],
                "url": m["url"],
                "date_published": m["matched_at"],
                "tags": [m["keyword"]],
            }
            for m in matches
        ],
    }
