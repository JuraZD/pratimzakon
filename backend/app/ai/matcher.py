"""
AI matcher — provjerava relevantnost dokumenta za korisnika.
Tri razine provjere: naslov, AI brzi, AI duboki.
Koristi: title, type, institution (legal_area trenutno prazan)
"""

import os
import logging
import anthropic

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

RELEVANT_TYPES = {"zakon", "pravilnik", "uredba", "odluka"}


def is_relevant_type(doc_type: str | None) -> bool:
    """Samo ova četiri tipa idu kroz AI matching."""
    if not doc_type:
        return False
    return doc_type.strip().lower() in RELEVANT_TYPES


def classify_document(title: str) -> str:
    """
    Klasificira dokument prema naslovu.
    Vraća: 'novi' | 'izmjena' | 'procisceni'
    """
    title_lower = title.lower()
    if any(w in title_lower for w in ["izmjenama", "dopunama", "izmjeni", "dopuni"]):
        return "izmjena"
    if "pročišćeni" in title_lower:
        return "procisceni"
    return "novi"


def _build_doc_context(doc) -> str:
    """
    Gradi kontekst dokumenta od dostupnih polja.
    Koristi što je dostupno — naslov, tip, institucija.
    """
    parts = [f"Naslov: {doc.title}"]

    if doc.type:
        parts.append(f"Tip: {doc.type}")

    if doc.institution:
        parts.append(f"Institucija: {doc.institution}")

    if doc.legal_area:
        parts.append(f"Pravno područje: {doc.legal_area}")

    return "\n".join(parts)


def keyword_in_title(keyword: str, title: str) -> bool:
    """Razina 1 — je li keyword direktno u naslovu."""
    return keyword.lower() in title.lower()


def fetch_doc_text(url: str) -> str:
    """Dohvaća tekst dokumenta s URL-a."""
    try:
        import requests
        from html.parser import HTMLParser

        class TextParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "header", "footer"):
                    self.skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "header", "footer"):
                    self.skip = False

            def handle_data(self, data):
                if not self.skip:
                    text = data.strip()
                    if text:
                        self.text.append(text)

        resp = requests.get(
            url, headers={"User-Agent": "PratimZakon/2.0"}, timeout=(5, 10)
        )
        if resp.status_code == 200:
            parser = TextParser()
            parser.feed(resp.text)
            return " ".join(parser.text)[:5000]

    except Exception as e:
        logging.warning(f"Nije moguće dohvatiti tekst za {url}: {e}")

    return ""


def ai_quick_check(doc, situation: str, keywords: list[str]) -> bool:
    """
    Razina 2 — AI čita naslov, tip i instituciju.
    Jeftin i brz poziv — samo 5 tokena odgovora.
    """
    try:
        kw_str = ", ".join(keywords) if keywords else "nije definirano"
        situation_str = situation if situation else "nije opisana"
        doc_context = _build_doc_context(doc)

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[
                {
                    "role": "user",
                    "content": f"""Ti si hrvatski pravni stručnjak.

Korisnikova situacija: {situation_str}
Korisnikove ključne riječi: {kw_str}

Dokument:
{doc_context}

Može li ovaj dokument biti relevantan za korisnika,
čak i neizravno?

Odgovori samo DA ili NE.""",
                }
            ],
        )
        return "DA" in msg.content[0].text.upper()

    except Exception as e:
        logging.error(f"AI quick check greška: {e}")
        return False


def ai_deep_check(doc, situation: str, keywords: list[str]) -> tuple[bool, str]:
    """
    Razina 3 — AI dublja analiza s razlogom.
    Vraća (je_relevantno, razlog).
    """
    try:
        kw_str = ", ".join(keywords) if keywords else "nije definirano"
        situation_str = situation if situation else "nije opisana"
        doc_context = _build_doc_context(doc)
        doc_class = classify_document(doc.title)

        if doc_class == "izmjena":
            type_note = "NAPOMENA: Ovo je izmjena postojećeg propisa."
        elif doc_class == "procisceni":
            type_note = "NAPOMENA: Ovo je pročišćeni tekst."
        else:
            type_note = "Ovo je novi propis."

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[
                {
                    "role": "user",
                    "content": f"""Ti si hrvatski pravni stručnjak.

Korisnikova situacija: {situation_str}
Korisnikove ključne riječi: {kw_str}

{type_note}

{doc_context}

Je li ovaj dokument relevantan za ovog konkretnog korisnika?
Uzmi u obzir neizravne veze — npr. promjena doprinosa 
utječe na troškove poslovanja, promjena PDV-a utječe 
na cijene, promjena radnog prava utječe na zaposlenike.

Odgovori TOČNO u ovom formatu:
RELEVANTNO: DA
RAZLOG: [jedna rečenica na hrvatskom, bez pravnog žargona,
zašto se konkretno tiče ovog korisnika]

ili:

RELEVANTNO: NE
RAZLOG: -""",
                }
            ],
        )

        response = msg.content[0].text.strip()
        lines = response.split("\n")

        is_relevant = False
        reason = ""

        for line in lines:
            if line.startswith("RELEVANTNO:"):
                is_relevant = "DA" in line.upper()
            if line.startswith("RAZLOG:"):
                reason = line.replace("RAZLOG:", "").strip()

        return is_relevant, reason

    except Exception as e:
        logging.error(f"AI deep check greška: {e}")
        return False, ""


def generate_summary(doc, situation: str) -> str:
    """
    Generira personalizirani sažetak dokumenta.
    Poziva se SAMO ako je dokument već prošao matching.
    """
    try:
        # Dohvati tekst dokumenta
        doc_text = fetch_doc_text(doc.url) if doc.url else ""

        situation_str = situation if situation else "općenito"
        doc_class = classify_document(doc.title)
        doc_context = _build_doc_context(doc)

        if doc_class == "izmjena":
            task = "Objasni što se točno mijenja u odnosu na prijašnji propis."
            amendment_note = (
                "\n\n⚠️ Napomena: Ovaj dokument mijenja postojeći propis. "
                "PratimZakon prati propise od siječnja 2026. "
                "Za cjeloviti tekst originalnog propisa posjetite Zakon.hr."
            )
        elif doc_class == "procisceni":
            task = "Sažmi trenutno stanje propisa nakon svih izmjena."
            amendment_note = ""
        else:
            task = "Objasni što ovaj novi propis uvodi."
            amendment_note = ""

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": f"""Ti si hrvatski pravni stručnjak koji objašnjava 
zakone običnim ljudima na jednostavnom hrvatskom jeziku.

Korisnikova situacija: {situation_str}

{task}

Pravila pisanja:
- Piši na hrvatskom standardnom jeziku
- Koristi "porezne obveze" ne "poreske obveze"
- Koristi "račun" ne "faktura"
- Koristi "zaposlenik" ne "radnik" ili "employee"
- Bez pravnog žargona
- Bez markdown formatiranja (bez **, bez #)
- 3-4 rečenice maksimalno
- Objasni konkretno što korisnik treba napraviti ili znati

{doc_context}

Tekst dokumenta:
{doc_text[:3000] if doc_text else 'Tekst nije dostupan u bazi.'}""",
                }
            ],
        )

        return msg.content[0].text.strip() + amendment_note

    except Exception as e:
        logging.error(f"Greška pri generiranju sažetka: {e}")
        return ""


def check_document_for_user(doc, user) -> tuple[bool, str]:
    """
    Glavna funkcija — tri razine provjere.
    Vraća (je_relevantno, razlog).

    Razina 1: keyword u naslovu → odmah True, besplatno
    Razina 2: AI brzi check (naslov + institucija + situacija)
    Razina 3: AI duboki check s razlogom zašto
    """

    # Samo relevantni tipovi
    if not is_relevant_type(doc.type):
        return False, ""

    keywords = [kw.keyword for kw in user.keywords] if user.keywords else []
    situation = getattr(user, "situation", "") or ""

    # Razina 1 — besplatno, trenutno
    for kw in keywords:
        if keyword_in_title(kw, doc.title):
            return True, f"Ključna riječ '{kw}' pronađena u naslovu"

    # Ako korisnik nema ni situaciju ni keywords — preskoči AI
    if not situation and not keywords:
        return False, ""

    # Razina 2 — AI brzi check
    if not ai_quick_check(doc, situation, keywords):
        return False, ""

    # Razina 3 — AI duboki check s razlogom
    is_rel, reason = ai_deep_check(doc, situation, keywords)

    if is_rel:
        return True, reason

    return False, ""
