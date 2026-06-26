"""
Pretraga arhive Narodnih novina.
Dostupno svim prijavljenim korisnicima.
"""

from datetime import date, datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
import logging
import os

from ..database import get_db
from ..models import Document, User, Log
from ..auth import get_current_user
from ..email.notifier import _stem_keyword

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
    doc_type: Optional[str] = Query(
        None, description="Tip: ZAKON, UREDBA, PRAVILNIK..."
    ),
    institution: Optional[str] = Query(None, description="Institucija (substring)"),
    part: Optional[str] = Query(None, description="SL ili MU"),
    date_from: Optional[date] = Query(None, description="Datum objave od (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Datum objave do (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if (
        not q
        and not doc_type
        and not institution
        and not part
        and not date_from
        and not date_to
    ):
        raise HTTPException(
            status_code=400, detail="Unesite barem jedan parametar pretrage."
        )

    query = db.query(Document)

    if q:
        terms = q.strip().split()
        for term in terms:
            query = query.filter(Document.title.ilike(f"%{_stem_keyword(term)}%"))

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
        query.order_by(Document.published_date.desc())
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
    total_docs = db.query(func.count(Document.id)).scalar() or 0

    # 1. Pokušaj s issue_number + published_date
    result = (
        db.query(Document.issue_number, Document.published_date)
        .filter(Document.issue_number.isnot(None))
        .order_by(Document.published_date.desc(), Document.issue_number.desc())
        .first()
    )
    if result:
        issue_number, published_date = result
        year = published_date.year if published_date else None
        label = f"NN {issue_number}/{year}" if issue_number and year else f"NN {issue_number}"
        return {
            "issue_number": issue_number,
            "published_date": str(published_date) if published_date else None,
            "label": label,
            "total_docs": total_docs,
        }

    # 2. Fallback: samo published_date
    r2 = (
        db.query(Document.published_date)
        .filter(Document.published_date.isnot(None))
        .order_by(Document.published_date.desc())
        .first()
    )
    if r2:
        return {
            "issue_number": None,
            "published_date": str(r2[0]),
            "label": str(r2[0]),
            "total_docs": total_docs,
        }

    # 3. Fallback: date_document
    r3 = (
        db.query(Document.date_document)
        .filter(Document.date_document.isnot(None))
        .order_by(Document.date_document.desc())
        .first()
    )
    if r3:
        return {
            "issue_number": None,
            "published_date": str(r3[0]),
            "label": str(r3[0]),
            "total_docs": total_docs,
        }

    # 4. Posljednji fallback: samo broj dokumenata
    return {"issue_number": None, "published_date": None, "label": None, "total_docs": total_docs}


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
    keyword: Optional[str] = Query(None, description="Ključna riječ koja je okidala match"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generira AI sažetak dokumenta personaliziran za korisnika."""
    from ..ai.matcher import generate_summary

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nije pronađen.")

    situation = getattr(current_user, "situation", "") or ""
    summary = generate_summary(doc, situation, keyword=keyword)

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


# ── Zadnji matchevi korisnika ──────────────────────────────────────────────
class MatchItem(BaseModel):
    id: int
    document_id: Optional[int]
    document_title: str
    keyword: str
    matched_at: str
    document_date: Optional[str] = None


@router.get("/matches/recent", response_model=List[MatchItem])
def get_recent_matches(
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Zadnjih N matcheva za trenutnog korisnika."""
    rows = (
        db.query(Log)
        .filter(Log.user_id == current_user.id)
        .filter(Log.event_type == "keyword_match")
        .order_by(Log.timestamp.desc())
        .limit(limit)
        .all()
    )
    # Parsiramo sve detalje i skupljamo doc_id-jeve i url-ove za batch lookup
    parsed_rows = []
    doc_ids_to_fetch: set[int] = set()
    urls_to_fetch: set[str] = set()
    for r in rows:
        detail = r.detail or ""
        parts = dict(p.split(":", 1) for p in detail.split("|") if ":" in p)
        doc_id = None
        if parts.get("doc_id", "").strip().isdigit():
            doc_id = int(parts["doc_id"])
            doc_ids_to_fetch.add(doc_id)
        if parts.get("url", "").strip():
            urls_to_fetch.add(parts["url"].strip())
        parsed_rows.append((r, parts, doc_id))

    # Batch dohvat po ID-u
    doc_id_map: dict[int, date] = {}
    if doc_ids_to_fetch:
        for d in db.query(Document.id, Document.published_date, Document.date_document).filter(
            Document.id.in_(doc_ids_to_fetch)
        ).all():
            pub = d.published_date or d.date_document
            if pub:
                doc_id_map[d.id] = pub

    # Batch dohvat po URL-u (fallback za stare logove bez doc_id)
    url_date_map: dict[str, date] = {}
    if urls_to_fetch:
        for d in db.query(Document.url, Document.published_date, Document.date_document).filter(
            Document.url.in_(urls_to_fetch)
        ).all():
            pub = d.published_date or d.date_document
            if pub:
                url_date_map[d.url] = pub

    results = []
    seen: set[tuple] = set()
    for r, parts, doc_id in parsed_rows:
        keyword = parts.get("keyword", "—")
        dedup_key = (doc_id, keyword.lower()) if doc_id else (r.id, keyword.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        pub = doc_id_map.get(doc_id) if doc_id else None
        if pub is None:
            pub = url_date_map.get(parts.get("url", "").strip())
        results.append(
            MatchItem(
                id=r.id,
                document_id=doc_id,
                document_title=parts.get("title", "Nepoznat dokument"),
                keyword=keyword,
                matched_at=r.timestamp.strftime("%d.%m.%Y.") if r.timestamp else "—",
                document_date=pub.strftime("%d.%m.%Y.") if pub else None,
            )
        )
    return results


# ── Aktivnost korisnika ────────────────────────────────────────────────────
class ActivityItem(BaseModel):
    id: int
    event_type: str
    message: str
    color: str
    timestamp: str


@router.get("/activity/recent", response_model=List[ActivityItem])
def get_recent_activity(
    limit: int = Query(50, ge=1, le=300),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Zadnjih N aktivnosti za trenutnog korisnika."""
    rows = (
        db.query(Log)
        .filter(Log.user_id == current_user.id)
        .order_by(Log.timestamp.desc())
        .limit(limit)
        .all()
    )
    EVENT_MAP = {
        "keyword_match": ("Match pronađen", "a-green"),
        "keyword_change": ("Promjena praćenja", "a-orange"),
        "email_sent": ("Email poslan", "a-navy"),
        "situation_updated": ("Situacija ažurirana", "a-navy"),
        "pref_digest": ("Digest postavka", "a-navy"),
        "archived": ("Arhivirano", "a-navy"),
        "scrape": ("Tražilica završila", "a-green"),
        "scrape_error": ("Tražilica — greška", "a-red"),
        "signup": ("Registracija", "a-navy"),
        "subscription_expired": ("Pretplata istekla", "a-orange"),
    }
    results = []
    for r in rows:
        label, color = EVENT_MAP.get(r.event_type, (r.event_type, "a-navy"))
        detail = r.detail or ""
        parts = dict(p.split(":", 1) for p in detail.split("|") if ":" in p)
        # Special formatting for keyword_change
        if r.event_type == "keyword_change":
            action = parts.get("action", "")
            kw = parts.get("keyword", "")
            if action == "added":
                message = f"Dodano praćenje: {kw}"
                color = "a-green"
            elif action == "removed":
                message = f"Uklonjeno praćenje: {kw}"
                color = "a-orange"
            else:
                message = label
        elif r.event_type == "pref_digest":
            enabled = "1" in detail
            message = "Tjedni sažetak uključen" if enabled else "Tjedni sažetak isključen"
        else:
            title = parts.get("title", "")
            message = f"{label} — {title}" if title else label
        results.append(
            ActivityItem(
                id=r.id,
                event_type=r.event_type,
                message=message,
                color=color,
                timestamp=r.timestamp.strftime("%d.%m. %H:%M") if r.timestamp else "—",
            )
        )
    return results


# ── ARHIVA ────────────────────────────────────────────────────────────────

class ArchiveItem(BaseModel):
    document_id: int
    document_title: str
    archived_at: str
    url: str

class ArchiveStatus(BaseModel):
    archived: bool


@router.get("/archive", response_model=List[ArchiveItem])
def get_archive(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dohvaća sve korisnikove arhivirane dokumente."""
    rows = (
        db.query(Log)
        .filter(Log.user_id == current_user.id)
        .filter(Log.event_type == "archived")
        .order_by(Log.timestamp.desc())
        .all()
    )
    results = []
    for r in rows:
        parts = dict(p.split(":", 1) for p in (r.detail or "").split("|") if ":" in p)
        doc_id_str = parts.get("doc_id")
        if not doc_id_str:
            continue
        doc_id = int(doc_id_str)
        doc = db.query(Document).filter(Document.id == doc_id).first()
        results.append(ArchiveItem(
            document_id=doc_id,
            document_title=parts.get("title", doc.title if doc else "—"),
            archived_at=r.timestamp.strftime("%d.%m.%Y.") if r.timestamp else "—",
            url=doc.url if doc else "",
        ))
    return results


@router.get("/archive/{document_id}", response_model=ArchiveStatus)
def check_archive(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Provjeri je li dokument u korisnikovoj arhivi."""
    existing = (
        db.query(Log)
        .filter(Log.user_id == current_user.id)
        .filter(Log.event_type == "archived")
        .filter(Log.detail.contains(f"doc_id:{document_id}"))
        .first()
    )
    return {"archived": existing is not None}


@router.post("/archive/{document_id}", response_model=ArchiveStatus)
def toggle_archive(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Spremi ili ukloni dokument iz arhive (toggle)."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nije pronađen.")

    existing = (
        db.query(Log)
        .filter(Log.user_id == current_user.id)
        .filter(Log.event_type == "archived")
        .filter(Log.detail.contains(f"doc_id:{document_id}"))
        .first()
    )

    if existing:
        db.delete(existing)
        db.commit()
        return {"archived": False}
    else:
        log = Log(
            user_id=current_user.id,
            event_type="archived",
            detail=f"doc_id:{document_id}|title:{doc.title[:120]}",
        )
        db.add(log)
        db.commit()
        return {"archived": True}


# ── POVEZANI PROPISI ──────────────────────────────────────────────────────

@router.get("/document/{document_id}/related", response_model=List[DocumentResult])
def get_related_documents(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dohvaća dokumente iste institucije, isključuje trenutni dokument."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nije pronađen.")

    base = db.query(Document).filter(Document.id != document_id)

    if doc.institution:
        related = (
            base
            .filter(Document.institution == doc.institution)
            .order_by(Document.published_date.desc())
            .limit(5)
            .all()
        )
    else:
        related = (
            base
            .filter(Document.type == doc.type)
            .order_by(Document.published_date.desc())
            .limit(5)
            .all()
        )

    return related


# ── BILJEŠKE ──────────────────────────────────────────────────────────────────

class NoteBody(BaseModel):
    text: str


@router.get("/notes")
def get_all_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dohvaća sve korisnikove bilješke kao {doc_id: text}."""
    rows = (
        db.query(Log)
        .filter(Log.user_id == current_user.id, Log.event_type == "note")
        .all()
    )
    result = {}
    for r in rows:
        detail = r.detail or ""
        if "|text:" in detail:
            head, note_text = detail.split("|text:", 1)
            if head.startswith("doc_id:"):
                try:
                    doc_id = int(head.replace("doc_id:", "").strip())
                    result[doc_id] = note_text
                except ValueError:
                    pass
    return result


@router.post("/note/{document_id}")
def save_note(
    document_id: int,
    body: NoteBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Spremi ili obriši bilješku za dokument (prazno tekst = brisanje)."""
    existing = (
        db.query(Log)
        .filter(Log.user_id == current_user.id, Log.event_type == "note")
        .filter(Log.detail.contains(f"doc_id:{document_id}|"))
        .first()
    )
    text = (body.text or "").strip()[:500]
    if existing:
        if text:
            existing.detail = f"doc_id:{document_id}|text:{text}"
        else:
            db.delete(existing)
    elif text:
        db.add(Log(
            user_id=current_user.id,
            event_type="note",
            detail=f"doc_id:{document_id}|text:{text}",
        ))
    db.commit()
    return {"text": text}


# ── DUBOKA ANALIZA ─────────────────────────────────────────────────────────────

class DeepAnalysisResponse(BaseModel):
    tko: str
    iznosi: str
    rokovi: str
    paziti: str
    source: str  # "html" | "title_only"


@router.get("/document/{document_id}/deep-analysis", response_model=DeepAnalysisResponse)
def deep_analysis(
    document_id: int,
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dohvati puni tekst dokumenta s NN.hr i generiraj detaljnu analizu pomoću Claude-a."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nije pronađen.")

    # Dohvati HTML tekst s NN.hr
    article_text = ""
    source = "title_only"
    if doc.url:
        try:
            import requests
            from bs4 import BeautifulSoup
            resp = requests.get(doc.url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # NN.hr struktura: članci su u <div class="article-body"> ili <div id="nn-article">
                article_div = (
                    soup.find("div", class_="article-body")
                    or soup.find("div", id="nn-article")
                    or soup.find("div", class_="nn-article")
                    or soup.find("article")
                    or soup.find("div", class_="content")
                )
                if article_div:
                    article_text = article_div.get_text(separator="\n", strip=True)
                else:
                    # Fallback: cijeli <body> bez navigacije
                    for tag in soup(["nav", "header", "footer", "script", "style"]):
                        tag.decompose()
                    article_text = soup.get_text(separator="\n", strip=True)
                # Ograniči na ~6000 znakova da ne prelazimo token limite
                article_text = article_text[:6000]
                if len(article_text) > 100:
                    source = "html"
        except Exception as e:
            logging.warning(f"Deep analysis: ne mogu dohvatiti {doc.url}: {e}")

    # Sagradi prompt
    situation = getattr(current_user, "situation", "") or ""
    kw_context = f"\nKorisnik je pronašao ovaj dokument po ključnoj riječi: {keyword}." if keyword else ""
    sit_context = f"\nKorisnikova situacija: {situation}" if situation else ""

    if source == "html":
        content_block = f"PUNI TEKST PROPISA:\n{article_text}"
    else:
        content_block = (
            f"Puni tekst nije dostupan. Analiziraj na temelju naslova i metapodataka.\n"
            f"Naslov: {doc.title}\n"
            f"Tip: {doc.type or '—'}\n"
            f"Institucija: {doc.institution or '—'}"
        )

    prompt = (
        f"Analiziraj sljedeći propis iz Narodnih novina.{kw_context}{sit_context}\n\n"
        f"{content_block}\n\n"
        "Odgovori ISKLJUČIVO u ovom JSON formatu (bez ikakvih objašnjenja izvan JSON-a):\n"
        "{\n"
        '  "tko": "Tko može koristiti ili na koga se odnosi propis (2-3 rečenice)",\n'
        '  "iznosi": "Konkretni iznosi, postoci, limiti potpore ili financijske stavke (2-3 rečenice; ako nema, napiši \\"Nije primjenjivo.\\")",\n'
        '  "rokovi": "Rokovi, datumi, procedure prijave i nadležne institucije (2-3 rečenice)",\n'
        '  "paziti": "Najvažnije obveze, zabrane ili sankcije na koje treba paziti (2-3 rečenice)"\n'
        "}"
    )

    try:
        import anthropic as _anthropic
        _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        msg = _client.messages.create(
            model=model,
            max_tokens=800,
            system=(
                "Ti si hrvatski pravni stručnjak. Odgovaraš isključivo u JSON formatu. "
                "Bez markdown, bez koda, samo čisti JSON objekt. "
                "Piši na jednostavnom, razumljivom hrvatskom jeziku bez pravnog žargona."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        raw = msg.content[0].text.strip()
        # Ukloni eventualni markdown code block
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return DeepAnalysisResponse(
            tko=data.get("tko", "—"),
            iznosi=data.get("iznosi", "—"),
            rokovi=data.get("rokovi", "—"),
            paziti=data.get("paziti", "—"),
            source=source,
        )
    except Exception as e:
        logging.error(f"Deep analysis Claude error: {e}")
        raise HTTPException(status_code=500, detail="Greška pri generiranju analize.")
