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
# Flag da li je lista svih institucija već dohvaćena
_institution_list_fetched: bool = False

# Prefiksi naslova dokumenata koji nisu nazivi institucija
_DOC_TYPE_PREFIXES = (
    "Odluka", "Pravilnik", "Uredba", "Zakon", "Naredba", "Rješenje", "Naputak",
    "Statut", "Plan", "Program", "Strategija", "Ugovor", "Sporazum", "Protokol",
    "Pravilni", "Opći", "Posebni", "Izmjen", "Dopun", "Na temelju", "Temeljem",
)


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
        # Odbaci preduge tekstove i one koji počinju malim slovom
        if len(name) > 120 or name[0].islower():
            return None
        # Odbaci naslove dokumenata (počinju tipom dokumenta, ne imenom institucije)
        for prefix in _DOC_TYPE_PREFIXES:
            if name.startswith(prefix):
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
                break

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


def _parse_institution_xml(xml_bytes: bytes) -> int:
    """Parsira RDF/XML SKOS vocabulary s listom institucija. Vraća broj učitanih."""
    import xml.etree.ElementTree as ET
    SKOS = "http://www.w3.org/2004/02/skos/core#"
    RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    count = 0
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logging.debug(f"  institution XML parse error: {e}")
        return 0
    # Traži sve Concept elemente — mogu biti direktna djeca ili ugniježđena
    for elem in root.iter():
        # rdf:about atribut → URI institucije
        about = elem.get(f"{{{RDF}}}about", "")
        if not about or "nn-institutions" not in about:
            continue
        # Traži skos:prefLabel (preferiramo hr jezik)
        label = ""
        for child in elem:
            if child.tag == f"{{{SKOS}}}prefLabel":
                lang = child.get("{http://www.w3.org/XML/1998/namespace}lang", "")
                if child.text:
                    if lang in ("hr", "hrv") or not label:
                        label = child.text.strip()
                        if lang in ("hr", "hrv"):
                            break
        if label:
            _institution_cache[about.rstrip("/")] = label
            count += 1
    return count


def _prefetch_institution_list(session) -> None:
    """Jednokratni dohvat liste svih institucija iz NN ELI vocabulary.
    Puni _institution_cache za sve institucije odjednom."""
    global _institution_list_fetched
    if _institution_list_fetched:
        return
    _institution_list_fetched = True

    base_url = "https://narodne-novine.nn.hr/eli/vocabularies/nn-institutions"
    try:
        resp = session.get(
            base_url,
            headers={"Accept": "application/rdf+xml, application/xml;q=0.9, */*;q=0.8"},
            timeout=(5, 30),  # dulji read timeout za 2MB
        )
        ct = resp.headers.get("content-type", "")
        logging.debug(f"  institution list fetch: {base_url} → {resp.status_code} ct={ct!r} len={len(resp.content)}")
        if resp.status_code != 200:
            return
        if "xml" in ct or "rdf" in ct:
            count = _parse_institution_xml(resp.content)
            logging.info(f"  institution list: {count} institucija učitano iz XML")
            return
        if "json" in ct:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("@graph", [data])
            count = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("@id", "")
                if not item_id or "nn-institutions" not in item_id:
                    continue
                label = _extract_label(item)
                if label:
                    _institution_cache[item_id.rstrip("/")] = label
                    count += 1
            logging.info(f"  institution list: {count} institucija učitano iz JSON")
    except Exception as e:
        logging.debug(f"  institution list fetch failed: {e}")


def _fetch_institution_name(inst_url: str, session) -> str | None:
    """Dohvaća naziv institucije iz ELI vocabulary URL-a (s cacheom).
    Pokušava JSON-LD prvo, pa HTML fallback."""
    norm = inst_url.rstrip("/")
    if norm in _institution_cache:
        return _institution_cache[norm]
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

    # HTML fallback
    try:
        resp = session.get(inst_url, timeout=ELI_TIMEOUT,
                           headers={"Accept": "text/html,application/xhtml+xml"})
        if resp.status_code not in (200, 404):
            _institution_cache[inst_url] = None
            return None
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
            if "json" in ct:
                return resp.json()
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

    if legal_resource_url:
        jsonld = _fetch_jsonld_act(legal_resource_url, session)
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

            if isinstance(act, dict):
                # Probaj prefixed i full-URI ključeve za passed_by
                passed_by_raw = (
                    act.get("eli:passed_by")
                    or act.get("passed_by")
                    or act.get(f"{ELI_NS}passed_by")
                )
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
    # Fallback: institution_label iz RDFa <meta> tagova
    if not institution:
        institution = rdfa.get("institution_label") or None

    # Fallback: XML vocabulary cache (via _fetch_institution_name koji koristi _institution_cache)
    if not institution:
        inst_url = rdfa.get("institution_url", "")
        if inst_url:
            institution = _fetch_institution_name(inst_url, session)

    # Fallback: <h2> tag u HTML-u članka
    if not institution:
        institution = _extract_institution_from_html(html_text)

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

    # Jednokratni prefetch liste svih institucija
    _prefetch_institution_list(session)

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
