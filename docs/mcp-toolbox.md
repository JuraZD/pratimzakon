# MCP Toolbox — Interna dokumentacija

Read-only AI sloj za operativni pregled PostgreSQL baze bez pisanja ad-hoc SQL upita.

## Svrha

MCP Toolbox je interni pomoćni servis koji daje AI alatima (Claude Code, Codex) strukturiran read-only pristup PostgreSQL bazi. Namijenjen je isključivo inženjerskim i operativnim potrebama — debugganje scrapera, pregled korisničkih podataka, analitička pitanja.

**Nije zamjena za SQLAlchemy u aplikaciji. Ne izlaže se korisnicima.**

## Pravilo Faze 1: samo čitanje

- Koristi se dedicirani PostgreSQL korisnik s privilegijama `CONNECT` i `SELECT` — bez `INSERT`, `UPDATE`, `DELETE`
- Nema generičkog `execute_sql` alata u produkciji
- Svi alati imaju fiksni SQL s eksplicitnim parametrima
- Toolbox ostaje interan — ne spaja se na javni API

## Tablice koje alati smiju čitati

| Tablica | Svrha | Osjetljivi stupci |
|---------|-------|-------------------|
| `users` | Pregled korisničkog računa | `password_hash`, `unsubscribe_token` — **isključeni iz alata** |
| `keywords` | Praćene ključne riječi i filteri | — |
| `documents` | Skrejpani zakoni i uredbe | — |
| `logs` | Revizijski trag scrape, email, match, signup | `detail` može sadržavati naslove dokumenata |
| `keyword_groups` | Grupe ključnih riječi | — |
| `user_settings` | Postavke korisnika (weekly digest) | — |
| `push_subscriptions` | Web push tokeni | `p256dh`, `auth` — **isključeni iz alata** |

## Pitanja na koja alati odgovaraju

**Support / debugging:**
- Koji je plan korisnika X? Je li email verificiran? Ima li uključene obavijesti?
- Koje ključne riječi prati korisnik X?
- Zašto korisnik X nije dobio email danas?

**Operativno / scraper:**
- Koliko je dokumenata uneseno danas / u zadnjih N dana?
- Jesu li dokumenti iz SL i MU dijelova dolazili uredno?
- Koji tipovi dokumenata dominiraju?

**Analitika:**
- Raspodjela korisnika po planovima
- Koji korisnici imaju ključne riječi ali nema matcheva?
- Trend novih registracija

## Alati (Faza 1)

| Alat | Ulaz | Svrha |
|------|------|-------|
| `get_user_by_email` | `email` | Pregled jednog korisnika |
| `list_recent_documents` | `hours_back`, `limit` | Što je scraper unio nedavno |
| `document_ingest_summary` | `days_back` | Zdravlje scrapera po danu/tipu/dijelu |
| `keyword_match_summary` | `days_back`, `user_email?` | Koji matchevi i korisnici su aktivni |
| `notification_health_summary` | `days_back` | Broj poslatih emailova, odjava, registracija |
| `plan_distribution` | — | Raspodjela korisnika po planu |
| `inactive_keyword_users` | `days_back`, `limit` | Korisnici s ključnim riječima ali bez matcheva |
| `top_document_types` | `days_back`, `limit` | Najčešći tipovi dokumenata |

## Konfiguracija

Konfiguracijski fajlovi nalaze se u `infra/mcp-toolbox/`:
- `tools.yaml` — definicije izvora i alata
- `.env.template` — predložak environment varijabli
- `README.md` — upute za pokretanje
- `mcp.json.example` — primjer MCP klijent konfiguracije

## Postojeća FastAPI aplikacija

**Ništa se ne mijenja u `backend/`.**

- SQLAlchemy modeli, rute, autentikacija i logika ostaju nepromijenjeni
- MCP Toolbox se spaja direktno na PostgreSQL kao zasebni proces
- Ne dijeli kod, port ni proces s FastAPI serverom

## Faza 2 (tek nakon što Faza 1 radi dobro)

Mogući dodaci:
- Detaljnija admin analitika (retention, churn)
- Support toolset (pregled logova po korisniku)
- Opcionalni write alati za staging okruženje (nikad produkcija)

Nije prioritet dok Faza 1 ne dokaže vrijednost.
