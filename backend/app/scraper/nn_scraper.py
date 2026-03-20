#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper za Narodne novine – PratimZakon verzija
Primarno koristi službeni NN API, s fallbackom na HTML scraping.
"""

import re
import sys
import time
import logging
import os
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))

# Postavljanje logginga
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)


def _get_db_session():
    """Vraća SQLAlchemy session iz environment-a."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))
    from app.database import SessionLocal
    return SessionLocal()


def _parse_date(datum_str: str) -> Optional[date]:
    """Parsira datum iz oblika '1. siječnja 2025.' u date objekt."""
    months = {
        "siječnja": 1, "veljače": 2, "ožujka": 3, "travnja": 4,
        "svibnja": 5, "lipnja": 6, "srpnja": 7, "kolovoza": 8,
        "rujna": 9, "listopada": 10, "studenog": 11, "prosinca": 12,
    }
    m = re.search(r"(\d{1,2})\.\s+(\w+)\s+(\d{4})", datum_str)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = months.get(month_name)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


class NarodneNovineScraper:
    def __init__(self):
        self.base_url = "https://narodne-novine.nn.hr"
        self.search_url = f"{self.base_url}/search.aspx"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
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
        """Dohvaća dokumente za određeni NN broj. API → fallback na HTML."""
        # Pokušaj putem API-ja
        entries = self._fetch_via_api(godina, broj)
        if entries:
            return entries

        # Fallback: staro HTML scraping
        logging.warning(f"[API] Fallback na HTML scraping za NN {broj}/{godina}")
        return self._scrape_nn_broj_html(godina, broj)

    def _fetch_via_api(self, godina: int, broj: int) -> List[Dict]:
        """Dohvaća sve propise za jedno izdanje putem NN API-ja."""
        act_nums = self.nn_api.get_acts(godina, broj)
        if not act_nums:
            logging.warning(f"[API] Nema propisa za NN {broj}/{godina} (ili izdanje ne postoji)")
            return []

        logging.info(f"[API] NN {broj}/{godina}: {len(act_nums)} propisa")
        results = []
        for act_num in act_nums:
            meta = self.nn_api.get_act_metadata(godina, broj, act_num)
            if meta:
                results.append(meta)
            else:
                logging.debug(f"[API] Nema metapodataka za akt {act_num}")

        logging.info(f"[API] NN {broj}/{godina}: dohvaćeno {len(results)}/{len(act_nums)} propisa")
        return results

    def _scrape_nn_broj_html(self, godina: int, broj: int) -> List[Dict]:
        """Stara HTML metoda za dohvaćanje dokumenata (fallback)."""
        try:
            params = {
                "godina": godina, "broj": broj,
                "kategorija": "1", "qtype": "1",
                "pretraga": "da", "sortiraj": "4", "rpp": "100",
            }
            response = self.session.get(self.search_url, params=params, timeout=30)
            response.raise_for_status()
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")

            datum_izdanja = ""
            first_meta = soup.find("div", class_="official-number-and-date")
            if first_meta:
                parts = first_meta.get_text(strip=True).split(",")
                if len(parts) >= 4:
                    datum_izdanja = parts[3].strip().rstrip(".")

            search_items = soup.find_all("div", class_="searchListItem")
            if not search_items:
                logging.warning(f"[HTML] Nema rezultata za NN {broj}/{godina}")
                return []

            results = []
            for idx, item in enumerate(search_items, 1):
                try:
                    title_div = item.find("div", class_="resultTitle")
                    if not title_div:
                        continue
                    link_elem = title_div.find("a")
                    if not link_elem:
                        continue

                    naziv = re.sub(r"\s*\d+\s*$", "", link_elem.get_text(strip=True)).strip()
                    href = link_elem.get("href", "")
                    link = (self.base_url + href) if href and not href.startswith("http") else href

                    meta_div = item.find("div", class_="official-number-and-date")
                    tip_dokumenta = ""
                    if meta_div:
                        meta_text = meta_div.get_text(strip=True)
                        parts = meta_text.split(",")
                        if len(parts) >= 3:
                            tip_dokumenta = parts[2].strip()
                        if not datum_izdanja and len(parts) >= 4:
                            datum_izdanja = parts[3].strip().rstrip(".")

                    results.append({
                        "title": naziv,
                        "url": link,
                        "type": tip_dokumenta,
                        "published_date": _parse_date(datum_izdanja),
                        "issue_number": broj,
                    })
                except Exception as e:
                    logging.error(f"[HTML] Greška pri parsiranju stavke {idx}: {e}")

            logging.info(f"[HTML] NN {broj}/{godina}: {len(results)} zapisa")
            return results

        except requests.exceptions.RequestException as e:
            logging.error(f"[HTML] Greška pri dohvaćanju NN {broj}/{godina}: {e}")
            return []

    # ------------------------------------------------------------------
    # Spremanje i notifikacije
    # ------------------------------------------------------------------

    def save_documents(self, entries: List[Dict], db_session) -> Tuple[int, List[int]]:
        """Sprema dokumente u PostgreSQL bazu. Vraća (broj_novih, lista_id)."""
        from app.models import Document
        from sqlalchemy.exc import IntegrityError

        new_count = 0
        new_ids = []

        for entry in entries:
            existing = db_session.query(Document).filter(
                Document.url == entry["url"]
            ).first()
            if existing:
                continue

            doc = Document(
                title=entry["title"],
                url=entry["url"],
                type=entry.get("type", ""),
                published_date=entry.get("published_date"),
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
        """Provjera novih objava i slanje notifikacija. Vraća broj novih zapisa."""
        from app.models import Document, Log
        from app.email.notifier import send_keyword_notifications

        db = _get_db_session()
        try:
            godina, zadnji_broj = self.get_latest_nn_broj(db)
            entries = self.scrape_nn_broj(godina, zadnji_broj)

            if not entries:
                logging.warning("Nema dokumenata za scraping")
                db.add(Log(event_type="scrape", detail="Nema novih objava"))
                db.commit()
                return 0

            new_count, new_ids = self.save_documents(entries, db)
            logging.info(f"Novih zapisa: {new_count}")

            db.add(Log(event_type="scrape", detail=f"NN {zadnji_broj}/{godina}: {new_count} novih"))
            db.commit()

            if new_ids:
                send_keyword_notifications(new_ids, db)

            return new_count
        finally:
            db.close()


def run_check():
    """Entry point za GitHub Actions job."""
    scraper = NarodneNovineScraper()
    count = scraper.check_for_updates()
    logging.info(f"Scraper završen. Ukupno novih: {count}")


if __name__ == "__main__":
    run_check()
