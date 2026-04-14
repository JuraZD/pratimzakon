"""
Tool definicije za Claude agent — Korak 3.

Svaki tool ima: name, description, input_schema.
Claude čita description da odluči koji tool pozvati i kada.
"""

TOOLS = [
    {
        "name": "dohvati_nedavne_dokumente",
        "description": (
            "Dohvaća zakone i propise objavljene u Narodnim novinama "
            "u zadanih N dana. Koristiti kao prvi korak — saznati što je novo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "broj_dana": {
                    "type": "integer",
                    "description": "Koliko dana unazad dohvatiti (1–7).",
                    "minimum": 1,
                    "maximum": 7,
                }
            },
            "required": ["broj_dana"],
        },
    },
    {
        "name": "dohvati_korisnike",
        "description": (
            "Dohvaća aktivne korisnike koji imaju postavljene ključne riječi "
            "ili opis situacije. Vraća ID, email i pregled ključnih riječi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "provjeri_relevantnost",
        "description": (
            "Provjerava je li određeni dokument relevantan za određenog korisnika "
            "na temelju njegovih ključnih riječi i opisa situacije. "
            "Vraća {relevantno: bool, razlog: str}. "
            "Koristiti prije slanja notifikacije da se izbjegnu nepotrebni emailovi."
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
    {
        "name": "posalji_notifikacije",
        "description": (
            "Šalje email notifikacije svim korisnicima za koje su dokumenti relevantni. "
            "Koristiti SAMO kada postoje dokumenti koji su prošli provjeru relevantnosti. "
            "Interna provjera relevantnosti sprječava slanje nepotrebnih emailova."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Lista ID-jeva dokumenata za koje treba poslati notifikacije.",
                    "minItems": 1,
                }
            },
            "required": ["doc_ids"],
        },
    },
]
