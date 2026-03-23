#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enrichment skript — dopunjava postojeće dokumente u bazi s podacima iz ELI RDFa metapodataka.
Narodne novine embedaju ELI metapodatke kao RDFa <meta> tagove u HTML stranicama.

Dohvaća: institution, pdf_url, date_document iz HTML stranice dokumenta.

Pokretanje:
    python -m app.scraper.enrich [--batch 500] [--offset 0] [--dry-run]
"""

import sys
import os
import time
import logging
import argparse

load_env_path = os.path.join(os.path.dirname(__file__), "../../../.env")
import dotenv
dotenv.load_dotenv(load_env_path)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

SLEEP_BETWEEN = 0.4  # sekunde između zahtjeva
ELI_TIMEOUT = (5, 10)  # (connect, read) timeout u sekundama
ELI_NS = "http://data.europa.eu/eli/ontology#"

# Cache za ime institucije (institution URL → naziv)
_institution_cache: dict = {}


def _parse_date(datum_str):
    from datetime import date
    if not datum_str:
        return None
    try:
        return date.fromisoformat(str(datum_str)[:10])
    except (ValueError, TypeError):
        return None


def _parse_rdfa(html_text: str) -> dict:
    """Parsira ELI RDFa <meta> tagove iz HTML stranice. Vraća dict s metapodacima."""
    from html.parser import HTMLParser

    class _Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.props = {}   # (about, property) -> value (content ili resource)
            self.types = {}   # about -> typeof

        def handle_starttag(self, tag, attrs):
            if tag != "meta":
                return
            d = dict(attrs)
            about = d.get("about", "")
            prop = d.get("property", "")
            typeof = d.get("typeof", "")
            content = d.get("content", "")
            resource = d.get("resource", "")
            if about and prop:
                self.props[(about, prop)] = content or resource
            if about and typeof:
                self.types[about] = typeof

    parser = _Parser()
    parser.feed(html_text)

    # Pronađi LegalResource (glavni dokument)
    legal_resource = None
    for about, typeof in parser.types.items():
        if "LegalResource" in typeof:
            legal_resource = about
            break

    if not legal_resource:
        return {}

    result = {"_legal_resource": legal_resource}

    # date_document
    date_val = parser.props.get((legal_resource, f"{ELI_NS}date_document"), "")
    if date_val:
        result["date_document"] = date_val

    # passed_by → institution URL
    inst_url = parser.props.get((legal_resource, f"{ELI_NS}passed_by"), "")
    if inst_url:
        result["institution_url"] = inst_url

    # type_document → zadnji segment URL-a (npr. ODLUKA, ZAKON, UREDBA...)
    type_url = parser.props.get((legal_resource, f"{ELI_NS}type_document"), "")
    if type_url:
        result["type_document"] = type_url.rstrip("/").split("/")[-1]

    # PDF URL — traži meta tag s format = application/pdf
    for (about, prop), value in parser.props.items():
        if prop == f"{ELI_NS}format" and "pdf" in value.lower():
            result["pdf_url"] = about
            break

    return result


def _fetch_institution_name(inst_url: str, session) -> str | None:
    """Dohvaća naziv institucije iz ELI vocabulary URL-a (s cacheom)."""
    if inst_url in _institution_cache:
        return _institution_cache[inst_url]

    from html.parser import HTMLParser

    class _TitleParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_title = False
            self.title = ""
        def handle_starttag(self, tag, attrs):
            if tag == "title":
                self.in_title = True
        def handle_endtag(self, tag):
            if tag == "title":
                self.in_title = False
        def handle_data(self, data):
            if self.in_title:
                self.title += data

    try:
        resp = session.get(inst_url, timeout=ELI_TIMEOUT,
                           headers={"Accept": "text/html,application/xhtml+xml"})
        if resp.status_code != 200:
            _institution_cache[inst_url] = None
            return None
        p = _TitleParser()
        p.feed(resp.text)
        name = p.title.strip() or None
        _institution_cache[inst_url] = name
        return name
    except Exception as e:
        logging.debug(f"Institucija dohvat neuspješan za {inst_url}: {e}")
        _institution_cache[inst_url] = None
        return None


def _enrich_doc(html_url: str, session) -> dict | None:
    """Dohvaća HTML stranicu i vraća enrichment dict ili None ako nije uspjelo."""
    try:
        resp = session.get(
            html_url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=ELI_TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except Exception as e:
        logging.warning(f"Dohvat neuspješan za {html_url}: {e}")
        return None

    rdfa = _parse_rdfa(resp.text)
    if not rdfa:
        return None

    institution = None
    inst_url = rdfa.get("institution_url", "")
    if inst_url:
        institution = _fetch_institution_name(inst_url, session)

    return {
        "institution": institution,
        "pdf_url": rdfa.get("pdf_url"),
        "legal_area": None,  # nije dostupno u RDFa
        "date_document": _parse_date(rdfa.get("date_document")),
        "type_document": rdfa.get("type_document"),
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

    # Provjeri dostupnost narodne-novine.nn.hr
    try:
        test_resp = session.get(
            "https://narodne-novine.nn.hr/clanci/sluzbeni/2020_01_10_67.html",
            headers={"Accept": "text/html"},
            timeout=ELI_TIMEOUT,
        )
        logging.info(f"Connectivity check: HTTP {test_resp.status_code}")
        if test_resp.status_code >= 400:
            logging.error("narodne-novine.nn.hr nije dostupan. Prekidam.")
            return 0
    except Exception as e:
        logging.error(f"narodne-novine.nn.hr nije dostupan: {e}")
        return 0

    try:
        with SessionLocal() as count_db:
            total_count = (
                count_db.query(Document)
                .filter(Document.institution.is_(None))
                .count()
            )
        logging.info(f"Dokumenata bez institution: {total_count}, krećem od offseta {offset}")

        processed = 0
        current_offset = offset

        while True:
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
                    enriched = _enrich_doc(doc.url, session)

                    if enriched is None:
                        total_failed += 1
                        processed += 1
                        if total_failed <= 5 or total_failed % 100 == 0:
                            logging.warning(f"  Fail #{total_failed}: {doc.url}")
                        time.sleep(SLEEP_BETWEEN)
                        continue

                    if not dry_run:
                        if enriched["institution"]:
                            doc.institution = enriched["institution"]
                        if enriched["pdf_url"] and not doc.pdf_url:
                            doc.pdf_url = enriched["pdf_url"]
                        if enriched["legal_area"] and not doc.legal_area:
                            doc.legal_area = enriched["legal_area"]
                        if enriched["date_document"] and not doc.date_document:
                            doc.date_document = enriched["date_document"]
                        if enriched["type_document"] and not doc.type:
                            doc.type = enriched["type_document"]

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
    parser = argparse.ArgumentParser(description="Enrichment dokumenata iz ELI RDFa metapodataka")
    parser.add_argument("--batch", type=int, default=500, help="Veličina batcha (default: 500)")
    parser.add_argument("--offset", type=int, default=0, help="Početni offset (default: 0)")
    parser.add_argument("--dry-run", action="store_true", help="Ne upisuj u bazu, samo logiraj")
    args = parser.parse_args()
    run_enrich(args.batch, args.offset, args.dry_run)
