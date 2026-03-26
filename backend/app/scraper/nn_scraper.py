#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper za Narodne novine – PratimZakon verzija
Primarno koristi JSON-LD (ELI) API, HTML scraping kao fallback.
"""

import re
import sys
import time
import logging
import os
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)


def _get_db_session():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))
    from app.database import SessionLocal
    return SessionLocal()


def _parse_date(datum_str: Optional[str]) -> Optional[date]:
    """Parsira ISO datum (2025-01-15) ili tekstualni oblik u date objekt."""
    if not datum_str:
        return None
    # ISO format
    try:
        return date.fromisoformat(datum_str[:10])
    except (ValueError, TypeError):
        pass
    # Tekstualni hrvatski format
    months = {
        "siječnja": 1, "veljače": 2, "ožujka": 3, "travnja": 4,
        "svibnja": 5, "lipnja": 6, "srpnja": 7, "kolovoza": 8,
        "rujna": 9, "listopada": 10, "studenog": 11, "prosinca": 12,
    }
    m = re.search(r"(\d{1,2})\.\s+(\w+)\s+(\d{4})", str(datum_str))
    if m:
        day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = months.get(month_name)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


def _extract_label(obj) -> str:
    """Izvlači string iz ELI/SKOS objekta koji može biti dict, lista ili string."""
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
                    # uzmi hrvatski ako postoji, inače prvi
                    for item in val:
                        if isinstance(item, dict) and item.get("@language") in ("hr", "hrv"):
                            return item.get("@value", "")
                    return _extract_label(val[0])
                if isinstance(val, str):
                    return val
    return ""


def _extract_url(obj) -> str:
    """Izvlači URL string iz ELI objekta."""
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return obj.get("@id", obj.get("url", obj.get("eli:url", "")))
    return ""


class NarodneNovineScraper:
    BASE_URL = "https://narodne-novine.nn.hr"
    ELI_SL = f"{BASE_URL}/eli/sluzbeni-list"
    ELI_MU = f"{BASE_URL}/eli/medunarodni-ugovori"
    SEARCH_URL = f"{BASE_URL}/search.aspx"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PratimZakon/2.0 (+https://pratimzakon.hr)",
            "Accept-Language": "hr,en;q=0.9",
        })

        # Inicijaliziramo NN API klijent
        from app.scraper.nn_api import NarodneNovineAPI
        self.nn_api = NarodneNovineAPI()

    # ------------------------------------------------------------------
    # Dohvaćanje zadnjeg broja – API (primarno) + fallback na HTML
    # ------------------------------------------------------------------

    def get_latest_nn_broj(self, db_session=None) -> Tuple[int, int]:
        """Dohvaća zadnji objavljeni NN broj putem API-ja, s fallbackom."""
        godina = datetime.now().year

        # Pokušaj putem API-ja
        zadnji = self.nn_api.get_latest_edition(godina)
        if zadnji:
            logging.info(f"[API] Zadnji NN broj za {godina}: {zadnji}")
            return godina, zadnji

        # Fallback: HTML scraping (stara logika)
        logging.warning("[API] Fallback na HTML scraping za detekciju zadnjeg broja")
        return self._get_latest_nn_broj_html(db_session)

    def _get_latest_nn_broj_html(self, db_session=None) -> Tuple[int, int]:
        """Stara HTML metoda za detekciju zadnjeg broja (fallback)."""
        from app.models import Document

        godina = datetime.now().year
        last_known = 0

        if db_session:
            result = (
                db_session.query(Document)
                .filter(Document.issue_number.isnot(None))
                .order_by(Document.issue_number.desc())
                .first()
            )
            if result and result.published_date and result.published_date.year == godina:
                last_known = result.issue_number

        logging.info(f"Zadnji poznati NN broj u bazi za {godina}: {last_known}")

        start_broj = max(1, last_known)
        end_check = start_broj + (10 if last_known == 0 else 20)
        latest_found = start_broj
        consecutive_missing = 0

        for broj in range(start_broj, end_check):
            test_url = (
                f"{self.search_url}?sortiraj=4&kategorija=1&godina={godina}"
                f"&broj={broj}&rpp=10&qtype=1&pretraga=da"
            )
            try:
                response = self.session.get(test_url, timeout=10)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                all_tr = soup.find_all("tr")
                results = [tr for tr in all_tr if re.search(rf"\b{broj}/{godina}\b", tr.get_text())]

                if results:
                    logging.info(f"✓ NN {broj}/{godina} postoji ({len(results)} dokumenata)")
                    latest_found = broj
                    consecutive_missing = 0
                else:
                    consecutive_missing += 1
                    if consecutive_missing >= 3:
                        break

                time.sleep(0.5)
            except requests.exceptions.RequestException as e:
                logging.warning(f"Greška pri provjeri NN {broj}/{godina}: {e}")
                consecutive_missing += 1
                if consecutive_missing >= 3:
                    break

        logging.info(f"Najnoviji NN broj (HTML): {latest_found}/{godina}")
        return godina, latest_found

    # ------------------------------------------------------------------
    # Dohvaćanje dokumenata – API (primarno) + fallback na HTML
    # ------------------------------------------------------------------

    def scrape_nn_broj(self, godina: int, broj: int) -> List[Dict]:
        """Dohvaća SL dokumente za određeni NN broj. API → fallback na HTML."""
        entries = self._fetch_via_api(godina, broj, "SL")
        if entries:
            return entries

        logging.warning(f"[API] Fallback na HTML scraping za NN {broj}/{godina}")
        return self._scrape_nn_broj_html(godina, broj)

    def scrape_mu_broj(self, godina: int, broj: int) -> List[Dict]:
        """Dohvaća MU (međunarodni ugovori) dokumente za određeni broj."""
        return self._fetch_via_api(godina, broj, "MU")

    def _fetch_via_api(self, godina: int, broj: int, part: str = "SL") -> List[Dict]:
        """Dohvaća sve propise za jedno izdanje putem NN API-ja."""
        act_nums = self.nn_api.get_acts(godina, broj, part)
        if not act_nums:
            logging.warning(f"[API] Nema propisa za NN {broj}/{godina} {part} (ili izdanje ne postoji)")
            return []

        logging.info(f"[API] NN {broj}/{godina} {part}: {len(act_nums)} propisa")
        results = []
        for act_num in act_nums:
            meta = self.nn_api.get_act_metadata(godina, broj, act_num, part)
            if meta:
                meta["part"] = part
                results.append(meta)
            else:
                logging.debug(f"[API] Nema metapodataka za akt {act_num}")

        logging.info(f"[API] NN {broj}/{godina} {part}: dohvaćeno {len(results)}/{len(act_nums)} propisa")
        return results

    def _scrape_nn_broj_html(self, godina: int, broj: int) -> List[Dict]:
        """Stara HTML metoda za dohvaćanje dokumenata (fallback)."""
    # ------------------------------------------------------------------
    # JSON-LD API (primarni način)
    # ------------------------------------------------------------------

    def _fetch_jsonld_issue(self, year: int, issue: int, part: str = "SL") -> Optional[dict]:
        """Dohvaća JSON-LD za jedno izdanje. Vraća parsed dict ili None."""
        base = self.ELI_SL if part == "SL" else self.ELI_MU
        url = f"{base}/{year}/{issue}/"
        try:
            resp = self.session.get(
                url,
                headers={"Accept": "application/ld+json, application/json;q=0.9"},
                timeout=20,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logging.warning(f"JSON-LD dohvat neuspješan za {part} {issue}/{year}: {e}")
            return None

    def _parse_jsonld_issue(self, data: dict, issue: int, part: str = "SL") -> List[Dict]:
        """Parsira JSON-LD odgovor za cijelo izdanje. Vraća listu dokumenata."""
        results = []

        # Pronađi listu akata — može biti pod raznim ključevima
        acts = []
        for key in ("dcterms:hasPart", "hasPart", "eli:has_part", "member", "@graph"):
            if key in data:
                val = data[key]
                acts = val if isinstance(val, list) else [val]
                break

        # Ako je @graph, filtriraj samo akte (ne sâmo izdanje)
        issue_id = data.get("@id", "")
        if not acts and "@graph" in data:
            acts = [
                item for item in data["@graph"]
                if item.get("@id", "") != issue_id
            ]

        published_date = _parse_date(
            data.get("eli:date_publication") or data.get("dcterms:date")
        )

        for act in acts:
            if not isinstance(act, dict):
                continue

            # Ako akt sadrži samo @id, pokušaj ga dohvatiti zasebno
            if len(act) == 1 and "@id" in act:
                act = self._fetch_single_act(act["@id"]) or act

            title = _extract_label(
                act.get("eli:title") or act.get("dcterms:title") or act.get("title", "")
            )
            if not title:
                continue

            html_url = ""
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
                elif "html" in fmt_type.lower() or not html_url:
                    html_url = url_val

            # Fallback: @id akta kao HTML url
            if not html_url:
                html_url = act.get("@id", "")

            doc_type = _extract_label(
                act.get("eli:type_document") or act.get("type_document", "")
            )
            institution = _extract_label(
                act.get("eli:passed_by") or act.get("passed_by", "")
            )

            # eli:is_about može biti lista pojmova
            is_about_raw = act.get("eli:is_about") or act.get("is_about", [])
            if isinstance(is_about_raw, list):
                legal_area = ", ".join(
                    _extract_label(x) for x in is_about_raw if _extract_label(x)
                )
            else:
                legal_area = _extract_label(is_about_raw)

            date_document = _parse_date(
                act.get("eli:date_document") or act.get("date_document")
            )
            date_pub = _parse_date(
                act.get("eli:date_publication") or act.get("date_publication")
            ) or published_date

            if not html_url:
                continue

            results.append({
                "title": title,
                "url": html_url,
                "pdf_url": pdf_url or None,
                "type": doc_type,
                "institution": institution or None,
                "legal_area": legal_area or None,
                "date_document": date_document,
                "published_date": date_pub,
                "part": part,
                "issue_number": issue,
            })

        logging.info(f"JSON-LD {part} {issue}: {len(results)} akata parsiranih")
        return results

    def _fetch_single_act(self, url: str) -> Optional[dict]:
        """Dohvaća JSON-LD za pojedini akt ako issue vraća samo @id reference."""
        try:
            resp = self.session.get(
                url,
                headers={"Accept": "application/ld+json, application/json;q=0.9"},
                timeout=15,
            )
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # HTML fallback scraper
    # ------------------------------------------------------------------

    def _scrape_html_issue(self, year: int, issue: int, part: str = "SL") -> List[Dict]:
        """HTML scraping via search.aspx. Podržava SL (kategorija=1) i MU (kategorija=2)."""
        from bs4 import BeautifulSoup

        kategorija = "1" if part == "SL" else "2"
        try:
            params = {
                "godina": year, "broj": issue,
                "kategorija": kategorija, "qtype": "1",
                "pretraga": "da", "sortiraj": "4", "rpp": "100",
            }
            resp = self.session.get(self.SEARCH_URL, params=params, timeout=30)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            datum_izdanja = ""
            first_meta = soup.find("div", class_="official-number-and-date")
            if first_meta:
                meta_parts = first_meta.get_text(strip=True).split(",")
                if len(meta_parts) >= 4:
                    datum_izdanja = meta_parts[3].strip().rstrip(".")

            results = []
            for item in soup.find_all("div", class_="searchListItem"):
                try:
                    title_div = item.find("div", class_="resultTitle")
                    if not title_div:
                        continue
                    link_elem = title_div.find("a")
                    if not link_elem:
                        continue

                    naziv = re.sub(r"\s*\d+\s*$", "", link_elem.get_text(strip=True)).strip()
                    href = link_elem.get("href", "")
                    link = (self.BASE_URL + href) if href and not href.startswith("http") else href

                    doc_type = ""
                    meta_div = item.find("div", class_="official-number-and-date")
                    if meta_div:
                        item_parts = meta_div.get_text(strip=True).split(",")
                        if len(item_parts) >= 3:
                            doc_type = item_parts[2].strip()
                        if not datum_izdanja and len(item_parts) >= 4:
                            datum_izdanja = item_parts[3].strip().rstrip(".")

                    results.append({
                        "title": naziv,
                        "url": link,
                        "pdf_url": None,
                        "type": doc_type,
                        "institution": None,
                        "legal_area": None,
                        "date_document": None,
                        "published_date": _parse_date(datum_izdanja),
                        "part": part,
                        "issue_number": issue,
                    })
                except Exception as e:
                    logging.error(f"HTML parse greška: {e}")

            logging.info(f"HTML {part} {issue}/{year}: {len(results)} akata")
            return results

        except requests.exceptions.RequestException as e:
            logging.error(f"HTML dohvat neuspješan za {part} {issue}/{year}: {e}")
            return []

    # ------------------------------------------------------------------
    # Javno sučelje
    # ------------------------------------------------------------------

    def scrape_issue(self, year: int, issue: int, part: str = "SL") -> List[Dict]:
        """Dohvaća akte za jedno NN izdanje — JSON-LD primarno, HTML fallback."""
        try:
            data = self._fetch_jsonld_issue(year, issue, part)
            if data:
                results = self._parse_jsonld_issue(data, issue, part)
                if results:
                    return results
                logging.warning(f"JSON-LD vratio 0 akata za {part} {issue}/{year}, koristim HTML fallback")
            else:
                logging.warning(f"JSON-LD vratio None za {part} {issue}/{year}, koristim HTML fallback")
        except Exception as e:
            logging.error(f"JSON-LD iznimka za {part} {issue}/{year}: {type(e).__name__}: {e}")
        return self._scrape_html_issue(year, issue, part)

    def get_latest_issue(self, db_session=None, part: str = "SL") -> Tuple[int, int]:
        """
        Pronalazi zadnji objavljeni broj NN testiranjem search.aspx
        sekvencijalno od zadnje poznate vrijednosti u bazi.
        """
        from app.models import Document

        year = datetime.now().year
        last_known = 1

        if db_session:
            result = (
                db_session.query(Document)
                .filter(
                    Document.issue_number.isnot(None),
                    Document.part == part,
                )
                .order_by(Document.issue_number.desc())
                .first()
            )
            if result and result.published_date and result.published_date.year == year:
                last_known = result.issue_number

        logging.info(f"Zadnji poznati {part} broj u bazi: {last_known}/{year}")

        latest = last_known
        consecutive_missing = 0

        for issue in range(last_known, last_known + 30):
            entries = self._scrape_html_issue(year, issue, part)
            if entries:
                latest = issue
                consecutive_missing = 0
                logging.info(f"✓ {part} {issue}/{year}: {len(entries)} akata")
            else:
                consecutive_missing += 1
                if consecutive_missing >= 3:
                    break
            time.sleep(0.5)

        logging.info(f"Najnoviji {part} broj: {latest}/{year}")
        return year, latest

    def save_documents(self, entries: List[Dict], db_session) -> Tuple[int, List[int]]:
        """Sprema dokumente u bazu. Preskače duplikate (po URL-u). Vraća (broj_novih, id_lista)."""
        from app.models import Document
        from sqlalchemy.exc import IntegrityError

        new_count = 0
        new_ids = []

        for entry in entries:
            if not entry.get("url"):
                continue
            existing = db_session.query(Document).filter(Document.url == entry["url"]).first()
            if existing:
                continue

            doc = Document(
                title=entry["title"],
                url=entry["url"],
                pdf_url=entry.get("pdf_url"),
                type=entry.get("type", ""),
                institution=entry.get("institution"),
                legal_area=entry.get("legal_area"),
                date_document=entry.get("date_document"),
                published_date=entry.get("published_date"),
                part=entry.get("part", "SL"),
                issue_number=entry.get("issue_number"),
            )
            db_session.add(doc)
            try:
                db_session.flush()
                new_count += 1
                new_ids.append(doc.id)
            except IntegrityError:
                db_session.rollback()

        db_session.commit()
        return new_count, new_ids

    def check_for_updates(self) -> int:
        """Provjera novih objava (SL + MU) i slanje notifikacija. Vraća broj novih zapisa."""
        from app.models import Log
        from app.email.notifier import send_keyword_notifications

        db = _get_db_session()
        total_new = 0
        all_new_ids = []

        try:
            for part in ("SL", "MU"):
                year, latest = self.get_latest_issue(db, part=part)
                entries = self.scrape_issue(year, latest, part=part)

                if not entries:
                    logging.warning(f"Nema dokumenata za {part} {latest}/{year}")
                    db.add(Log(event_type="scrape", detail=f"Nema novih objava ({part})"))
                    db.commit()
                    continue

                new_count, new_ids = self.save_documents(entries, db)
                logging.info(f"{part} {latest}/{year}: {new_count} novih")
                db.add(Log(event_type="scrape", detail=f"NN {part} {latest}/{year}: {new_count} novih"))
                db.commit()

                total_new += new_count
                all_new_ids.extend(new_ids)

            if all_new_ids:
                send_keyword_notifications(all_new_ids, db)

            return total_new
        finally:
            db.close()


def run_check():
    """Entry point za GitHub Actions job."""
    try:
        scraper = NarodneNovineScraper()
        count = scraper.check_for_updates()
        logging.info(f"Scraper završen. Ukupno novih: {count}")
    except Exception as e:
        logging.error(f"Scraper pao s greškom: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run_check()
