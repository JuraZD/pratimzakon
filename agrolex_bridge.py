"""
AGROLEX Bridge - Povezuje NN Scraper s AGROLEX sustavom.
Koristi file-based queue za komunikaciju.
"""

import json
import os
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_DIR = os.path.expanduser("~/.agrolex/queue")

class AgrolexBridge:
    def __init__(self, queue_dir: str = DEFAULT_QUEUE_DIR):
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    def notify_new_document(
        self,
        doc_id: int,
        naziv: str,
        nn_broj: str,
        datum: str,
        link: str,
        tip_dokumenta: str = ""
    ) -> bool:
        try:
            notification = {
                "type": "new_document",
                "timestamp": datetime.now().isoformat(),
                "data": {
                    "nn_doc_id": doc_id,
                    "naziv": naziv,
                    "nn_broj": nn_broj,
                    "datum": datum,
                    "link": link,
                    "tip_dokumenta": tip_dokumenta
                }
            }

            filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{doc_id}.json"
            filepath = self.queue_dir / filename

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(notification, f, ensure_ascii=False, indent=2)

            logger.info(f"AGROLEX notification queued: {filename}")
            return True

        except Exception as e:
            logger.error(f"Failed to queue AGROLEX notification: {e}")
            return False

    def get_pending_count(self) -> int:
        return len(list(self.queue_dir.glob("*.json")))


def notify_agrolex(doc_id: int, naziv: str, nn_broj: str, datum: str, link: str):
    bridge = AgrolexBridge()
    return bridge.notify_new_document(doc_id, naziv, nn_broj, datum, link)
