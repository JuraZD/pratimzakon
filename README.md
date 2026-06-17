# PratimZakon

Servis za praćenje hrvatskog zakonodavstva. Svakodnevno skrejpa nove dokumente iz Narodnih novina, koristi Claude AI za usporedbu s korisničkim ključnim riječima i šalje email obavijesti.

## Što radi

- **Scraper** povlači nove zakone, uredbe i odluke iz Narodnih novina REST API-ja svaki dan
- **AI matcher** uspoređuje naslove dokumenata s ključnim riječima korisnika (3-razinska analiza: title-check → brzi Claude → duboki Claude)
- **Notifier** šalje HTML email s relevantnim dokumentima korisnicima koji su uključili obavijesti
- **Web app** omogućuje korisnicima upravljanje ključnim riječima, pretplatom i postavkama

## Stack

| Sloj | Tehnologija |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy |
| Baza podataka | PostgreSQL |
| AI | Anthropic Claude (`claude-haiku` / `claude-sonnet`) |
| Plaćanje | Stripe |
| Deploy | Render.com (API) + GitHub Pages (frontend) |
| Email | SMTP (Gmail) |

## Lokalni razvoj

```bash
# Instaliraj ovisnosti
pip install -r backend/requirements.txt

# Pokreni DB migracije (idempotentno)
cd backend && python migrate.py

# Pokreni API server (http://localhost:8000)
cd backend && python run.py

# Kreiraj admin korisnika
cd backend && python create_admin.py
```

Potrebne env varijable idu u `backend/.env` — vidi `backend/.env.example` ili CLAUDE.md za popis.

## Scraper i agent

```bash
cd backend

# Skrejpaj današnje dokumente (bez slanja emailova)
python -m app.scraper.api_scraper daily --no-notify

# AI analiza + slanje emailova
python -m app.agent

# Backfill arhivskih dokumenata
python -m app.scraper.api_scraper backfill --from 2015 --to 2026

# Obogati ELI metapodatke
python -m app.scraper.enrich --batch 500 --offset 0
```

## Arhitektura

```
GitHub Actions (cron)
       │
       ▼
  api_scraper.py  ──► PostgreSQL (documents)
       │
       ▼
    agent.py
  ┌────────────────────────────────────────┐
  │ 1. Dohvat novih dokumenata             │
  │ 2. Claude AI analiza relevantnosti     │
  │ 3. Deterministička eskalacija          │
  │ 4. Slanje email obavijesti             │
  └────────────────────────────────────────┘
       │
       ▼
  notifier.py  ──► SMTP ──► Korisnici
```

## Planovi i limiti

| Plan | Ključne riječi | Cijena |
|------|---------------|--------|
| Free | 5 | besplatno |
| Basic | 10 | mjese čno |
| Plus | 20 | mjesečno |

Stripe webhooks automatski ažuriraju plan korisnika. GitHub Actions svaki dan označava istekle pretplate.

## Struktura repozitorija

```
backend/
  app/
    routers/        # FastAPI rute (auth, keywords, search, stripe, admin, stats)
    scraper/        # Scraper i ELI enrichment
    ai/             # Claude matcher (3-razinska analiza)
    email/          # HTML email notifikacije
    tools/          # Claude tool definitions i executor
    models.py       # SQLAlchemy modeli
    agent.py        # Glavni AI orchestrator
    database.py     # DB konekcija
  migrate.py        # Idempotentne SQL migracije
  run.py            # Lokalni dev server
frontend/           # Statički HTML, bez build koraka
docs/               # Interna dokumentacija
infra/              # Infrastrukturna konfiguracija (MCP Toolbox, itd.)
render.yaml         # Render.com deploy konfiguracija
```

## Dokumentacija

- [MCP Toolbox](docs/mcp-toolbox.md) — read-only AI pristup bazi za ops i debugging
- [CLAUDE.md](CLAUDE.md) — upute za Claude Code

## Deploy

Backend se automatski deploya na Render.com pri svakom pushu na `main`. Frontend ide na GitHub Pages. GitHub Actions pokreće scraper svaki dan i pinga `/health` svakih 10 minuta da Render free tier ostane budan.
