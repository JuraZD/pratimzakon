#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NN API Scraper — koristi službeni Narodne novine REST API.
Zamjenjuje nn_scraper.py + enrich.py.

Modovi:
  backfill  — povlači sve od 2015. do danas (jednokratno)
  daily     — povlači samo nova izdanja tekuće godine (dnevni cron)

Pokretanje:
  python -m app.scraper.api_scraper backfill [--from 2015] [--to 2026] [--dry-run]
  python -m app.scraper.api_scraper daily [--dry-run]
"""

import asyncio
import aiohttp
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from typing import Optional

import dotenv

dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

BASE_URL = "https://narodne-novine.nn.hr"
RATE_LIMIT = 3   # službeni NN API limit (req/s)
PARTS = ("SL", "MU")

# Regex za izvlačenje act_num iz HTML URL-a
# Format: /clanci/sluzbeni/YYYY_ISSUE_SEQ_ACTNUM.html
ACT_NUM_RE = re.compile(r"_(\d+)\.html$")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def _get(session: aiohttp.ClientSession, sem: asyncio.Semaphore, path: str):
    async with sem:
        try:
            async with session.get(f"{BASE_URL}{path}") as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as e:
            logging.warning(f"GET {path} neuspješan: {e}")
            return None


async def _post(session: aiohttp.ClientSession, sem: asyncio.Semaphore, path: str, payload: dict):
    async with sem:
        try:
            async with session.post(f"{BASE_URL}{path}", json=payload) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as e:
            logging.warning(f"POST {path} {payload} neuspješan: {e}")
            return None


# ── API pozivi ────────────────────────────────────────────────────────────────

async def fetch_years(session, sem) -> list[int]:
    data = await _get(session, sem, "/api/index")
    if isinstance(data, list):
        return [int(y) for y in data if str(y).isdigit()]
    return []


async def fetch_editions(session, sem, part: str, year: int) -> list[int]:
    data = await _post(session, sem, "/api/editions", {"part": part, "year": year})
    if isinstance(data, list):
        return [int(e) for e in data if str(e).isdigit()]
    return []


async def fetch_acts(session, sem, part: str, year: int, number: int) -> list[int]:
    data = await _post(session, sem, "/api/acts", {"part": part, "year": year, "number": number})
    if isinstance(data, list):
        return [int(a) for a in data if str(a).isdigit()]
    return []


async def fetch_act_jsonld(session, sem, part: str, year: int, number: int, act_num: int) -> Optional[dict]:
    return await _post(session, sem, "/api/act", {
        "part": part, "year": year, "number": number,
        "act_num": act_num, "format": "JSON-LD",
    })


# ── JSON-LD parsiranje ────────────────────────────────────────────────────────

def _extract_label(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return _extract_label(obj[0]) if obj else ""
    if isinstance(obj, dict):
        for key in ("skos:prefLabel", "rdfs:label", "eli:name", "@value", "name"):
            if key in obj:
                val = obj[key]
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and item.get("@language") in ("hr", "hrv"):
                            return item.get("@value", "")
                    return _extract_label(val[0])
                if isinstance(val, str):
                    return val
    return ""


def _extract_url(obj) -> str:
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return obj.get("@id", obj.get("url", obj.get("eli:url", "")))
    return ""


def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def parse_act_jsonld(data, part: str, year: int, number: int, act_num: int) -> dict:
    """Izvlači sve relevantne podatke iz JSON-LD akta."""
    # API ponekad vraća listu umjesto dict-a.
    # Tražimo dict koji sadrži stvarne podatke akta (ne @context objekt).
    if isinstance(data, list):
        relevant_keys = ("@graph", "eli:title", "eli:passed_by", "eli:is_realized_by")
        data = next(
            (item for item in data if isinstance(item, dict) and any(k in item for k in relevant_keys)),
            next((item for item in data if isinstance(item, dict)), {}),
        )

    act = data
    if "@graph" in data:
        for item in data["@graph"]:
            if isinstance(item, dict) and any(
                k in item for k in ("eli:title", "dcterms:title", "eli:passed_by", "eli:is_realized_by")
            ):
                act = item
                break

    title = _extract_label(act.get("eli:title") or act.get("dcterms:title") or act.get("title", ""))
    if not title:
        logging.warning(
            f"Prazan naslov {part} {year}/{number} akt {act_num} | "
            f"tip={type(data).__name__} | "
            f"ključevi={list(act.keys()) if isinstance(act, dict) else repr(act)[:300]}"
        )
    institution = _extract_label(act.get("eli:passed_by") or act.get("passed_by", ""))

    doc_type_raw = _extract_label(act.get("eli:type_document") or act.get("type_document", ""))
    doc_type = doc_type_raw.upper() if doc_type_raw else None

    pdf_url = ""
    html_url = ""
    for fmt in act.get("eli:is_realized_by", []) or []:
        if not isinstance(fmt, dict):
            continue
        fmt_type = _extract_label(fmt.get("eli:format") or fmt.get("format", ""))
        url_val = _extract_url(fmt.get("eli:uri") or fmt.get("url") or fmt.get("@id"))
        if not url_val:
            continue
        if "pdf" in fmt_type.lower() or url_val.lower().endswith(".pdf"):
            if not pdf_url:
                pdf_url = url_val
        elif "html" in fmt_type.lower() or url_val.lower().endswith(".html"):
            if not html_url:
                html_url = url_val

    is_about_raw = act.get("eli:is_about") or act.get("is_about", [])
    if isinstance(is_about_raw, list):
        legal_area = ", ".join(_extract_label(x) for x in is_about_raw if _extract_label(x))
    else:
        legal_area = _extract_label(is_about_raw)

    date_document = _parse_date(act.get("eli:date_document") or act.get("date_document"))
    published_date = _parse_date(act.get("eli:date_publication") or act.get("date_publication"))

    eli_section = "sluzbeni-list" if part == "SL" else "medunarodni-ugovori"
    eli_url = f"{BASE_URL}/eli/{eli_section}/{year}/{number}/{act_num}/"

    return {
        "title": title,
        "url": html_url or eli_url,  # HTML URL ako postoji, inače ELI
        "pdf_url": pdf_url or None,
        "type": doc_type,
        "institution": institution or None,
        "legal_area": legal_area or None,
        "date_document": date_document,
        "published_date": published_date,
        "part": part,
        "issue_number": number,
        "act_num": str(act_num),
    }


# ── DB upsert ─────────────────────────────────────────────────────────────────

def build_lookup(db) -> dict[str, int]:
    """Gradi mapu act_num -> document.id za brzo pronalaženje postojećih dokumenata."""
    from app.models import Document
    lookup = {}
    for doc_id, url in db.query(Document.id, Document.url).all():
        m = ACT_NUM_RE.search(url)
        if m:
            lookup[m.group(1)] = doc_id
    return lookup


def upsert_document(db, parsed: dict, lookup: dict) -> str:
    """Ažurira postojeći ili umeće novi dokument. Vraća 'updated', 'inserted' ili 'skipped'."""
    from app.models import Document

    act_num = parsed["act_num"]
    existing_id = lookup.get(act_num)

    if isinstance(existing_id, int):
        doc = db.get(Document, existing_id)
        if doc:
            if parsed["institution"]:
                doc.institution = parsed["institution"]
            if parsed["pdf_url"]:
                doc.pdf_url = parsed["pdf_url"]
            if parsed["type"]:
                doc.type = parsed["type"]
            if parsed["date_document"]:
                doc.date_document = parsed["date_document"]
            if parsed["legal_area"]:
                doc.legal_area = parsed["legal_area"]
            if parsed["published_date"] and not doc.published_date:
                doc.published_date = parsed["published_date"]
            return "updated"

    if existing_id is not None:
        # već insertan u ovom runu
        return "skipped"

    if not parsed["title"]:
        return "skipped"

    db.add(Document(
        title=parsed["title"],
        url=parsed["url"],
        pdf_url=parsed["pdf_url"],
        type=parsed["type"],
        institution=parsed["institution"],
        legal_area=parsed["legal_area"],
        date_document=parsed["date_document"],
        published_date=parsed["published_date"],
        part=parsed["part"],
        issue_number=parsed["issue_number"],
    ))
    lookup[act_num] = True  # označi kao obrađen
    return "inserted"


# ── Core runner ───────────────────────────────────────────────────────────────

async def _fetch_by_url(session: aiohttp.ClientSession, sem: asyncio.Semaphore, url: str) -> Optional[dict]:
    """Dohvaća punu JSON-LD reprezentaciju akta direktno s URL-a (za @id reference).
    Pokušaj 1: Accept: application/ld+json (brzo, direktno).
    Pokušaj 2: HTML stranica + izvlačenje embedded JSON-LD (fallback za nove objave).
    """
    import json as _json, re as _re

    # Pokušaj 1: JSON-LD Accept header
    async with sem:
        try:
            async with session.get(url, headers={"Accept": "application/ld+json"}) as resp:
                if resp.status != 404:
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
        except Exception as e:
            logging.warning(f"GET JSON-LD {url} neuspješan: {e}")
            return None

    # Pokušaj 2: HTML + embedded JSON-LD (za akte koji još nemaju ELI JSON-LD)
    async with sem:
        try:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                html = await resp.text()
                m = _re.search(
                    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, _re.DOTALL
                )
                if m:
                    return _json.loads(m.group(1))
        except Exception as e:
            logging.warning(f"GET HTML {url} neuspješan: {e}")

    return None


async def _process_edition(session, sem, db, lookup, part: str, year: int, number: int) -> tuple[int, int, int]:
    """Obradi jedno izdanje. Vraća (updated, inserted, failed)."""
    act_nums = await fetch_acts(session, sem, part, year, number)
    if not act_nums:
        return 0, 0, 0

    results = await asyncio.gather(*[
        fetch_act_jsonld(session, sem, part, year, number, a) for a in act_nums
    ])

    updated = inserted = failed = 0
    for act_num, data in zip(act_nums, results):
        if not data:
            failed += 1
            continue
        # API ponekad vraća samo {'@id': url} — dohvati punu JSON-LD via URL
        if isinstance(data, dict) and set(data.keys()) == {"@id"}:
            data = await _fetch_by_url(session, sem, data["@id"]) or data
        parsed = parse_act_jsonld(data, part, year, number, act_num)
        outcome = upsert_document(db, parsed, lookup)
        if outcome == "updated":
            updated += 1
        elif outcome == "inserted":
            inserted += 1
        else:
            failed += 1

    db.commit()
    return updated, inserted, failed


async def _run(year_from: int, year_to: int, dry_run: bool = False, min_editions: dict = None):
    from app.database import SessionLocal
    from app.models import Log, Document

    db = SessionLocal()
    lookup = build_lookup(db)
    logging.info(f"Učitano {len(lookup)} postojećih dokumenata iz baze")

    run_start = datetime.now(timezone.utc).replace(tzinfo=None)
    total_updated = total_inserted = total_failed = 0

    headers = {
        "User-Agent": "PratimZakon/2.0 (+https://pratimzakon.hr)",
        "Content-Type": "application/json",
    }
    sem = asyncio.Semaphore(RATE_LIMIT)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10),
        headers=headers,
    ) as session:
        logging.info(f"Obrada {year_from}–{year_to}, dijelovi: {PARTS}")

        for year in range(year_from, year_to + 1):
            for part in PARTS:
                editions = await fetch_editions(session, sem, part, year)
                if not editions:
                    logging.info(f"  {part} {year}: nema izdanja")
                    continue

                # Filtriraj samo nova izdanja (ona koja nisu u bazi)
                # Buffer od 2 unazad za sigurnost (u slučaju djelomično obrađenih)
                if min_editions and min_editions.get(part, 0) > 0:
                    cutoff = max(0, min_editions[part] - 2)
                    to_process = sorted(e for e in editions if e > cutoff)
                    if to_process:
                        logging.info(
                            f"{part} {year}: {len(to_process)} novih izdanja u godini : {len(editions)}"
                        )
                    else:
                        logging.info(f"{part} {year}: nema novih izdanja")
                        continue
                else:
                    to_process = sorted(editions)
                    logging.info(f"{part} {year}: {len(to_process)} izdanja")

                for number in to_process:
                    if dry_run:
                        logging.info(f"    [dry-run] {part} {year}/{number}")
                        continue
                    u, i, f = await _process_edition(session, sem, db, lookup, part, year, number)
                    total_updated += u
                    total_inserted += i
                    total_failed += f
                    logging.info(
                        f"    {part} {year}/{number}: "
                        f"ažurirano={u}, novo={i}, greška={f}"
                    )

    if not dry_run:
        if total_inserted > 0:
            from app.email.notifier import send_keyword_notifications
            new_docs = db.query(Document.id).filter(Document.created_at >= run_start).all()
            new_ids = [d.id for d in new_docs]
            if new_ids:
                logging.info(f"Slanje notifikacija za {len(new_ids)} novih dokumenata")
                send_keyword_notifications(new_ids, db)

        db.add(Log(
            event_type="api_scraper",
            detail=(
                f"API scraper {year_from}–{year_to}: "
                f"ažurirano={total_updated}, novo={total_inserted}, greška={total_failed}"
            ),
        ))
        db.commit()

    db.close()
    logging.info(
        f"Završeno. ažurirano={total_updated}, novo={total_inserted}, greška={total_failed}"
    )


def run_backfill(year_from: int = 2015, year_to: int = None, dry_run: bool = False):
    if year_to is None:
        year_to = datetime.now().year
    asyncio.run(_run(year_from, year_to, dry_run))


def run_daily(dry_run: bool = False):
    """
    Dnevni mod — dohvaća samo nova izdanja koja još nisu u bazi.
    Provjerava zadnji poznati broj u bazi za SL i MU, pa obrađuje
    samo izdanja viša od toga (uz buffer od 2 za sigurnost).
    Ako je siječanj, uključuje i prethodnu godinu.
    """
    from app.database import SessionLocal
    from app.models import Document
    from sqlalchemy import func
    from datetime import date as date_type

    now = datetime.now()
    year_from = now.year - 1 if now.month == 1 else now.year

    db = SessionLocal()
    min_editions: dict[str, int] = {}
    for part in PARTS:
        val = db.query(func.max(Document.issue_number)).filter(
            Document.part == part,
            Document.published_date >= date_type(year_from, 1, 1),
        ).scalar()
        min_editions[part] = val or 0
        logging.info(f"Zadnji {part} broj u bazi ({year_from}+): {min_editions[part]}")
    db.close()

    asyncio.run(_run(year_from, now.year, dry_run, min_editions=min_editions))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NN API Scraper")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    bp = subparsers.add_parser("backfill", help="Jednokratni backfill 2015–danas")
    bp.add_argument("--from", dest="year_from", type=int, default=2015)
    bp.add_argument("--to", dest="year_to", type=int, default=None)
    bp.add_argument("--dry-run", action="store_true")

    dp = subparsers.add_parser("daily", help="Dnevni scraper (tekuća godina)")
    dp.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.mode == "backfill":
        run_backfill(args.year_from, args.year_to, args.dry_run)
    else:
        run_daily(args.dry_run)
