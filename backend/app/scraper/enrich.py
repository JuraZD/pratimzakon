#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enrichment skript — dopunjava postojeće dokumente u bazi s podacima iz ELI API-ja.
Za svaki dokument koji nema `institution`, konstruira ELI URL iz HTML URL-a,
dohvaća JSON-LD i upisuje: pdf_url, institution, legal_area, date_document.

Pokretanje:
    python -m app.scraper.enrich [--batch 500] [--offset 0] [--dry-run]
"""

import sys
import os
import re
import time
import logging
import argparse
from datetime import datetime

load_env_path = os.path.join(os.path.dirname(__file__), "../../../.env")
import dotenv
dotenv.load_dotenv(load_env_path)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

SLEEP_BETWEEN = 0.4  # sekunde između API poziva
ELI_TIMEOUT = (5, 10)  # (connect, read) timeout u sekundama

# Regex za parsiranje HTML URL-a
# Format: .../clanci/sluzbeni/YYYY_BROJ_SEQ_ID.html
HTML_URL_RE = re.compile(
    r"/clanci/(sluzbeni|medunarodni)/(\d{4})_(\d+)_(\d+)_(\d+)\.html", re.IGNORECASE
)


def _html_url_to_eli_url(html_url: str) -> str | None:
    """Pretvara HTML URL akta u ELI URL za JSON-LD dohvat."""
    m = HTML_URL_RE.search(html_url)
    if not m:
        return None
    section, year, issue, act_id = m.group(1), m.group(2), m.group(3), m.group(5)
    eli_section = "sluzbeni-list" if section == "sluzbeni" else "medunarodni-ugovori"
    # ELI API ne prihvaća zero-padded brojeve (npr. "01" → "1")
    return f"https://narodne-novine.nn.hr/eli/{eli_section}/{year}/{int(issue)}/{int(act_id)}/"


def _fetch_eli_jsonld(url: str, session) -> dict | None:
    """Dohvaća JSON-LD za pojedini akt. Vraća parsed dict ili None."""
    import requests
    try:
        resp = session.get(
            url,
            headers={"Accept": "application/ld+json, application/json;q=0.9"},
            timeout=ELI_TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logging.warning(f"ELI dohvat neuspješan za {url}: {e}")
        return None


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


def _parse_date(datum_str):
    from datetime import date
    if not datum_str:
        return None
    try:
        return date.fromisoformat(str(datum_str)[:10])
    except (ValueError, TypeError):
        return None


def _parse_act_jsonld(data: dict) -> dict:
    """Izvlači institution, pdf_url, legal_area, date_document iz JSON-LD akta."""
    # Pronađi relevantni akt u @graph ili direktno u rootu
    act = data
    if "@graph" in data:
        for item in data["@graph"]:
            if isinstance(item, dict) and any(
                k in item for k in ("eli:title", "eli:passed_by", "eli:is_realized_by")
            ):
                act = item
                break

    institution = _extract_label(act.get("eli:passed_by") or act.get("passed_by", ""))

    pdf_url = ""
    for fmt in act.get("eli:is_realized_by", []) or []:
        if not isinstance(fmt, dict):
            continue
        fmt_type = _extract_label(fmt.get("eli:format") or fmt.get("format", ""))
        url_val = _extract_url(fmt.get("eli:uri") or fmt.get("url") or fmt.get("@id"))
        if not url_val:
            continue
        if "pdf" in fmt_type.lower() or url_val.lower().endswith(".pdf"):
            pdf_url = url_val
            break

    is_about_raw = act.get("eli:is_about") or act.get("is_about", [])
    if isinstance(is_about_raw, list):
        legal_area = ", ".join(
            _extract_label(x) for x in is_about_raw if _extract_label(x)
        )
    else:
        legal_area = _extract_label(is_about_raw)

    date_document = _parse_date(act.get("eli:date_document") or act.get("date_document"))

    return {
        "institution": institution or None,
        "pdf_url": pdf_url or None,
        "legal_area": legal_area or None,
        "date_document": date_document,
    }


def run_enrich(batch: int = 500, offset: int = 0, dry_run: bool = False):
    import requests
    from app.database import SessionLocal
    from app.models import Document, Log

    session = requests.Session()
    session.headers.update({
        "User-Agent": "PratimZakon/2.0 (+https://pratimzakon.hr)",
        "Accept-Language": "hr,en;q=0.9",
    })

    total_updated = 0
    total_skipped = 0
    total_failed = 0

    # Provjeri dostupnost ELI API-ja prije obrade
    try:
        test_resp = session.get(
            "https://narodne-novine.nn.hr/eli/sluzbeni-list/2020/1/67/",
            headers={"Accept": "application/ld+json, application/json;q=0.9"},
            timeout=ELI_TIMEOUT,
        )
        logging.info(f"ELI API connectivity check: HTTP {test_resp.status_code}")
    except Exception as e:
        logging.error(f"ELI API nije dostupan: {e}")
        logging.error("Prekidam — bez pristupa ELI API-ju nema smisla nastaviti.")
        return 0

    try:
        # Ukupan broj dohvatamo u zasebnoj kratkoj sesiji
        with SessionLocal() as count_db:
            total_count = (
                count_db.query(Document)
                .filter(Document.institution.is_(None))
                .count()
            )
        logging.info(f"Dokumenata bez institution: {total_count}, krećem od offseta {offset}")

        processed = 0
        current_offset = offset
        first_doc_logged = False

        while True:
            # Svaki batch dobiva svježu DB sesiju — izbjegavamo SSL timeout
            db = SessionLocal()
            try:
                docs = (
                    db.query(Document)
                    .filter(Document.institution.is_(None))
                    .order_by(Document.id)
                    .offset(current_offset)
                    .limit(batch)
                    .all()
                )
                if not docs:
                    break

                for doc in docs:
                    eli_url = _html_url_to_eli_url(doc.url)
                    if not first_doc_logged:
                        first_doc_logged = True
                        logging.info(f"PRVI DOK HTML URL: {doc.url!r}")
                        logging.info(f"PRVI DOK ELI URL: {eli_url!r}")
                    if not eli_url:
                        logging.debug(f"Ne mogu konstruirati ELI URL za: {doc.url}")
                        total_skipped += 1
                        processed += 1
                        continue

                    data = _fetch_eli_jsonld(eli_url, session)
                    if not data:
                        total_failed += 1
                        processed += 1
                        if total_failed <= 5 or total_failed % 50 == 0:
                            logging.warning(f"  ELI fail #{total_failed}: {eli_url}")
                        time.sleep(SLEEP_BETWEEN)
                        continue

                    enriched = _parse_act_jsonld(data)

                    if not dry_run:
                        if enriched["institution"]:
                            doc.institution = enriched["institution"]
                        if enriched["pdf_url"] and not doc.pdf_url:
                            doc.pdf_url = enriched["pdf_url"]
                        if enriched["legal_area"] and not doc.legal_area:
                            doc.legal_area = enriched["legal_area"]
                        if enriched["date_document"] and not doc.date_document:
                            doc.date_document = enriched["date_document"]

                    total_updated += 1
                    processed += 1

                    if processed % 100 == 0:
                        if not dry_run:
                            db.commit()
                        logging.info(
                            f"  Napredak: {processed}/{total_count - offset} | "
                            f"ažurirano={total_updated}, preskočeno={total_skipped}, greška={total_failed}"
                        )

                    time.sleep(SLEEP_BETWEEN)

                if not dry_run:
                    db.commit()

            finally:
                db.close()

            current_offset += batch

        with SessionLocal() as log_db:
            if not dry_run:
                log_db.add(Log(
                    event_type="enrich",
                    detail=f"Enrichment završen: ažurirano={total_updated}, preskočeno={total_skipped}, greška={total_failed}",
                ))
                log_db.commit()

        logging.info(
            f"Enrichment završen. Ažurirano={total_updated}, preskočeno={total_skipped}, greška={total_failed}"
        )

    finally:
        session.close()

    return total_updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrichment dokumenata iz ELI API-ja")
    parser.add_argument("--batch", type=int, default=500, help="Veličina batcha (default: 500)")
    parser.add_argument("--offset", type=int, default=0, help="Početni offset (default: 0)")
    parser.add_argument("--dry-run", action="store_true", help="Ne upisuj u bazu, samo logiraj")
    args = parser.parse_args()
    run_enrich(args.batch, args.offset, args.dry_run)
