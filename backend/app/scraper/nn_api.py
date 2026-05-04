#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Klijent za Narodne novine API (https://narodne-novine.nn.hr/api)
Dokumentacija: https://narodne-novine.nn.hr/api (besplatno, 3 req/s)
"""

import time
import logging
from datetime import date
from typing import List, Optional, Dict

import requests

logger = logging.getLogger(__name__)

NN_API_BASE = "https://narodne-novine.nn.hr/api"
# Limit: 3 zahtjeva u sekundi → čekamo minimalno 0.35s između poziva
_MIN_DELAY = 0.35


class NarodneNovineAPI:
    """Wrapper za Narodne novine REST API."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._last_call = 0.0

    def _throttle(self):
        """Pazi na ograničenje od 3 req/s."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < _MIN_DELAY:
            time.sleep(_MIN_DELAY - elapsed)
        self._last_call = time.monotonic()

    def get_available_years(self) -> List[int]:
        """
        GET /api/index
        Vraća listu godina za koje postoje ELI metapodatci.
        """
        self._throttle()
        try:
            r = self.session.get(f"{NN_API_BASE}/index", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"[NN API] Greška pri dohvaćanju godina: {e}")
            return []

    def get_editions(self, year: int, part: str = "SL") -> List[int]:
        """
        POST /api/editions
        Vraća listu brojeva izdanja za traženu godinu.
        part: "SL" (službeni) ili "MU" (međunarodni)
        """
        self._throttle()
        try:
            r = self.session.post(
                f"{NN_API_BASE}/editions",
                json={"part": part, "year": year},
                timeout=15,
            )
            r.raise_for_status()
            return sorted(r.json())
        except Exception as e:
            logger.error(f"[NN API] Greška pri dohvaćanju izdanja za {year}: {e}")
            return []

    def get_acts(self, year: int, number: int, part: str = "SL") -> List[str]:
        """
        POST /api/acts
        Vraća listu brojeva propisa u određenom izdanju.
        """
        self._throttle()
        try:
            r = self.session.post(
                f"{NN_API_BASE}/acts",
                json={"part": part, "year": year, "number": number},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"[NN API] Greška pri dohvaćanju propisa za {number}/{year}: {e}")
            return []

    def get_act_metadata(
        self, year: int, number: int, act_num: str, part: str = "SL"
    ) -> Optional[Dict]:
        """
        POST /api/act
        Vraća JSON-LD metapodatke za jedan propis.
        Parsira u rječnik s ključevima: title, url, type, published_date
        """
        self._throttle()
        try:
            r = self.session.post(
                f"{NN_API_BASE}/act",
                json={
                    "part": part,
                    "year": year,
                    "number": number,
                    "act_num": act_num,
                    "format": "JSON-LD",
                },
                timeout=15,
            )
            r.raise_for_status()
            return _parse_jsonld(r.json(), year, number, act_num)
        except Exception as e:
            logger.error(f"[NN API] Greška pri dohvaćanju akta {act_num} ({number}/{year}): {e}")
            return None

    def get_latest_edition(self, year: int, part: str = "SL") -> Optional[int]:
        """Vraća broj zadnjeg objavljenog izdanja za danu godinu."""
        editions = self.get_editions(year, part)
        return max(editions) if editions else None


def _parse_jsonld(data: list, year: int, number: int, act_num: str) -> Optional[Dict]:
    """
    Parsira JSON-LD odgovor NN API-ja u jednostavan rječnik.
    Traži entitet tipa LegalExpression (sadrži naslov i formate) i
    entitet tipa LegalResource (sadrži datum i tip dokumenta).
    """
    ELI = "http://data.europa.eu/eli/ontology#"

    legal_resource = None
    legal_expression = None
    html_url = None
    pdf_url = None

    for item in data:
        types = [t for t in item.get("@type", [])]
        if f"{ELI}LegalResource" in types:
            legal_resource = item
        elif f"{ELI}LegalExpression" in types:
            legal_expression = item
        elif f"{ELI}Format" in types:
            fmt_id = item.get("@id", "")
            fmt_type = item.get(f"{ELI}format", [{}])[0].get("@id", "")
            if fmt_id.endswith("/html") and "text/html" in fmt_type:
                html_url = fmt_id
            elif fmt_id.endswith("/pdf") and "application/pdf" in fmt_type:
                pdf_url = fmt_id

    if not legal_expression:
        return None

    # Naslov
    title_list = legal_expression.get(f"{ELI}title", [])
    title = title_list[0].get("@value", "") if title_list else ""

    # URL dokumenta (HTML verzija)
    if not html_url:
        embodied = legal_expression.get(f"{ELI}is_embodied_by", [])
        if embodied:
            html_url = embodied[0].get("@id", "")

    # Datum i tip dokumenta
    published_date = None
    doc_type = ""
    institution = None
    if legal_resource:
        SKOS = "http://www.w3.org/2004/02/skos/core#"

        # Datum objave: date_publication (primarno) ili date_document (fallback)
        for date_field in (f"{ELI}date_publication", f"{ELI}date_document"):
            date_list = legal_resource.get(date_field, [])
            if date_list:
                try:
                    published_date = date.fromisoformat(date_list[0].get("@value", ""))
                    break
                except (ValueError, AttributeError):
                    pass

        type_doc = legal_resource.get(f"{ELI}type_document", [{}])[0].get("@id", "")
        # URL oblika: .../document-type/UREDBA → izvuci "UREDBA"
        if "/" in type_doc:
            doc_type = type_doc.rsplit("/", 1)[-1]

        # Institucija koja je donijela propis (eli:passed_by)
        passed_by = legal_resource.get(f"{ELI}passed_by", [])
        if passed_by:
            pb = passed_by[0] if isinstance(passed_by, list) else passed_by
            if isinstance(pb, dict):
                for key in (f"{SKOS}prefLabel", "skos:prefLabel", "rdfs:label", f"{ELI}name", "name", "@value"):
                    labels = pb.get(key)
                    if labels:
                        if isinstance(labels, list):
                            for lbl in labels:
                                if isinstance(lbl, dict) and lbl.get("@language") in ("hr", "hrv"):
                                    institution = lbl.get("@value")
                                    break
                            if not institution:
                                first = labels[0]
                                institution = first.get("@value", str(first)) if isinstance(first, dict) else str(first)
                        elif isinstance(labels, str):
                            institution = labels
                        if institution:
                            break

    if not title or not html_url:
        return None

    return {
        "title": title,
        "url": html_url,
        "pdf_url": pdf_url,
        "type": doc_type,
        "institution": institution,
        "published_date": published_date,
        "issue_number": number,
        "act_num": act_num,
    }
