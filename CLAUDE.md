# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PratimZakon** is a legal document monitoring service for Croatian law (Narodne novine). It scrapes new legislative documents daily, uses Claude AI to match them against user keywords, and sends email notifications.

## Development Commands

```bash
# Install dependencies
pip install -r backend/requirements.txt

# Run DB migrations (idempotent, safe to re-run)
cd backend && python migrate.py

# Start local API server (http://localhost:8000)
cd backend && python run.py

# Create admin user (one-off)
cd backend && python create_admin.py
```

**Scraper commands (also used by GitHub Actions):**
```bash
cd backend
python -m app.scraper.api_scraper daily --no-notify   # scrape today's documents
python -m app.agent                                    # run AI analysis + send emails
python -m app.scraper.api_scraper backfill --from 2015 --to 2026
python -m app.scraper.enrich --batch 500 --offset 0   # enrich ELI metadata
```

There are no automated tests currently. The `tests/` directory exists but is empty.

## Architecture

### Data Flow
1. **Scraper** (`app/scraper/api_scraper.py`) — async `aiohttp` fetches from Narodne novine REST API (rate-limited at 3 req/s), extracts JSON-LD/ELI metadata, stores `Document` records.
2. **Agent** (`app/agent.py`) — 4-phase orchestrator: fetch data → Claude AI analysis → deterministic escalation → send emails. Claude only classifies relevance; code decides who gets notified.
3. **AI Matcher** (`app/ai/matcher.py`) — 3-level matching: title-only keyword check → fast Claude analysis (~500 tokens, cached prompt) → deep Claude analysis (~1000 tokens). Uses `cache_control: ephemeral` on system prompts for prompt caching.
4. **Notifier** (`app/email/notifier.py`) — HTML + plain-text SMTP emails with unsubscribe tokens. Includes simple Croatian suffix stemming for fuzzy keyword matching.

### Key Models (`app/models.py`)
- **User** — email, Argon2 password hash, plan (`free`/`basic`/`plus`), Stripe subscription ID, notification toggle, unsubscribe token
- **Keyword** — user FK, keyword text, optional filters for `doc_type`, `institution`, `part` (SL/MU)
- **Document** — title, URL, PDF URL, type (ZAKON/UREDBA/etc.), institution, legal area, dates, issue number, part (SL/MU)
- **Log** — audit log for email_sent, scrape, subscription_expired, signup, plan_set events

### Subscription & Keyword Limits
Keyword limits enforced per-request: Free=3, Basic=10, Plus=20. Stripe webhooks update user plan. Daily GH Actions job marks expired subscriptions.

### Routers (`app/routers/`)
- `auth.py` — register, login, email verification, profile settings, unsubscribe
- `keywords.py` — CRUD + AI suggestions via Claude
- `search.py` — full-text document search (login required)
- `stripe_router.py` — checkout session creation, plan switching, webhook handler
- `admin.py` — stats, manual plan assignment, trigger scrape (restricted to `ADMIN_EMAIL`)
- `stats.py` — public document statistics

## Infrastructure

- **Backend:** Deployed on Render.com (Python service + PostgreSQL). `render.yaml` configures the service; `wsgi.py` is the WSGI entrypoint.
- **Frontend:** Static HTML (no build step), deployed to GitHub Pages on push to `main`.
- **Keep-alive:** GitHub Actions pings `/health` every 10 minutes to prevent Render free-tier sleep.
- **Migrations:** `migrate.py` uses raw `IF NOT EXISTS` SQL — safe to run on every deploy (not Alembic despite it being in requirements).

## Environment Variables

All secrets go in `backend/.env`. Key variables:
```
DATABASE_URL          # PostgreSQL connection string
SECRET_KEY            # JWT signing key (32+ chars)
ANTHROPIC_API_KEY     # Claude API
STRIPE_SECRET_KEY     # Stripe API
STRIPE_WEBHOOK_SECRET # Stripe webhook verification
SMTP_SERVER/PORT/USERNAME/PASSWORD
FROM_EMAIL / FROM_NAME
BASE_URL              # Backend URL
FRONTEND_URL          # Comma-separated allowed origins for CORS
ADMIN_EMAIL           # Email address with /admin/* access
ENV                   # "production" or "development"
```

## Security Patterns

- Passwords: Argon2 primary, bcrypt fallback for legacy hashes
- JWT Bearer tokens in Authorization header (7-day expiry default)
- Security headers added via middleware in `main.py` (CSP, HSTS in prod, DENY framing, etc.)
- Rate limiting via SlowAPI (per-IP), configured in `limiter.py`
- CORS configured centrally in `main.py` from `FRONTEND_URL` env var
