"""
Agent orchestration loop — Korak 3/4.

Claude prima task, sam odlučuje koje tools pozvati i u kom redoslijedu,
obrađuje rezultate i vraća finalni izvještaj.

Pokretanje:
  python -m app.agent
"""

import json
import logging
import os

import anthropic

from .tools.definitions import TOOLS
from .tools.executor import execute_tool

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MAX_ITERATIONS = 20


def run_agent(db, task: str | None = None) -> str:
    """
    Pokreće orchestration loop.

    1. Šalje task Claudeu s listom dostupnih tools.
    2. Ako Claude pozove tool (stop_reason="tool_use"):
       - izvrši tool
       - vrati rezultat Claudeu
       - nastavi petlju
    3. Kada Claude završi (stop_reason="end_turn"):
       - vrati finalni tekstualni odgovor
    """
    if task is None:
        task = (
            "Provjeri zakone objavljene u zadnjem danu. "
            "Dohvati aktivne korisnike, provjeri relevantnost svakog dokumenta "
            "za svakog korisnika, i pošalji notifikacije gdje postoji match."
        )

    messages = [{"role": "user", "content": task}]
    logging.info(f"[Agent] Start — {task[:80]}")

    for iteration in range(MAX_ITERATIONS):
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )

        logging.info(
            f"[Agent] Iteracija {iteration + 1}, stop_reason={response.stop_reason}"
        )

        # Claude je završio — vrati tekstualni odgovor
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        # Claude želi pozvati tool(s)
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                logging.info(
                    f"[Agent] → {block.name}("
                    f"{json.dumps(block.input, ensure_ascii=False)})"
                )
                result = execute_tool(block.name, block.input, db)
                logging.info(
                    f"[Agent] ← {block.name}: {str(result)[:120]}"
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

            messages.append({"role": "user", "content": tool_results})

    logging.warning(f"[Agent] Dostignut MAX_ITERATIONS ({MAX_ITERATIONS})")
    return ""


if __name__ == "__main__":
    import dotenv
    import sys

    dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        summary = run_agent(db)
        print("\n=== AGENT IZVJEŠTAJ ===")
        print(summary)
    finally:
        db.close()
