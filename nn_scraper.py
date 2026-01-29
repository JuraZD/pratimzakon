#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper za Narodne novine - Verzija s Email Notifikacijama
Automatski preuzima, prati nove objave i šalje email notifikacije
"""

import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import time
import schedule
import re
from typing import List, Dict, Optional
import logging

# Import email notifikacijskog sustava
try:
    from email_notifier import WatchlistManager, EmailNotifier

    NOTIFICATIONS_ENABLED = True
except ImportError:
    NOTIFICATIONS_ENABLED = False
    logging.warning("Email notifikacije nisu dostupne - provjerite email_notifier.py")

# Import AGROLEX bridge
try:
    from agrolex_bridge import notify_agrolex
    AGROLEX_BRIDGE_ENABLED = True
except ImportError:
    AGROLEX_BRIDGE_ENABLED = False
    logging.warning("AGROLEX bridge nije dostupan")

# Postavljanje logginga
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("nn_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


class NarodneNovineScraper:
    def __init__(self, db_name="narodne_novine.db"):
        self.base_url = "https://narodne-novine.nn.hr"
        self.search_url = f"{self.base_url}/search.aspx"
        self.db_name = db_name
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "hr,en;q=0.9",
            }
        )
        self.init_database()

        # Inicijaliziraj watchlist manager za notifikacije
        if NOTIFICATIONS_ENABLED:
            self.watchlist_manager = WatchlistManager(db_name)
        else:
            self.watchlist_manager = None

    def init_database(self):
        """Inicijalizacija SQLite baze podataka"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS objave (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                redni_broj INTEGER,
                naziv TEXT NOT NULL,
                nn_broj TEXT,
                broj_dokumenta TEXT,
                tip_dokumenta TEXT,
                datum TEXT,
                link TEXT,
                datum_preuzimanja TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(nn_broj, naziv)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS provjere (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datum_provjere TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                broj_novih_zapisa INTEGER,
                status TEXT
            )
        """
        )

        conn.commit()
        conn.close()
        logging.info(f"Baza podataka '{self.db_name}' inicijalizirana")

    def get_latest_entry(self) -> Optional[Dict]:
        """Dohvaća zadnji zapis iz baze"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT redni_broj, naziv, nn_broj, datum 
            FROM objave 
            ORDER BY id DESC 
            LIMIT 1
        """
        )

        result = cursor.fetchone()
        conn.close()

        if result:
            return {
                "redni_broj": result[0],
                "naziv": result[1],
                "nn_broj": result[2],
                "datum": result[3],
            }
        return None

    def scrape_nn_broj(self, godina: int, broj: int) -> List[Dict]:
        """
        Preuzima podatke za određeni NN broj
        Npr. godina=2025, broj=149 -> NN 149/2025
        """
        try:
            params = {
                "godina": godina,
                "broj": broj,
                "kategorija": "1",  # 1 = Službeni dio
                "qtype": "1",
                "pretraga": "da",
                "sortiraj": "4",
                "rpp": "100",  # Broj rezultata po stranici
            }

            response = self.session.get(self.search_url, params=params, timeout=30)
            response.raise_for_status()
            response.encoding = "utf-8"

            soup = BeautifulSoup(response.text, "html.parser")

            # Dohvati datum izdanja NN broja iz naslova ili prvog dokumenta
            datum_izdanja = ""

            first_meta = soup.find("div", class_="official-number-and-date")
            if first_meta:
                meta_text = first_meta.get_text(strip=True)
                parts = meta_text.split(",")
                if len(parts) >= 4:
                    datum_izdanja = parts[3].strip().rstrip(".")

            # Pronađi sve divove sa klasom 'searchListItem'
            search_items = soup.find_all("div", class_="searchListItem")

            if not search_items:
                logging.warning(f"Nema rezultata za NN {broj}/{godina}")
                return []

            results = []

            for idx, item in enumerate(search_items, 1):
                try:
                    # Dohvati naslov i link
                    title_div = item.find("div", class_="resultTitle")
                    if not title_div:
                        continue

                    link_elem = title_div.find("a")
                    if not link_elem:
                        continue

                    naziv = link_elem.get_text(strip=True)
                    naziv = re.sub(r"\s*\d+\s*$", "", naziv).strip()

                    href = link_elem.get("href", "")
                    if href and not href.startswith("http"):
                        link = self.base_url + href
                    else:
                        link = href

                    # Dohvati metapodatke
                    meta_div = item.find("div", class_="official-number-and-date")
                    broj_dokumenta = ""
                    tip_dokumenta = ""

                    if meta_div:
                        meta_text = meta_div.get_text(strip=True)

                        broj_match = re.search(r"\((\d+)\)", meta_text)
                        if broj_match:
                            broj_dokumenta = broj_match.group(1)

                        parts = meta_text.split(",")
                        if len(parts) >= 3:
                            tip_dokumenta = parts[2].strip()

                        if not datum_izdanja and len(parts) >= 4:
                            datum_izdanja = parts[3].strip().rstrip(".")

                    result = {
                        "redni_broj": idx,
                        "naziv": naziv,
                        "nn_broj": f"{broj}/{godina}",
                        "broj_dokumenta": broj_dokumenta,
                        "tip_dokumenta": tip_dokumenta,
                        "datum": datum_izdanja,
                        "link": link,
                    }

                    results.append(result)

                except Exception as e:
                    logging.error(f"Greška pri parsiranju stavke {idx}: {e}")
                    continue

            if results:
                logging.info(
                    f"NN {broj}/{godina}: pronađeno {len(results)} zapisa (datum izdanja: {datum_izdanja})"
                )
            else:
                logging.warning(f"Nema dokumenata za NN {broj}/{godina}")

            return results

        except requests.exceptions.RequestException as e:
            logging.error(f"Greška pri dohvaćanju NN {broj}/{godina}: {e}")
            return []
        except Exception as e:
            logging.error(f"Neočekivana greška za NN {broj}/{godina}: {e}")
            return []

    def get_latest_nn_broj(self) -> tuple:
        """Dohvaća zadnji objavljeni NN broj - Smart pristup"""
        godina = datetime.now().year
        # Podešavanje raspona provjere (konzervativno za početak godine)
        initial_check_span = 10
        followup_check_span = 20
        max_consecutive_missing = 3

        # 1. Provjeri posljednji poznat broj u bazi
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT MAX(CAST(SUBSTR(nn_broj, 1, INSTR(nn_broj, '/')-1) AS INTEGER)) as max_broj
                FROM objave 
                WHERE nn_broj LIKE ? AND CAST(SUBSTR(nn_broj, INSTR(nn_broj, '/')+1) AS INTEGER) = ?
            """,
                (f"%/{godina}", godina),
            )

            result = cursor.fetchone()
            last_known = result[0] if result[0] else 0

        except Exception as e:
            logging.warning(f"Ne mogu pročitati zadnji broj iz baze: {e}")
            last_known = 0
        finally:
            conn.close()

        logging.info(f"Zadnji poznati NN broj u bazi za {godina}: {last_known}")

        # 2. Provjeri brojeve od zadnjeg poznatog do +20 (sigurnosna margina)
        # Nova godina kreće od 1/{godina}; ne naslanjamo se na prošlogodišnje brojeve.
        if last_known <= 0:
            start_broj = 1
            end_check = start_broj + initial_check_span
        else:
            start_broj = last_known
            end_check = start_broj + followup_check_span

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

                # Provjeri ima li rezultata
                all_tr = soup.find_all("tr")
                results = [
                    tr
                    for tr in all_tr
                    if re.search(rf"\b{broj}/{godina}\b", tr.get_text())
                ]

                if results and len(results) > 0:
                    logging.info(
                        f"✓ NN {broj}/{godina} POSTOJI ({len(results)} dokumenata)"
                    )
                    latest_found = broj
                    consecutive_missing = 0  # Reset brojača
                else:
                    consecutive_missing += 1
                    logging.info(f"✗ NN {broj}/{godina} ne postoji")

                    # Ako 3 uzastopna broja ne postoje, pretpostavljamo da smo na kraju
                    if consecutive_missing >= max_consecutive_missing:
                        logging.info(
                            f"Pronađena 3 uzastopna broja bez rezultata - zadnji je {latest_found}/{godina}"
                        )
                        break

                # Kratka pauza između zahtjeva
                time.sleep(0.5)

            except requests.exceptions.RequestException as e:
                logging.warning(f"Greška pri provjeri NN {broj}/{godina}: {e}")
                consecutive_missing += 1
                if consecutive_missing >= max_consecutive_missing:
                    break

        if latest_found > last_known:
            logging.info(f"Pronađen najnoviji NN broj: {latest_found}/{godina}")
            return godina, latest_found
        else:
            # Fallback na posljednji poznati (ili 1/{godina} ako nema zapisa za ovu godinu)
            logging.warning(
                f"Nisam pronašao nove brojeve, koristim posljednji poznati: {last_known}/{godina}"
            )
            return godina, last_known if last_known > 0 else 1

    def scrape_range(self, start_broj: int, end_broj: int, godina: int) -> List[Dict]:
        """Preuzima raspon NN brojeva"""
        all_results = []

        for broj in range(start_broj, end_broj + 1):
            results = self.scrape_nn_broj(godina, broj)
            all_results.extend(results)
            time.sleep(1)

        return all_results

    def save_to_database(
        self, entries: List[Dict], check_watchlist: bool = True
    ) -> tuple[int, List[int]]:
        """
        Sprema zapise u bazu podataka

        Returns: (broj_novih_zapisa, lista_id_novih_zapisa)
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        new_entries = 0
        new_entry_ids = []

        for entry in entries:
            try:
                cursor.execute(
                    """
                    INSERT INTO objave 
                    (redni_broj, naziv, nn_broj, broj_dokumenta, tip_dokumenta, datum, link)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        entry["redni_broj"],
                        entry["naziv"],
                        entry["nn_broj"],
                        entry.get("broj_dokumenta", ""),
                        entry.get("tip_dokumenta", ""),
                        entry.get("datum", ""),
                        entry["link"],
                    ),
                )
                new_entries += 1
                new_entry_ids.append(cursor.lastrowid)

                # Notify AGROLEX
                if AGROLEX_BRIDGE_ENABLED:
                    notify_agrolex(
                        doc_id=cursor.lastrowid,
                        naziv=entry["naziv"],
                        nn_broj=entry["nn_broj"],
                        datum=entry.get("datum", ""),
                        link=entry["link"]
                    )

            except sqlite3.IntegrityError:
                # Zapis već postoji
                pass

        conn.commit()
        conn.close()

        # Provjeri watchlist i pošalji notifikacije
        if check_watchlist and new_entry_ids and self.watchlist_manager:
            logging.info(
                f"Provjera watchlist-a za {len(new_entry_ids)} novih zapisa..."
            )
            try:
                matches = self.watchlist_manager.check_for_matches(new_entry_ids)

                if matches:
                    logging.info(
                        f"Pronađeno {sum(len(m) for m in matches.values())} matcheva za {len(matches)} korisnika"
                    )
                    stats = self.watchlist_manager.send_notifications(matches)
                    logging.info(
                        f"Notifikacije: {stats['sent']} poslano, {stats['failed']} neuspješno"
                    )
                else:
                    logging.info("Nema matcheva za trenutni watchlist")
            except Exception as e:
                logging.error(f"Greška kod slanja notifikacija: {e}")

        return new_entries, new_entry_ids

    def log_check(self, new_entries: int, status: str):
        """Bilježi provjeru u bazu"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO provjere (broj_novih_zapisa, status)
            VALUES (?, ?)
        """,
            (new_entries, status),
        )

        conn.commit()
        conn.close()

    def run_full_scrape(
        self, broj_brojeva: int = 10, check_watchlist: bool = True
    ) -> int:
        """Pokreće potpuno preuzimanje zadnjih N brojeva"""
        logging.info("=" * 60)
        logging.info("Pokretanje scrapinga...")

        godina, zadnji_broj = self.get_latest_nn_broj()
        start_broj = max(1, zadnji_broj - broj_brojeva + 1)

        logging.info(f"Preuzimaanje NN {start_broj}-{zadnji_broj}/{godina}")

        all_entries = self.scrape_range(start_broj, zadnji_broj, godina)
        logging.info(f"Ukupno prikupljeno zapisa: {len(all_entries)}")

        # Spremi u bazu i provjeri watchlist
        new_entries, new_ids = self.save_to_database(all_entries, check_watchlist)

        logging.info(f"Novih zapisa spremljeno: {new_entries}")
        self.log_check(new_entries, "Uspješno")

        return new_entries

    def check_for_updates(self, send_notifications: bool = True):
        """Provjerava ima li novih objava i šalje notifikacije"""
        logging.info("=" * 60)
        logging.info("Provjera novih objava...")

        latest_db = self.get_latest_entry()
        godina, zadnji_broj = self.get_latest_nn_broj()

        latest_web = self.scrape_nn_broj(godina, zadnji_broj)

        if not latest_web:
            logging.warning("Nije pronađen nijedan zapis na webu")
            self.log_check(0, "Greška - nema zapisa")
            return

        if latest_db:
            logging.info(
                f"Zadnji zapis u bazi: {latest_db['naziv']} ({latest_db['nn_broj']})"
            )
            logging.info(f"Najnoviji NN broj na webu: {zadnji_broj}/{godina}")

            if latest_db["nn_broj"]:
                match = re.match(r"(\d+)/(\d+)", latest_db["nn_broj"])
                if match:
                    db_broj = int(match.group(1))
                    db_godina = int(match.group(2))

                    if db_godina == godina and db_broj >= zadnji_broj:
                        logging.info("Nema novih objava")
                        self.log_check(0, "Nema novih objava")
                        return

        # Ima novih objava
        logging.info("Pronađene nove objave! Pokretanje scrapinga...")
        new_entries = self.run_full_scrape(
            broj_brojeva=5, check_watchlist=send_notifications
        )

        logging.info(f"Dodano {new_entries} novih zapisa u bazu")

    def export_to_csv(self, filename="narodne_novine_export.csv"):
        """Izvozi podatke iz baze u CSV"""
        import pandas as pd

        conn = sqlite3.connect(self.db_name)

        df = pd.read_sql_query(
            """
            SELECT redni_broj, naziv, nn_broj, broj_dokumenta, 
                   tip_dokumenta, datum, link, datum_preuzimanja
            FROM objave
            ORDER BY id DESC
        """,
            conn,
        )

        conn.close()

        df.to_csv(filename, index=False, encoding="utf-8-sig")
        logging.info(f"Podaci izvezeni u {filename}")

        return df


def job():
    """Funkcija koja se izvršava po rasporedu"""
    scraper = NarodneNovineScraper()
    scraper.check_for_updates(send_notifications=True)


def main():
    """Glavna funkcija"""
    import sys

    scraper = NarodneNovineScraper()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "init":
            logging.info("Pokretanje inicijalnog scrapinga...")
            scraper.run_full_scrape(broj_brojeva=20, check_watchlist=False)

        elif command == "check":
            scraper.check_for_updates(send_notifications=True)

        elif command == "export":
            filename = sys.argv[2] if len(sys.argv) > 2 else "narodne_novine_export.csv"
            df = scraper.export_to_csv(filename)
            print(f"\nIzvezeno {len(df)} zapisa u {filename}")

        elif command == "schedule":
            logging.info("Pokretanje schedulera - provjera svaki dan u 7:00")
            schedule.every().day.at("07:00").do(job)

            print("Scheduler pokrenut. Provjera se izvršava svaki dan u 7:00")
            print("Pritisnite Ctrl+C za zaustavljanje")

            while True:
                schedule.run_pending()
                time.sleep(60)

        else:
            print("Nepoznata naredba!")
            print_usage()

    else:
        print_usage()


def print_usage():
    """Ispisuje upute za korištenje"""
    print(
        """
Upotreba: python nn_scraper_v2.py [naredba]

Naredbe:
  init       - Inicijalno preuzimanje zadnjih 20 brojeva (bez notifikacija)
  check      - Provjera novih objava (s notifikacijama)
  export     - Izvoz podataka u CSV
  schedule   - Pokreni scheduler (provjera svaki dan u 7:00 s notifikacijama)

Primjeri:
  python nn_scraper_v2.py init       # Prvo pokretanje
  python nn_scraper_v2.py check      # Ručna provjera s notifikacijama
  python nn_scraper_v2.py export     # Izvoz u CSV
  python nn_scraper_v2.py schedule   # Automatsko praćenje s notifikacijama
    """
    )


if __name__ == "__main__":
    main()
