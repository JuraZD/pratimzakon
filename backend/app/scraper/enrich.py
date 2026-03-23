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


_SKOS_LABEL_KEYS = (
    "skos:prefLabel",
    "rdfs:label",
    "eli:name",
    "@value",
    "name",
    # full-URI ključevi iz JSON-LD expanded formata
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "http://www.w3.org/2000/01/rdf-schema#label",
    f"{ELI_NS}name",
)


def _extract_label(obj) -> str:
    """Izvlači string iz ELI/SKOS objekta koji može biti dict, lista ili string."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        if not obj:
            return ""
        for item in obj:
            if isinstance(item, dict) and item.get("@language") in ("hr", "hrv"):
                return item.get("@value", "")
        return _extract_label(obj[0])
    if isinstance(obj, dict):
        for key in _SKOS_LABEL_KEYS:
            if key in obj:
                val = obj[key]
                if isinstance(val, list):
                    return _extract_label(val)
                if isinstance(val, str):
                    return val
    return ""


def _parse_date(datum_str):
    from datetime import date
    if not datum_str:
        return None
    try:
        return date.fromisoformat(str(datum_str)[:10])
    except (ValueError, TypeError):
        return None


def _extract_jsonld_from_html(html_text: str) -> dict | None:
    """Izvlači JSON-LD iz <script type='application/ld+json'> u HTML-u."""
    import json, re
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for m in pattern.finditer(html_text):
        try:
            data = json.loads(m.group(1))
            logging.debug(f"  HTML embedded JSON-LD (200ch): {m.group(1)[:200]!r}")
            return data
        except Exception:
            pass
    return None


def _extract_institution_from_html(html_text: str) -> str | None:
    """Pokušava izvući naziv institucije iz <h2> taga u HTML-u članka NN-a."""
    from html.parser import HTMLParser

    class _H2Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_h2 = False
            self.depth = 0
            self.first_h2 = None

        def handle_starttag(self, tag, attrs):
            if tag == "h2":
                self.in_h2 = True
                self.depth += 1

        def handle_endtag(self, tag):
            if tag == "h2" and self.in_h2:
                self.depth -= 1
                if self.depth <= 0:
                    self.in_h2 = False

        def handle_data(self, data):
            if self.in_h2 and self.first_h2 is None:
                text = data.strip()
                if text:
                    self.first_h2 = text

    p = _H2Parser()
    p.feed(html_text)
    name = p.first_h2
    if name:
        # Ukloni eventualne oznake poput "Na temelju..." koje nisu ime institucije
        if len(name) > 100 or name[0].islower():
            return None
    return name or None


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

    logging.debug(f"  RDFa about-entities={list(parser.types.keys())}")
    logging.debug(f"  RDFa props keys (first 10)={list(parser.props.keys())[:10]}")

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

    # date_publication
    date_pub = parser.props.get((legal_resource, f"{ELI_NS}date_publication"), "")
    if date_pub:
        result["date_publication"] = date_pub

    # passed_by → institution URL
    inst_url = parser.props.get((legal_resource, f"{ELI_NS}passed_by"), "")
    if inst_url:
        result["institution_url"] = inst_url
        # Pokušaj naći institution label direktno u RDFa (skos:prefLabel, rdfs:label, ...)
        _INST_LABEL_PROPS = (
            "skos:prefLabel",
            "rdfs:label",
            "schema:name",
            "http://www.w3.org/2004/02/skos/core#prefLabel",
            "http://www.w3.org/2000/01/rdf-schema#label",
        )
        for lp in _INST_LABEL_PROPS:
            lv = parser.props.get((inst_url, lp), "")
            if lv:
                result["institution_label"] = lv
                logging.debug(f"  RDFa institution_label nađen via {lp!r}: {lv!r}")
                break
        else:
            logging.debug(f"  RDFa institution_label nije u <meta> tagovima")

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
    """Dohvaća naziv institucije iz ELI vocabulary URL-a (s cacheom).
    Pokušava JSON-LD prvo, pa HTML fallback."""
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

    # Pokušaj JSON-LD (NN ELI institution stranice podržavaju content negotiation)
    try:
        resp = session.get(inst_url, timeout=ELI_TIMEOUT,
                           headers={"Accept": "application/ld+json, application/json;q=0.9"})
        if resp.status_code == 200:
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                try:
                    data = resp.json()
                    # JSON-LD može biti list ili dict
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    for key in ("skos:prefLabel", "rdfs:label", "eli:name", "name"):
                        name = _extract_label(data.get(key))
                        if name:
                            _institution_cache[inst_url] = name
                            return name
                except Exception:
                    pass
    except Exception as e:
        logging.debug(f"Institucija JSON-LD neuspješan za {inst_url}: {e}")

    # HTML fallback (pokušaj i kod 404 - stranica može imati sadržaj)
    try:
        resp = session.get(inst_url, timeout=ELI_TIMEOUT,
                           headers={"Accept": "text/html,application/xhtml+xml"})
        logging.debug(f"  _fetch_institution_name HTML status={resp.status_code} len={len(resp.content)} ct={resp.headers.get('content-type','')!r}")
        if resp.status_code not in (200, 404):
            _institution_cache[inst_url] = None
            return None
        # Logiraj dio HTML-a za dijagnostiku
        logging.debug(f"  institution HTML (300ch): {resp.text[:300]!r}")
        # Pokušaj JSON-LD embedded u HTML-u
        embedded = _extract_jsonld_from_html(resp.text)
        if embedded:
            if isinstance(embedded, list):
                for item in embedded:
                    if isinstance(item, dict):
                        lbl = _extract_label(item)
                        if lbl:
                            _institution_cache[inst_url] = lbl
                            return lbl
            elif isinstance(embedded, dict):
                lbl = _extract_label(embedded)
                if lbl:
                    _institution_cache[inst_url] = lbl
                    return lbl
        p = _TitleParser()
        p.feed(resp.text)
        name = p.title.strip()
        # Ukloni suffix " | Narodne novine" i slično
        if " | " in name:
            name = name.split(" | ")[0].strip()
        # Odbaci generičke NN naslove koji nisu ime institucije
        if name.lower() in ("narodne novine", "stranica nije pronađena", "not found", ""):
            name = None
        _institution_cache[inst_url] = name
        return name
    except Exception as e:
        logging.debug(f"Institucija HTML neuspješan za {inst_url}: {e}")
        _institution_cache[inst_url] = None
        return None


def _fetch_jsonld_act(eli_url: str, session) -> dict | None:
    """Dohvaća JSON-LD za pojedini akt putem ELI URL-a (legal_resource)."""
    try:
        resp = session.get(
            eli_url,
            headers={"Accept": "application/ld+json, application/json;q=0.9"},
            timeout=ELI_TIMEOUT,
        )
        if resp.ok:
            ct = resp.headers.get("content-type", "")
            logging.debug(f"  JSON-LD resp ct={ct!r} len={len(resp.content)} url={resp.url}")
            if "json" in ct:
                data = resp.json()
                logging.debug(f"  JSON-LD raw (500 ch): {resp.text[:500]!r}")
                return data
            else:
                logging.debug(f"  JSON-LD preskočen (ct nije json): {resp.text[:200]!r}")
    except Exception as e:
        logging.debug(f"JSON-LD act dohvat neuspješan za {eli_url}: {e}")
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

    html_text = resp.text
    rdfa = _parse_rdfa(html_text)
    if not rdfa:
        return None

    institution = None
    legal_area = None

    # Pokušaj dohvatiti institution i legal_area iz JSON-LD (ELI act URL iz RDFa)
    legal_resource_url = rdfa.get("_legal_resource", "")
    logging.debug(f"  rdfa keys={list(rdfa.keys())} legal_resource={legal_resource_url!r}")

    # Provjeri postoji li JSON-LD embedan direktno u HTML-u članka
    html_embedded_jsonld = _extract_jsonld_from_html(html_text)
    if html_embedded_jsonld:
        logging.debug(f"  HTML embedded JSON-LD nađen")

    if legal_resource_url:
        jsonld = _fetch_jsonld_act(legal_resource_url, session)
        # Ako ELI endpoint vraća samo @id (prazno), pokušaj HTML embedded
        if not jsonld or (isinstance(jsonld, dict) and list(jsonld.keys()) == ["@id"]):
            if html_embedded_jsonld:
                logging.debug(f"  Koristim HTML embedded JSON-LD umjesto ELI endpoint odgovora")
                jsonld = html_embedded_jsonld
        if not jsonld:
            logging.debug(f"  JSON-LD nije vraćen za {legal_resource_url}")
        if jsonld:
            # Izgradi index svih stavki po @id (za cross-reference institucija itd.)
            jsonld_index: dict = {}
            items_iter = jsonld if isinstance(jsonld, list) else jsonld.get("@graph", [])
            for _item in items_iter:
                if isinstance(_item, dict) and "@id" in _item:
                    jsonld_index[_item["@id"].rstrip("/")] = _item

            # Pronađi pravi akt: item čiji @id odgovara legal_resource_url
            norm_url = legal_resource_url.rstrip("/")
            act = jsonld_index.get(norm_url, {})

            # JSON-LD može biti lista na vrhu ili dict s @graph
            if not act and isinstance(jsonld, list):
                # Fallback: traži po tipu LegalResource
                for _item in jsonld:
                    if isinstance(_item, dict):
                        itype = _item.get("@type", "")
                        if "LegalResource" in str(itype):
                            act = _item
                            break
                if not act:
                    act = {}
            elif not act and isinstance(jsonld, dict):
                if "@graph" in jsonld:
                    for _item in jsonld["@graph"]:
                        if isinstance(_item, dict):
                            itype = _item.get("@type", "")
                            if "LegalResource" in str(itype):
                                act = _item
                                break
                    logging.debug(f"  JSON-LD @graph len={len(jsonld['@graph'])}")
                else:
                    act = jsonld

            logging.debug(f"  JSON-LD act @id={act.get('@id', 'N/A')!r} keys={list(act.keys())[:8]}")

            if isinstance(act, dict):
                # Probaj prefixed i full-URI ključeve za passed_by
                passed_by_raw = (
                    act.get("eli:passed_by")
                    or act.get("passed_by")
                    or act.get(f"{ELI_NS}passed_by")
                )
                logging.debug(f"  JSON-LD passed_by_raw={passed_by_raw!r}")

                # Iz passed_by izvuci label ili @id referendu za cross-lookup
                institution = _extract_label(passed_by_raw) or None
                if not institution and passed_by_raw:
                    # passed_by može biti {"@id": "...institutions/XXXXX"} ili lista
                    ref_id = None
                    if isinstance(passed_by_raw, dict):
                        ref_id = passed_by_raw.get("@id", "")
                    elif isinstance(passed_by_raw, list) and passed_by_raw:
                        first = passed_by_raw[0]
                        if isinstance(first, dict):
                            ref_id = first.get("@id", "")
                    if ref_id:
                        inst_item = jsonld_index.get(ref_id.rstrip("/"))
                        if inst_item:
                            institution = _extract_label(inst_item) or None
                            logging.debug(f"  jsonld_index institution lookup → {institution!r}")

                is_about = (
                    act.get("eli:is_about")
                    or act.get("is_about")
                    or act.get(f"{ELI_NS}is_about", [])
                )
                if isinstance(is_about, list):
                    legal_area = ", ".join(
                        _extract_label(x) for x in is_about if _extract_label(x)
                    ) or None
                else:
                    legal_area = _extract_label(is_about) or None
    else:
        logging.debug(f"  Nema _legal_resource u rdfa za {html_url}")

    # Fallback 0: institution_label direktno iz RDFa <meta> tagova
    if not institution:
        institution = rdfa.get("institution_label") or None
        if institution:
            logging.debug(f"  rdfa institution_label → {institution!r}")

    # Fallback 1: Pretraži jsonld_index po institution_url iz RDFa
    if not institution:
        inst_url = rdfa.get("institution_url", "")
        logging.debug(f"  institution_url fallback={inst_url!r}")
        if inst_url and "jsonld_index" in dir():
            inst_item = jsonld_index.get(inst_url.rstrip("/"))
            if inst_item:
                institution = _extract_label(inst_item) or None
                logging.debug(f"  jsonld_index rdfa inst lookup → {institution!r}")

    # Fallback 2: HTML stranica institucije (ELI vocabulary)
    if not institution:
        inst_url = rdfa.get("institution_url", "")
        if inst_url:
            institution = _fetch_institution_name(inst_url, session)
            logging.debug(f"  _fetch_institution_name → {institution!r}")

    # Fallback 3: <h2> tag u HTML-u članka (ako ništa drugo ne radi)
    if not institution:
        institution = _extract_institution_from_html(html_text)
        logging.debug(f"  h2 fallback → {institution!r}")

    return {
        "institution": institution,
        "pdf_url": rdfa.get("pdf_url"),
        "legal_area": legal_area,
        "date_document": _parse_date(rdfa.get("date_document")),
        "published_date": _parse_date(rdfa.get("date_publication")),
        "type_document": rdfa.get("type_document"),
    }


def run_enrich(batch: int = 500, offset: int = 0, dry_run: bool = False):
    import requests
    from sqlalchemy import or_
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

    # Filter: dokumenti kojima nedostaje barem jedno od ključnih polja
    def _incomplete_filter(q):
        return q.filter(
            or_(
                Document.institution.is_(None),
                Document.published_date.is_(None),
            )
        )

    try:
        with SessionLocal() as count_db:
            total_count = _incomplete_filter(count_db.query(Document)).count()
        logging.info(f"Dokumenata bez institution ili published_date: {total_count}, krećem od offseta {offset}")

        processed = 0
        last_id = 0

        # Dohvati ID prvog dokumenta od zadanog offseta
        if offset > 0:
            with SessionLocal() as skip_db:
                skip_doc = (
                    _incomplete_filter(skip_db.query(Document.id))
                    .order_by(Document.id)
                    .offset(offset)
                    .limit(1)
                    .scalar()
                )
                last_id = skip_doc - 1 if skip_doc else 0

        while True:
            db = SessionLocal()
            try:
                docs = (
                    _incomplete_filter(db.query(Document))
                    .filter(Document.id > last_id)
                    .order_by(Document.id)
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
                        if enriched["institution"] and not doc.institution:
                            doc.institution = enriched["institution"]
                        if enriched["pdf_url"] and not doc.pdf_url:
                            doc.pdf_url = enriched["pdf_url"]
                        if enriched["legal_area"] and not doc.legal_area:
                            doc.legal_area = enriched["legal_area"]
                        if enriched["date_document"] and not doc.date_document:
                            doc.date_document = enriched["date_document"]
                        if enriched["published_date"] and not doc.published_date:
                            doc.published_date = enriched["published_date"]
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

                last_id = docs[-1].id

            finally:
                db.close()

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
    parser.add_argument("--debug", action="store_true", help="Uključi DEBUG razinu logiranja")
    args = parser.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    run_enrich(args.batch, args.offset, args.dry_run)
