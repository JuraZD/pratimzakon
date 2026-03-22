#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill skript — popunjava bazu s povijesnim podacima od 2015. do danas.
Jednokratno se pokreće ručno putem GitHub Actions workflow_dispatch.
"""

import sys
import os
import time
import logging
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

MAX_ISSUES_PER_YEAR = 200   # sigurni maksimum; SL nikad nema više od ~170
CONSECUTIVE_MISS_LIMIT = 3  # toliko uzastopnih 404 = nema više izdanja te godine
SLEEP_BETWEEN = 0.4         # sekunde između API poziva (ne preoptereti server)


def run_backfill(year_from: int = 2015, year_to: int = None):
    from app.database import SessionLocal
    from app.scraper.nn_scraper import NarodneNovineScraper
    from app.models import Log

    if year_to is None:
        year_to = datetime.now().year

    db = SessionLocal()
    scraper = NarodneNovineScraper()
    grand_total = 0

    logging.info(f"Backfill start: {year_from}–{year_to}, SL + MU")

    try:
        for year in range(year_from, year_to + 1):
            for part in ("SL", "MU"):
                year_total = 0
                consecutive_missing = 0

                for issue in range(1, MAX_ISSUES_PER_YEAR + 1):
                    data = scraper._fetch_jsonld_issue(year, issue, part)

                    if data is None:
                        consecutive_missing += 1
                        if consecutive_missing >= CONSECUTIVE_MISS_LIMIT:
                            break
                        time.sleep(SLEEP_BETWEEN)
                        continue

                    consecutive_missing = 0
                    entries = scraper._parse_jsonld_issue(data, issue, part)

                    if entries:
                        new_count, _ = scraper.save_documents(entries, db)
                        year_total += new_count
                        grand_total += new_count
                        logging.info(
                            f"  {part} {issue}/{year}: {len(entries)} akata, {new_count} novih"
                        )

                    time.sleep(SLEEP_BETWEEN)

                logging.info(f"{part} {year}: {year_total} novih ukupno")

        db.add(Log(
            event_type="backfill",
            detail=f"Backfill {year_from}–{year_to}: {grand_total} novih dokumenata",
        ))
        db.commit()
        logging.info(f"Backfill završen. Ukupno novih dokumenata: {grand_total}")

    finally:
        db.close()

    return grand_total


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill NN arhive")
    parser.add_argument("--from", dest="year_from", type=int, default=2015)
    parser.add_argument("--to", dest="year_to", type=int, default=datetime.now().year)
    args = parser.parse_args()
    run_backfill(args.year_from, args.year_to)
