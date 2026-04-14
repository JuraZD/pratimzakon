"""
Tool definicije za Claude analysis agent — Korak 4.

Analiza agent ima SAMO jedan tool: provjeri_relevantnost.
Dohvat podataka i slanje notifikacija rade se deterministički
u orchestratoru — nisu Claude toolovi.
"""

TOOLS = [
    {
        "name": "provjeri_relevantnost",
        "description": (
            "Provjerava je li određeni dokument relevantan za određenog korisnika "
            "na temelju njegovih ključnih riječi i opisa situacije. "
            "Vraća {relevantno: bool, razlog: str}. "
            "Ne odlučuj o slanju notifikacija — samo analiziraj relevantnost."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "integer",
                    "description": "ID dokumenta iz baze podataka.",
                },
                "user_id": {
                    "type": "integer",
                    "description": "ID korisnika iz baze podataka.",
                },
            },
            "required": ["doc_id", "user_id"],
        },
    },
]
